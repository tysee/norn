import pytest

from norn_scheduler.manifest import SchedulerManifest

GOOD = """\
jobs:
  - name: ot-timesfm
    action: forecast
    job: {job}
    schedule: "0 6 * * *"
  - name: ot-calibrate
    action: calibrate
    job: {job}
    schedule: "30 6 * * *"
    retries: 0
    enabled: false
"""


def _write(tmp_path, body=GOOD, **fmt):
    # from_yaml fail-fast checks that enabled entries' job YAMLs exist,
    # so the fixture ships a real (minimal) job file next to the manifest
    job = tmp_path / "job.yml"
    job.write_text("metric: value\nsource: test_mart\n")
    p = tmp_path / "jobs.yml"
    p.write_text(body.format(job=job, **fmt))
    return p


def test_manifest_parses_and_defaults(tmp_path):
    m = SchedulerManifest.from_yaml(_write(tmp_path))
    assert [j.name for j in m.jobs] == ["ot-timesfm", "ot-calibrate"]
    j0, j1 = m.jobs
    assert j0.action == "forecast" and j0.enabled is True and j0.retries is None
    assert j1.retries == 0 and j1.enabled is False
    assert [j.name for j in m.enabled_jobs()] == ["ot-timesfm"]


def test_manifest_rejects_duplicate_names(tmp_path):
    p = _write(tmp_path, GOOD.replace("ot-calibrate", "ot-timesfm"))
    with pytest.raises(ValueError, match="duplicate job name"):
        SchedulerManifest.from_yaml(p)


def test_manifest_rejects_bad_cron(tmp_path):
    p = _write(tmp_path, GOOD.replace('"0 6 * * *"', '"not a cron"'))
    with pytest.raises(ValueError, match="cron"):
        SchedulerManifest.from_yaml(p)


def test_manifest_rejects_unknown_action(tmp_path):
    p = _write(tmp_path, GOOD.replace("action: forecast", "action: ingest"))
    with pytest.raises(ValueError):
        SchedulerManifest.from_yaml(p)


def test_manifest_rejects_missing_job_file(tmp_path):
    # fail-fast: a typo in `job:` must surface at startup, not at the first cron tick
    p = tmp_path / "jobs.yml"
    p.write_text(GOOD.format(job="/jobs/definitely_missing.yml"))
    with pytest.raises(FileNotFoundError, match="ot-timesfm"):
        SchedulerManifest.from_yaml(p)


def test_manifest_ignores_missing_job_file_for_disabled_entry(tmp_path):
    job = tmp_path / "job.yml"
    job.write_text("metric: value\nsource: test_mart\n")
    p = tmp_path / "jobs.yml"
    # only the disabled entry points at a missing file -> manifest still loads
    p.write_text(GOOD.format(job=job).replace(f"job: {job}\n    schedule: \"30 6 * * *\"",
                                              "job: /missing.yml\n    schedule: \"30 6 * * *\""))
    m = SchedulerManifest.from_yaml(p)
    assert [j.name for j in m.enabled_jobs()] == ["ot-timesfm"]
