import pytest

from norn_scheduler.manifest import SchedulerManifest

GOOD = """\
jobs:
  - name: ot-timesfm
    action: forecast
    job: /jobs/ot_timesfm.yml
    schedule: "0 6 * * *"
  - name: ot-calibrate
    action: calibrate
    job: /jobs/ot_timesfm.yml
    schedule: "30 6 * * *"
    retries: 0
    enabled: false
"""


def test_manifest_parses_and_defaults(tmp_path):
    p = tmp_path / "jobs.yml"
    p.write_text(GOOD)
    m = SchedulerManifest.from_yaml(p)
    assert [j.name for j in m.jobs] == ["ot-timesfm", "ot-calibrate"]
    j0, j1 = m.jobs
    assert j0.action == "forecast" and j0.enabled is True and j0.retries is None
    assert j1.retries == 0 and j1.enabled is False
    assert [j.name for j in m.enabled_jobs()] == ["ot-timesfm"]


def test_manifest_rejects_duplicate_names(tmp_path):
    p = tmp_path / "jobs.yml"
    p.write_text(GOOD.replace("ot-calibrate", "ot-timesfm"))
    with pytest.raises(ValueError, match="duplicate job name"):
        SchedulerManifest.from_yaml(p)


def test_manifest_rejects_bad_cron(tmp_path):
    p = tmp_path / "jobs.yml"
    p.write_text(GOOD.replace('"0 6 * * *"', '"not a cron"'))
    with pytest.raises(ValueError, match="cron"):
        SchedulerManifest.from_yaml(p)


def test_manifest_rejects_unknown_action(tmp_path):
    p = tmp_path / "jobs.yml"
    p.write_text(GOOD.replace("action: forecast", "action: ingest"))
    with pytest.raises(ValueError):
        SchedulerManifest.from_yaml(p)
