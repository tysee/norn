"""
packages/forecast/src/norn_forecast/calibration.py

Rolling-origin калибровка прогнозов платформы norn. Несколько раз «отматывает»
ряд назад на горизонт (cutoff'ы), строит прогноз только по прошлому и сравнивает
его с реальным hold-out: насколько факт попадает в интервал p10..p90 (coverage)
и насколько точна центральная оценка (wape/mape/bias). Так измеряется доверие к
форкастеру по каждому сегменту; метрики складываются в контракт-таблицу
forecast_segment, откуда их читает агент через MCP-инструмент get_calibration.

Методы:
- backtest_metrics(values, forecaster, horizon, n_cutoffs) -> dict —
  coverage/wape/mape/bias/n_points по одному ряду (чистая функция, без ввода-вывода).
- calibrate_job(job, client, forecaster=None) -> str — прогон по всем сегментам
  job, запись метрик в forecast_segment; возвращает run_id калибровки.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
from clickhouse_connect.driver.client import Client

from norn_core.contract import ForecastJob
from norn_forecast.forecaster import Forecaster, make_forecaster
from norn_forecast.runner import _segment_key, _segments, _series


def backtest_metrics(
    values: list[float], forecaster: Forecaster, horizon: int, n_cutoffs: int = 3
) -> dict:
    # --- rolling-origin прогон: копим пары (факт, прогноз) по всем cutoff'ам ---
    n = len(values)
    actual: list[float] = []
    yhat: list[float] = []
    lo: list[float] = []
    hi: list[float] = []
    for k in range(n_cutoffs, 0, -1):
        cut = n - k * horizon
        if cut <= 0:
            continue
        ctx = values[:cut]
        truth = values[cut : cut + horizon]
        if len(truth) < horizon:
            continue
        fc = forecaster.forecast(ctx, horizon)
        for row, a in zip(fc, truth):
            actual.append(a)
            yhat.append(row["y_hat"])
            lo.append(row["p10"])
            hi.append(row["p90"])

    if not actual:
        return {"coverage": 0.0, "wape": 0.0, "mape": 0.0, "bias": 0.0, "n_points": 0}

    # --- агрегированные метрики качества по накопленным парам ---
    a = np.array(actual)
    yh = np.array(yhat)
    p_lo = np.array(lo)
    p_hi = np.array(hi)
    coverage = float(np.mean((a >= p_lo) & (a <= p_hi)))
    wape = float(np.sum(np.abs(a - yh)) / (np.sum(np.abs(a)) or 1.0))
    nz = a != 0
    mape = float(np.mean(np.abs((a[nz] - yh[nz]) / a[nz]))) if nz.any() else 0.0
    bias = float(np.mean(yh - a))
    return {
        "coverage": coverage,
        "wape": wape,
        "mape": mape,
        "bias": bias,
        "n_points": int(a.size),
    }


def calibrate_job(job: ForecastJob, client: Client, forecaster: Forecaster | None = None) -> str:
    job = job.resolved()
    from norn_core.config import get_settings

    # --- параметры прогона и выбор форкастера ---
    n_cutoffs = get_settings().forecast.calibration.n_cutoffs
    forecaster = forecaster or make_forecaster(job)
    run_id = str(uuid.uuid4())

    # --- посегментная калибровка: ряд из ClickHouse -> метрики -> строка вставки ---
    rows: list[list] = []
    for dims in _segments(client, job):
        _ts, vals = _series(client, job, dims)
        if not vals:
            continue
        m = backtest_metrics(vals, forecaster, job.horizon, n_cutoffs=n_cutoffs)
        rows.append([
            run_id, job.metric, _segment_key(dims),
            m["n_points"], 1 if m["n_points"] == 0 else 0,
            m["wape"], m["mape"], m["coverage"], m["bias"],
            datetime.now(UTC),
        ])

    # --- пакетная запись метрик в контракт-таблицу forecast_segment ---
    if rows:
        client.insert(
            "forecast_segment", rows,
            column_names=[
                "forecast_run_id", "metric_name", "segment_key", "n_points",
                "is_sparse", "wape", "mape", "coverage", "bias", "created_at",
            ],
        )
    return run_id
