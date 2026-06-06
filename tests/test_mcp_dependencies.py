import asyncio
from datetime import datetime

from norn_forecast import mcp_tools
from norn_forecast.mcp_server import TOOL_NAMES, build_server


def _seed(ch, run_id="dep-1"):
    ch.insert(
        "metric_dependency",
        [[run_id, "log_return", "symbol=BTCUSDT", "symbol=TONUSDT",
          "lagged_cross_correlation", 3, 0.8, "source_leads", None, 0.8,
          datetime(2025, 1, 1), datetime(2025, 6, 1), datetime(2025, 6, 2)]],
        column_names=[
            "analysis_run_id", "metric_name", "source_segment", "target_segment",
            "method", "lag", "score", "direction", "p_value", "confidence",
            "window_start", "window_end", "created_at",
        ],
    )
    ch.insert(
        "dependency_explanation",
        [[run_id, "log_return", "symbol=BTCUSDT", "symbol=TONUSDT", 3, "source_leads",
          1, 0.7, "BTC leads TON by 3d", "correlation != causation",
          "corr stable vs prior", "test", datetime(2025, 6, 2)]],
        column_names=[
            "analysis_run_id", "metric_name", "source_segment", "target_segment",
            "lag", "direction", "is_real", "confidence", "explanation", "caveats",
            "change_note", "llm_model", "created_at",
        ],
    )


def test_get_dependencies_merges_evidence_and_decision(ch):
    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    _seed(ch)
    out = mcp_tools.get_dependencies(ch, "symbol=TONUSDT", metric="log_return")
    assert len(out) == 1
    rel = out[0]
    assert rel["source_segment"] == "symbol=BTCUSDT"
    assert rel["lag"] == 3 and rel["is_real"] is True
    assert any(m["method"] == "lagged_cross_correlation" for m in rel["methods"])
    assert rel["caveats"]
    assert rel["change_note"] == "corr stable vs prior"
    assert rel["explained"] is True


def test_get_dependencies_fallback_when_unexplained(ch):
    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    # seed metric_dependency WITHOUT a dependency_explanation row (LLM degraded path)
    ch.insert(
        "metric_dependency",
        [["dep-x", "log_return", "symbol=BTCUSDT", "symbol=TONUSDT",
          "granger", 2, 8.4, "source_leads", 0.004, 0.9,
          datetime(2025, 1, 1), datetime(2025, 6, 1), datetime(2025, 6, 2)]],
        column_names=[
            "analysis_run_id", "metric_name", "source_segment", "target_segment",
            "method", "lag", "score", "direction", "p_value", "confidence",
            "window_start", "window_end", "created_at",
        ],
    )
    out = mcp_tools.get_dependencies(ch, "symbol=TONUSDT", metric="log_return")
    assert len(out) == 1
    rel = out[0]
    assert rel["explained"] is False
    assert rel["is_real"] is None and rel["explanation"] == ""
    assert any(m["method"] == "granger" for m in rel["methods"])


def test_get_dependency_history_is_chronological(ch):
    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    _seed(ch, run_id="dep-old")
    _seed(ch, run_id="dep-new")
    hist = mcp_tools.get_dependency_history(ch, "symbol=TONUSDT", "symbol=BTCUSDT", "log_return")
    assert len(hist) == 2
    assert {h["analysis_run_id"] for h in hist} == {"dep-old", "dep-new"}
    assert all("methods" in h and "change_note" in h for h in hist)


def test_server_registers_dependency_tools():
    server = build_server(client=object())
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"get_dependencies", "get_dependency_history"} <= names
    assert {"get_dependencies", "get_dependency_history"} <= set(TOOL_NAMES)
