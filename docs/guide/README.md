# norn — User Guide

_Audience: anyone running, configuring, authoring jobs for, or integrating with a norn deployment._

norn is a vendor-neutral, domain-agnostic forecasting platform that sits on top of
your data warehouse. It reads metric marts, produces multi-segment forecasts with
quantile bands, discovers lead/lag dependencies between metrics, and serves all of
this to agents and bots over MCP. The platform itself ships **no domain defaults** —
it is generic infrastructure. A concrete _instance_ plugs in the ingestion, marts,
forecast jobs, and dashboards for a given domain.

This guide describes the platform exactly as it exists: the CLI, the configuration
layers, the job contracts, the 11 MCP tools, and how to deploy it locally or in the
cloud. Examples are intentionally abstract (`metric: <your_metric>`,
`source: <your_mart>`, `segment=<dim=value>`) — substitute your own names.

## Reading order

Work through the pages in this order; each builds lightly on the previous one.

1. **[Overview](overview.md)** — what norn is, the `scheduler → dbt → ClickHouse → forecast worker → Lightdash` architecture, the platform-versus-instance model, and the feature list.
2. **[Getting started](getting-started.md)** — a copy-pasteable local quickstart: install, stand up a local ClickHouse, apply the schema, run your first forecast, and query it.
3. **[Configuration](configuration.md)** — the config model (`config/*.yml` + env, no hidden defaults), every field of `database.yml` / `forecast.yml` / `agent.yml` / `mcp.yml` / `scheduler.yml`, and the LLM-provider table.
4. **[Jobs](jobs.md)** — authoring forecast and dependency jobs, calibration, covariates / `use_dependencies`, schema ownership, and what lands in the contract tables.
5. **[MCP](mcp.md)** — connecting an agent over streamable-http and the full reference for all 11 read tools.
6. **[Deployment](deployment.md)** — running locally with Docker (infra vs. `docker-compose.services.yml`), the norn services (`scheduler`, `mcp`, `agent`, `timesfm`) and their ports, and environment-agnostic cloud / Kubernetes deployment.

## Audience map

Different readers care about different pages. Start where you fit:

| You are a…                                                                    | Read                                                           |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------- |
| **Operator / Deployer** — you run norn locally or in the cloud                | [Deployment](deployment.md), [Configuration](configuration.md) |
| **Job author** — you write forecast and dependency jobs                       | [Jobs](jobs.md), [Configuration](configuration.md)             |
| **Agent integrator** — you consume forecasts/dependencies from a bot or agent | [MCP](mcp.md)                                                  |

If you are new to norn entirely, read [Overview](overview.md) first regardless of role.

## Instances

Domain specifics (metrics, dimensions, dashboards) live in an instance repo —
e.g. `norn-ett-instance` (the public example, the Electricity Transformer
Temperature dataset, mounted at `instances/ett`). The platform stays generic;
the instance supplies the ingestion, marts, jobs, and dashboards for its domain.

## See also

- [Project root README](../../README.md) — the repository entry point and top-level quickstart.
