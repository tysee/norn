# norn

Vendor-neutral forecasting layer: `dbt → ClickHouse → forecast worker → Lightdash`,
plus an MCP interface for agents. This repo is the **generic platform** — domain
instances (e.g. crypto) live in linked submodule repos.

## Quickstart (local, one command)

```bash
uv sync
uv run norn up            # ClickHouse in Docker
uv run norn schema-apply  # forecast-contract tables
uv run norn forecast forecasts/example.yml
```

## Layout
- `packages/core` — contract (forecast-job, forecast-point) + ClickHouse client
- `packages/integration` — ClickHouse DDL + (later) dbt/Lightdash glue
- `packages/forecast` — forecaster (baseline now; TimesFM in Plan 2) + runner
- `cli` — `norn` entrypoint
- `deploy/docker-compose.yml` — local sidecar

## Tests
Requires a local ClickHouse: `docker compose -f deploy/docker-compose.yml up -d clickhouse`,
then `uv run pytest`.