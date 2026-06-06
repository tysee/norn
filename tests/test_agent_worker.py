from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from norn_agent.agent import SYSTEM_PROMPT
from norn_agent.agent_worker import create_app
from norn_agent.contract import DependencyDecision

BODY = {
    "measurements": [{
        "method": "granger", "lag": 3, "score": 0.9,
        "direction": "source_leads", "p_value": 0.01, "confidence": 0.9,
    }],
    "meta": {"source_segment": "a", "target_segment": "b", "metric_name": "m"},
    "prior_measurements": [],
}


def _test_agent():
    return Agent(TestModel(), output_type=DependencyDecision, system_prompt=SYSTEM_PROMPT)


def test_health():
    assert TestClient(create_app(agent=_test_agent())).get("/health").json() == {"status": "ok"}


def test_judge_returns_decision():
    resp = TestClient(create_app(agent=_test_agent())).post("/judge", json=BODY)
    assert resp.status_code == 200
    DependencyDecision.model_validate(resp.json())  # contract-valid


def test_judge_llm_down_is_503():
    from norn_agent.agent import LLMUnavailable

    class _Boom:
        def run_sync(self, _):
            raise LLMUnavailable("ollama down")

    resp = TestClient(create_app(agent=_Boom())).post("/judge", json=BODY)
    assert resp.status_code == 503
    assert "ollama down" in resp.json()["detail"]
