from datetime import datetime

import pytest

from norn_forecast import mcp_tools


def _seed_run(ch, run_id="run-A", status="success", started=None):
    ch.insert(
        "forecast_run",
        [[run_id, "job.yml", status, "timesfm-2.5", "2.5", started or datetime(2026, 5, 30),
          datetime(2026, 5, 30), 1, 0, None]],
        column_names=[
            "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
            "started_at", "finished_at", "segments_total", "segments_skipped", "error",
        ],
    )


def _seed_points(ch, run_id, metric="close", segment="symbol=BTC", with_run=True,
                 created=None):
    # readers only serve run_ids that exist in forecast_run with status='success',
    # so a seeded forecast needs its run row too (with_run=False seeds an orphan)
    if with_run:
        _seed_run(ch, run_id)
    rows = [
        [run_id, metric, segment, datetime(2026, 6, 1 + h), h + 1,
         100.0 + h, 90.0 + h, 100.0 + h, 110.0 + h, None, "timesfm-2.5",
         created or datetime(2026, 5, 30)]
        for h in range(3)
    ]
    ch.insert(
        "forecast_point", rows,
        column_names=[
            "forecast_run_id", "metric_name", "segment_key", "forecast_ts", "horizon_step",
            "y_hat", "p10", "p50", "p90", "y_actual", "model_name", "created_at",
        ],
    )


def test_get_forecast_returns_latest_run_points(ch):
    _seed_points(ch, "run-A")
    out = mcp_tools.get_forecast(ch, "close", "symbol=BTC")
    assert len(out) == 3
    assert out[0]["horizon_step"] == 1
    assert out[0]["y_hat"] == 100.0 and out[0]["p90"] == 110.0
    assert out[0]["p10"] <= out[0]["p50"] <= out[0]["p90"]


def test_get_forecast_horizon_limit(ch):
    _seed_points(ch, "run-A")
    out = mcp_tools.get_forecast(ch, "close", "symbol=BTC", horizon=2)
    assert [p["horizon_step"] for p in out] == [1, 2]


def test_get_forecast_unknown_segment_empty(ch):
    out = mcp_tools.get_forecast(ch, "close", "symbol=NOPE")
    assert out == []


def test_get_expected_range_widths(ch):
    _seed_points(ch, "run-A")
    out = mcp_tools.get_expected_range(ch, "close", "symbol=BTC")
    assert len(out) == 3
    assert out[0]["low"] == 90.0 and out[0]["high"] == 110.0
    assert out[0]["width"] == pytest.approx(20.0)


def test_classify_levels_vs_band_classifies_against_band(ch):
    _seed_points(ch, "run-A")  # band envelope: p10 min=90, p90 max=112
    out = mcp_tools.classify_levels_vs_band(ch, "close", "symbol=BTC", [80.0, 100.0, 130.0])
    verdicts = {r["level"]: r["verdict"] for r in out}
    assert verdicts[80.0] == "below_band"
    assert verdicts[100.0] == "in_band"
    assert verdicts[130.0] == "above_band"


def test_classify_levels_vs_band_no_forecast(ch):
    out = mcp_tools.classify_levels_vs_band(ch, "close", "symbol=NOPE", [100.0])
    assert out == [{"level": 100.0, "verdict": "no_forecast"}]


def test_get_band_position_positions(ch):
    _seed_points(ch, "run-A")  # nearest horizon (step 1): p10=90, p90=110
    assert mcp_tools.get_band_position(ch, "close", "symbol=BTC", 85.0)["position"] == "below_p10"
    assert mcp_tools.get_band_position(ch, "close", "symbol=BTC", 100.0)["position"] == "in_band"
    assert mcp_tools.get_band_position(ch, "close", "symbol=BTC", 120.0)["position"] == "above_p90"
    assert mcp_tools.get_band_position(ch, "close", "symbol=BTC", 100.0)["in_band"] is True


def test_get_band_position_no_forecast(ch):
    d = mcp_tools.get_band_position(ch, "close", "symbol=NOPE", 100.0)
    assert d["position"] == "no_forecast" and d["in_band"] is None


def _seed_segment(ch, run_id, metric="close", segment="symbol=BTC", is_sparse=0):
    ch.insert(
        "forecast_segment",
        [[run_id, metric, segment, 21, is_sparse, 0.05, 0.04, 0.83, -0.1, datetime(2026, 5, 30)]],
        column_names=[
            "forecast_run_id", "metric_name", "segment_key", "n_points", "is_sparse",
            "wape", "mape", "coverage", "bias", "created_at",
        ],
    )


def test_get_calibration_returns_latest(ch):
    _seed_segment(ch, "cal-A")
    out = mcp_tools.get_calibration(ch, "close", "symbol=BTC")
    assert out["available"] is True
    assert out["coverage"] == pytest.approx(0.83)
    assert out["n_points"] == 21


def test_get_calibration_missing(ch):
    out = mcp_tools.get_calibration(ch, "close", "symbol=NOPE")
    assert out == {"available": False}


def test_get_run_status_latest(ch):
    _seed_run(ch, "run-A")
    out = mcp_tools.get_run_status(ch)
    assert out["available"] is True
    assert out["forecast_run_id"] == "run-A" and out["status"] == "success"
    assert out["model_name"] == "timesfm-2.5"


def test_get_forecast_status_for_series(ch):
    _seed_points(ch, "run-A")
    out = mcp_tools.get_forecast_status(ch, "close", "symbol=BTC")
    assert out["available"] is True
    assert out["forecast_run_id"] == "run-A" and out["status"] == "success"
    assert out["last_created_at"] is not None and out["last_forecast_ts"] is not None


def test_get_forecast_status_unknown_segment(ch):
    assert mcp_tools.get_forecast_status(ch, "close", "symbol=NOPE") == {"available": False}


def test_list_metrics_and_segments(ch):
    _seed_points(ch, "run-A", metric="close", segment="symbol=BTC")
    _seed_points(ch, "run-A", metric="close", segment="symbol=ETH")
    _seed_points(ch, "run-A", metric="volume", segment="symbol=BTC")
    metrics = mcp_tools.list_metrics(ch)
    assert "close" in metrics and "volume" in metrics
    segs = mcp_tools.list_segments(ch, "close")
    assert "symbol=BTC" in segs and "symbol=ETH" in segs
    assert "symbol=BTC" in segs and len(mcp_tools.list_segments(ch, "volume")) == 1


def test_get_calibration_includes_is_sparse(ch):
    _seed_segment(ch, "cal-sparse", is_sparse=1)
    out = mcp_tools.get_calibration(ch, "close", "symbol=BTC")
    assert out["available"] is True
    assert out["is_sparse"] is True


def test_latest_run_excludes_orphaned_and_backtest_points(ch):
    # run-OLD: complete (points + success run row). run-ORPHAN: newer points with
    # NO forecast_run row — a run that died between its point and run inserts, or
    # calibration backtest points (calibrate never writes forecast_run). Readers
    # must keep serving run-OLD, not the orphaned epoch.
    _seed_points(ch, "run-OLD", created=datetime(2026, 5, 30))
    _seed_points(ch, "run-ORPHAN", with_run=False, created=datetime(2026, 6, 2))
    out = mcp_tools.get_forecast(ch, "close", "symbol=BTC")
    assert len(out) == 3
    assert mcp_tools.get_forecast_status(ch, "close", "symbol=BTC")["forecast_run_id"] == "run-OLD"


def test_latest_run_excludes_failed_runs(ch):
    _seed_points(ch, "run-GOOD", created=datetime(2026, 5, 30))
    # newer run exists but failed -> its (partial) points must not be served
    _seed_run(ch, "run-BAD", status="failed")
    _seed_points(ch, "run-BAD", with_run=False, created=datetime(2026, 6, 2))
    assert mcp_tools.get_forecast_status(ch, "close", "symbol=BTC")["forecast_run_id"] == "run-GOOD"
