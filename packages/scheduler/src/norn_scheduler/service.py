"""
packages/scheduler/src/norn_scheduler/service.py

Built-in norn scheduler: manifest -> APScheduler (cron triggers) -> running
actions with retries. Single-replica by design (no distributed locks).
The scheduler's own state is ephemeral: last_results lives in memory until
restart, durable audit stays in the contract (forecast_run, etc.).

Classes/functions:
- NornScheduler — BackgroundScheduler wiring: register/start/shutdown,
  trigger(name) for a manual run, last_results for /jobs.
- serve(manifest_path) — build the scheduler + FastAPI and spin up uvicorn
  (invoked by the `norn scheduler` command).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from pydantic import ValidationError

from norn_scheduler.actions import run_action
from norn_scheduler.manifest import ManifestJob, SchedulerManifest
from norn_scheduler.retry import with_retries

logger = logging.getLogger(__name__)

# Configuration errors do not fix themselves between attempts — retrying a
# missing/invalid job YAML only burns the whole backoff chain before surfacing.
_CONFIG_ERRORS = (FileNotFoundError, ValidationError)


class NornScheduler:
    """APScheduler wiring around manifest jobs. `run` is injected in tests."""

    def __init__(self, manifest: SchedulerManifest,
                 run: Callable[[ManifestJob], str] = run_action,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        from norn_core.config import get_settings

        self.manifest = manifest
        self._run = run
        self._sleep = sleep  # injected for tests: real retries sleep config seconds
        self._cfg = get_settings().scheduler
        self.aps = BackgroundScheduler(timezone="UTC")
        # name -> {status, run_id|error, at}; only for /jobs (lost on restart — ok)
        self.last_results: dict[str, dict] = {}
        self._running: set[str] = set()
        self._lock = threading.Lock()

    # --- lifecycle ---
    def start(self) -> None:
        for entry in self.manifest.enabled_jobs():
            self.aps.add_job(
                self._execute, CronTrigger.from_crontab(entry.schedule),
                id=entry.name, args=[entry],
                max_instances=1, coalesce=True,
                misfire_grace_time=self._cfg.misfire_grace_seconds,
            )
        self.aps.start()
        logger.info("scheduler started: %d enabled jobs", len(self.manifest.enabled_jobs()))

    def shutdown(self) -> None:
        # graceful: new ticks don't start, the current job finishes
        self.aps.shutdown(wait=True)

    # --- execution ---
    def _execute(self, entry: ManifestJob) -> None:
        # Atomic check-and-reserve: this is the real overlap guard. trigger()'s
        # pre-check only provides the 409 UX; without this re-check a manual run
        # enqueued between that check and APScheduler dispatch could overlap a
        # cron tick (their job ids differ, so max_instances=1 cannot help).
        with self._lock:
            if entry.name in self._running:
                logger.warning("job %s skipped: previous run still in progress", entry.name)
                return
            self._running.add(entry.name)
        attempts = entry.retries if entry.retries is not None else self._cfg.retries
        try:
            run_id = with_retries(lambda: self._run(entry), attempts,
                                  self._cfg.retry_base_seconds, sleep=self._sleep,
                                  no_retry=_CONFIG_ERRORS)
            self.last_results[entry.name] = {
                "status": "success", "run_id": run_id,
                "at": datetime.now(UTC).isoformat(),
            }
        except Exception as e:  # after all retries: log + last_results, service stays up
            logger.error("job %s failed after retries: %s", entry.name, e, exc_info=True)
            self.last_results[entry.name] = {
                "status": "failed", "error": str(e),
                "at": datetime.now(UTC).isoformat(),
            }
        finally:
            with self._lock:
                self._running.discard(entry.name)

    def trigger(self, name: str) -> None:
        """Manual out-of-schedule run (same overlap protection as cron ticks)."""
        entry = next((j for j in self.manifest.jobs if j.name == name), None)
        if entry is None:
            raise KeyError(name)
        with self._lock:
            if name in self._running:
                raise RuntimeError(f"job {name!r} is already running")
        self.aps.add_job(self._execute, id=f"{name}:manual:{datetime.now(UTC).timestamp()}",
                         args=[entry], max_instances=1)

    # --- introspection for the API ---
    def job_states(self) -> list[dict]:
        cron = {j.id: j.next_run_time for j in self.aps.get_jobs() if ":manual:" not in j.id}
        return [{
            "name": j.name, "action": j.action, "job": j.job,
            "schedule": j.schedule, "enabled": j.enabled,
            "next_run": cron.get(j.name).isoformat() if cron.get(j.name) else None,
            "running": j.name in self._running,
            "last": self.last_results.get(j.name),
        } for j in self.manifest.jobs]


def serve(manifest_path: str) -> None:
    """Entry for `norn scheduler`: manifest -> scheduler -> uvicorn API (blocking)."""
    import uvicorn

    from norn_core.config import get_settings
    from norn_scheduler.api import create_app

    manifest = SchedulerManifest.from_yaml(manifest_path)  # fail-fast if invalid
    sched = NornScheduler(manifest)
    cfg = get_settings().scheduler
    sched.start()
    try:
        uvicorn.run(create_app(sched), host=cfg.host, port=cfg.port)
    finally:
        sched.shutdown()
