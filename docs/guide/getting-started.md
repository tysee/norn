# Getting Started

*Audience: new operators and job authors who want a working forecast on a local stack in a few minutes.*

This is a copy-pasteable local quickstart. It takes you from a fresh checkout to a
forecast you can query over MCP, using an **abstract** example job — no domain data
required. Everything runs against a local ClickHouse sidecar.

> norn is a generic, domain-agnostic forecasting platform: it ships **no** domain
> metrics, marts, or jobs. Below you forecast a placeholder metric so you can see the
> end-to-end flow. Real metrics come from an *instance* repo (see the last section).

---

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — the package/runtime manager used to install and run norn.
- **Docker** — for the local ClickHouse sidecar (`norn up` / Docker Compose). Local-dev only.
- **Python ≥ 3.12**.

---

## 1. Install

Clone the platform repo and sync the workspace:

```bash
uv sync
```

This installs all packages (`norn_core`, `norn_forecast`, `norn_agent`, the CLI, …)
into a single managed environment. Every command below is run with `uv run`.

---

## 2. Bring up a local ClickHouse

The platform connects to ClickHouse purely via config/env, so any ClickHouse will do.
For local development there is a convenience command that starts a ClickHouse sidecar
via Docker Compose:

```bash
uv run norn up
```

> **`norn up` is a local-dev convenience, not a platform requirement.** It runs
> `docker compose -f deploy/docker-compose.yml up -d clickhouse` under the hood. You can
> run that yourself, or override the compose file with `NORN_COMPOSE_FILE`. For
> cloud/k8s you skip `norn up` entirely and point the platform at a managed ClickHouse —
> see [Deployment](deployment.md).

Equivalent explicit form:

```bash
docker compose -f deploy/docker-compose.yml up -d clickhouse
```

### Set the database password

The ClickHouse password is a **secret and is read from the environment only** — it is
never stored in `config/database.yml`. The platform reads it from `NORN_DB_PASSWORD`;
the local sidecar's default password is `norn`:

```bash
export NORN_DB_PASSWORD=norn
```

The rest of the connection (`host`, `port`, `user`, `database`, `secure`) lives in
`config/database.yml`. You can instead point at any ClickHouse with a single DSN via the
env var `NORN_CLICKHOUSE_URL`. See [Configuration](configuration.md) for the full model.

---

## 3. Apply the contract schema

norn writes its results into five **contract tables** (`forecast_run`, `forecast_point`,
`forecast_segment`, `metric_dependency`, `dependency_explanation`). Create them once:

```bash
uv run norn schema-apply
```

This honors the `manage_schema` flag in `config/database.yml`:

- `manage_schema: true` — norn idempotently creates the contract tables (zero-setup, the
  local default).
- `manage_schema: false` — norn runs **no DDL**; you own the tables. In that mode use
  `uv run norn print-schema` to emit the canonical DDL for your own dbt/migrations.

See [Jobs → Schema ownership](jobs.md) for the full explanation.

---

## 4. Run your first forecast

A forecast is described by a declarative **job YAML** (a `ForecastJob`). The repo
ships a ready example at `forecasts/example.yml` — open it and substitute your own
metric, mart, and dimension. The fields, for reference:

```yaml
# forecasts/example.yml (shipped in the repo — edit in place)
metric: <your_metric>              # the metric column/name to forecast
source: <your_mart>                # ClickHouse table to read, e.g. analytics.mart_metric
grain: daily                       # daily | hourly (default: daily)
dimensions: [<dim>]                # segment dimensions; one forecast per segment
horizon: 30                        # steps to forecast ahead
model: baseline-seasonal-naive     # default model; needs no external worker
```

(Don't run it with the literal `<...>` placeholders — point `metric`/`source` at a
real column and mart first.) Then run it:

```bash
uv run norn forecast forecasts/example.yml
```

The command extracts the series, forecasts each segment, writes rows into the contract
tables, and prints `run_id=<...>`.

> **Concrete example.** The vendored ETT instance ships a real version of this job at
> [`instances/ett/forecasts/ot_baseline.yml`](../../instances/ett/forecasts/ot_baseline.yml):
> it forecasts the `ot` (oil-temperature) metric from the `fct_ot` mart at `hourly`
> grain over `dimensions: [dataset, feature]`. Once that instance's marts exist you can
> run `uv run norn forecast instances/ett/forecasts/ot_baseline.yml` verbatim.

**About `model`:** the default `baseline-seasonal-naive` runs entirely in-process with no
external dependencies — ideal for a first run. The alternative `timesfm-2.5` requires a
separate TimesFM worker; if you select it and the worker is unreachable, the run **fails
explicitly** (records `forecast_run.status=failed`) — there is no silent fallback. See
[Jobs](jobs.md) and the worker setup in [Deployment](deployment.md).

Optional fields (`context_length`, `seasonality`, `covariates`, `use_dependencies`,
`schedule`) are documented in [Jobs](jobs.md). Unset tunables fall back to
`forecast.defaults` from your config.

---

## 5. Query the forecast over MCP

Forecast results are served to agents and bots through an MCP server. Start it with:

```bash
uv run norn mcp
```

This serves over **streamable-http** on the host/port from `config/mcp.yml` (default
`127.0.0.1:9200`). Point an MCP client at it and call the read tools — for example, to
fetch the forecast for one segment:

```text
get_forecast(metric="<your_metric>", segment="<dim=value>")
```

For the ETT instance the concrete call is
`get_forecast(metric="ot", segment="dataset=ETTh1|feature=ot")`.

There are **11 tools** in total (forecasts, expected ranges, band classification,
calibration, dependencies, run status, and listing). The full connection setup and tool
reference is in [MCP](forecast/mcp.md).

---

## Where domain data comes from

The platform is intentionally empty of domain content: it reads marts you provide and
writes forecasts back. An **instance** repo supplies the ingestion, marts (your
`<your_mart>`), jobs, and dashboards.

**Starting a new instance — [`instances/example`](../../instances/example)** is a
plain tracked directory (not a submodule) that serves as the copyable starting template.
It contains the full instance skeleton — config files, example forecast and dependency
jobs, and a minimal dbt skeleton — with no ingestion or live data.
Copy the directory, adjust the config and job files to point at your mart, and you have a
working instance. See the layout note in [README](../../README.md) for the directory
structure.

**Concrete worked example — [`instances/ett`](../../instances/ett)** (`norn-ett-instance`
submodule) ingests the public ETT (Electricity Transformer Temperature) dataset, builds
the `mart_metric` / `fct_ot` marts, and ships ready-to-run jobs
(`forecasts/ot_baseline.yml`, `forecasts/ot_timesfm.yml`, `forecasts/ot_timesfm_xreg.yml`,
and `forecasts/deps/*.yml`) that forecast the `ot` oil-temperature metric per
`dataset=ETTh1|feature=ot` segment.

---

## See also

- [Configuration](configuration.md) — config layer, all sections, env overrides, LLM providers.
- [Jobs](jobs.md) — full forecast/dependency job fields, calibration, schema modes.
- [MCP](forecast/mcp.md) — connecting and the 11-tool reference.
- [Deployment](deployment.md) — local Docker, the TimesFM worker, and cloud/k8s.
- Project root: [README](../../README.md).
