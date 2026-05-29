from datetime import datetime

import pytest

from norn_forecast import mcp_tools


def _seed_points(ch, run_id, metric="close", segment="symbol=BTC"):
    rows = [
        [run_id, metric, segment, datetime(2026, 6, 1 + h), h + 1,
         100.0 + h, 90.0 + h, 100.0 + h, 110.0 + h, None, "timesfm-2.5", datetime(2026, 5, 30)]
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


def test_check_ladder_rungs_classifies_against_band(ch):
    _seed_points(ch, "run-A")  # band envelope: p10 min=90, p90 max=112
    out = mcp_tools.check_ladder_rungs(ch, "close", "symbol=BTC", [80.0, 100.0, 130.0])
    verdicts = {r["rung"]: r["verdict"] for r in out}
    assert verdicts[80.0] == "below_band"
    assert verdicts[100.0] == "in_band"
    assert verdicts[130.0] == "above_band"


def test_check_ladder_rungs_no_forecast(ch):
    out = mcp_tools.check_ladder_rungs(ch, "close", "symbol=NOPE", [100.0])
    assert out == [{"rung": 100.0, "verdict": "no_forecast"}]


def test_get_divergence_positions(ch):
    _seed_points(ch, "run-A")  # nearest horizon (step 1): p10=90, p90=110
    assert mcp_tools.get_divergence(ch, "close", "symbol=BTC", 85.0)["position"] == "below_p10"
    assert mcp_tools.get_divergence(ch, "close", "symbol=BTC", 100.0)["position"] == "in_band"
    assert mcp_tools.get_divergence(ch, "close", "symbol=BTC", 120.0)["position"] == "above_p90"
    assert mcp_tools.get_divergence(ch, "close", "symbol=BTC", 100.0)["in_band"] is True


def test_get_divergence_no_forecast(ch):
    d = mcp_tools.get_divergence(ch, "close", "symbol=NOPE", 100.0)
    assert d["position"] == "no_forecast" and d["in_band"] is None
