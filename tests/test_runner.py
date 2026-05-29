from datetime import datetime, timedelta

from norn_core.contract import ForecastJob
from norn_forecast.runner import run_job


def _seed_mart(ch, rows):
    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    ch.insert("test_mart", rows, column_names=["ts", "region", "value"])


def test_run_job_writes_points_per_segment(ch):
    start = datetime(2026, 1, 1)
    rows = []
    for d in range(28):  # 4 weeks
        ts = start + timedelta(days=d)
        rows.append([ts, "eu", float(d % 7 + 1)])
        rows.append([ts, "us", float((d % 7 + 1) * 10)])
    _seed_mart(ch, rows)

    job = ForecastJob(
        metric="value",
        source="test_mart",
        dimensions=["region"],
        horizon=7,
        seasonality=7,
    )
    run_id = run_job(job, client=ch)

    res = ch.query(
        "SELECT segment_key, count() FROM forecast_point "
        "WHERE forecast_run_id = %(r)s GROUP BY segment_key ORDER BY segment_key",
        parameters={"r": run_id},
    ).result_rows
    assert res == [("region=eu", 7), ("region=us", 7)]

    run = ch.query(
        "SELECT status, segments_total FROM forecast_run WHERE forecast_run_id = %(r)s",
        parameters={"r": run_id},
    ).result_rows
    assert run == [("success", 2)]


def test_run_job_no_dimensions_single_segment(ch):
    start = datetime(2026, 1, 1)
    rows = [[start + timedelta(days=d), "x", float(d % 7)] for d in range(21)]
    _seed_mart(ch, rows)

    job = ForecastJob(metric="value", source="test_mart", horizon=3, seasonality=7)
    run_id = run_job(job, client=ch)
    seg = ch.query(
        "SELECT DISTINCT segment_key FROM forecast_point WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows
    assert seg == [("all",)]


def test_run_job_uses_injected_forecaster(ch):
    from datetime import datetime, timedelta

    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    start = datetime(2026, 1, 1)
    ch.insert(
        "test_mart",
        [[start + timedelta(days=d), "eu", float(d)] for d in range(20)],
        column_names=["ts", "region", "value"],
    )

    class ConstForecaster:
        def forecast(self, values, horizon):
            return [
                {"horizon_step": h, "y_hat": 42.0, "p10": 40.0, "p50": 42.0, "p90": 44.0}
                for h in range(1, horizon + 1)
            ]

    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"], horizon=4)
    run_id = run_job(job, client=ch, forecaster=ConstForecaster())
    rows = ch.query(
        "SELECT y_hat FROM forecast_point WHERE forecast_run_id=%(r)s LIMIT 1",
        parameters={"r": run_id},
    ).result_rows
    assert rows == [(42.0,)]


def test_run_job_uses_recent_context(ch):
    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    start = datetime(2026, 1, 1)
    rows = []
    for d in range(20):
        ts = start + timedelta(days=d)
        if d < 15:
            val = 1.0
        else:
            val = 100.0 + (d - 15)  # 100, 101, 102, 103, 104
        rows.append([ts, "eu", val])
    ch.insert("test_mart", rows, column_names=["ts", "region", "value"])

    job = ForecastJob(
        metric="value",
        source="test_mart",
        dimensions=["region"],
        horizon=3,
        context_length=5,
        seasonality=7,
    )
    run_id = run_job(job, client=ch)

    # Compare on the calendar date (ClickHouse-side, timezone-stable) to avoid
    # client-side naive-datetime round-trip skew.
    first_date, max_y_hat = ch.query(
        "SELECT toDate(min(forecast_ts)), max(y_hat) FROM forecast_point "
        "WHERE forecast_run_id = %(r)s",
        parameters={"r": run_id},
    ).result_rows[0]

    last_bar_date = ch.query(
        "SELECT toDate(max(ts)) FROM test_mart WHERE region = 'eu'"
    ).result_rows[0][0]

    # Forecast continues AFTER the most-recent bar, not after the 5th-oldest bar
    # (which the old `ORDER BY ts LIMIT` would have selected). The oldest-5 rows
    # span 2026-01-01..05, so the buggy forecast would have started ~2026-01-06.
    assert first_date == last_bar_date + timedelta(days=1)
    # context_length 5 < seasonality 7, so the baseline carries the last value
    # (~104) from the recent window, NOT the stale 1.0 from the oldest rows.
    assert max_y_hat >= 50.0
