# Jobs

*Audience: job authors who define what norn forecasts and which dependencies it analyzes.*

norn is driven by **jobs** — small YAML files that describe what to forecast (a
forecast job) or which lead/lag relationships to analyze (a dependency job). You
run a job with the CLI; results land in the forecast/dependency **contract
tables** in ClickHouse, where the [MCP tools](mcp.md) read them.

This page covers forecast jobs, calibration, dependency jobs, schema ownership,
and the contract tables they write to.

---

## Forecast jobs

A forecast job is a YAML file validated against the `ForecastJob` contract. Run
it with:

```bash
uv run norn forecast path/to/job.yml
```

### Fields

| field | type | default | meaning |
|---|---|---|---|
| `metric` | string | *(required)* | The metric (`metric_name`) to forecast, e.g. `ot`. |
| `source` | string | *(required)* | ClickHouse table to read history from, e.g. `fct_ot`. |
| `grain` | `daily` \| `hourly` | `daily` | Time-series frequency. |
| `dimensions` | list of strings | `[]` | Dimension columns to segment by; each combination becomes a segment. |
| `filter` | map of string→string | `{}` | Column=value equality filters that scope the source. |
| `covariates` | list of `CovariateSpec` | `[]` | Explicit leader series fed as XReg covariates (see below). |
| `use_dependencies` | bool | `false` | Auto-attach confirmed dependencies as covariates (see below). |
| `horizon` | int | from `forecast.defaults.horizon` | Steps to forecast ahead. |
| `context_length` | int | from `forecast.defaults.context_length` | History window length. |
| `seasonality` | int | from `forecast.defaults.seasonality` | Seasonal period. |
| `model` | string | `baseline-seasonal-naive` | Forecaster: `baseline-seasonal-naive` or `timesfm-2.5`. |
| `transform` | `none` \| `log` | `none` | `log` forecasts in log-space for positive multiplicative series (falls back to the base model if any value ≤ 0). |
| `schedule` | string | *(none)* | Optional schedule hint (cron-style); orchestration is external. |

Unset tunables (`horizon`, `context_length`, `seasonality`) are filled from
`forecast.defaults` in [configuration](configuration.md) at run time — explicit
job values always win.

A `CovariateSpec` (one entry under `covariates`) has:

| field | type | default | meaning |
|---|---|---|---|
| `metric` | string | *(required)* | The leader metric to read. |
| `segment` | string | *(required)* | Leader segment key, e.g. `dataset=ETTh1\|feature=hufl`. |
| `lag` | int | *(required)* | Lag (in grain steps) to apply to the leader. |
| `mart` | string | `mart_metric` | Long store to read the leader from. |

### Example

This is the `ot_baseline` job from the ETT example instance — forecasting oil
temperature (`ot`) per `(dataset, feature)` segment:

```yaml
# instances/ett/forecasts/ot_baseline.yml
metric: ot
source: fct_ot
grain: hourly
dimensions: [dataset, feature]
horizon: 24
context_length: 512
seasonality: 24
model: baseline-seasonal-naive
transform: none
```

```bash
uv run norn forecast instances/ett/forecasts/ot_baseline.yml
```

The TimesFM variant (`instances/ett/forecasts/ot_timesfm.yml`) is identical
except `model: timesfm-2.5`.

### Choosing a model

- **`baseline-seasonal-naive`** (default) — runs entirely inside the platform.
  No external worker is required; this is the right choice to get a forecast
  with zero extra infrastructure.
- **`timesfm-2.5`** — delegates to the TimesFM worker (see
  [deployment](deployment.md)), which the platform reaches via
  `forecast.timesfm.worker_url`. If the worker is **unreachable**, the run
  **fails explicitly**: norn records the forecast run with
  `forecast_run.status=failed` rather than silently falling back to the
  baseline. To forecast without the worker, use a `baseline-seasonal-naive`
  job.

### Covariates and `use_dependencies` (XReg)

norn can feed *leader* series into a forecast as exogenous regressors (XReg).
There are two ways to supply them:

- **Explicit `covariates`** — list one or more `CovariateSpec` entries naming
  the leader `metric`, `segment`, and `lag`.
- **`use_dependencies: true`** — auto-attach the **confirmed** lead/lag
  dependencies for the target (discovered by a [dependency job](#dependency-jobs))
  as covariates, without listing them by hand. The platform reads
  `dependency_explanation` and attaches every row where `is_real=1` and
  `direction='source_leads'`.

In the ETT example, `instances/ett/forecasts/ot_timesfm_xreg.yml` sets
`use_dependencies: true` on the TimesFM job, so the load features confirmed as
leads of `ot` by the `deps/*.yml` runs become XReg covariates:

```yaml
# instances/ett/forecasts/ot_timesfm_xreg.yml
metric: ot
source: fct_ot
grain: hourly
dimensions: [dataset, feature]
horizon: 24
context_length: 512
seasonality: 24
model: timesfm-2.5
transform: none
use_dependencies: true
```

How covariate history is extended over the forecast horizon is controlled by
`forecast.covariates.horizon_policy` in [configuration](configuration.md):

- **`strict`** (default) — the covariate must already cover the full horizon,
  otherwise the run does not fabricate future leader values. A leader whose
  `lag` is shorter than `horizon` is dropped under this policy.
- **`ffill`** — forward-fill the last known leader value across the horizon.

For the ETT XReg job the lead lags are shorter than the 24-hour horizon, so run
it with `ffill`:

```bash
NORN_FORECAST_COVARIATES__HORIZON_POLICY=ffill \
  uv run norn forecast instances/ett/forecasts/ot_timesfm_xreg.yml
```

The XReg backend is selected by `forecast.covariates.xreg_mode`.

---

## Calibration

Calibration measures how trustworthy a job's forecasts are by running a
**rolling-origin** backtest on the *same* job file:

```bash
uv run norn calibrate path/to/job.yml
```

The number of rolling cutoffs comes from `forecast.calibration.n_cutoffs` in
[configuration](configuration.md). Per-segment quality metrics are written to
the `forecast_segment` table:

| column | meaning |
|---|---|
| `coverage` | Share of actuals that fell inside the predicted interval. |
| `wape` | Weighted absolute percentage error. |
| `mape` | Mean absolute percentage error. |
| `bias` | Systematic over-/under-forecast. |
| `n_points` | Number of points the segment was evaluated on. |
| `is_sparse` | Flag: the segment had too little history for a reliable verdict. |

These same fields surface through the `get_calibration` [MCP tool](mcp.md), so
an agent can check calibration (including `is_sparse`) before trusting a band.

---

## Dependency jobs

A dependency job discovers lead/lag relationships between two segments of a
metric, validated against the `DependencyJob` contract. Run it with:

```bash
uv run norn deps path/to/dep.yml
```

### Fields

| field | type | default | meaning |
|---|---|---|---|
| `source_segment` | string | *(required)* | Candidate leader segment, e.g. `dataset=ETTh1\|feature=hufl`. |
| `target_segment` | string | *(required)* | Segment whose movement we want to explain, e.g. `dataset=ETTh1\|feature=ot`. |
| `metric` | string | *(required)* | Metric (`metric_name`) analyzed for both segments. |
| `mart` | string | `mart_metric` | Long store to read both series from. |
| `max_lag` | int | from `agent.max_lag` | Maximum lag (in grain steps) to test. |
| `context_length` | int | from `agent.context_length` | History window for the analysis. |
| `methods` | list of strings | from `agent.methods` | Statistical methods, e.g. `lagged_cross_correlation`, `granger`. |

Unset `max_lag`, `context_length`, and `methods` are filled from the `agent`
section of [configuration](configuration.md).

### Example

This is `hufl_etth1` from the ETT example instance — testing whether the HUFL
load feature leads oil temperature (`ot`) within ETTh1. Note the `metric` here
is the long-store `metric_name` (`reading` in the ETT marts), not the
forecast-level `ot`:

```yaml
# instances/ett/forecasts/deps/hufl_etth1.yml
source_segment: dataset=ETTh1|feature=hufl
target_segment: dataset=ETTh1|feature=ot
metric: reading
mart: mart_metric
max_lag: 48
```

```bash
uv run norn deps instances/ett/forecasts/deps/hufl_etth1.yml
```

### Outputs and graceful degradation

A dependency run writes to **two** tables:

- **`metric_dependency`** — the statistical evidence (lag, direction,
  confidence). Always written when the analysis runs.
- **`dependency_explanation`** — the LLM's structured interpretation
  (`is_real`, `explanation`, `caveats`, `change_note`). Written **only when the
  LLM is available**.

If the configured LLM provider is unavailable, the run **degrades explicitly**:
it raises `LLMUnavailable` internally, logs the failure (ERROR with traceback),
and skips the explanation step — the **statistical evidence in
`metric_dependency` is still written**. There is no silent fabrication of a
verdict. Consumers detect this via the `explained` flag returned by the
`get_dependencies` [MCP tool](mcp.md): `explained=false` means a numeric
dependency exists but no LLM verdict was produced.

---

## Schema ownership

Who creates the contract tables is controlled by `database.manage_schema` in
[configuration](configuration.md):

- **`manage_schema: true`** — norn owns the schema. `uv run norn schema-apply`
  creates the contract tables (idempotent), and forecast/dependency runs can
  assume the tables exist.
- **`manage_schema: false`** — norn runs **no DDL** at all. You own the schema
  externally (dbt, migrations). `uv run norn schema-apply` refuses to act and
  points you to:

  ```bash
  uv run norn print-schema
  ```

  which emits the contract DDL to stdout so you can feed it into your own
  migrations or dbt models. This is the recommended mode for cloud/managed
  ClickHouse — see [deployment](deployment.md).

---

## Contract tables

Jobs read your marts and write to these five tables (defined in the
integration schema):

| table | written by | what it holds |
|---|---|---|
| `forecast_run` | every forecast/calibrate run | One row per run: `status`, model name/version, timestamps, segment counts — the registry linking points and quality metrics. |
| `forecast_point` | `norn forecast` | The forecast values: one row per (metric, segment, horizon step) with `y_hat` and the `p10`/`p50`/`p90` interval. |
| `forecast_segment` | `norn calibrate` | Per-segment quality for a run: `coverage`, `wape`, `mape`, `bias`, `n_points`, `is_sparse`. |
| `metric_dependency` | `norn deps` | Discovered statistical lead/lag relationships between segments (lag, direction, confidence). |
| `dependency_explanation` | `norn deps` (LLM available) | The LLM's structured interpretation of a dependency: `is_real`, explanation, caveats, change note. |

---

## Running jobs on a schedule

A job is a one-shot CLI invocation. To run jobs repeatedly, the built-in
scheduler reads a `jobs.yml` manifest that maps each job file to an `action`
(`forecast` | `calibrate` | `deps`) and a cron `schedule`:

```bash
uv run norn scheduler --manifest instances/ett/deploy/jobs.yml
```

The manifest's `schedule` is the single source of truth — a `schedule:` hint
inside a forecast job YAML is ignored by the scheduler. See
[deployment](deployment.md) for the manifest format and the scheduler service.

---

## See also

- [Configuration](configuration.md) — `forecast`, `agent`, and `database`
  sections that supply job defaults, the LLM provider, and `manage_schema`.
- [MCP](mcp.md) — the read tools that serve these contract tables to agents.
- [Deployment](deployment.md) — the built-in scheduler and the services that
  run jobs.
- Domain specifics (concrete metrics, marts, segments) live in an instance
  repo — e.g. the ETT example instance (`norn-ett-instance`, mounted at
  `instances/ett`).
- Project root: [README](../../README.md).
