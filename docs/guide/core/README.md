# norn-core

*Audience: contributors working on any norn package, and operators who want to understand how settings, contracts, and the warehouse connection are shared platform-wide.*

`packages/core` (`norn_core`) is the platform's foundation: the typed
configuration layer (YAML + environment overrides), the job/point data
contracts, and the ClickHouse client. Every other package imports these
primitives instead of parsing files, defining its own settings shapes, or
opening its own warehouse connection — so the platform has a single source of
settings, a single exchange format between services, and a single way to reach
the store.

## Functionality

### Config loader (`config.py`)

One `pydantic-settings` section class per config file, all sharing the same
source order and "no hidden defaults" discipline.

- **Config directory** is `config/` by default, overridable with the env var
  **`NORN_CONFIG_DIR`** (point it at the right directory in containers / k8s
  where the working directory is not the repo root).
- **One YAML file per section.** `DatabaseSettings` → `database.yml`,
  `ForecastSettings` → `forecast.yml`, `AgentSettings` → `agent.yml`,
  `McpSettings` → `mcp.yml`, `SchedulerSettings` → `scheduler.yml`. Each section
  reads its own file from `NORN_CONFIG_DIR` on instantiation.
- **Priority: env > yaml.** An environment variable always wins over the YAML
  value. (Init arguments take precedence over both, but they are used only by
  tests, not at runtime.)
- **No hidden Python defaults.** Section fields carry no fallback values: if a
  required key is missing from both env and YAML, pydantic raises an explicit
  `ValidationError` naming the field rather than silently substituting a
  default.
- **Missing file fails loudly.** If a section's YAML file is not found under
  `NORN_CONFIG_DIR`, the loader raises `FileNotFoundError` stating which file is
  missing, where it looked, and the current working directory — instead of a
  generic "field required".
- **Nested fields** use the `__` delimiter (e.g. `NORN_FORECAST_TIMESFM__WORKER_URL`).
- **`get_settings(refresh=False) -> Settings`** returns the aggregate of all five
  sections, cached via `functools.lru_cache(maxsize=1)`. Passing `refresh=True`
  clears the cache and rebuilds it (used when env/files change at runtime, e.g.
  in tests). `Settings` exposes `.database`, `.forecast`, `.agent`, `.mcp`, and
  `.scheduler`.

This module owns config loading for **all** sections; the per-section field
tables are documented on each package's page (see [Configuration](#configuration)).

### Contracts (`contract.py`)

The shared, validated language between the forecast worker (which reads jobs and
writes points) and the integration layer that consumes them.

- **`Grain`** — a string enum (`hourly` | `daily`) setting the series frequency.
- **`CovariateSpec`** — one leader-series spec for XReg covariates: `metric`,
  `segment`, `lag` (int), and `mart` (defaults to `mart_metric`, the long store
  the leader is read from).
- **`ForecastJob`** — the YAML-defined forecast job. Fields: `metric`, `source`
  (ClickHouse table), `grain` (defaults to `daily`), `dimensions`, `filter`
  (column=value equality map), `covariates` (list of `CovariateSpec`),
  `use_dependencies` (default `false`), the optional tunables `horizon` /
  `context_length` / `seasonality` (each `int | None`), `model` (defaults to
  `baseline-seasonal-naive`), `transform` (`none` | `log`, defaults to `none`),
  and `schedule` (optional cron-style hint).
  - **`ForecastJob.from_yaml(path)`** — loads a YAML file and validates it into a
    `ForecastJob`.
  - **`ForecastJob.resolved()`** — returns a copy with the three unset tunables
    filled from `get_settings().forecast.defaults` (`horizon`, `context_length`,
    `seasonality`). An explicit job value always wins; a `None` field takes the
    default. The job's field is used only when it is not `None`.
- **`ForecastPoint`** — one row of the results table: `forecast_run_id`,
  `metric_name`, `segment_key`, `forecast_ts`, `horizon_step`, the prediction
  `y_hat`, the interval `p10` / `p50` / `p90`, an optional `y_actual` (`float |
  None`), `model_name`, and `created_at`.

### ClickHouse client (`clickhouse.py`)

The platform's single connection point to the warehouse, so every service
(forecast worker, agent, integration layer) opens the connection the same way
and does not duplicate DSN parsing or port/protocol selection.

- **`get_client(dsn=None) -> Client`** — assembles a `clickhouse-connect`
  client. Source priority: an explicit `dsn` argument wins; otherwise, if the
  config layer's `database.dsn` is set (from `NORN_CLICKHOUSE_URL`) that DSN is
  used; otherwise the per-file `host` / `port` / `user` / `password` /
  `database` / `secure` fields are used.
- **`parse_dsn(dsn) -> dict`** — parses a DSN into connection parameters. The
  scheme sets security (`https` → secure), the path is the database name, and
  the default port depends on the protocol (`8443` for https, `8123` for http).
  A missing database path raises `ValueError` — and the DSN is never echoed in
  the error, since it carries the password.
- **`_safe_identifier(name)`** — defense-in-depth helper for the rest of the
  platform. `clickhouse-connect` binds *values* via parameters but cannot bind
  SQL *identifiers* (table/column names), which must be interpolated. This
  restricts any interpolated identifier to a safe allowlist shape
  (`^[A-Za-z_][A-Za-z0-9_.]*$`, dotted `db.table` allowed), raising `ValueError`
  on anything else so attacker-controlled job fields cannot inject SQL.

## Configuration

This package **owns** config loading for every section, but documents only the
section it defines its own client around — `database.yml`. The per-section field
tables for `forecast`, `agent`, `mcp`, and `scheduler` live on their respective
package pages and in [Configuration](../configuration.md).

### `database.yml`

ClickHouse connection used to read marts and write the contract tables.

| Field           | Type   | Description                                                                                                                                                                                        |
| --------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `host`          | string | ClickHouse host (e.g. `localhost` for the local sidecar).                                                                                                                                          |
| `port`          | int    | ClickHouse HTTP-interface port (e.g. `8123`).                                                                                                                                                      |
| `user`          | string | Database user.                                                                                                                                                                                     |
| `database`      | string | Database holding the contract schema.                                                                                                                                                              |
| `secure`        | bool   | Use a TLS connection (`true` for secured connections).                                                                                                                                             |
| `manage_schema` | bool   | `true`: norn idempotently **creates** its contract tables (zero-setup). `false`: norn runs **no DDL** (INSERT-only) — you create the tables yourself; `norn print-schema` emits the canonical DDL. |

**Env override prefix:** every field above is overridable at runtime with
`NORN_DB_<FIELD>` (env beats YAML), e.g. `NORN_DB_MANAGE_SCHEMA=false`.

**Secret (env-only):** `database.password` is **never** read from YAML. It comes
exclusively from the env var **`NORN_DB_PASSWORD`** (required, no default).

**DSN override (env-only, optional):** **`NORN_CLICKHOUSE_URL`** sets the full
ClickHouse DSN. When present it overrides the assembled `host`/`port`/`user`/…
connection; unset (the default) means no override.

## Used by

- [norn-integration](../integration/README.md) — consumes the contract tables
  and the ClickHouse client.
- [norn-forecast](../forecast/README.md) — reads `ForecastJob`s and writes
  `ForecastPoint`s.
- [norn-agent](../agent/README.md) — dependency analysis; reads `agent`
  settings.
- [norn-scheduler](../scheduler/README.md) — runs jobs on cron; reads
  `scheduler` settings.

General guides:

- [Configuration](../configuration.md) — every section's full field table, the
  LLM provider matrix, and the `NORN_<SECTION>_<FIELD>` override convention.
- [Jobs](../jobs.md) — the `ForecastJob` contract from a job author's
  perspective.
