from datetime import UTC, datetime, timedelta

from norn_core.contract import CovariateSpec, ForecastJob
from norn_forecast.covariates import build_covariate_array, resolve_covariate_specs

STEP = timedelta(days=1)


def test_build_strict_uses_known_leader_actuals():
    # leader S known for many days; lag 5 >= horizon 3 -> full horizon covered by actuals
    start = datetime(2026, 1, 1)
    src_ts = [start + STEP * i for i in range(40)]
    src_vals = [float(i) for i in range(40)]          # S(day i) = i
    target_ts = [start + STEP * i for i in range(10, 30)]  # context days 10..29
    arr = build_covariate_array(target_ts, src_ts, src_vals, lag=5, horizon=3, step=STEP, policy="strict")
    assert arr is not None
    assert len(arr) == len(target_ts) + 3
    # context point 0 (day 10): S(day 10-5=5) = 5.0
    assert arr[0] == 5.0
    # last horizon point (day 29+3=32): S(32-5=27) = 27.0  (known actual since lag>=horizon)
    assert arr[-1] == 27.0


def test_build_strict_skips_when_lag_lt_horizon():
    start = datetime(2026, 1, 1)
    src_ts = [start + STEP * i for i in range(40)]
    src_vals = [float(i) for i in range(40)]
    target_ts = [start + STEP * i for i in range(10, 30)]
    assert build_covariate_array(target_ts, src_ts, src_vals, lag=2, horizon=10, step=STEP, policy="strict") is None


def test_resolve_specs_explicit_and_from_dependencies(ch):
    # explicit
    job = ForecastJob(metric="close", source="t", dimensions=["symbol"],
                      covariates=[CovariateSpec(metric="log_return", segment="symbol=BTCUSDT", lag=3)])
    specs = resolve_covariate_specs(ch, job, target_segment="symbol=TONUSDT")
    assert any(s.segment == "symbol=BTCUSDT" and s.lag == 3 for s in specs)

    # from confirmed dependency
    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency"); ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    ch.insert("metric_dependency",
              [["r1", "log_return", "symbol=BTCUSDT", "symbol=TONUSDT", "lagged_cross_correlation",
                4, 0.8, "source_leads", None, 0.8, datetime(2025, 1, 1), datetime(2025, 6, 1),
                # created_at must stay inside the contract tables' 12-month TTL window —
                # a hardcoded date silently expires and the insert's rows vanish (time-bomb flake)
                datetime.now(UTC)]],
              column_names=["analysis_run_id","metric_name","source_segment","target_segment","method",
                            "lag","score","direction","p_value","confidence","window_start","window_end","created_at"])
    ch.insert("dependency_explanation",
              [["r1", "log_return", "symbol=BTCUSDT", "symbol=TONUSDT", 4, "source_leads",
                1, 0.7, "x", "c", "n", "m", datetime.now(UTC)]],
              column_names=["analysis_run_id","metric_name","source_segment","target_segment","lag",
                            "direction","is_real","confidence","explanation","caveats","change_note","llm_model","created_at"])
    job2 = ForecastJob(metric="close", source="t", dimensions=["symbol"], use_dependencies=True)
    specs2 = resolve_covariate_specs(ch, job2, target_segment="symbol=TONUSDT")
    assert any(s.segment == "symbol=BTCUSDT" and s.lag == 4 for s in specs2)
