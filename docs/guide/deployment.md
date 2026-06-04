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

The worker listens on port **9100**. With the local compose stack, prefer the
services file (named HF-cache volume, weights survive rebuilds):

```bash
cd deploy && docker compose -f docker-compose.services.yml --profile timesfm up -d timesfm
```

Or standalone, without compose:

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

## Services (scheduler, MCP, agent worker)

Beyond the one-shot CLI, norn ships three long-running services. They are not three
codebases — a container's role is the **command it runs**. Two of them share one
light platform image; the LLM judge gets its own light image.

| Service | Image | Command | Port | Purpose |
|---|---|---|---|---|
| scheduler | `norn:local` (`deploy/norn.Dockerfile`) | `norn scheduler --manifest …` | 9300 | cron for norn jobs (forecast/calibrate/deps) + HTTP API |
| MCP | `norn:local` (same image) | `norn mcp` | 9200 | read-only API for agents |
| agent worker | `norn-agent:local` (`deploy/agent.Dockerfile`) | uvicorn `norn_agent.agent_worker:build_app` | 9400 | switchable LLM dependency judge |

The platform image is light by design: `torch`/`jax` live in the TimesFM worker image,
and the LLM provider lives behind the agent worker (or the provider's own HTTP API).

### Compose: infra vs services (two files, one project)

norn's services live in a **separate compose file** from the infra so that taking
services down can never remove ClickHouse/Lightdash by accident:

- `deploy/docker-compose.yml` — **infra**: ClickHouse + the Lightdash stack
- `deploy/docker-compose.services.yml` — **norn services**: `timesfm`, `scheduler`,
  `mcp`, `agent` (opt-in profiles)

Both files share one compose project (same network, same container/volume names), so
service aliases resolve across files and `down` on the services file only removes the
services in it. Two rules: bring infra up first, and **never pass
`--remove-orphans`** (each file sees the other's containers as orphans;
`COMPOSE_IGNORE_ORPHANS=true` in `.env` silences the warning).

```bash
cd deploy
docker compose up -d                       # infra first (clickhouse + lightdash)

# Scheduler: mount the instance root at /jobs (manifest -> /jobs/deploy/jobs.yml).
NORN_JOBS_DIR=../instances/ett docker compose -f docker-compose.services.yml \
  --profile scheduler up -d scheduler

# Read-only MCP server (:9200), same platform image.
docker compose -f docker-compose.services.yml --profile mcp up -d mcp

# Switchable LLM judge (:9400). Leave it off and deps jobs degrade explicitly.
docker compose -f docker-compose.services.yml --profile agent up -d agent

# Stop ALL norn services — infra is untouched by construction:
docker compose -f docker-compose.services.yml \
  --profile timesfm --profile scheduler --profile mcp --profile agent down
```

The scheduler is **single-replica by design** — there is no distributed locking in v1.
Run exactly one.

### jobs.yml manifest

The manifest is the single source of schedule (the `schedule:` field inside a job YAML
is only a hint and is ignored by the scheduler). It is an explicit list — no globs —
and an invalid manifest fails fast at startup. One manifest per instance (example:
`instances/ett/deploy/jobs.yml`):

```yaml
jobs:
  - name: ot-timesfm          # unique; used for overlap locks and /trigger
    action: forecast          # forecast | calibrate | deps
    job: /jobs/forecasts/ot_timesfm.yml   # path to an existing job YAML (inside the container)
    schedule: "15 6 * * *"    # cron (5 fields); manifest OVERRIDES the yml hint
    retries: 2                # optional; default from config/scheduler.yml
    enabled: true             # optional; default true
```

With `NORN_JOBS_DIR=../instances/ett` the instance root is mounted at `/jobs`, so the
manifest is reachable at `/jobs/deploy/jobs.yml` (the compose `command` points there)
and the job YAMLs at `/jobs/forecasts/…`.

### HTTP API (:9300)

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness probe → `{"status":"ok"}` |
| `GET /jobs` | manifest entries + APScheduler `next_run` + last status |
| `POST /jobs/{name}/trigger` | manual out-of-schedule run (202; 404 unknown, 409 already running) |

`/jobs` last-status comes from the scheduler's **in-memory last-result map**, not from
`forecast_run`: `forecast_run` rows carry no manifest-job name (only the metric) and
calibrate/deps do not write there at all, so in-memory is the only uniform source for
all three actions. A restart clears the map; `forecast_run` remains the durable audit.

### Failure matrix

| Failure | Behavior |
|---|---|
| timesfm worker down | retries (exponential, N from manifest) → `forecast_run.status=failed` + error; next tick runs normally |
| agent worker off/down | `LLMUnavailable` → evidence written, `explained=false`; NO retries (off is normal) |
| ClickHouse down | retries → ERROR log (nowhere to audit); `/jobs` shows last_status=error |
| job overruns its interval | `max_instances=1`: next tick skipped with WARN (no queueing) |
| scheduler restart | misfire grace: one catch-up within window, older ticks skipped; no state lost (none held) |
| invalid jobs.yml | fail-fast at startup with a clear message |
| SIGTERM | graceful: no new ticks, current job finishes (k8s grace period ≥ job length) |

### Service environment

| Variable | Service(s) | Purpose |
|---|---|---|
| `NORN_SCHEDULER_HOST` / `NORN_SCHEDULER_PORT` | scheduler | HTTP-API bind address (in-container `0.0.0.0`) and port (default `9300`) |
| `NORN_SCHEDULER_RETRIES` / `NORN_SCHEDULER_RETRY_BASE_SECONDS` / `NORN_SCHEDULER_MISFIRE_GRACE_SECONDS` | scheduler | retry/misfire defaults (override `config/scheduler.yml`) |
| `NORN_FORECAST_TIMESFM__WORKER_URL` | scheduler | where `timesfm-2.5` jobs reach the inference worker |
| `NORN_AGENT_WORKER_URL` | scheduler | URL of the agent worker; unset/empty = LLM judge runs in-process |
| `NORN_MCP_HOST` / `NORN_MCP_PORT` | MCP | MCP server bind address and port (default `9200`) |
| `NORN_AGENT_BASE_URL` | agent worker | LLM provider endpoint (e.g. Ollama via `host.docker.internal`) |
| `NORN_CLICKHOUSE_URL` | scheduler, MCP | full ClickHouse DSN (overrides `database.yml`) |
| `NORN_DB_PASSWORD` | scheduler, MCP, agent worker | ClickHouse password (the agent worker needs it because settings load eagerly, even though it never touches ClickHouse) |

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
