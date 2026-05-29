from datetime import datetime, timedelta

from norn_core.contract import ForecastJob
from norn_forecast.calibration import backtest_metrics, calibrate_job


def test_backtest_metrics_clean_series_is_perfect():
    # A clean weekly series -> seasonal-naive predicts exactly -> coverage 1, errors 0.
    values = [float(d % 7) for d in range(56)]
    from norn_forecast.forecaster import BaselineForecaster

    m = backtest_metrics(values, BaselineForecaster(7), horizon=7, n_cutoffs=3)
    assert m["n_points"] == 21
    assert m["coverage"] == 1.0
    assert m["wape"] == 0.0
    assert m["bias"] == 0.0


def test_calibrate_job_writes_segment_rows(ch):
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
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"], horizon=7)
    run_id = calibrate_job(job, client=ch)
    rows = ch.query(
        "SELECT segment_key, coverage, wape, n_points FROM forecast_segment "
        "WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows
    assert rows == [("region=eu", 1.0, 0.0, 21)]
