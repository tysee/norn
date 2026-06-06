# norn — User Guide

_Audience: anyone running, configuring, authoring jobs for, or integrating with a norn deployment._

norn is a vendor-neutral, domain-agnostic forecasting platform that sits on top of
your data warehouse. It reads metric marts, produces multi-segment forecasts with
quantile bands, discovers lead/lag dependencies between metrics, and serves all of
this to agents and bots over MCP. The platform itself ships **no domain defaults** —
it is generic infrastructure. A concrete _instance_ plugs in the ingestion, marts,
forecast jobs, and dashboards for a given domain.

The guide has two layers: **platform-wide guides** in this folder (architecture,
quickstart, the config model, job authoring, deployment) and a **per-package
reference** in subfolders — one page per package with its full description,
functionality, and configuration. Examples are intentionally abstract
(`metric: <your_metric>`, `source: <your_mart>`, `segment=<dim=value>`) —
substitute your own names.

## Reading order (platform guides)

Work through the pages in this order; each builds lightly on the previous one.

1. **[Overview](overview.md)** — what norn is, the `scheduler → dbt → ClickHouse → forecast worker → Lightdash` architecture, the platform-versus-instance model, and the feature list.
2. **[Getting started](getting-started.md)** — a copy-pasteable local quickstart: install, stand up a local ClickHouse, apply the schema, run your first forecast, and query it.
3. **[Configuration](configuration.md)** — the config model: `config/*.yml` + env overrides, no hidden defaults, instance config dirs; per-section key tables live on the package pages.
4. **[Jobs](jobs.md)** — authoring forecast and dependency jobs, calibration, covariates / `use_dependencies`, schema ownership, and what lands in the contract tables.
5. **[Deployment](deployment.md)** — running locally with Docker (infra vs. `docker-compose.services.yml`), the norn services (`scheduler`, `mcp`, `agent`, `timesfm`) and their ports, and environment-agnostic cloud / Kubernetes deployment.

## Package reference

One page per package: what it is, what it does, and every configuration key it owns.

| Package | Page | Owns config | In short |
| --- | --- | --- | --- |
| `packages/core` | [norn-core](core/README.md) | `database.yml` | config loader, job/point contracts, ClickHouse client |
| `packages/integration` | [norn-integration](integration/README.md) | — (`manage_schema`, TTL) | canonical DDL of the 5 contract tables, schema ownership |
| `packages/forecast` | [norn-forecast](forecast/README.md) | `forecast.yml`, `mcp.yml` | forecasters, runner, calibration, XReg covariates, MCP server |
| `packages/agent` | [norn-agent](agent/README.md) | `agent.yml` | lead/lag dependency discovery, LLM judge, agent worker |
| `packages/scheduler` | [norn-scheduler](scheduler/README.md) | `scheduler.yml` | built-in cron for norn jobs + HTTP control API |

The forecast folder also holds two deep-dives:
[Forecast methodology](forecast/methodology.md) (the math: baseline, TimesFM,
quantiles, calibration) and the [MCP tool reference](forecast/mcp.md) (all 11
read tools).

## Audience map

Different readers care about different pages. Start where you fit:

| You are a…                                                                     | Read                                                            |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| **Operator / Deployer** — you run norn locally or in the cloud                 | [Deployment](deployment.md), [Configuration](configuration.md)  |
| **Job author** — you write forecast and dependency jobs                        | [Jobs](jobs.md), [norn-forecast](forecast/README.md), [norn-agent](agent/README.md) |
| **Agent integrator** — you consume forecasts/dependencies from a bot or agent  | [MCP tool reference](forecast/mcp.md)                           |
| **Contributor** — you change platform code                                     | the [package reference](#package-reference) for the package you touch |

If you are new to norn entirely, read [Overview](overview.md) first regardless of role.

## Instances

Domain specifics (metrics, dimensions, dashboards) live in an instance repo —
e.g. `norn-ett-instance` (the public example, the Electricity Transformer
Temperature dataset, mounted at `instances/ett`), or start from the copyable
template at `instances/example`. The platform stays generic; the instance
supplies the ingestion, marts, jobs, and dashboards for its domain.

## See also

- [Project root README](../../README.md) — the repository entry point and top-level quickstart.
- [Architecture & data model](../erd/monorepo-and-data-model.md) — monorepo layout, the ER model, canonical diagrams.
