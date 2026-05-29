"""
packages/forecast/src/norn_forecast/timesfm_worker.py

FastAPI-воркер прогнозов. Модель инъектируется, поэтому контракт тестируется
без torch; реальная модель TimesFM подставляется в контейнере.

Методы:
- TimesFMModel — Protocol с predict(values, horizon, quantiles) -> list[dict].
- ForecastRequest — pydantic-схема запроса.
- create_app(model) -> FastAPI — приложение с POST /forecast и GET /health.
"""
from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI
from pydantic import BaseModel


class TimesFMModel(Protocol):
    def predict(
        self, values: list[float], horizon: int, quantiles: list[float]
    ) -> list[dict]: ...


class ForecastRequest(BaseModel):
    values: list[float]
    horizon: int
    quantiles: list[float] = [0.1, 0.5, 0.9]


def create_app(model: TimesFMModel) -> FastAPI:
    app = FastAPI(title="norn-timesfm-worker")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/forecast")
    def forecast(req: ForecastRequest) -> dict:
        rows = model.predict(req.values, req.horizon, req.quantiles)
        return {"rows": rows}

    return app
