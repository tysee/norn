from datetime import datetime, timedelta

import pytest

from norn_core.contract import ForecastJob
from norn_forecast.runner import run_job


def test_run_job_rejects_unsafe_source(ch):
    # An attacker-controlled source identifier must be rejected before any SQL
    # is executed against the warehouse (defense-in-depth), not interpolated.
    job = ForecastJob(metric="value", source="bad; drop", horizon=3, seasonality=7)
    with pytest.raises(ValueError):
        run_job(job, client=ch)


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


def test_run_job_forecast_ts_has_no_timezone_skew(ch):
    # Regression: forecast_ts must continue EXACTLY one step (to the second) after
    # the last source bar. clickhouse-connect localizes naive datetimes on INSERT,
    # so a naive forecast_ts is shifted by the machine's UTC offset; the runner tags
    # last_ts as UTC to prevent that. The gap is computed ClickHouse-side so the
    # assertion itself is timezone-stable (it would read 75600s on a UTC+3 box before
    # the fix, 86400s after).
    start = datetime(2026, 1, 1)
    rows = [[start + timedelta(days=d), "eu", float(d % 7 + 1)] for d in range(28)]
    _seed_mart(ch, rows)

    job = ForecastJob(
        metric="value", source="test_mart", dimensions=["region"],
        horizon=3, seasonality=7,
    )
    run_id = run_job(job, client=ch)

    gap = ch.query(
        "SELECT dateDiff('second', "
        "  (SELECT max(ts) FROM test_mart WHERE region = 'eu'), "
        "  min(forecast_ts)) "
        "FROM forecast_point WHERE forecast_run_id = %(r)s",
        parameters={"r": run_id},
    ).result_rows[0][0]
    assert gap == 86400  # exactly +1 day, no UTC-offset skew


def test_run_job_resolves_defaults_from_config(ch, monkeypatch):
    from datetime import datetime, timedelta

    monkeypatch.setenv("NORN_CONFIG_DIR", "config")
    ch.command("CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
               "ENGINE = MergeTree ORDER BY (region, ts)")
    start = datetime(2026, 1, 1)
    ch.insert("test_mart", [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(40)],
              column_names=["ts", "region", "value"])
    # horizon unset on the job -> resolved from config (30)
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"])
    run_id = run_job(job, client=ch)
    n = ch.query("SELECT count() FROM forecast_point WHERE forecast_run_id=%(r)s",
                 parameters={"r": run_id}).result_rows[0][0]
    assert n == 30  # default horizon from config


def test_run_job_without_covariates_unchanged(ch):
    from datetime import datetime, timedelta
    ch.command("CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
               "ENGINE = MergeTree ORDER BY (region, ts)")
    start = datetime(2026, 1, 1)
    ch.insert("test_mart", [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(40)],
              column_names=["ts", "region", "value"])
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"], horizon=5)  # no covariates
    run_id = run_job(job, client=ch)
    n = ch.query("SELECT count() FROM forecast_point WHERE forecast_run_id=%(r)s",
                 parameters={"r": run_id}).result_rows[0][0]
    assert n == 5  # plain baseline path, unchanged


def test_run_job_records_failed_run_on_forecaster_error(ch):
    start = datetime(2026, 1, 1)
    rows = [[start + timedelta(days=d), "x", float(d % 7)] for d in range(21)]
    _seed_mart(ch, rows)

    class _BoomForecaster:
        def forecast(self, *a, **k):
            raise ConnectionError("[Errno 61] Connection refused")  # e.g. TimesFM worker down

    job = ForecastJob(metric="value", source="test_mart", horizon=3, seasonality=7)
    with pytest.raises(RuntimeError):
        run_job(job, client=ch, forecaster=_BoomForecaster())

    run = ch.query("SELECT status, error FROM forecast_run").result_rows
    assert len(run) == 1
    assert run[0][0] == "failed" and "Connection refused" in run[0][1]


def test_run_job_filter_scopes_to_one_value(ch):
    start = datetime(2026, 1, 1)
    rows = []
    for d in range(21):
        ts = start + timedelta(days=d)
        rows.append([ts, "A", float(d % 7 + 1)])
        rows.append([ts, "B", float((d % 7 + 1) * 10)])
    _seed_mart(ch, rows)
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"],
                      filter={"region": "B"}, horizon=3, seasonality=7)
    run_id = run_job(job, client=ch)
    segs = ch.query(
        "SELECT DISTINCT segment_key FROM forecast_point WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows
    assert segs == [("region=B",)]  # only B forecast, not A


def test_run_job_empty_filter_unchanged(ch):
    start = datetime(2026, 1, 1)
    rows = []
    for d in range(21):
        ts = start + timedelta(days=d)
        rows.append([ts, "A", float(d % 7 + 1)])
        rows.append([ts, "B", float((d % 7 + 1) * 10)])
    _seed_mart(ch, rows)
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"],
                      horizon=3, seasonality=7)  # no filter
    run_id = run_job(job, client=ch)
    segs = {r[0] for r in ch.query(
        "SELECT DISTINCT segment_key FROM forecast_point WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id}).result_rows}
    assert segs == {"region=A", "region=B"}  # backward-compatible: both


def test_run_job_filter_unsafe_column_rejected(ch):
    job = ForecastJob(metric="value", source="test_mart",
                      filter={"bad; drop": "x"}, horizon=3, seasonality=7)
    with pytest.raises(ValueError):
        run_job(job, client=ch)


def test_run_job_failed_run_reports_zero_skipped(ch):
    # 'skipped' means "segment had no data", not "failed": the failed-run row
    # carries its semantics in status+error (review F-12)
    start = datetime(2026, 1, 1)
    rows = [[start + timedelta(days=d), "x", float(d % 7)] for d in range(21)]
    _seed_mart(ch, rows)

    class _Boom:
        def forecast(self, *a, **k):
            raise ConnectionError("down")

    job = ForecastJob(metric="value", source="test_mart", horizon=3, seasonality=7)
    with pytest.raises(RuntimeError):
        run_job(job, client=ch, forecaster=_Boom())
    total, skipped = ch.query(
        "SELECT segments_total, segments_skipped FROM forecast_run"
    ).result_rows[0]
    assert total == 1 and skipped == 0


def test_run_job_closes_owned_forecaster(ch, monkeypatch):
    # a forecaster created inside run_job may own an httpx pool — it must be
    # closed on the way out (review F-21); injected forecasters stay open
    from norn_forecast import runner as runner_mod

    start = datetime(2026, 1, 1)
    rows = [[start + timedelta(days=d), "x", float(d % 7)] for d in range(21)]
    _seed_mart(ch, rows)

    closed = {"n": 0}

    class _Closable:
        def forecast(self, values, horizon, covariates=None):
            return [{"horizon_step": h, "y_hat": 1.0, "p10": 0.0, "p50": 1.0, "p90": 2.0}
                    for h in range(1, horizon + 1)]

        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(runner_mod, "make_forecaster", lambda job: _Closable())
    job = ForecastJob(metric="value", source="test_mart", horizon=3, seasonality=7)
    run_job(job, client=ch)
    assert closed["n"] == 1

    # injected forecaster: caller owns it, run_job must NOT close it
    fc = _Closable()
    run_job(job, client=ch, forecaster=fc)
    assert closed["n"] == 1
