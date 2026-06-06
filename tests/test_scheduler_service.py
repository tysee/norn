import time

from norn_scheduler.manifest import ManifestJob, SchedulerManifest
from norn_scheduler.service import NornScheduler


def _manifest():
    return SchedulerManifest(jobs=[
        ManifestJob(name="a", action="forecast", job="/jobs/a.yml", schedule="0 6 * * *"),
        ManifestJob(name="b", action="deps", job="/jobs/b.yml", schedule="*/5 * * * *",
                    retries=0, enabled=False),
    ])


def test_registers_enabled_jobs_with_limits():
    sched = NornScheduler(_manifest(), run=lambda entry: "rid")
    try:
        sched.start()
        jobs = {j.id: j for j in sched.aps.get_jobs()}
        assert set(jobs) == {"a"}  # disabled job not registered
        assert jobs["a"].max_instances == 1
        assert jobs["a"].misfire_grace_time == 3600
    finally:
        sched.shutdown()


def test_trigger_runs_action_and_records_result():
    calls: list[str] = []
    sched = NornScheduler(_manifest(), run=lambda entry: calls.append(entry.name) or "rid-9")
    try:
        sched.start()
        sched.trigger("a")
        deadline = time.monotonic() + 5
        while not sched.last_results.get("a") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls == ["a"]
        res = sched.last_results["a"]
        assert res["status"] == "success" and res["run_id"] == "rid-9"
    finally:
        sched.shutdown()


def test_trigger_failure_recorded_not_raised():
    def boom(entry):
        raise RuntimeError("worker down")

    # no-op sleep: config default is 2 retries @30s base — don't block on real backoff
    sched = NornScheduler(_manifest(), run=boom, sleep=lambda _: None)
    try:
        sched.start()
        sched.trigger("a")
        deadline = time.monotonic() + 5
        while not sched.last_results.get("a") and time.monotonic() < deadline:
            time.sleep(0.05)
        res = sched.last_results["a"]
        assert res["status"] == "failed" and "worker down" in res["error"]
    finally:
        sched.shutdown()


def test_trigger_unknown_or_running():
    import pytest

    started = []

    def slow(entry):
        started.append(entry.name)
        time.sleep(0.5)
        return "rid"

    sched = NornScheduler(_manifest(), run=slow)
    try:
        sched.start()
        with pytest.raises(KeyError):
            sched.trigger("nope")
        sched.trigger("a")
        deadline = time.monotonic() + 5
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(RuntimeError, match="already running"):
            sched.trigger("a")
    finally:
        sched.shutdown()
