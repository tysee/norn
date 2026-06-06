# norn-scheduler

*Audience: operators wiring up automated norn runs, and developers extending the scheduler package.*

`norn-scheduler` is the built-in cron service for norn jobs. It reads a
`jobs.yml` **manifest** and turns it into APScheduler cron triggers that run
`forecast`, `calibrate`, and `deps` actions on a schedule — with in-process
retries, overlap protection, a misfire catch-up, graceful shutdown, and a small
HTTP control API on port **9300**. It is deliberately narrow in scope: the
scheduler automates **norn jobs only**. Upstream ingest, dbt runs, and Lightdash
deploys stay external (your own cron/CI), exactly as they would without it. It
ships as the `norn` CLI command `norn scheduler --manifest <jobs.yml>` and as a
service container (see [Running it](#running-it)).

---

## Functionality

### The manifest contract

The manifest is the **single source of the schedule**. The `schedule:` field in
an individual job YAML is only a hint; the scheduler ignores it and uses the
manifest's `schedule` instead. The manifest is an explicit list — no globs.

A `ManifestJob` has these fields:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `name` | string | yes | Unique entry name; used for the APScheduler job id, overlap locks, and `/jobs/{name}/trigger`. |
| `action` | `forecast` \| `calibrate` \| `deps` | yes | Which action to run. Validated as a literal — any other value fails the manifest. |
| `job` | string | yes | Path to an existing job YAML (a `ForecastJob` for `forecast`/`calibrate`, a `DependencyJob` for `deps`). |
| `schedule` | string | yes | A 5-field cron expression. Overrides the schedule hint in the job YAML. |
| `retries` | int | no | Per-job retry count on top of the first attempt. Unset (`None`) falls back to `scheduler.retries` from config. |
| `enabled` | bool | no | Defaults to `true`. Only enabled jobs are registered with APScheduler. |

Validation is **fail-fast** and happens when the manifest is loaded
(`SchedulerManifest.from_yaml`), before the service starts serving:

- **Unique names** — a duplicate `name` raises a validation error.
- **Valid cron** — each `schedule` is parsed with APScheduler's
  `CronTrigger.from_crontab`; an unparseable expression raises a clear error
  naming the offending value.
- **Known action** — the `action` literal restricts values to the three
  supported actions.

The `norn scheduler` command and `serve()` both load the manifest up front, so
an invalid manifest is reported as an operator error at startup rather than
failing silently at the first tick.

### The service

`NornScheduler` wraps a `BackgroundScheduler` running in UTC. On `start()` it
registers one cron job per **enabled** manifest entry with:

- a `CronTrigger` built from the entry's `schedule`;
- `max_instances=1` — **overlap protection**. If a job overruns its interval the
  next tick is **skipped** (APScheduler logs a WARN); ticks are never queued.
- `coalesce=True` together with `misfire_grace_time = misfire_grace_seconds` —
  if a tick was missed while the service was down, **one** catch-up run fires on
  restart within the grace window; older missed ticks are dropped.

Each execution runs through an **exponential retry wrapper** (`with_retries`):
the action is attempted, and on exception it is retried up to `attempts` times
(`entry.retries`, or `scheduler.retries` if unset) with a backoff of
`retry_base_seconds * 2**attempt` between attempts, then the final exception is
re-raised. After all retries are exhausted the service logs the error and stays
up — the failure is recorded in the in-memory last-result map (see below); the
next cron tick is the recovery mechanism.

**Graceful shutdown:** `shutdown()` calls `aps.shutdown(wait=True)` — no new
ticks start and the currently running job is allowed to finish. `serve()` wires
this into the process lifecycle so SIGTERM drains in flight. (For Kubernetes,
set a termination grace period at least as long as your longest job.)

### Actions

`run_action` is the shared unit for a cron tick, a retry, and a manual trigger.
It mirrors the CLI one-shot lifecycle: open the ClickHouse client, run
`prepare_schema` (honoring `database.manage_schema`), dispatch the action, then
close the client. The actions write their own audit rows; only dispatch happens
here. Each returns a `run_id`:

| `action` | Calls | Package |
|---|---|---|
| `forecast` | `run_job(ForecastJob.from_yaml(job))` | `norn_forecast.runner` |
| `calibrate` | `calibrate_job(ForecastJob.from_yaml(job))` | `norn_forecast.calibration` |
| `deps` | `analyze_dependencies(DependencyJob.from_yaml(job)).run_id` | `norn_agent.analyze` |

### The HTTP control API (`:9300`)

`create_app(scheduler)` builds a small FastAPI app:

| Endpoint | Status | Purpose |
|---|---|---|
| `GET /health` | 200 | Liveness probe → `{"status": "ok"}`. |
| `GET /jobs` | 200 | Every manifest entry with its `action`, `job`, `schedule`, `enabled`, the APScheduler `next_run` for cron jobs, a `running` flag, and the `last` result. |
| `POST /jobs/{name}/trigger` | 202 | Manual out-of-schedule run of `name`. **404** if the job name is unknown; **409** if the job is already running (same overlap protection as cron ticks). |

The `last` status returned by `/jobs` comes from the scheduler's **in-memory
last-result map**, not from `forecast_run`. `forecast_run` rows carry no
manifest-job name and `calibrate`/`deps` do not write there at all, so the
in-memory map is the only uniform source across all three actions. A **restart
clears the map**; `forecast_run` remains the durable audit of forecast runs.

### Single-replica by design

The scheduler holds no distributed lock — overlap protection is per-process
(`max_instances=1` plus an in-process running-set). Run **exactly one** replica.
Two replicas would each fire the schedule independently.

### Statelessness

The scheduler's own state is ephemeral. The only state it keeps is the
in-memory last-result map and the running-set, both lost on restart with no
durability loss — **all persistent state lives in ClickHouse** (the contract
tables written by the actions). The container is therefore stateless and freely
restartable; there is nothing to back up or migrate on the scheduler side.

---

## Configuration

The scheduler reads its settings from `config/scheduler.yml` (the
`SchedulerSettings` section), with the standard **env > yaml** override rule.

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `127.0.0.1` | Interface the HTTP API binds to. `127.0.0.1` = loopback only. |
| `port` | int | `9300` | TCP port of the HTTP API (`/health`, `/jobs`, `/jobs/{name}/trigger`). |
| `retries` | int | `2` | Default retry attempts per job, on top of the first run. A manifest entry's `retries` overrides this per job. |
| `retry_base_seconds` | int | `30` | Base of the exponential backoff between retries: `base * 2**attempt`. |
| `misfire_grace_seconds` | int | `3600` | How late a cron tick missed during a restart may still fire (once) within this window. |

### Environment overrides

Every field can be overridden with the **`NORN_SCHEDULER_`** env prefix (env
beats YAML), e.g. `NORN_SCHEDULER_RETRIES`, `NORN_SCHEDULER_RETRY_BASE_SECONDS`,
`NORN_SCHEDULER_MISFIRE_GRACE_SECONDS`.

The service **container overrides the bind host to `0.0.0.0`** via
`NORN_SCHEDULER_HOST=0.0.0.0` so the API is reachable from outside the
container; the YAML default stays loopback-only for local runs.

Per-job retry tuning lives in the manifest, not in config: set `retries:` on an
individual `ManifestJob` entry to override `scheduler.retries` for that job
only.

---

## Running it

CLI (one process, blocking — runs the API and the scheduler together):

```bash
norn scheduler --manifest /jobs/deploy/jobs.yml
```

An invalid manifest is reported on stderr and the command exits non-zero before
the service starts.

In Compose, the scheduler is an opt-in profile in
`deploy/docker-compose.services.yml`. Mount the instance root at `/jobs` with
`NORN_JOBS_DIR` so the manifest and the job YAMLs it references resolve inside
the container:

```bash
cd deploy
NORN_JOBS_DIR=../instances/ett docker compose -f docker-compose.services.yml \
  --profile scheduler up -d scheduler
```

With `NORN_JOBS_DIR=../instances/ett` the instance root mounts at `/jobs`, so
the manifest is reachable at `/jobs/deploy/jobs.yml` and the job YAMLs at
`/jobs/forecasts/…`.

For the full operations guide — the infra-vs-services compose split, the failure
matrix, the service-environment table, and cloud/Kubernetes notes — see
[../deployment.md](../deployment.md).

---

## See also

- [../core/README.md](../core/README.md) — configuration model, ClickHouse client, contract schema.
- [../jobs.md](../jobs.md) — forecast / calibrate / dependency jobs the scheduler runs.
- [../deployment.md](../deployment.md) — the full services ops guide (compose, failure matrix, cloud/k8s).
