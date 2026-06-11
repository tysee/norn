"""
packages/core/src/norn_core/contract.py

Contract of the norn platform's forecast layer: typed models describing a
forecast-job (defined in YAML) and a single forecast point
(forecast-point, a row of the results table). This is the shared language between the
forecast-worker, which reads jobs and writes points, and the integration layer that consumes them;
the models guarantee a single data format and validation at service boundaries.

Classes/methods:
- Grain — time-series grain (hourly | daily), sets the point frequency.
- CovariateSpec — leader-series spec (metric/segment/lag/mart) for XReg covariates.
- ForecastJob — forecast-job description (metric, source, dimensions, hyperparameters, schedule).
  * ForecastJob.from_yaml(path) — load and validate a job from a YAML-file.
  * ForecastJob.resolved() — copy of the job with tunables filled in from the config layer (explicit values win).
- ForecastPoint — a single forecast point: the y_hat prediction and the p10/p50/p90 intervals,
  an optional y_actual fact, run/metric/segment identifiers and timestamps.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import AwareDatetime, BaseModel, Field


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
    horizon: int | None = Field(default=None, ge=1)
    context_length: int | None = Field(default=None, ge=1)
    seasonality: int | None = Field(default=None, ge=1)  # 0 would divide by zero in the baseline
    model: str = "baseline-seasonal-naive"
    transform: str = "none"  # "none" | "log": forecast in log-space (positive multiplicative series)
    schedule: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ForecastJob":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)

    def resolved(self) -> "ForecastJob":
        """Fill unset tunables from the config layer (explicit job values win)."""
        from norn_core.config import get_settings

        # --- defaults source: the forecast.defaults section from the config layer ---
        d = get_settings().forecast.defaults
        # --- merge: the job value takes priority over the default (None => use default) ---
        return self.model_copy(update={
            "horizon": self.horizon if self.horizon is not None else d.horizon,
            "context_length": self.context_length if self.context_length is not None else d.context_length,
            "seasonality": self.seasonality if self.seasonality is not None else d.seasonality,
        })


class ForecastPoint(BaseModel):
    forecast_run_id: str
    metric_name: str
    segment_key: str
    # tz-aware only: clickhouse-connect shifts naive datetimes by the machine's
    # UTC offset on insert, so a naive timestamp at this boundary is always a bug.
    forecast_ts: AwareDatetime
    horizon_step: int
    y_hat: float
    p10: float
    p50: float
    p90: float
    y_actual: float | None = None
    model_name: str
    created_at: AwareDatetime
