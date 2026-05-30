"""
packages/forecast/src/norn_forecast/forecaster.py

Единый интерфейс форкастера и его адаптеры для платформы norn. Прячет за общим
Protocol две реализации — лёгкую baseline и тяжёлую TimesFM — так, что runner и
калибровка не зависят от модели, а выбор делается по полю job.model. TimesFM
вынесен в отдельный HTTP-воркер, поэтому torch в этот процесс не тянется.

Классы/методы:
- Forecaster — Protocol: forecast(values, horizon) -> list[dict] (строки с
  horizon_step/y_hat/p10/p50/p90).
- BaselineForecaster — обёртка над seasonal_naive_forecast (seasonal-naive).
- TimesFMForecaster — HTTP-клиент к torch-воркеру: POST /forecast, без torch здесь.
  Владеет httpx.Client, если тот не внедрён извне: close()/контекст-менеджер
  закрывают пул соединений только для собственного клиента.
- make_forecaster(job, timesfm_url=None) -> Forecaster — фабрика: по job.model
  возвращает TimesFM (url из конфига) либо baseline (с сезонностью job).
"""
from __future__ import annotations

from typing import Protocol

import httpx

from norn_core.contract import ForecastJob
from norn_forecast.baseline import seasonal_naive_forecast


class Forecaster(Protocol):
    def forecast(self, values: list[float], horizon: int) -> list[dict]: ...


class BaselineForecaster:
    def __init__(
        self,
        seasonality: int = 7,
        quantiles: tuple[float, float, float] = (0.1, 0.5, 0.9),
    ) -> None:
        self.seasonality = seasonality
        self.quantiles = quantiles

    def forecast(self, values: list[float], horizon: int) -> list[dict]:
        return seasonal_naive_forecast(values, horizon, self.seasonality, self.quantiles)


class TimesFMForecaster:
    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        quantiles: tuple[float, ...] = (0.1, 0.5, 0.9),
    ) -> None:
        self._base = base_url.rstrip("/")
        # Own the client only when we created it; an injected client is the
        # caller's to close (so we never tear down a shared connection pool).
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=60.0)
        self._quantiles = list(quantiles)

    def forecast(self, values: list[float], horizon: int) -> list[dict]:
        resp = self._client.post(
            f"{self._base}/forecast",
            json={"values": values, "horizon": horizon, "quantiles": self._quantiles},
        )
        resp.raise_for_status()
        return resp.json()["rows"]

    def close(self) -> None:
        """Close the underlying httpx.Client only if this forecaster owns it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "TimesFMForecaster":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def make_forecaster(job: ForecastJob, timesfm_url: str | None = None) -> Forecaster:
    from norn_core.config import get_settings

    if job.model == "timesfm-2.5":
        if timesfm_url is None:
            timesfm_url = get_settings(refresh=True).forecast.timesfm.worker_url
        q = tuple(get_settings(refresh=True).forecast.quantiles)
        return TimesFMForecaster(timesfm_url, quantiles=q)

    q = tuple(get_settings(refresh=True).forecast.quantiles)
    return BaselineForecaster(job.seasonality if job.seasonality is not None else 7, q)
