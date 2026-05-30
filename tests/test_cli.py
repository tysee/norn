from typer.testing import CliRunner

import norn_cli.main as cli_main
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
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", "http://norn:norn@localhost:8123/norn")

    job = tmp_path / "job.yml"
    job.write_text(
        "metric: value\nsource: test_mart\ndimensions: [region]\nhorizon: 5\nseasonality: 7\n"
    )
    result = runner.invoke(app, ["forecast", str(job)])
    assert result.exit_code == 0, result.output
    assert "run_id=" in result.output


def test_schema_apply_command(ch, monkeypatch):
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", "http://norn:norn@localhost:8123/norn")
    result = runner.invoke(app, ["schema-apply"])
    assert result.exit_code == 0, result.output
    assert "schema applied" in result.output.lower()
