# Deployment

> **Audience:** operators and deployers running norn locally, with the TimesFM worker, or on cloud / Kubernetes.

norn is **environment-agnostic**: the platform ships no domain defaults and reads
everything from `config/*.yml` plus environment variables (priority: **env > yaml**,
no hidden Python defaults). Deploying is therefore mostly a matter of pointing norn
at a ClickHouse instance and supplying secrets via env. This page covers the three
common targets — local Docker, the optional TimesFM inference worker, and
cloud/Kubernetes.

---

## Local (Docker)

For local development, a Compose stack provides a ClickHouse sidecar (and, optionally,
a Lightdash BI stack) at `deploy/docker-compose.yml`.

The `clickhouse` service exposes:

- HTTP `127.0.0.1:8123` (used by dbt and Lightdash)
- native protocol `127.0.0.1:9009` (mapped from container port `9000`, to avoid
  clashing with other local services)

Bring up only ClickHouse:

```bash
docker compose -f deploy/docker-compose.yml up -d clickhouse
```

Or use the local-dev convenience wrapper, which runs the same Compose file:

```bash
uv run norn up
```

`norn up` is a **local-dev convenience only** — it shells out to Compose and is not
intended for production. It honors `database.manage_schema` (it does not apply DDL
itself; run `norn schema-apply` once ClickHouse is healthy). The Compose file path
defaults to `deploy/docker-compose.yml` and can be overridden with the
`NORN_COMPOSE_FILE` environment variable. In cloud/k8s you point at managed
ClickHouse via config/env and **skip `norn up`** entirely.

The same file also defines an optional Lightdash stack (`lightdash`, `lightdash-db`,
`minio`, `headless-browser`, and init jobs) for local dashboards — start those
services only if you want BI locally.

Set the database password (env-only, never in YAML):

```bash
export NORN_DB_PASSWORD=norn   # local default
```

---

## TimesFM worker

`timesfm-2.5` forecast jobs run inference against a **separate HTTP worker** rather
than in the platform process. The worker is **self-contained** — it needs no norn
config; you only point the platform at it.

### Build

```bash
docker build -f deploy/timesfm.Dockerfile -t norn-timesfm .
```

### Run

The worker listens on port **9100**:

```bash
docker run --rm -p 9100:9100 \
  -e NORN_TIMESFM_MAX_CONTEXT=1024 \
  -e NORN_TIMESFM_MAX_HORIZON=1024 \
  -e NORN_TIMESFM_XREG_MODE=xreg+timesfm \
  norn-timesfm
```

### Worker environment

All three are optional and configure the worker itself (not the platform):

| Env var | Purpose |
|---|---|
| `NORN_TIMESFM_MAX_CONTEXT` | Max context length the worker accepts (default `1024`). |
| `NORN_TIMESFM_MAX_HORIZON` | Max horizon the worker accepts (default `1024`). |
| `NORN_TIMESFM_XREG_MODE` | Covariate handling mode (default `xreg+timesfm`). |

### Point the platform at it

Set the worker URL in `forecast.yml`:

```yaml
# config/forecast.yml
timesfm:
  worker_url: "http://localhost:9100"   # where the TimesFM worker is reachable
  max_context: 1024
  max_horizon: 1024
```

### Explicit failure (no silent fallback)

If a job's `model` is `timesfm-2.5` and the worker is **unreachable**, the run
**fails explicitly**: norn records a `forecast_run` with `status='failed'` so the
failed run is visible in the contract tables. There is **no silent fallback** to the
baseline model. To forecast without the worker, use a `baseline-seasonal-naive` job
(the default `model`), which runs entirely in-process and needs no worker.

---

## Cloud / Kubernetes

The platform has no environment-specific code: a container running the `norn` CLI
plus a config directory and a set of env vars is all you need. Nothing about the
target (managed ClickHouse, k8s, etc.) is hardcoded.

### 1. Point at managed ClickHouse

Provide the full DSN via the `NORN_CLICKHOUSE_URL` environment variable (overrides
the `database.yml` connection fields), or set the individual `database.yml` fields
(`host`, `port`, `user`, `database`, `secure`) and supply only the password via env.

```bash
export NORN_CLICKHOUSE_URL="clickhouse://user:***@ch.example.internal:9440/norn?secure=true"
```

### 2. Mount your config directory

Set `NORN_CONFIG_DIR` to the directory holding your `*.yml` section files. A missing
config dir or section file raises a clear `FileNotFoundError`; a missing required key
raises a `ValidationError` — there are no silent defaults.

```bash
export NORN_CONFIG_DIR=/etc/norn/config
```

### 3. Let your platform own the schema

Set `manage_schema: false` in `database.yml` so norn runs **no DDL**. With this
setting, `norn schema-apply` refuses to act (and tells you to use `print-schema`
instead), and forecast/dependency runs are INSERT-only. Generate the contract DDL
yourself and feed it into your dbt project or migration tooling:

```bash
uv run norn print-schema > schema.sql
```

This emits the canonical DDL for the five contract tables (`forecast_run`,
`forecast_point`, `forecast_segment`, `metric_dependency`, `dependency_explanation`).
Apply it through your own change-management pipeline. When `manage_schema: true`,
norn creates these tables itself via `norn schema-apply` — typically reserved for
local/dev.

### 4. Inject secrets via env

Never put secrets in YAML. Provide them through the environment:

- `NORN_DB_PASSWORD` — ClickHouse password (database secret).
- The per-provider LLM key for the dependency-analysis agent — exactly one of
  `OPENAI_API_KEY`, `NORN_OPENAI_OAUTH_TOKEN`, `OPENROUTER_API_KEY`,
  `ANTHROPIC_API_KEY` (the `ollama` provider needs none). See
  [Configuration](configuration.md) for the provider/secret mapping.

If you use `timesfm-2.5` jobs, deploy the TimesFM worker (above) as its own service
and set `forecast.timesfm.worker_url` to its in-cluster address.

---

## Required environment per environment

| Variable | Local | Cloud / Kubernetes |
|---|---|---|
| `NORN_DB_PASSWORD` | Required (local default `norn`) | Required |
| `NORN_CONFIG_DIR` | Optional (defaults to `config/`) | Required (your mounted config dir) |
| `NORN_CLICKHOUSE_URL` | Not used (`norn up` / local Compose) | Required, **or** set `NORN_DB_*` / `database.yml` fields |
| LLM provider key (`OPENAI_API_KEY` / `NORN_OPENAI_OAUTH_TOKEN` / `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY`) | Required only if running dependency jobs (not for `ollama`) | Required only if running dependency jobs (not for `ollama`) |
| `NORN_COMPOSE_FILE` | Optional (overrides the local Compose path) | Not used |
| `NORN_TIMESFM_MAX_CONTEXT` / `NORN_TIMESFM_MAX_HORIZON` / `NORN_TIMESFM_XREG_MODE` | Optional (only if running the TimesFM worker) | Optional (set on the worker, only if running it) |

> Per-section settings can also be overridden with the generic
> `NORN_<SECTION>_<FIELD>` env pattern (e.g. `NORN_DB_MANAGE_SCHEMA`). See
> [Configuration](configuration.md).

---

## See also

- [Configuration](configuration.md) — config layer, all sections, and LLM providers.
- [Jobs](jobs.md) — forecast/dependency jobs, calibration, and schema-ownership modes.
- [User Guide index](README.md) · [Project README](../../README.md)
