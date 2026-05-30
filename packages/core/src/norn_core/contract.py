"""
packages/core/src/norn_core/contract.py

Контракт слоя прогнозов: модели forecast-job (из YAML) и forecast-point
(строка таблицы прогноза). Общий для forecast-воркера и integration-слоя.

Классы/методы:
- Grain — зерно ряда (hourly | daily).
- ForecastJob — конфиг прогноза; ForecastJob.from_yaml(path) — загрузка из YAML.
- ForecastPoint — одна точка прогноза с интервалами p10/p50/p90.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Grain(str, Enum):
    hourly = "hourly"
    daily = "daily"


class ForecastJob(BaseModel):
    metric: str
    source: str  # ClickHouse table, e.g. "analytics.mart_metric"
    grain: Grain = Grain.daily
    dimensions: list[str] = Field(default_factory=list)
    horizon: int | None = None
    context_length: int | None = None
    seasonality: int | None = None
    model: str = "baseline-seasonal-naive"
    schedule: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ForecastJob":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)

    def resolved(self) -> "ForecastJob":
        """Fill unset tunables from the config layer (explicit job values win)."""
        from norn_core.config import get_settings

        d = get_settings(refresh=True).forecast.defaults
        return self.model_copy(update={
            "horizon": self.horizon if self.horizon is not None else d.horizon,
            "context_length": self.context_length if self.context_length is not None else d.context_length,
            "seasonality": self.seasonality if self.seasonality is not None else d.seasonality,
        })


class ForecastPoint(BaseModel):
    forecast_run_id: str
    metric_name: str
    segment_key: str
    forecast_ts: datetime
    horizon_step: int
    y_hat: float
    p10: float
    p50: float
    p90: float
    y_actual: float | None = None
    model_name: str
    created_at: datetime
