"""
packages/forecast/src/norn_forecast/forecaster.py

Интерфейс форкастера и адаптеры. Делает baseline и TimesFM взаимозаменяемыми
для runner и калибровки.

Классы/методы:
- Forecaster — Protocol с forecast(values, horizon) -> list[dict].
- BaselineForecaster — обёртка над seasonal_naive_forecast.
- TimesFMForecaster — HTTP-клиент к torch-воркеру (без torch в этом процессе).
- make_forecaster(job, timesfm_url=None) -> Forecaster — выбор по job.model.
"""
from __future__ import annotations

from typing import Protocol

import httpx

from norn_core.contract import ForecastJob
from norn_forecast.baseline import seasonal_naive_forecast


class Forecaster(Protocol):
    def forecast(self, values: list[float], horizon: int) -> list[dict]: ...


class BaselineForecaster:
    def __init__(self, seasonality: int = 7) -> None:
        self.seasonality = seasonality

    def forecast(self, values: list[float], horizon: int) -> list[dict]:
        return seasonal_naive_forecast(values, horizon, self.seasonality)


class TimesFMForecaster:
    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        quantiles: tuple[float, ...] = (0.1, 0.5, 0.9),
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=60.0)
        self._quantiles = list(quantiles)

    def forecast(self, values: list[float], horizon: int) -> list[dict]:
        resp = self._client.post(
            f"{self._base}/forecast",
            json={"values": values, "horizon": horizon, "quantiles": self._quantiles},
        )
        resp.raise_for_status()
        return resp.json()["rows"]


def make_forecaster(job: ForecastJob, timesfm_url: str | None = None) -> Forecaster:
    if job.model == "timesfm-2.5":
        if timesfm_url is None:
            from norn_core.config import get_settings

            timesfm_url = get_settings(refresh=True).forecast.timesfm.worker_url
        return TimesFMForecaster(timesfm_url)
    return BaselineForecaster(job.seasonality if job.seasonality is not None else 7)
