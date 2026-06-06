"""
packages/agent/src/norn_agent/agent_worker.py

HTTP worker for the LLM dependency judge — a mirror of the timesfm_worker pattern: a
thin FastAPI boundary around judge_dependencies so the judge can be switched on and
off as a separate container. Inside the worker, judge is called with an EXPLICIT agent
(never via worker_url — otherwise recursion). LLMUnavailable -> 503: the client side
maps any non-200 back into LLMUnavailable (explained=false).

Members:
- JudgeRequest — pydantic schema for the body (measurements/meta/prior_measurements).
- create_app(agent=None) -> FastAPI — POST /judge and GET /health; agent is
  injected in tests, by default built from config/agent.yml.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from norn_agent.agent import LLMUnavailable, build_agent, judge_dependencies
from norn_agent.contract import DependencyMeasurement


class JudgeRequest(BaseModel):
    measurements: list[DependencyMeasurement]
    meta: dict
    prior_measurements: list[DependencyMeasurement] = []


def create_app(agent=None) -> FastAPI:
    app = FastAPI(title="norn-agent-worker")
    # one agent per process: the model object is built from config/agent.yml lazily,
    # but before the first request — fail-fast on a broken config at startup.
    judge_agent = agent if agent is not None else build_agent()
    # The worker's own provider/model — used by the client to record correct
    # provenance (dependency_explanation.llm_model). Only known when we built
    # from config; an injected agent (tests) leaves it unset.
    if agent is None:
        from norn_core.config import get_settings

        _a = get_settings().agent
        _provider, _model = _a.provider, _a.model
    else:
        _provider = _model = None

    @app.get("/health")
    def health() -> dict:
        out = {"status": "ok"}
        if _model is not None:
            out["provider"], out["model"] = _provider, _model
        return out

    @app.post("/judge")
    def judge(req: JudgeRequest) -> dict:
        try:
            decision = judge_dependencies(
                req.measurements, req.meta,
                prior_measurements=req.prior_measurements or None,
                agent=judge_agent,
            )
        except LLMUnavailable as e:
            raise HTTPException(503, str(e))
        return decision.model_dump()

    return app


def build_app() -> FastAPI:
    """uvicorn factory: `uvicorn norn_agent.agent_worker:build_app --factory`."""
    return create_app()
