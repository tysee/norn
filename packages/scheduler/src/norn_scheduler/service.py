"""
packages/scheduler/src/norn_scheduler/service.py

Встроенный шедулер norn: манифест -> APScheduler (cron-триггеры) -> запуск
действий с ретраями. Single-replica по дизайну (без распределённых локов).
Состояние самого шедулера эфемерно: last_results живёт в памяти до рестарта,
durable-аудит остаётся в контракте (forecast_run и т.п.).

Классы/функции:
- NornScheduler — обвязка BackgroundScheduler: register/start/shutdown,
  trigger(name) для ручного запуска, last_results для /jobs.
- serve(manifest_path) — собрать шедулер + FastAPI и крутить uvicorn (вызывается
  командой `norn scheduler`).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from norn_scheduler.actions import run_action
from norn_scheduler.manifest import ManifestJob, SchedulerManifest
from norn_scheduler.retry import with_retries

logger = logging.getLogger(__name__)


class NornScheduler:
    """APScheduler wiring around manifest jobs. `run` инъектируется в тестах."""

    def __init__(self, manifest: SchedulerManifest,
                 run: Callable[[ManifestJob], str] = run_action,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        from norn_core.config import get_settings

        self.manifest = manifest
        self._run = run
        self._sleep = sleep  # инъекция для тестов: реальные ретраи спят config-секунды
        self._cfg = get_settings().scheduler
        self.aps = BackgroundScheduler(timezone="UTC")
        # имя -> {status, run_id|error, at}; только для /jobs (рестарт теряет — ок)
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
        # graceful: новые тики не стартуют, текущая джоба дорабатывает
        self.aps.shutdown(wait=True)

    # --- execution ---
    def _execute(self, entry: ManifestJob) -> None:
        with self._lock:
            self._running.add(entry.name)
        attempts = entry.retries if entry.retries is not None else self._cfg.retries
        try:
            run_id = with_retries(lambda: self._run(entry), attempts,
                                  self._cfg.retry_base_seconds, sleep=self._sleep)
            self.last_results[entry.name] = {
                "status": "success", "run_id": run_id,
                "at": datetime.now(UTC).isoformat(),
            }
        except Exception as e:  # после всех ретраев: лог + last_results, сервис живёт
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

    manifest = SchedulerManifest.from_yaml(manifest_path)  # fail-fast при невалидном
    sched = NornScheduler(manifest)
    cfg = get_settings().scheduler
    sched.start()
    try:
        uvicorn.run(create_app(sched), host=cfg.host, port=cfg.port)
    finally:
        sched.shutdown()
