# Overview

*Audience: anyone evaluating norn or trying to understand how the pieces fit together before deploying, authoring jobs, or integrating an agent.*

## What is norn

norn is a **vendor-neutral, domain-agnostic forecasting platform** that sits on
top of your data warehouse. It reads metrics from your **marts**, produces
multi-segment forecasts with quantile bands, discovers lead/lag dependencies
between metrics, and serves all of that to agents and bots over **MCP**.

Concretely, norn:

- **reads** prepared marts from ClickHouse,
- **writes** its results back into a small set of generic **contract tables**
  (`forecast_run`, `forecast_point`, `forecast_segment`, `metric_dependency`,
  `dependency_explanation`),
- **serves** those tables to consumers (agents, dashboards) through a read-only
  MCP tool surface.

The platform ships **no domain defaults** — no built-in metrics,
dimensions, ingestion formats, dashboards, prompts, or model choice. You point
it at your own marts and describe what to forecast with abstract jobs such as
`metric: <your_metric>`, `source: <your_mart>`, `segment=<dim=value>`.

## Architecture

The data flow is:

```text
scheduler (cron) ──┐
                   ▼
dbt → ClickHouse → forecast worker (baseline / TimesFM) → Lightdash
                          │
                          ├── MCP server (agent / bot interface)
                          └── dependency-analysis agent (stats + LLM)
```

- **dbt** transforms raw data into marts inside **ClickHouse** (the warehouse is
  the single shared datastore for marts and contract tables).
- The **scheduler** is the built-in cron service: it reads a `jobs.yml`
  manifest and triggers `forecast`, `calibrate`, and `deps` runs on a schedule
  (APScheduler under a small FastAPI control API). You can also run any job
  one-off from the CLI; the scheduler just automates the same lifecycle.
- The **forecast worker** extracts each metric series and writes back forecasts.
  The forecaster is selected per job by `model`: `baseline-seasonal-naive`
  (built-in, needs no external service) or `timesfm-2.5` (calls the standalone
  TimesFM worker).
- **Lightdash** reads the contract tables for actual-vs-forecast dashboards.
- The **MCP server** exposes read tools over the same contract tables for agents.
- The **dependency-analysis agent** computes lead/lag relationships
  (statistical methods) and adds an LLM-written explanation when a provider is
  available.

For the full component diagram (modules, data stores, edges), see the canonical
diagram: [`../erd/architecture.mermaid`](../erd/architecture.mermaid). It is not
duplicated here.

## Platform ↔ instance model

norn separates the **generic platform** from a domain-specific **instance**:

> norn is a vendor-neutral, domain-agnostic forecasting platform:
> multi-segment metric forecasting and dependency discovery over **any**
> warehouse, via a generic contract (`forecast_point` / `forecast_segment`),
> configurable model/provider/database, and an MCP contract. The platform code
> (`packages/*`, `cli`) carries **no domain defaults** — no built-in metrics,
> dimensions, ingestion formats, dashboards, prompts, or LLM model
> choice. All domain specifics live in a separate instance repo.

- **Platform** (`packages/*`, `cli`): generic engine — forecasting, calibration,
  dependency analysis, the MCP server, configuration, and the contract schema.
- **Instance**: supplies the domain — ingestion, marts, forecast/dependency
  jobs, and dashboards. An instance plugs into the platform (typically as a
  submodule) and provides everything domain-specific.

Domain specifics (metrics, dimensions, dashboards) live in an instance repo —
e.g. `norn-ett-instance` (the public example, the Electricity Transformer
Temperature dataset, mounted at `instances/ett`). Any concrete domain mentioned
anywhere in this guide is a **labeled example pointing at that instance**, never
a platform requirement.

## Features

- **Multi-segment metric forecasting with quantile bands** — forecast a metric
  across many segments at once, with prediction quantiles (e.g.
  `[0.1, 0.5, 0.9]`).
- **Rolling-origin calibration** — back-test a job over multiple cutoffs to get
  coverage, WAPE, MAPE, bias, and a sparse-data flag per segment.
- **Lead/lag dependency discovery** — statistical lead/lag detection between
  metrics, with an LLM-written explanation layered on top, and **graceful
  degradation**: if the LLM is unavailable the numeric dependency is still
  written, only the explanation is empty.
- **XReg covariates** — confirmed lead/lag dependencies can become forecast
  covariates (`use_dependencies`); covariate horizons are handled per
  `covariates.horizon_policy` (`strict` | `ffill`).
- **Built-in scheduler** — a small cron service (`norn scheduler --manifest
  jobs.yml`) runs `forecast` / `calibrate` / `deps` jobs on a schedule via
  APScheduler, with a FastAPI control API (`/health`, `/jobs`, manual
  `/jobs/{name}/trigger`).
- **MCP tool surface** — exactly **11 read-only tools** over the contract tables
  for agents and bots.
- **Configurable LLM provider** — 5 providers for dependency explanations:
  `ollama`, `openai-api`, `openai-oauth`, `openrouter`, `anthropic-api`.
- **Pluggable schema ownership** — `manage_schema: true` lets norn create the
  contract tables; `false` makes norn run no DDL (you own the schema via your
  own migrations/dbt).
- **Environment-agnostic configuration** — all settings come from
  `config/*.yml` plus environment variables (priority `env > yaml`), with no
  hidden Python defaults, so the same code runs locally, in the cloud, or on
  Kubernetes.

## See also

- [Getting Started](getting-started.md) — install and run your first forecast.
- [Configuration](configuration.md) — config layers, all sections, and LLM
  providers.
- Project root: [`../../README.md`](../../README.md).
