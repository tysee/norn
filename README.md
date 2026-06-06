# norn

![norn — read the marts, forecast the metrics, explain the drivers, serve the dashboards](docs/assets/hero.png)

**Forecast any metric in your warehouse — and find out what moves it.**

Your dashboards tell you what already happened. norn tells you what happens
next, and why: it reads the marts you already build with dbt, produces
multi-segment forecasts with prediction intervals, discovers which metrics are
leading indicators of which, and serves all of it to AI agents over MCP and to
people through BI dashboards.

Think of it as a **forecasting layer for your data warehouse**:

- **Forecast any metric, across all segments at once** — quantile bands
  (`p10 / p50 / p90`) instead of a single guess.
- **Zero-shot accuracy out of the box** — Google's TimesFM 2.5 foundation
  model, or a dependency-free seasonal baseline. No ML infrastructure, no
  model training.
- **Know what drives your KPIs** — statistical lead/lag discovery finds which
  metrics move first, with LLM-written explanations; confirmed drivers feed
  back into forecasts as covariates.
- **Trust the numbers** — rolling-origin backtesting gives you coverage, WAPE
  and bias per segment before you rely on a forecast.
- **Built for AI agents** — 11 read-only MCP tools so Claude, bots and
  pipelines can ask "where is this metric heading?" directly.
- **Your domain, your rules** — the platform ships no built-in metrics or
  models; point it at your own marts and describe jobs in YAML.

Under the hood: `dbt → ClickHouse → forecast worker (baseline / TimesFM) →
Lightdash`, plus an MCP interface for agents. This repo is the **generic
platform** — it ships no domain defaults. Concrete domain instances (e.g. the
[`instances/ett`](instances/ett) ETT example) plug in ingestion, marts, jobs,
and dashboards from linked submodule repos.

## Quickstart (local)

```bash
uv sync
uv run norn up            # ClickHouse in Docker (local-dev convenience)
uv run norn schema-apply  # create the 5 forecast-contract tables
uv run norn forecast forecasts/example.yml   # run an abstract example job
```

`forecasts/example.yml` is an abstract example job (substitute your own
`metric: <your_metric>`, `source: <your_mart>`, `dimensions: [<dim>]`). The local
ClickHouse password is set via the `NORN_DB_PASSWORD` env var (env-only secret).

## Documentation

Full user guide lives in [`docs/guide/`](docs/guide/README.md):

- [Overview & architecture](docs/guide/overview.md) — what norn is, the data flow, the platform ↔ instance model.
- [Getting started](docs/guide/getting-started.md) — copy-pasteable local quickstart.
- [Configuration](docs/guide/configuration.md) — config layers, all sections, LLM providers, env overrides.
- [Jobs](docs/guide/jobs.md) — forecast/dependency job contracts, calibration, schema-ownership modes.
- [Forecast methodology](docs/guide/forecast-methodology.md) — how the forecasters work: baseline math, TimesFM, quantiles, calibration.
- [MCP](docs/guide/mcp.md) — connecting and the 11-tool reference for agents.
- [Deployment](docs/guide/deployment.md) — local Docker, the TimesFM worker, the long-running services (scheduler, MCP, agent worker), cloud/Kubernetes.

## Layout

- `packages/core` — config + job contracts (forecast-job, forecast-point) + ClickHouse client
- `packages/integration` — the canonical ClickHouse DDL (the 5 contract tables: `forecast_run`, `forecast_point`, `forecast_segment`, `metric_dependency`, `dependency_explanation`)
- `packages/forecast` — forecasters (`baseline-seasonal-naive` and `timesfm-2.5`), runner, the TimesFM HTTP worker, and the MCP server (11 tools)
- `packages/agent` — lead/lag dependency analysis (stats + LLM explanation) and the agent worker
- `packages/scheduler` — built-in cron scheduler (APScheduler from a `jobs.yml` manifest) + FastAPI control API (port `9300`)
- `cli` — the `norn` entrypoint (`schema-apply`, `print-schema`, `forecast`, `calibrate`, `deps`, `mcp`, `scheduler`, `up`)
- `instances/ett` — the public example instance (ETT — Electricity Transformer Temperature): ingestion, dbt marts (`mart_metric` / `fct_ot`), and forecast/deps jobs
- `deploy/docker-compose.yml` — infra stack: local ClickHouse sidecar + optional Lightdash BI stack
- `deploy/docker-compose.services.yml` — norn's own services (`timesfm`, `scheduler`, `mcp`, `agent`), split into a separate file so taking services down can never remove the infra
- `deploy/timesfm.Dockerfile` — self-contained TimesFM forecast worker (port `9100`)

## Tests

Requires a local ClickHouse: `docker compose -f deploy/docker-compose.yml up -d clickhouse`,
then `uv run pytest`.
