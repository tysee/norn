from datetime import datetime, timedelta

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from norn_agent.agent import SYSTEM_PROMPT
from norn_agent.analyze import analyze_dependencies
from norn_agent.contract import DependencyDecision, DependencyJob


def _seed_mart(ch):
    ch.command(
        "CREATE TABLE mart_metric (ts DateTime, metric_name String, value Float64, "
        "segment_key String) ENGINE = MergeTree ORDER BY (metric_name, segment_key, ts)"
    )
    import numpy as np

    # Shared noisy base so BTC[i] == TON[i+3] holds exactly (xcorr lag 3) while the
    # series is NOT perfectly collinear (Granger stays non-singular).
    rng = np.random.default_rng(0)
    base = np.sin(np.linspace(0, 20, 230)) + 0.05 * rng.standard_normal(230)
    start = datetime(2025, 1, 1)
    rows = []
    for i in range(200):
        ts = start + timedelta(days=i)
        # BTC leads TON by 3 days: ton[i] = btc[i-3]
        rows.append([ts, "log_return", float(base[i + 10]), "symbol=BTCUSDT"])
        rows.append([ts, "log_return", float(base[i + 7]), "symbol=TONUSDT"])
    ch.insert("mart_metric", rows,
              column_names=["ts", "metric_name", "value", "segment_key"])


def test_analyze_writes_evidence_and_explanation(ch):
    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    ch.command("DROP TABLE IF EXISTS mart_metric")
    _seed_mart(ch)
    job = DependencyJob(source_segment="symbol=BTCUSDT", target_segment="symbol=TONUSDT",
                        metric="log_return", max_lag=10)
    test_agent = Agent(TestModel(), output_type=DependencyDecision, system_prompt=SYSTEM_PROMPT)
    run_id = analyze_dependencies(job, client=ch, agent=test_agent).run_id

    dep = ch.query(
        "SELECT method, lag, direction FROM metric_dependency "
        "WHERE analysis_run_id=%(r)s ORDER BY method",
        parameters={"r": run_id},
    ).result_rows
    methods = {row[0] for row in dep}
    assert methods == {"lagged_cross_correlation", "granger"}
    xcorr = [row for row in dep if row[0] == "lagged_cross_correlation"][0]
    assert xcorr[1] == 3 and xcorr[2] == "source_leads"  # detected BTC leads TON by 3

    exp = ch.query(
        "SELECT count() FROM dependency_explanation WHERE analysis_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows
    assert exp[0][0] >= 1


def test_history_accumulates_and_prior_is_found(ch):
    from norn_agent.analyze import _prior_measurements

    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    ch.command("DROP TABLE IF EXISTS mart_metric")
    _seed_mart(ch)
    job = DependencyJob(source_segment="symbol=BTCUSDT", target_segment="symbol=TONUSDT", metric="log_return", max_lag=10)
    test_agent = Agent(TestModel(), output_type=DependencyDecision, system_prompt=SYSTEM_PROMPT)

    run1 = analyze_dependencies(job, client=ch, agent=test_agent).run_id
    prior = _prior_measurements(ch, job)            # should reflect run1
    assert {m.method for m in prior} == {"lagged_cross_correlation", "granger"}
    run2 = analyze_dependencies(job, client=ch, agent=test_agent).run_id

    runs = ch.query(
        "SELECT count(DISTINCT analysis_run_id) FROM metric_dependency "
        "WHERE source_segment='symbol=BTCUSDT' AND target_segment='symbol=TONUSDT'"
    ).result_rows
    assert runs[0][0] == 2 and run1 != run2  # append-only history of both runs


def test_analyze_degrades_explicitly_when_llm_unavailable(ch):
    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    ch.command("DROP TABLE IF EXISTS mart_metric")
    _seed_mart(ch)
    from norn_agent.analyze import analyze_dependencies, AnalysisResult
    from norn_agent.agent import LLMUnavailable
    job = DependencyJob(source_segment="symbol=BTCUSDT", target_segment="symbol=TONUSDT",
                        metric="log_return", max_lag=10)
    class _Boom:
        def run_sync(self, _):
            raise LLMUnavailable("ConnectionError: ollama down")
    res = analyze_dependencies(job, client=ch, agent=_Boom())
    assert isinstance(res, AnalysisResult)
    assert res.explained is False
    assert res.degradation_reason
    # statistical evidence still written for this run:
    n = ch.query("SELECT count() FROM metric_dependency WHERE analysis_run_id=%(r)s",
                 parameters={"r": res.run_id}).result_rows[0][0]
    assert n > 0
    # no explanation rows when degraded:
    m = ch.query("SELECT count() FROM dependency_explanation WHERE analysis_run_id=%(r)s",
                 parameters={"r": res.run_id}).result_rows[0][0]
    assert m == 0


def test_analyze_logs_stage_progress(ch, caplog):
    # Observability regression: a deps run spends minutes inside a silent LLM call
    # (local Ollama) — without stage logs the whole run looks hung. analyze must
    # log extract/judge progress at INFO on its own logger.
    import logging

    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    ch.command("DROP TABLE IF EXISTS mart_metric")
    _seed_mart(ch)
    job = DependencyJob(source_segment="symbol=BTCUSDT", target_segment="symbol=TONUSDT",
                        metric="log_return", max_lag=10)
    test_agent = Agent(TestModel(), output_type=DependencyDecision, system_prompt=SYSTEM_PROMPT)
    with caplog.at_level(logging.INFO, logger="norn_agent.analyze"):
        analyze_dependencies(job, client=ch, agent=test_agent)
    msgs = [r.message for r in caplog.records]
    assert any("extract" in m for m in msgs), msgs   # aligned-points stage
    assert any("judge" in m for m in msgs), msgs     # LLM stage (the slow one)


def test_explanation_uses_canonical_segment_keys(ch):
    # Regression for the E2E contract bug: dependency_explanation must use the job's
    # canonical 'symbol=...' segment keys (same as metric_dependency), NOT whatever the
    # LLM echoed back (it tends to strip the 'symbol=' prefix). Otherwise the MCP
    # get_dependencies LEFT-join misses and explained=false despite a real verdict.
    from norn_agent.analyze import AnalysisResult
    from norn_agent.contract import DependencyDecision, DependencyRelation
    from norn_forecast import mcp_tools

    ch.command("TRUNCATE TABLE IF EXISTS metric_dependency")
    ch.command("TRUNCATE TABLE IF EXISTS dependency_explanation")
    ch.command("DROP TABLE IF EXISTS mart_metric")
    _seed_mart(ch)

    class _Ret:
        def __init__(self, output):
            self.output = output

    class _StripAgent:  # mimics an LLM that drops the 'symbol=' prefix on the keys
        def run_sync(self, _prompt):
            return _Ret(DependencyDecision(relations=[DependencyRelation(
                source_segment="BTCUSDT", target_segment="TONUSDT", metric_name="log_return",
                lag=3, direction="source_leads", is_real=True, confidence=0.8,
                explanation="BTC leads TON", caveats="correlation != causation", change_note="")]))

    job = DependencyJob(source_segment="symbol=BTCUSDT", target_segment="symbol=TONUSDT",
                        metric="log_return", max_lag=10)
    res = analyze_dependencies(job, client=ch, agent=_StripAgent())
    assert isinstance(res, AnalysisResult) and res.explained is True

    keys = ch.query(
        "SELECT DISTINCT source_segment, target_segment FROM dependency_explanation "
        "WHERE analysis_run_id=%(r)s", parameters={"r": res.run_id},
    ).result_rows
    assert keys == [("symbol=BTCUSDT", "symbol=TONUSDT")]

    # end-to-end: MCP now returns the verdict joined with the numeric methods
    deps = mcp_tools.get_dependencies(ch, "symbol=TONUSDT", "log_return")
    assert len(deps) == 1
    assert deps[0]["explained"] is True and deps[0]["is_real"] is True
    assert any(m["method"] == "granger" for m in deps[0]["methods"])
