"""
packages/scheduler/src/norn_scheduler/api.py

Scheduler HTTP surface: /health for probes, /jobs for observability
(manifest + next_run + last in-memory result), POST /jobs/{name}/trigger
for a manual run. Wrapped by create_app() over NornScheduler.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from norn_scheduler.service import NornScheduler


def create_app(sched: NornScheduler) -> FastAPI:
    app = FastAPI(title="norn-scheduler")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/jobs")
    def jobs() -> list[dict]:
        return sched.job_states()

    @app.post("/jobs/{name}/trigger", status_code=202)
    def trigger(name: str) -> dict:
        try:
            sched.trigger(name)
        except KeyError:
            raise HTTPException(404, f"unknown job: {name}")
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"triggered": name}

    return app
