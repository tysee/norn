import time

from fastapi.testclient import TestClient

from norn_scheduler.api import create_app
from norn_scheduler.manifest import ManifestJob, SchedulerManifest
from norn_scheduler.service import NornScheduler


def _sched(run=lambda e: "rid-1"):
    m = SchedulerManifest(jobs=[
        ManifestJob(name="a", action="forecast", job="/jobs/a.yml", schedule="0 6 * * *"),
    ])
    s = NornScheduler(m, run=run)
    s.start()
    return s


def test_health():
    s = _sched()
    try:
        assert TestClient(create_app(s)).get("/health").json() == {"status": "ok"}
    finally:
        s.shutdown()


def test_jobs_listing():
    s = _sched()
    try:
        body = TestClient(create_app(s)).get("/jobs").json()
        assert body[0]["name"] == "a" and body[0]["next_run"]
        assert body[0]["last"] is None and body[0]["running"] is False
    finally:
        s.shutdown()


def test_trigger_and_last_status():
    s = _sched()
    try:
        c = TestClient(create_app(s))
        assert c.post("/jobs/a/trigger").status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            last = c.get("/jobs").json()[0]["last"]
            if last:
                break
            time.sleep(0.05)
        assert last["status"] == "success" and last["run_id"] == "rid-1"
    finally:
        s.shutdown()


def test_trigger_unknown_404_and_running_409():
    def slow(e):
        time.sleep(0.5)
        return "rid"

    s = _sched(run=slow)
    try:
        c = TestClient(create_app(s))
        assert c.post("/jobs/zzz/trigger").status_code == 404
        assert c.post("/jobs/a/trigger").status_code == 202
        deadline = time.monotonic() + 5
        while "a" not in s._running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert c.post("/jobs/a/trigger").status_code == 409
    finally:
        s.shutdown()
