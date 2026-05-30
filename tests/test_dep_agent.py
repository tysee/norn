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


def test_judge_raises_llm_unavailable_on_model_error():
    import pytest
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    from norn_agent.agent import LLMUnavailable

    class _Boom:
        def run_sync(self, _):
            raise UnexpectedModelBehavior("boom")

    measurements = [
        DependencyMeasurement(method="lagged_cross_correlation", lag=2, score=0.7,
                              direction="source_leads", p_value=None, confidence=0.7),
    ]
    meta = {"source_segment": "segment=A", "target_segment": "segment=B",
            "metric_name": "log_return"}
    with pytest.raises(LLMUnavailable):
        judge_dependencies(measurements, meta, agent=_Boom())


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


def _agent_settings(**overrides):
    """Build a complete AgentSettings (no field defaults anymore — pass every key)."""
    base = dict(
        provider="ollama",
        model="gemma4:e2b",
        base_url="http://localhost:11434/v1",
        output_mode="native",
        max_lag=10,
        context_length=512,
        methods=["lagged_cross_correlation", "granger"],
        granger_min_points_factor=3,
        granger_significance=0.05,
    )
    base.update(overrides)
    return AgentSettings(**base)


def test_build_model_ollama_no_key():
    m = _build_model(_agent_settings(provider="ollama", model="gemma4:e2b"))
    # OllamaModel wraps the model name; no API key required
    assert "gemma4:e2b" in repr(m) or getattr(m, "model_name", "") == "gemma4:e2b"


def test_build_model_openrouter_uses_env_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-or")
    m = _build_model(_agent_settings(provider="openrouter", model="anthropic/claude-sonnet-4-5", base_url=None))
    assert "claude-sonnet-4-5" in repr(m) or getattr(m, "model_name", "") == "anthropic/claude-sonnet-4-5"


def test_build_model_anthropic_uses_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-an")
    m = _build_model(_agent_settings(provider="anthropic-api", model="claude-sonnet-4-5", base_url=None))
    assert m is not None


def test_build_model_openai_oauth_token(monkeypatch):
    monkeypatch.setenv("NORN_OPENAI_OAUTH_TOKEN", "tok")
    m = _build_model(_agent_settings(provider="openai-oauth", model="gpt-4o-mini", base_url=None))
    assert m is not None


def test_build_model_unknown_provider():
    with pytest.raises(ValueError):
        _build_model(_agent_settings(provider="bogus", model="x", base_url=None))


def test_judge_raises_llm_unavailable_when_build_fails(monkeypatch):
    # provider needs a key that is absent -> build_agent() raises during construction
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("NORN_AGENT_PROVIDER", "anthropic-api")
    monkeypatch.setenv("NORN_CONFIG_DIR", "config")
    monkeypatch.setenv("NORN_DB_PASSWORD", "norn")
    from norn_agent.agent import LLMUnavailable, judge_dependencies
    from norn_agent.contract import DependencyMeasurement

    measurements = [DependencyMeasurement(method="granger", lag=1, score=2.0,
                                          direction="source_leads", p_value=0.01, confidence=0.99)]
    meta = {"source_segment": "symbol=BTCUSDT", "target_segment": "symbol=TONUSDT", "metric_name": "log_return"}
    with pytest.raises(LLMUnavailable):
        judge_dependencies(measurements, meta)               # no agent injected -> build from (broken) config


def test_output_type_native_mode():
    # output_mode=native -> NativeOutput (config-driven, not provider-hardcoded).
    from pydantic_ai import NativeOutput

    from norn_agent.agent import _output_type

    ot = _output_type(_agent_settings(provider="ollama", model="gemma4:e2b", output_mode="native"))
    assert isinstance(ot, NativeOutput)


def test_output_type_tool_mode():
    # output_mode=tool -> the bare model class (default tool-calling).
    from norn_agent.agent import _output_type
    from norn_agent.contract import DependencyDecision

    assert _output_type(_agent_settings(provider="anthropic-api", model="claude-sonnet-4-5",
                                        base_url=None, output_mode="tool")) is DependencyDecision


def test_build_model_ollama_requires_base_url():
    import pytest
    from norn_agent.agent import _build_model
    with pytest.raises(ValueError):
        _build_model(_agent_settings(provider="ollama", model="gemma4:e2b", base_url=None))


def test_output_type_from_mode():
    from pydantic_ai import NativeOutput, PromptedOutput
    from norn_agent.agent import _output_type
    from norn_agent.contract import DependencyDecision

    def cfg(mode):
        return _agent_settings(provider="ollama", model="m", base_url="http://x", output_mode=mode)

    assert isinstance(_output_type(cfg("native")), NativeOutput)
    assert isinstance(_output_type(cfg("prompted")), PromptedOutput)
    assert _output_type(cfg("tool")) is DependencyDecision
    import pytest
    with pytest.raises(ValueError):
        _output_type(cfg("bogus"))
