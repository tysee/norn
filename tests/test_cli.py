from typer.testing import CliRunner

from norn_cli.main import app

runner = CliRunner()


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
