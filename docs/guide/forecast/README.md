# norn-forecast

*Audience: contributors working on the forecasting engine, and operators who run forecast / calibrate jobs and serve their results to agents.*

`packages/forecast` (`norn_forecast`) is the platform's forecasting engine. It
turns a `ForecastJob` into quantile forecasts written back to the contract
tables, then exposes them to agents over MCP. Concretely it owns: a small
**model registry** behind one `Forecaster` protocol (a cheap in-process baseline
and TimesFM 2.5 via an HTTP worker), the **runner** that pulls each segment's
series from ClickHouse and materializes future points into `forecast_point`
(with a run audit in `forecast_run`), **rolling-origin calibration** that scores
how much to trust those forecasts, **XReg covariates** that feed confirmed
leading series into TimesFM, and the **MCP server** that serves all of it
read-only to consumers. The package never parses config files or opens its own
warehouse connection — it imports `ForecastJob` / `ForecastPoint`, the typed
settings, and the ClickHouse client from [norn-core](../core/README.md).

## Functionality

### Forecasters (`forecaster.py`, `baseline.py`, `timesfm_model.py`, `timesfm_worker.py`)

All models hide behind one `Forecaster` protocol —
`forecast(values, horizon, covariates=None) -> list[dict]` returning, per step, a
row of `horizon_step` / `y_hat` / `p10` / `p50` / `p90` — so the runner and
calibration depend on the interface, not the model. `make_forecaster(job)` picks
the implementation from `job.model`.

- **`baseline-seasonal-naive`** (`BaselineForecaster` → `seasonal_naive_forecast`)
  — the default model, runs **in-process** with no torch. It repeats the value
  from the previous seasonal cycle as the point forecast and derives the
  `p10/p90` band from a normal approximation (`z`-multipliers from the configured
  quantiles times a `sigma` estimated from period-over-period residuals), widening
  `~sqrt(cycles ahead)`. It ignores covariates and serves as the honest reference
  baseline. See [Methodology](methodology.md) for the interval math.
- **`timesfm-2.5`** (`TimesFMForecaster`) — an HTTP client that `POST`s to the
  **TimesFM worker** at `forecast.timesfm.worker_url` (default
  `http://localhost:9100`); no torch is imported in the platform process. The
  worker (`timesfm_worker.py` / `timesfm_model.py`) is **self-contained** — a
  separate container that loads and compiles TimesFM 2.5 once at startup and reads
  its own limits from `NORN_TIMESFM_*` env vars (it does **not** depend on
  `norn_core.config`). It exposes `POST /forecast` and `GET /health`. If the
  chosen model is `timesfm-2.5` and the worker is unreachable, the run **fails
  explicitly** (no silent fallback to the baseline).
- **`LogTransformForecaster`** — an optional wrapper (`job.transform: log`) that
  forecasts in log-space for positive multiplicative series and exponentiates the
  point and every quantile back; it falls back to the base forecaster if any value
  is `<= 0`.

### Runner (`runner.py`)

`run_job(job, client, forecaster=None) -> run_id` executes a single run:

1. **Extract** — expand the job into segments (`SELECT DISTINCT <dimensions>`),
   and pull the last `context_length` points of the `metric` per segment from
   ClickHouse, chronological. Naive-UTC timestamps are tagged UTC so the insert
   keeps the true instant.
2. **Per-segment forecast** — run the series (plus covariates, when present)
   through the chosen forecaster.
3. **Write-back** — future points to `forecast_point` (`forecast_ts =
   last_actual + step * horizon_step`), and a run summary to `forecast_run`. A
   covariate run is marked `model_version = v0+xreg`.
4. **Run audit** — the whole loop runs under `try`: a forecaster failure writes
   `forecast_run` with `status='failed'` and the error, then re-raises (an
   input/identifier `ValueError` is a rejected job and is **not** recorded as a
   failed run). A success always writes a `forecast_run` row, even with no points.

### Calibration (`calibration.py`)

`calibrate_job(job, client, forecaster=None)` runs a **rolling-origin backtest**
per segment: it rewinds the series by `horizon` `n_cutoffs` times
(`forecast.calibration.n_cutoffs`), forecasts from the past only, and compares
against the held-out truth. `backtest_metrics` reports per segment:

| Metric | Meaning |
| --- | --- |
| `coverage` | Share of actuals inside the `p10..p90` band (target ≈ the band width, e.g. 0.8). |
| `wape` | `sum|actual − y_hat| / sum|actual|` (point accuracy, lower is better). |
| `mape` | Mean absolute percentage error over nonzero actuals. |
| `bias` | `mean(y_hat − actual)`; negative = forecasts run low. |
| `n_points` | Number of held-out points scored. |

Aggregates land in `forecast_segment` (with an **`is_sparse`** flag when a segment
had no scorable points, so the agent treats its metrics with caution); the
per-point `(forecast, actual)` pairs are also persisted to `forecast_point`,
tagged `model_name '<model> (backtest)'` (with a `+xreg` marker when covariates
are active). For an xreg job the same covariates are reconstructed at each cutoff,
trimmed to the end of context to avoid lookahead leakage, so calibration measures
exactly the xreg model.

### Covariates / XReg (`covariates.py`)

When a job carries covariate specs, the runner and calibration attach leading
series as TimesFM **dynamic numerical covariates** (XReg).

- **Specs** — `resolve_covariate_specs` takes the job's explicit `covariates`
  and, when `job.use_dependencies` is true, appends the confirmed leads from
  `dependency_explanation` (`is_real=1`, `direction='source_leads'`) produced by
  [norn-agent](../agent/README.md).
- **Alignment** — `covariate_series` reads each leader from the long mart by
  `metric_name` + `segment_key`; `build_covariate_array` shifts it by `lag` and
  aligns it across context + horizon.
- **`horizon_policy`** (`forecast.covariates.horizon_policy`) — `strict` drops a
  leader whose `lag < horizon` (its future is not known across the whole horizon);
  `ffill` forward-fills the last known value to extend the leader. A gap in the
  leader history makes the covariate unusable (dropped).
- **`xreg_mode`** (`forecast.covariates.xreg_mode`) — passed to TimesFM 2.5's
  `forecast_with_covariates` (`xreg + timesfm` or `timesfm + xreg`, spaced form).

### MCP server (`mcp_server.py`, `mcp_tools.py`)

`build_server()` is a thin [FastMCP](https://github.com/jlowin/fastmcp) wrapper
over the pure `mcp_tools` functions, registering exactly **11 read-only tools**
and binding to `mcp.host` / `mcp.port` (default `127.0.0.1:9200`). Every tool runs
a single read against the forecast / dependency contract tables (always the
freshest run) and returns plain JSON — there is **no write path**. The tools cover
forecasts and expected ranges, band classification, calibration, lead/lag
dependencies and their history, run/forecast status, and metric/segment discovery.
See the full reference in [MCP tool reference](mcp.md).

## Configuration

The package reads two config sections via `norn_core.config`. Every field is
overridable at runtime with the section's env prefix (env beats YAML); nested
keys use the `__` delimiter.

### `forecast.yml` — prefix `NORN_FORECAST_`

| Field | Type | Description |
| --- | --- | --- |
| `defaults.horizon` | int | Default forecast depth, in series steps (e.g. `30`). A job may override it. |
| `defaults.context_length` | int | Default history window length (number of points) fed to the model. |
| `defaults.seasonality` | int | Default seasonality period, in steps (e.g. `7` = weekly). |
| `quantiles` | list[float] | Forecast quantiles forming the band, e.g. `[0.1, 0.5, 0.9]` (low / median / high). |
| `timesfm.worker_url` | string | URL of the HTTP TimesFM worker (default `http://localhost:9100`). |
| `timesfm.max_context` | int | Upper bound on the context length the model accepts (default `1024`). |
| `timesfm.max_horizon` | int | Upper bound on the model's forecast depth (default `1024`). |
| `calibration.n_cutoffs` | int | Number of rolling-origin cutoffs used to score quality (default `8`). |
| `covariates.horizon_policy` | string | `strict` (covariate `lag >= horizon`) or `ffill` (forward-fill / extend the leader). |
| `covariates.xreg_mode` | string | XReg mode (TimesFM 2.5 spaced form): `xreg + timesfm` or `timesfm + xreg`. |
| `retention_months` | int | TTL on the contract tables (months), on top of the monthly `PARTITION BY`. `0` = no auto-deletion. |

Common overrides use the `__` nesting, e.g.:

```bash
export NORN_FORECAST_TIMESFM__WORKER_URL=http://timesfm:9100   # forecast.timesfm.worker_url
export NORN_FORECAST_COVARIATES__HORIZON_POLICY=ffill          # forecast.covariates.horizon_policy
export NORN_FORECAST_CALIBRATION__N_CUTOFFS=8                   # forecast.calibration.n_cutoffs
```

### `mcp.yml` — prefix `NORN_MCP_`

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `host` | string | `127.0.0.1` | Interface the MCP server binds. `127.0.0.1` = loopback only; set a reachable address (e.g. `0.0.0.0` via `NORN_MCP_HOST`) to expose it. |
| `port` | int | `9200` | TCP port of the MCP server. |

The **TimesFM worker** is configured separately from the platform — it reads its
own optional env vars (`NORN_TIMESFM_MAX_CONTEXT`, `NORN_TIMESFM_MAX_HORIZON`,
`NORN_TIMESFM_XREG_MODE`), not `NORN_FORECAST_*`. See the TimesFM worker section in
[Deployment](../deployment.md).

## In this folder

- [Methodology](methodology.md) — how a forecast is built, the interval math, the
  calibration metrics, and how to read them.
- [MCP tool reference](mcp.md) — the full 11-tool surface, connection details, and
  freshness / degradation semantics.

## See also

- [norn-core](../core/README.md) — the `ForecastJob` / `ForecastPoint` contracts,
  typed settings, and the ClickHouse client this package builds on.
- [norn-agent](../agent/README.md) — where the confirmed lead/lag dependencies
  used as `use_dependencies` XReg covariates come from.
- [Jobs](../jobs.md) — authoring forecast / calibrate / deps jobs.
- [Deployment](../deployment.md) — running the platform and the TimesFM worker.
