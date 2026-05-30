from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from norn_agent.agent import SYSTEM_PROMPT, judge_dependencies
from norn_agent.contract import DependencyDecision, DependencyMeasurement


def test_judge_returns_structured_decision_with_testmodel():
    measurements = [
        DependencyMeasurement(method="lagged_cross_correlation", lag=2, score=0.7,
                              direction="source_leads", p_value=None, confidence=0.7),
        DependencyMeasurement(method="granger", lag=2, score=2.0,
                              direction="source_leads", p_value=0.01, confidence=0.99),
    ]
    meta = {"source_segment": "symbol=BTCUSDT", "target_segment": "symbol=TONUSDT",
            "metric_name": "log_return"}
    test_agent = Agent(TestModel(), output_type=DependencyDecision, system_prompt=SYSTEM_PROMPT)
    decision = judge_dependencies(measurements, meta, agent=test_agent)
    assert isinstance(decision, DependencyDecision)
    assert len(decision.relations) >= 1


def test_judge_degrades_on_model_error():
    class _BoomAgent:
        def run_sync(self, prompt):
            raise RuntimeError("model/transport failure")

    measurements = [
        DependencyMeasurement(method="lagged_cross_correlation", lag=2, score=0.7,
                              direction="source_leads", p_value=None, confidence=0.7),
    ]
    meta = {"source_segment": "segment=A", "target_segment": "segment=B",
            "metric_name": "log_return"}
    decision = judge_dependencies(measurements, meta, agent=_BoomAgent())
    assert isinstance(decision, DependencyDecision)
    assert decision.relations == []


def test_build_agent_model_from_settings(monkeypatch):
    import norn_agent.agent as am

    monkeypatch.setenv("NORN_CONFIG_DIR", "config")
    monkeypatch.setenv("NORN_AGENT_MODEL", "test-model-x")
    captured = {}
    monkeypatch.setattr(am, "Agent", lambda model, **kw: captured.update({"model": model}) or "AGENT")
    am.build_agent()
    assert captured["model"] == "test-model-x"
