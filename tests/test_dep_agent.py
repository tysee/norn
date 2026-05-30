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
    monkeypatch.setattr(am, "_build_model", lambda a: a.model)
    am.build_agent()
    assert captured["model"] == "test-model-x"


import os  # noqa: E402

import pytest  # noqa: E402

from norn_agent.agent import _build_model  # noqa: E402
from norn_core.config import AgentSettings  # noqa: E402


def test_build_model_ollama_no_key():
    m = _build_model(AgentSettings(provider="ollama", model="gemma3n:e2b", base_url=None))
    # OllamaModel wraps the model name; no API key required
    assert "gemma3n:e2b" in repr(m) or getattr(m, "model_name", "") == "gemma3n:e2b"


def test_build_model_openrouter_uses_env_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-or")
    m = _build_model(AgentSettings(provider="openrouter", model="anthropic/claude-sonnet-4-5"))
    assert "claude-sonnet-4-5" in repr(m) or getattr(m, "model_name", "") == "anthropic/claude-sonnet-4-5"


def test_build_model_anthropic_uses_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-an")
    m = _build_model(AgentSettings(provider="anthropic-api", model="claude-sonnet-4-5"))
    assert m is not None


def test_build_model_openai_oauth_token(monkeypatch):
    monkeypatch.setenv("NORN_OPENAI_OAUTH_TOKEN", "tok")
    m = _build_model(AgentSettings(provider="openai-oauth", model="gpt-4o-mini"))
    assert m is not None


def test_build_model_unknown_provider():
    with pytest.raises(ValueError):
        _build_model(AgentSettings(provider="bogus", model="x"))
