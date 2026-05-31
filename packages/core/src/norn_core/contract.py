"""
packages/core/src/norn_core/contract.py

Контракт слоя прогнозов платформы norn: типизированные модели описания
прогноз-задания (forecast-job, задаётся в YAML) и единичной точки прогноза
(forecast-point, строка таблицы результатов). Это общий язык между forecast-воркером,
который читает задания и пишет точки, и integration-слоем, который их потребляет;
модели гарантируют единый формат данных и валидацию на границах сервисов.

Классы/методы:
- Grain — зерно временного ряда (hourly | daily), задаёт частоту точек.
- CovariateSpec — спецификация ряда-лидера (metric/segment/lag/mart) для XReg-ковариат.
- ForecastJob — описание прогноз-задания (метрика, источник, разрезы, гиперпараметры, расписание).
  * ForecastJob.from_yaml(path) — загрузка и валидация задания из YAML-файла.
  * ForecastJob.resolved() — копия задания с дозаполненными из config-слоя tunables (явные значения важнее).
- ForecastPoint — одна точка прогноза: предсказание y_hat и интервалы p10/p50/p90,
  опциональный факт y_actual, идентификаторы прогона/метрики/сегмента и метки времени.
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


class CovariateSpec(BaseModel):
    metric: str
    segment: str
    lag: int
    mart: str = "mart_metric"   # long store to read the leader from (ts, metric_name, value, segment_key)


class ForecastJob(BaseModel):
    metric: str
    source: str  # ClickHouse table, e.g. "analytics.mart_metric"
    grain: Grain = Grain.daily
    dimensions: list[str] = Field(default_factory=list)
    filter: dict[str, str] = Field(default_factory=dict)  # column=value equality; scopes the source
    covariates: list[CovariateSpec] = Field(default_factory=list)
    use_dependencies: bool = False
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

        # --- источник дефолтов: секция forecast.defaults из config-слоя ---
        d = get_settings().forecast.defaults
        # --- merge: значение из задания приоритетнее дефолта (None => берём дефолт) ---
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
