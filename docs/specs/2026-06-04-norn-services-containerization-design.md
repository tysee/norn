# norn services & containerization — design

**Date:** 2026-06-04
**Status:** approved in brainstorming, pending spec review

## Problem

norn today is libraries + a one-shot CLI; orchestration is host cron. The target
runtime is Kubernetes/cloud, where every workload must be a container and the
operator expects a built-in scheduler instead of host cron. The package layout
(`core`/`agent`/`forecast`/`integration`) does not map 1:1 onto services, which
confused operators: packages are libraries; a container is defined by the
*command* it runs, not by a package.

## Decisions (from brainstorming)

1. Containers are required; target = k8s/cloud, local compose stays first-class.
2. norn gets its **own scheduler service** (not k8s CronJob).
3. Scheduler scope: **norn jobs only** (forecast / calibrate / deps).
   Ingest, dbt, lightdash deploy remain external (cron/CI) — out of scope.
4. **One platform image, role chosen by command**: `norn scheduler`, `norn mcp`,
   ad-hoc `norn forecast …` (CLI mode is preserved; same image works as k8s Job).
5. Scheduler guarantees v1: retries, overlap protection, graceful shutdown,
   HTTP API (`/health`, `/jobs`, manual trigger).
6. The LLM dependency judge is extracted into a **separate agent-worker
   container that can be switched off**; when off, deps jobs degrade explicitly
   (existing `LLMUnavailable` → `explained=false`) and do not fail.

## Architecture

```
                       ┌────────────────────────────────────────────┐
  image: norn ───────► │ norn scheduler        norn mcp             │
                       │ (cron by jobs.yml,    (read-only API :9200)│
                       │  HTTP :9300)                               │
                       └──────┬──────────────────────┬──────────────┘
                              │ forecast/calibrate   │ deps
                              ▼                      ▼
  image: timesfm ───►  norn-timesfm :9100     norn-agent :9400  ◄─── image: agent
                       (torch+jax)            (LLM judge, ON/OFF)
                              │                      │
                              ▼                      ▼
                        ClickHouse (all state)   LLM provider (Ollama / cloud)
```

- All state lives in ClickHouse; every container is stateless and restartable.
- Scheduler is **single-replica** (documented; no distributed locking in v1).
- Three images: `norn` (light platform), `norn-timesfm` (exists), `norn-agent`
  (new, light: pydantic-ai + httpx, no torch).
- Packages stay libraries. The timesfm worker was extracted for heavy deps
  (torch/jax, py3.12); the agent package is light — its heavy part (the LLM)
  is already behind the provider's HTTP API, so the agent-worker boundary is
  about *operability* (switch off the judge), not dependency weight.

## Components

### packages/scheduler (`norn_scheduler`)

- **manifest.py** — `jobs.yml` contract (pydantic):
  ```yaml
  jobs:
    - name: ot-timesfm          # unique, used for locks + /trigger
      action: forecast          # forecast | calibrate | deps
      job: /jobs/ot_timesfm.yml # existing job YAML
      schedule: "0 6 * * *"     # cron; manifest OVERRIDES the yml hint
      retries: 2                # optional, default from config/scheduler.yml
      enabled: true             # optional
  ```
  One manifest per instance (example: `instances/ett/deploy/jobs.yml`).
  Explicit list, no globs. Invalid manifest → fail-fast at startup.
- **service.py** — APScheduler wiring: cron trigger per job,
  `max_instances=1` (overlap skip + WARN), `misfire_grace_time` from config,
  exponential retry wrapper around the existing `run_job` / `calibrate_job` /
  `analyze_dependencies`, graceful shutdown (current job finishes).
- **api.py** — FastAPI on :9300: `GET /health`; `GET /jobs` (manifest entries +
  APScheduler next_run + last status from `forecast_run`);
  `POST /jobs/{name}/trigger` (same overlap protection).
- **CLI**: `norn scheduler --manifest /jobs/jobs.yml` (same pattern as `norn mcp`).
- **Config**: new `config/scheduler.yml` (host/port, retry defaults, misfire
  grace, timezone); secrets env-only as everywhere.

### Agent worker (image `norn-agent`)

- `packages/agent/src/norn_agent/agent_worker.py` — FastAPI mirror of the
  timesfm worker: `POST /judge` (measurements + meta + prior_measurements →
  `DependencyDecision` JSON), `GET /health`. Uses the existing `_build_model`
  provider config + env secrets.
- Client side: new `agent.worker_url` config key (null = current in-process
  LLM behavior, fully backwards compatible). When set, `judge_dependencies`
  calls HTTP; unreachable/5xx → existing `LLMUnavailable` → stats written,
  `explained=false`. "Switched off" (replicas=0 / profile down) is therefore a
  NORMAL state, not an error: no retries for the judge.
- `deploy/agent.Dockerfile` — slim image, only `packages/agent` + `core`.

### Platform image (`deploy/norn.Dockerfile`)

- `python:3.13-slim` + `uv sync` (workspace, no dev deps); repo `config/`
  baked as default `NORN_CONFIG_DIR`; env > yaml override as today.
- Compose profiles next to the existing `timesfm`: `scheduler`, `mcp`, `agent`.
  Instance job YAMLs mounted at `/jobs` (k8s: ConfigMap).

## Failure matrix

| Failure | Behavior |
|---|---|
| timesfm worker down | retries (exponential, N from manifest) → `forecast_run.status=failed` + error; next tick runs normally |
| agent worker off/down | `LLMUnavailable` → evidence written, `explained=false`; NO retries (off is normal) |
| ClickHouse down | retries → ERROR log (nowhere to audit); `/jobs` shows last_status=error |
| job overruns its interval | `max_instances=1`: next tick skipped with WARN (no queueing) |
| scheduler restart | misfire grace: one catch-up within window, older ticks skipped; no state lost (none held) |
| invalid jobs.yml | fail-fast at startup with a clear message |
| SIGTERM | graceful: no new ticks, current job finishes (k8s grace period ≥ job length) |

## Testing

- manifest: parse/validate/override/defaults (unit).
- service: fake actions — retry-then-succeed, overlap skip, graceful shutdown;
  APScheduler driven without wall-clock.
- api: TestClient — /health, /jobs contents, trigger fires the action.
- agent worker: HTTP contract on TestModel (no real LLM); client degradation
  test — worker_url set + worker down → `explained=false` (extends the
  existing degradation test).
- e2e: compose profiles up → HTTP trigger → rows in isolated `norn_test` DB.
- images: CI smoke — build + `norn --help` / worker `/health`.

## Out of scope (v1)

- Ingest / dbt / lightdash-deploy scheduling (stays external cron/CI).
- Multi-replica scheduler / distributed locks.
- Web UI; `/jobs` JSON is the v1 surface.
- Retry queue persistence (retries are in-process; a restart drops them — the
  next cron tick is the recovery mechanism).
