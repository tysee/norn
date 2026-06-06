import norn_scheduler.actions as actions
from norn_scheduler.manifest import ManifestJob


class _FakeClient:
    closed = False

    def close(self):
        self.closed = True


def _stub_env(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(actions, "get_client", lambda: fake)
    monkeypatch.setattr(actions, "prepare_schema", lambda *a, **k: None)
    return fake


def test_forecast_action_dispatch(monkeypatch, tmp_path):
    fake = _stub_env(monkeypatch)
    seen = {}
    jb = tmp_path / "j.yml"
    jb.write_text("metric: value\nsource: test_mart\nhorizon: 7\n")
    monkeypatch.setattr(actions, "run_job", lambda job, client: seen.__setitem__("run", (job.metric, client)) or "rid-1")
    entry = ManifestJob(name="f", action="forecast", job=str(jb), schedule="0 6 * * *")
    assert actions.run_action(entry) == "rid-1"
    assert seen["run"][0] == "value" and seen["run"][1] is fake
    assert fake.closed  # one-shot lifecycle, like the CLI


def test_calibrate_action_dispatch(monkeypatch, tmp_path):
    _stub_env(monkeypatch)
    jb = tmp_path / "j.yml"
    jb.write_text("metric: value\nsource: test_mart\nhorizon: 7\n")
    monkeypatch.setattr(actions, "calibrate_job", lambda job, client: "rid-2")
    entry = ManifestJob(name="c", action="calibrate", job=str(jb), schedule="0 6 * * *")
    assert actions.run_action(entry) == "rid-2"


def test_deps_action_dispatch(monkeypatch, tmp_path):
    _stub_env(monkeypatch)
    jb = tmp_path / "d.yml"
    jb.write_text("source_segment: a\ntarget_segment: b\nmetric: m\nmax_lag: 5\n")

    class _Res:
        run_id = "rid-3"

    monkeypatch.setattr(actions, "analyze_dependencies", lambda job, client: _Res())
    entry = ManifestJob(name="d", action="deps", job=str(jb), schedule="0 6 * * *")
    assert actions.run_action(entry) == "rid-3"


def test_client_closed_on_failure(monkeypatch, tmp_path):
    fake = _stub_env(monkeypatch)
    jb = tmp_path / "j.yml"
    jb.write_text("metric: value\nsource: test_mart\nhorizon: 7\n")

    def boom(job, client):
        raise RuntimeError("worker down")

    monkeypatch.setattr(actions, "run_job", boom)
    entry = ManifestJob(name="f", action="forecast", job=str(jb), schedule="0 6 * * *")
    try:
        actions.run_action(entry)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    assert fake.closed
