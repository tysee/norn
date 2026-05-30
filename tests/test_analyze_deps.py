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
