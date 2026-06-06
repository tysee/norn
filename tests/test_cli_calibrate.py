from datetime import datetime, timedelta

from typer.testing import CliRunner

from conftest import DSN
from norn_cli.main import app

runner = CliRunner()


def test_calibrate_command(ch, tmp_path, monkeypatch):
    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    start = datetime(2026, 1, 1)
    ch.insert(
        "test_mart",
        [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(56)],
        column_names=["ts", "region", "value"],
    )
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", DSN)  # same DB as the ch fixture, never a hardcoded live DB
    job = tmp_path / "job.yml"
    job.write_text("metric: value\nsource: test_mart\ndimensions: [region]\nhorizon: 7\n")
    result = runner.invoke(app, ["calibrate", str(job)])
    assert result.exit_code == 0, result.output
    assert "calibration run_id=" in result.output
