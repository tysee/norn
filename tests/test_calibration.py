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
    # clean weekly series -> seasonal-naive is perfect: coverage 1.0, wape 0.0.
    # n_points = (valid cutoffs) * horizon; cutoffs are capped by series length
    # (56 days, horizon 7 -> at most 7 origins), so it tracks the config n_cutoffs.
    from norn_core.config import get_settings

    expected_pts = min(get_settings().forecast.calibration.n_cutoffs, 7) * 7
    assert rows == [("region=eu", 1.0, 0.0, expected_pts)]
