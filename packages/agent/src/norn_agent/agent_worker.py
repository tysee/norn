"""
packages/agent/src/norn_agent/agent_worker.py

HTTP-воркер LLM-судьи зависимостей — зеркало паттерна timesfm_worker: тонкая
FastAPI-граница вокруг judge_dependencies, чтобы судью можно было включать и
выключать как отдельный контейнер. Внутри воркера judge зовётся с ЯВНЫМ агентом
(никогда не через worker_url — иначе рекурсия). LLMUnavailable -> 503: клиентская
сторона маппит любой не-200 обратно в LLMUnavailable (explained=false).

Методы:
- JudgeRequest — pydantic-схема тела (measurements/meta/prior_measurements).
- create_app(agent=None) -> FastAPI — POST /judge и GET /health; agent
  инъектируется в тестах, по умолчанию строится из config/agent.yml.
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
    # один агент на процесс: модель-объект строится из config/agent.yml лениво,
    # но до первого запроса — fail-fast на битом конфиге при старте.
    judge_agent = agent if agent is not None else build_agent()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

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
