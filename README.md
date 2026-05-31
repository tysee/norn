# norn

Vendor-neutral, domain-agnostic forecasting platform: `dbt → ClickHouse → forecast
worker (baseline / TimesFM) → Lightdash`, plus an MCP interface for agents. This
repo is the **generic platform** — it ships no domain defaults. Concrete domain
instances (e.g. crypto) plug in ingestion, marts, jobs, and dashboards from linked
submodule repos.

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
- [MCP](docs/guide/mcp.md) — connecting and the 11-tool reference for agents.
- [Deployment](docs/guide/deployment.md) — local Docker, the TimesFM worker, cloud/Kubernetes.

## Layout
- `packages/core` — config + job contracts (forecast-job, forecast-point) + ClickHouse client
- `packages/integration` — ClickHouse DDL (the 5 contract tables) + dbt/Lightdash glue
- `packages/forecast` — forecasters (`baseline-seasonal-naive` and `timesfm-2.5`), runner, and the MCP server (11 tools)
- `packages/agent` — lead/lag dependency analysis (stats + LLM explanation)
- `cli` — the `norn` entrypoint (`schema-apply`, `print-schema`, `forecast`, `calibrate`, `deps`, `mcp`, `up`)
- `deploy/docker-compose.yml` — local ClickHouse sidecar (optional Lightdash stack)
- `deploy/timesfm.Dockerfile` — self-contained TimesFM forecast worker (port `9100`)

## Tests
Requires a local ClickHouse: `docker compose -f deploy/docker-compose.yml up -d clickhouse`,
then `uv run pytest`.
