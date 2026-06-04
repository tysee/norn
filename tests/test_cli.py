from typer.testing import CliRunner

import norn_cli.main as cli_main
from conftest import DSN
from norn_cli.main import app

runner = CliRunner()


class _FakeClient:
    """Recording stand-in for a ClickHouse client (no real connection)."""

    def __init__(self) -> None:
        self.closed = False

    def command(self, *_args, **_kwargs) -> None:  # used by apply_schema
        return None

    def close(self) -> None:
        self.closed = True


def test_schema_apply_closes_client(monkeypatch):
    """One-shot commands must release the ClickHouse connection pool."""
    fake = _FakeClient()
    monkeypatch.setattr(cli_main, "get_client", lambda *a, **k: fake)

    result = runner.invoke(app, ["schema-apply"])

    assert result.exit_code == 0, result.output
    assert fake.closed is True


def test_forecast_command_runs_and_prints_run_id(ch, tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    start = datetime(2026, 1, 1)
    ch.insert(
        "test_mart",
        [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(21)],
        column_names=["ts", "region", "value"],
    )
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", DSN)  # same DB as the ch fixture, never a hardcoded live DB

    job = tmp_path / "job.yml"
    job.write_text(
        "metric: value\nsource: test_mart\ndimensions: [region]\nhorizon: 5\nseasonality: 7\n"
    )
    result = runner.invoke(app, ["forecast", str(job)])
    assert result.exit_code == 0, result.output
    assert "run_id=" in result.output


def test_schema_apply_command(ch, monkeypatch):
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", DSN)  # same DB as the ch fixture, never a hardcoded live DB
    result = runner.invoke(app, ["schema-apply"])
    assert result.exit_code == 0, result.output
    assert "schema applied" in result.output.lower()


def test_up_requires_docker(monkeypatch):
    # `norn up` is a local-dev convenience: with Docker absent it must exit cleanly
    # with a helpful message, not crash on a docker subprocess.
    monkeypatch.setattr(cli_main.shutil, "which", lambda _: None)
    monkeypatch.setattr(cli_main.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run docker")))
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1


def test_print_schema_outputs_ddl():
    result = runner.invoke(app, ["print-schema"])
    assert result.exit_code == 0, result.output
    assert "CREATE TABLE IF NOT EXISTS forecast_point" in result.output


def test_schema_apply_refuses_when_unmanaged(monkeypatch):
    # manage_schema=false -> schema-apply must NOT run DDL; exits 1 with guidance, never connects.
    monkeypatch.setenv("NORN_DB_MANAGE_SCHEMA", "false")
    monkeypatch.setattr(cli_main, "get_client",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect")))
    result = runner.invoke(app, ["schema-apply"])
    assert result.exit_code == 1


def test_print_schema_threads_retention():
    # print-schema must substitute the configured retention into the DDL.
    result = runner.invoke(app, ["print-schema"])
    assert result.exit_code == 0, result.output
    assert "TTL created_at + INTERVAL 12 MONTH" in result.output


def test_schema_apply_threads_retention(monkeypatch):
    # schema-apply must pass the configured retention into apply_schema.
    fake = _FakeClient()
    monkeypatch.setattr(cli_main, "get_client", lambda *a, **k: fake)
    seen: dict = {}
    monkeypatch.setattr(
        cli_main, "apply_schema",
        lambda client, retention_months: seen.update(retention=retention_months),
    )
    result = runner.invoke(app, ["schema-apply"])
    assert result.exit_code == 0, result.output
    assert seen["retention"] == 12


def test_forecast_threads_retention_into_prepare(ch, tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    start = datetime(2026, 1, 1)
    ch.insert(
        "test_mart",
        [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(21)],
        column_names=["ts", "region", "value"],
    )
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", DSN)  # same DB as the ch fixture, never a hardcoded live DB
    seen: dict = {}
    monkeypatch.setattr(
        cli_main, "prepare_schema",
        lambda client, manage_schema, retention_months: seen.update(
            retention=retention_months
        ),
    )
    job = tmp_path / "job.yml"
    job.write_text(
        "metric: value\nsource: test_mart\ndimensions: [region]\nhorizon: 5\nseasonality: 7\n"
    )
    result = runner.invoke(app, ["forecast", str(job)])
    assert result.exit_code == 0, result.output
    assert seen["retention"] == 12


def test_up_missing_compose_file(monkeypatch, tmp_path):
    # Docker present but the compose file (NORN_COMPOSE_FILE) is missing -> clear exit,
    # no cryptic path crash (covers the pip-install case where deploy/ is absent).
    monkeypatch.setattr(cli_main.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setenv("NORN_COMPOSE_FILE", str(tmp_path / "absent.yml"))
    monkeypatch.setattr(cli_main.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run docker")))
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1


def test_scheduler_command_fails_fast_on_bad_manifest(tmp_path):
    bad = tmp_path / "jobs.yml"
    bad.write_text("jobs:\n  - name: x\n    action: ingest\n    job: j.yml\n    schedule: '0 6 * * *'\n")
    result = runner.invoke(app, ["scheduler", "--manifest", str(bad)])
    assert result.exit_code == 1
    out = result.output + str(result.exception or "")
    assert "action" in out  # validation names the offending field


def test_scheduler_command_serves(monkeypatch, tmp_path):
    good = tmp_path / "jobs.yml"
    good.write_text("jobs:\n  - name: x\n    action: forecast\n    job: j.yml\n    schedule: '0 6 * * *'\n")
    seen = {}
    import norn_scheduler.service as svc
    monkeypatch.setattr(svc, "serve", lambda p: seen.setdefault("path", p))
    result = runner.invoke(app, ["scheduler", "--manifest", str(good)])
    assert result.exit_code == 0, result.output
    assert seen["path"] == str(good)
