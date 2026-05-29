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
    horizon: int = 30
    context_length: int = 512
    seasonality: int = 7
    model: str = "baseline-seasonal-naive"
    schedule: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ForecastJob":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)


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
