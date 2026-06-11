from datetime import datetime, timedelta

from norn_core.contract import CovariateSpec, ForecastJob
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


class _CapturingForecaster:
    """Echoes a flat forecast and records the covariates passed per call."""

    def __init__(self):
        self.calls: list[dict | None] = []

    def forecast(self, values, horizon, covariates=None):
        self.calls.append(covariates)
        v = float(values[-1])
        return [
            {"horizon_step": h + 1, "y_hat": v, "p10": v - 1, "p50": v, "p90": v + 1}
            for h in range(horizon)
        ]


def _seed_xreg_mart(ch):
    # target 'value'/eu in test_mart + leader series in mart_metric (covariate source).
    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    ch.command("DROP TABLE IF EXISTS mart_metric")
    ch.command(
        "CREATE TABLE mart_metric (ts DateTime, metric_name String, value Float64, "
        "segment_key String) ENGINE = MergeTree ORDER BY (metric_name, segment_key, ts)"
    )
    start = datetime(2026, 1, 1)
    ch.insert(
        "test_mart",
        [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(56)],
        column_names=["ts", "region", "value"],
    )
    # leader history starts BEFORE the target (covers ts[0]-lag) — like production,
    # where covariate_series pulls context_length+lag points back.
    ch.insert(
        "mart_metric",
        [[start + timedelta(days=d), "value", float(d), "region=lead"] for d in range(-10, 56)],
        column_names=["ts", "metric_name", "value", "segment_key"],
    )
    return start


def test_calibrate_job_passes_covariates_per_cutoff(ch, monkeypatch):
    # Regression (e2e verify finding): the xreg job's calibration was byte-identical
    # to the plain model because backtest never attached covariates. With explicit
    # covariates the forecaster must receive a context+horizon array at EVERY cutoff.
    monkeypatch.setenv("NORN_FORECAST_COVARIATES__HORIZON_POLICY", "ffill")
    start = _seed_xreg_mart(ch)
    job = ForecastJob(
        metric="value", source="test_mart", dimensions=["region"], horizon=7,
        covariates=[CovariateSpec(metric="value", segment="region=lead", lag=3)],
    )
    fc = _CapturingForecaster()
    run_id = calibrate_job(job, client=ch, forecaster=fc)

    assert fc.calls, "no forecast calls made"
    # every backtest cutoff (metrics + points share cutoffs) got the covariate
    assert all(c is not None and len(c) == 1 for c in fc.calls), fc.calls
    key = next(iter(fc.calls[0]))
    assert key == "region=lead:value@lag3"
    # the leader is value=d (by day index); at lag 3 the covariate at context day i
    # must be the leader's REAL value at i-3 — and the horizon tail must keep
    # extending from history known at the cutoff (no lookahead past the cutoff).
    from norn_core.config import get_settings

    n_cutoffs = min(get_settings().forecast.calibration.n_cutoffs, 7)
    first = fc.calls[0][key]
    cut = 56 - n_cutoffs * 7
    assert len(first) == cut + 7  # context + horizon
    assert first[3] == float(0)   # day 3 sees leader day 0
    assert first[cut - 1] == float(cut - 1 - 3)
    # horizon part: production-faithful = leader known only up to the cutoff,
    # so steps with (ts - lag) beyond the cutoff are ffilled from the last known.
    assert max(first[cut:]) <= float(cut - 1), "lookahead leak: used leader data after cutoff"

    # backtest model rows are tagged as xreg so they're distinguishable
    models = {r[0] for r in ch.query(
        "SELECT DISTINCT model_name FROM forecast_point WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id}).result_rows}
    assert models == {"baseline-seasonal-naive+xreg (backtest)"}


def test_calibrate_job_without_covariates_unchanged(ch):
    # plain jobs keep the old behavior: no covariates kwarg, old model tag.
    _seed_xreg_mart(ch)
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"], horizon=7)
    fc = _CapturingForecaster()
    run_id = calibrate_job(job, client=ch, forecaster=fc)
    assert fc.calls and all(c is None for c in fc.calls)
    models = {r[0] for r in ch.query(
        "SELECT DISTINCT model_name FROM forecast_point WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id}).result_rows}
    assert models == {"baseline-seasonal-naive (backtest)"}


def test_backtest_metrics_all_cutoffs_skipped_returns_zero():
    # series shorter than one fold (len <= horizon -> every cut <= 0) -> the
    # explicit zero-metrics early return, not a crash
    from norn_forecast.forecaster import BaselineForecaster

    m = backtest_metrics([1.0] * 3, BaselineForecaster(7), horizon=3, n_cutoffs=3)
    assert m == {"coverage": 0.0, "wape": 0.0, "mape": 0.0, "bias": 0.0, "n_points": 0}


def test_calibrate_job_single_backtest_pass(ch):
    # metrics are derived from backtest_points, not recomputed by a second
    # rolling-origin pass — the forecaster must be called exactly once per cutoff
    _seed_xreg_mart(ch)
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"], horizon=7)
    fc = _CapturingForecaster()
    calibrate_job(job, client=ch, forecaster=fc)
    from norn_core.config import get_settings

    expected_cutoffs = min(get_settings().forecast.calibration.n_cutoffs, 7)
    assert len(fc.calls) == expected_cutoffs


def test_calibrate_job_marks_single_fold_as_sparse(ch):
    # 14 days with horizon 7 admits exactly one rolling-origin fold (7 points):
    # metrics from a single window are too noisy -> is_sparse=1 (review F-5)
    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    start = datetime(2026, 1, 1)
    ch.insert(
        "test_mart",
        [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(14)],
        column_names=["ts", "region", "value"],
    )
    job = ForecastJob(metric="value", source="test_mart", dimensions=["region"], horizon=7)
    run_id = calibrate_job(job, client=ch)
    n_points, is_sparse = ch.query(
        "SELECT n_points, is_sparse FROM forecast_segment WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows[0]
    assert n_points == 7 and is_sparse == 1
