"""
packages/forecast/src/norn_forecast/timesfm_worker.py

FastAPI forecast worker for the norn platform — the HTTP boundary around the
model. Isolates the heavy torch/TimesFM in a separate process (container):
TimesFMForecaster reaches it over the network. The model is declared via a
Protocol and injected, so the HTTP contract can be tested against a fake model
without torch, while the real TimesFM is wired in only inside the worker container.

Methods:
- TimesFMModel — Protocol: predict(values, horizon, quantiles,
  dynamic_numerical_covariates=None) -> list[dict].
- ForecastRequest — pydantic schema of the request body (values/horizon/quantiles/
  dynamic_numerical_covariates).
- create_app(model) -> FastAPI — application with POST /forecast and GET /health,
  closing over the provided model.
"""
from __future__ import annotations

from typing import Protocol

from fastapi import FastAPI
from pydantic import BaseModel, field_validator


class TimesFMModel(Protocol):
    def predict(
        self,
        values: list[float],
        horizon: int,
        quantiles: list[float],
        dynamic_numerical_covariates: dict[str, list[float]] | None = None,
    ) -> list[dict]: ...


class ForecastRequest(BaseModel):
    values: list[float]
    horizon: int
    quantiles: list[float] = [0.1, 0.5, 0.9]
    dynamic_numerical_covariates: dict[str, list[float]] = {}

    @field_validator("quantiles")
    @classmethod
    def _three_quantiles(cls, v: list[float]) -> list[float]:
        # the model maps these positionally to p10/p50/p90 — anything else
        # would otherwise surface as a bare IndexError-driven 500
        if len(v) != 3:
            raise ValueError(f"quantiles must have exactly 3 values (low, mid, high), got {len(v)}")
        return v


def create_app(model: TimesFMModel) -> FastAPI:
    app = FastAPI(title="norn-timesfm-worker")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/forecast")
    def forecast(req: ForecastRequest) -> dict:
        rows = model.predict(
            req.values,
            req.horizon,
            req.quantiles,
            req.dynamic_numerical_covariates or None,
        )
        return {"rows": rows}

    return app
