# Norn — monorepo, ERD and tech stack

*Accompanies `erd.mermaid` and `architecture.mermaid`.*

> **Platform invariant.** norn is a vendor-neutral, domain-AGNOSTIC forecasting platform: multi-segment forecasting of metrics and dependency discovery on top of any warehouse via a generic contract (`forecast_point`/`forecast_segment`), with configurable model/provider/DB and an MCP contract. The platform code (`packages/*`, `cli`) carries NO domain defaults — no built-in metrics, dimensions, ingestion formats, dashboards, prompts, nor any choice of LLM model. All domain specifics live in a separate instance repo, attached as a submodule. The public open-source example is the **ETT instance** (`instances/ett`, repo `norn-ett-instance`): the public ETDataset "ETT-small" (Electricity Transformer Temperature), forecast metric `ot` (oil temperature), with the 6 transformer-load channels as covariates. Any concrete domain in this document (the metric `ot`, dimensions `dataset`/`feature`, segments such as `dataset=ETTh1|feature=ot`, model choice) is a flagged EXAMPLE pointing at the ETT instance, not a platform requirement; domain details live in the instance repo. (The platform also offers a generic `log` transform for positive multiplicative series; the ETT jobs use `transform: none`.)

A plug-and-play sidecar to Lightdash: with a single command an analyst stands up forecasts and dependency analysis on top of their existing `dbt + ClickHouse + Lightdash` stack. We do **not fork Lightdash and do not write our own BI** — we add three layers alongside it.

---

## 1. Monorepo layout (5 packages + CLI)

```text
norn/                    # repository tysee/norn (uv workspace, all packages v0.0.0)
├── packages/
│   ├── core/           # 1) foundation: typed config, contract models, ClickHouse client
│   ├── integration/    # 2) contract schema (schema.sql, 5 tables) + apply/prepare
│   ├── forecast/       # 3) forecasters + calibration + covariates + MCP server + TimesFM worker
│   ├── agent/          # 4) dependency-analysis: lead/lag stats + LLM judge (PydanticAI)
│   └── scheduler/      # 5) built-in cron scheduler (APScheduler) + FastAPI control API
├── cli/                # typer orchestrator: `norn forecast|calibrate|deps|mcp|scheduler|…`
├── deploy/             # docker-compose (infra + services), Dockerfiles, Lightdash bring-up
├── config/             # central YAML config (database/forecast/agent/mcp/scheduler)
├── instances/          # domain instances: ett + crypto are submodules; example is a plain tracked dir (copyable template)
└── pyproject.toml      # uv workspace, shared lint/types, requires-python >=3.12
```

The forecasting strategy maps onto the packages: **contract + config** (`core`/`integration`), **forecasting** (`forecast`), **dependency discovery** (`agent`), and **scheduling** (`scheduler`). Each package is an installable distribution (`norn-core`, `norn-integration`, `norn-forecast`, `norn-agent`, `norn-scheduler`) plus the `norn` CLI (`norn = norn_cli.main:app`).

---

## 2. Tech stack and the "fewer dependencies" principle

Base rule: use off-the-shelf if it does not impose unnecessary constraints; our own code is only glue. Each dependency must "earn its place".

| Part | Language / runtime | Off-the-shelf (reused) | Our code (glue) |
|-------|----------------|---------------------------|-----------------|
| core | Python ≥3.12 | `pydantic` / `pydantic-settings` (typed config); `pyyaml`; `clickhouse-connect` | typed YAML+env settings, contract models, DSN parsing, `_safe_identifier` SQL guard |
| integration | Python ≥3.12 | `clickhouse-connect` (via `core`); the instance's `dbt-core` + `dbt-clickhouse` (subprocess) | canonical contract DDL (`schema.sql`, 5 tables), idempotent `apply_schema` / `prepare_schema` |
| forecast | Python (worker env, see §5) | `timesfm` (2.5) + `torch` (worker container); `numpy`; `httpx`; `FastAPI`; `mcp` (FastMCP) | extract→group→inference→write-back; baseline; calibration; covariates (XReg); MCP server |
| agent | Python ≥3.12 | `pydantic-ai` (PydanticAI); `numpy`/`scipy`/`statsmodels` (lagged corr, Granger); provider LLM SDK | analysis orchestration, building explanations with caveats; optional HTTP judge worker |
| scheduler | Python ≥3.12 | `apscheduler` (cron); `fastapi` / `uvicorn` | jobs.yml manifest, cron dispatch (forecast/calibrate/deps), retries, control API |
| cli | Python ≥3.12 | `typer` | `forecast` / `calibrate` / `deps` / `schema-apply` / `print-schema` / `mcp` / `scheduler` / `up` |
| metadata | — | none (no addon Postgres): jobs are `forecasts/*.yml` in the instance, the run log lives in the ClickHouse contract tables | — |

Deliberately NOT pulling in: our own ORM (configs are YAML, the run log lives in ClickHouse), our own dashboard engine (Lightdash), our own transform (dbt). We **do** ship a thin built-in scheduler (`packages/scheduler`, APScheduler single-replica) so the instance can drive cron jobs without a host crontab; a host `cron` (see the ETT instance's `crontab.sample`) remains a fully supported alternative.

---

## 3. Stores and where the ERD entities live

There is **no addon metadata store** — the platform persists everything in ClickHouse and reads
forecast jobs from YAML. Legend for `erd.mermaid`:

- **`[CH-contract]` ClickHouse contract tables** — the 5 tables norn owns and writes, defined by the
  canonical DDL in `packages/integration/src/norn_integration/schema.sql`: `forecast_run`,
  `forecast_point`, `forecast_segment`, `metric_dependency`, `dependency_explanation`. All are
  `ENGINE = MergeTree`, `PARTITION BY toYYYYMM(created_at)`, with an optional `TTL` (see §6). The run
  log (`forecast_run`) is the durable audit; there is no separate Postgres registry.
- **`[CH-mart]` ClickHouse marts/views** — built by the **instance's** dbt project and read by norn. For
  the ETT example (`instances/ett/dbt/models`): the long store `mart_metric` (wide→long unpivot of the
  7 ETT channels, the series and covariate source), `fct_ot` (`mart_metric where feature='ot'`, the
  forecast target), plus the overlay views Lightdash reads — `actual_vs_forecast`, `calibration`,
  `backtest_point`, `feature_leads`.

**Job & analysis definitions live in YAML, not a table.** A forecast job is a `forecasts/*.yml` file
(the `ForecastJob` contract in `packages/core/.../contract.py`); a dependency job is a `forecasts/deps/*.yml`
file (`DependencyJob` in `packages/agent/.../contract.py`). The scheduler manifest (the instance's
`deploy/jobs.yml`, e.g. `instances/ett/deploy/jobs.yml`) references those job files by path. The ERD therefore omits a relational job/connection registry: the
`forecast_run.forecast_job` column carries the job path, and the contract tables are keyed by
`forecast_run_id` / `analysis_run_id`.

---

## 4. CLI and runtime UX

The `norn` CLI (typer) has eight commands — none of them auto-discover or scaffold; jobs are authored
as YAML in the instance:

```text
norn schema-apply              # idempotently create the contract tables (honors database.manage_schema)
norn print-schema              # print the canonical contract DDL (feed your own dbt/migrations)
norn forecast  <job.yml>       # extract -> forecast -> write forecast_run/point; prints run_id
norn calibrate <job.yml>       # rolling-origin backtest -> forecast_segment; prints calibration run_id
norn deps      <job.yml>       # lead/lag analysis + LLM explanation; prints deps run_id
norn mcp                       # run the MCP server (streamable-http) for agents
norn scheduler --manifest <jobs.yml>   # run the built-in cron scheduler + control API
norn up                        # [local-dev only] docker compose up the ClickHouse sidecar
```

`norn up` is a developer convenience only: it runs `docker compose up -d clickhouse` from
`deploy/docker-compose.yml` (override with `NORN_COMPOSE_FILE`). For cloud/k8s the platform connects
purely via `NORN_CLICKHOUSE_URL` / `NORN_DB_*` and skips `norn up`. The forecast/MCP/scheduler/agent
services are deployed as containers via `deploy/docker-compose.services.yml` (see §8).

### Local BI stack (debugging)

`deploy/docker-compose.yml` brings up the **infra** stack locally: ClickHouse + the full Lightdash
stack (its Postgres, headless browser, minio) + a `setup`-profile one-shot (`lightdash-init`) that
registers the warehouse/dbt connection. The instance's dbt project (e.g. `instances/ett/dbt`, models
`mart_metric` / `fct_ot` / `actual_vs_forecast` / `calibration` / `backtest_point` / `feature_leads`)
is mounted in. The TimesFM worker is a separate torch-pinned container (`deploy/timesfm.Dockerfile`,
`:9100`); the forecast layer talks to it over HTTP behind a `Forecaster` interface (baseline remains
the fallback). Raw-data seeding — the ETT instance's `ett backfill` / `ett update` filling `raw_ett` —
is the instance's concern, outside the platform.

**MCP layer (agents):** `norn mcp` brings up a FastMCP server (`norn`, streamable-http,
`http://{host}:{port}/mcp`, default `127.0.0.1:9200`) with 11 read tools:
`get_forecast` / `get_expected_range` / `classify_levels_vs_band` / `get_band_position` /
`get_calibration` (incl. is_sparse) / `get_dependencies` (explained flag + numeric fallback on LLM
degradation) / `get_dependency_history` / `get_run_status` / `get_forecast_status` / `list_metrics` /
`list_segments` on top of the tables `forecast_point` / `forecast_segment` / `forecast_run` /
`metric_dependency` / `dependency_explanation`. Discovery (`list_*`) and status/freshness
(`get_*_status`) let an agent find series and assess forecast freshness. "Lightdash for humans, MCP for
agents." Example: `get_forecast(metric="ot", segment="dataset=ETTh1|feature=ot")`.

**Dependency agent (`packages/agent`):** a PydanticAI dependency-analysis agent. The two methods
(`lagged_cross_correlation` + `granger`) run on the series and produce evidence → the agent judges
reality and explains → `metric_dependency` (numbers) + `dependency_explanation` (decision). `norn deps
<job.yml>`; MCP `get_dependencies` returns both the numbers and the agent's decision. In the ETT
example each deps job tests one transformer-load channel as a lead of `ot` within a dataset (e.g.
`dataset=ETTh1|feature=hufl` → `dataset=ETTh1|feature=ot`). Tests run on the PydanticAI `TestModel` (no
real LLM). A confirmed lead (`is_real=1` AND `direction='source_leads'`) becomes a TimesFM covariate
(XReg, §6).

**The agent's LLM provider and model** are configurable (`config/agent.yml` → `provider` / `model`):
ollama (local), openai-api, openai-oauth (bearer), openrouter, anthropic-api. The concrete
model and provider are the instance's choice in `config/agent.yml`; the platform has NO default LLM model.
Secrets come from env (OPENAI_API_KEY / NORN_OPENAI_OAUTH_TOKEN / OPENROUTER_API_KEY /
ANTHROPIC_API_KEY). For local Ollama: a running daemon on :11434 + `ollama pull <model>`
(model from the instance config). When the provider is unavailable/incorrect, `norn deps` degrades
(writes metric_dependency, without an explanation) instead of crashing.

---

## 5. Python interpreter split (torch/dbt isolation)

The platform packages target **Python ≥3.12** (`requires-python = ">=3.12"`). Two heavy dependencies are
kept out of the platform process so a newer interpreter is never blocked by lagging wheels:

- **`torch` / `timesfm`** — only the TimesFM worker needs them.
- **`dbt-core` / `dbt-clickhouse`** — the instance's transform, not the platform's.

How the monorepo isolates this:

1. **TimesFM worker** runs in its own container pinned to **Python 3.12** (`deploy/timesfm.Dockerfile`,
   pinned for the torch/timesfm wheels), exposing `POST /forecast` over FastAPI/HTTP. The forecast
   package only ships an `httpx`-based client — torch is **not** a platform dependency. The light
   platform image (`deploy/norn.Dockerfile`) runs on Python 3.13.
2. **dbt** is invoked as a **subprocess** (CLI) by the instance, not imported into our process → dbt's
   Python version is decoupled from ours.
3. `core` / `integration` / `agent` / `scheduler` are pure Python (+ numpy/scipy/statsmodels) and carry
   no torch/dbt constraint.

XReg (`forecast_with_covariates`) additionally needs an importable `jax` + `scikit-learn` in the worker
image (plain CPU `jax`, not the upstream `[xreg]` extra which pins `jax[cuda]`).

---

## 6. Configuration (YAML-native)

All generic platform settings live in a central `config/` (five files, split by concern:
`database.yml`/`forecast.yml`/`agent.yml`/`mcp.yml`/`scheduler.yml`), read by the typed layer
`norn_core.config` (pydantic-settings, one section per file, `lru_cached` `get_settings`). Priority:
**init-args (tests) > env > YAML** — there are **no Python field defaults**, so a key missing from all
sources raises a `ValidationError` at startup (the nested env delimiter is `__`). Secrets live **only**
in env: the DB password is `NORN_DB_PASSWORD` (never in YAML), the LLM provider keys are
`OPENAI_API_KEY` / `NORN_OPENAI_OAUTH_TOKEN` / `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY`. A full DSN
override is `NORN_CLICKHOUSE_URL` (config, not a secret). `NORN_CONFIG_DIR` overrides the config path
(default `config`). Domain values (the metric, dimensions) do NOT go into the platform config — that is
the instance.

Magic constants are eliminated: baseline intervals are derived from `forecast.quantiles`
(normal approximation), the Granger significance/threshold from `agent.*`, the TimesFM quantile
columns are derived from the requested quantiles. Numeric tolerances (eps) are named constants.

**Covariates (XReg):** a forecast job may declare explicit `covariates` (metric/segment/lag/mart) or set
`use_dependencies: true` (take confirmed leads from `dependency_explanation` where `is_real=1` AND
`direction='source_leads'`). The runner builds a timestamp-aligned leader series over context+horizon
(policy `strict` | `ffill` from `forecast.covariates.horizon_policy`) and passes it to TimesFM 2.5 as
`dynamic_numerical_covariates` (`forecast_with_covariates`, `xreg_mode` "xreg + timesfm" |
"timesfm + xreg"). Under `strict`, a leader whose lag < horizon is dropped. Without covariates — a
regular forecast (default, unchanged). The baseline forecaster ignores covariates. In the ETT example,
`ot_timesfm_xreg.yml` sets `use_dependencies: true` and is run with
`NORN_FORECAST_COVARIATES__HORIZON_POLICY=ffill` (the confirmed load-feature leads have lags < the 24h
horizon).

**Config — YAML-native with no hidden defaults:** settings fields have no Python defaults; the value
is taken from `config/<section>.yml` (or an env override), and a missing required key → an explicit
`ValidationError` at startup. The DB secret (`password`) comes only from env `NORN_DB_PASSWORD`. The LLM
inference mode (`agent.output_mode`: native|tool|prompted) and `agent.base_url` are explicit configuration, without
fallbacks in code. **LLM degradation is explicit:** `judge_dependencies` raises `LLMUnavailable`,
`analyze_dependencies` catches it at the boundary (ERROR log with traceback), returns an `AnalysisResult`
(`explained=False` + reason), and the CLI prints `⚠ LLM explanation skipped: …`; the statistics
(`metric_dependency`) are always written.

**Ownership of the contract tables' schema (`database.manage_schema`):** norn is warehouse-table-native,
dbt-optional. `true` (default) — norn idempotently creates its own contract tables in its DB
(zero-setup, greenfield/local). `false` — norn does NOT execute DDL (INSERT only); the tables are
provisioned by the user via their own dbt/migrations, the canonical DDL is printed by `norn print-schema`; before
writing, norn runs a pre-flight check and, if the tables are missing, fails explicitly with `ContractSchemaMissing`.
This way the platform does not impose runtime DDL on a governed store. dbt is the typical, but not mandatory,
way to build both the mart and these tables.

**Storing the contract tables as they grow:** one table per contract type, but with
`PARTITION BY toYYYYMM(created_at)` and a configurable `TTL` (`forecast.retention_months`,
default 12 months; 0 = no TTL). This is idiomatic for ClickHouse (we do not split into separate tables).
**Upgrading existing tables:** ClickHouse does not add `PARTITION BY` via `ALTER` —
already-created tables require recreation (drop + `norn schema-apply`); forecast data is
reproducible by re-running the jobs, so this is a safe, routine step. `TTL` can be added separately
with `ALTER TABLE ... MODIFY TTL ...`.

---

## 7. Scheduling and services (deployment)

Two compose files in `deploy/`, **same** compose project, deliberately split so `down` on one can never
take the other down (never pass `--remove-orphans` to either; `COMPOSE_IGNORE_ORPHANS=true` silences the
cosmetic warning):

- `deploy/docker-compose.yml` — **infra** only: ClickHouse + the Lightdash stack (Postgres, headless
  browser, minio) + the `setup`-profile `lightdash-init`. Bring this up **first**.
- `deploy/docker-compose.services.yml` — norn's **own** services, each behind an opt-in profile:
  `timesfm` (`:9100`), `scheduler` (`:9300`), `mcp` (`:9200`), `agent` (worker `:9400`). The
  scheduler/MCP reuse one light image (`deploy/norn.Dockerfile`, role = the command); timesfm and agent
  have their own Dockerfiles. Cross-file DNS works over the shared project network.

**Built-in scheduler (`packages/scheduler`).** `norn scheduler --manifest jobs.yml` validates the
manifest fail-fast (unique names, valid 5-field cron, known action) then runs an APScheduler
`BackgroundScheduler` (UTC, single replica, `max_instances=1`, `coalesce=True`, `misfire_grace_time`
from config) behind a FastAPI control API on `:9300`: `GET /health`, `GET /jobs`,
`POST /jobs/{name}/trigger` (202; 404 unknown; 409 already running). Each manifest job has
`name` / `action` (`forecast|calibrate|deps`) / `job` (path) / `schedule` (cron — the manifest is the
single source of truth; a job YAML's own `schedule:` is a hint and ignored) / optional `retries` /
`enabled`. `run_action` mirrors the CLI one-shot lifecycle (open client → `prepare_schema` → dispatch →
close); retries are exponential (`retry_base_seconds * 2^attempt`). State is ephemeral (`last_results`
in memory); the durable audit lives in `forecast_run`.

## 8. Instances

`instances/` holds domain instances in two forms:

- **`instances/ett`** — a git submodule (`norn-ett-instance`). The public worked example with real data, ingestion, and scheduler wiring.
- **`instances/crypto`** — a git submodule (private). A production crypto-forecasting instance.
- **`instances/example`** — a plain tracked directory (not a submodule). The copyable starting template: config files for all five sections, example forecast jobs (`orders_baseline.yml`, `orders_timesfm.yml`) and a dependency job (`deps/visits_orders.yml`), and a minimal dbt skeleton. Copy this directory to bootstrap a new instance; replace the placeholder mart and metric names with your own.

### The ETT example, end to end

The public example instance `instances/ett` (`ett` CLI, `norn-ett` package) ingests the ETDataset
"ETT-small" CSVs into `raw_ett` and exposes the dbt marts above. A typical run:

1. `uv run ett backfill` → fills `raw_ett` (ETTh1/ETTh2/ETTm1/ETTm2; only the hourly ETTh* are unpivoted).
2. `dbt run` (instance dbt) → `mart_metric` + `fct_ot` (+ overlay views).
3. `norn forecast instances/ett/forecasts/ot_baseline.yml` (or `ot_timesfm.yml`) → `forecast_point`.
4. `norn deps instances/ett/forecasts/deps/*.yml` → discovers which load features lead `ot`
   (`metric_dependency` + `dependency_explanation`).
5. `NORN_FORECAST_COVARIATES__HORIZON_POLICY=ffill norn forecast instances/ett/forecasts/ot_timesfm_xreg.yml`
   → re-forecasts `ot` using the confirmed leads as XReg covariates.
6. `norn calibrate instances/ett/forecasts/ot_timesfm.yml` → `forecast_segment` (+ backtest points).
7. MCP `get_forecast(metric="ot", segment="dataset=ETTh1|feature=ot")`; Lightdash shows
   actual-vs-forecast.

The scheduler manifest `instances/ett/deploy/jobs.yml` wires the same steps as cron jobs
(`ot-timesfm`, `ot-timesfm-calibrate`, `ot-baseline-calibrate`, `deps-hufl-etth1`); a host-crontab
alternative is `instances/ett/deploy/crontab.sample`. ETT is a static historical dataset, so all
schedules are illustrative hints only.

## 9. Relation to the diagrams

- `erd.mermaid` — the 5 ClickHouse contract tables (matching `schema.sql`) + the ETT example marts/views.
- `architecture.mermaid` — the sidecar component diagram: CLI → 5 packages → ClickHouse / Lightdash / dbt
  / LLM, with the TimesFM (`:9100`), scheduler (`:9300`), MCP (`:9200`) and agent (`:9400`) services.

Both files render in Cowork; open their cards to see the diagrams.
