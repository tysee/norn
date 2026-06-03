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


def backtest_points(
    ts: list, values: list[float], forecaster: Forecaster, horizon: int, n_cutoffs: int = 3
) -> list[dict]:
    """Per-point rolling-origin backtest: each held-out step with its real timestamp,
    the forecast (y_hat/p10/p50/p90) and the realized y_actual. Same cutoffs as
    backtest_metrics — lets us persist 'past forecast vs actual' pairs for charts."""
    n = len(values)
    out: list[dict] = []
    for k in range(n_cutoffs, 0, -1):
        cut = n - k * horizon
        if cut <= 0:
            continue
        ctx = values[:cut]
        truth = values[cut : cut + horizon]
        truth_ts = ts[cut : cut + horizon]
        if len(truth) < horizon:
            continue
        for row, a, t in zip(forecaster.forecast(ctx, horizon), truth, truth_ts):
            # tag naive-UTC reads so the insert keeps the true instant (see runner).
            ft = t if getattr(t, "tzinfo", None) else t.replace(tzinfo=UTC)
            out.append({
                "forecast_ts": ft, "horizon_step": row["horizon_step"],
                "y_hat": row["y_hat"], "p10": row["p10"], "p50": row["p50"],
                "p90": row["p90"], "y_actual": a,
            })
    return out


def calibrate_job(job: ForecastJob, client: Client, forecaster: Forecaster | None = None) -> str:
    job = job.resolved()
    from norn_core.config import get_settings

    # --- параметры прогона и выбор форкастера ---
    n_cutoffs = get_settings().forecast.calibration.n_cutoffs
    forecaster = forecaster or make_forecaster(job)
    run_id = str(uuid.uuid4())

    # --- посегментная калибровка: ряд из ClickHouse -> метрики (+ по-точечные пары) ---
    rows: list[list] = []
    points: list[list] = []
    now = datetime.now(UTC)
    bt_model = f"{job.model} (backtest)"  # tag so the live-forecast view ignores these
    for dims in _segments(client, job):
        seg_ts, vals = _series(client, job, dims)
        if not vals:
            continue
        seg_key = _segment_key(dims)
        m = backtest_metrics(vals, forecaster, job.horizon, n_cutoffs=n_cutoffs)
        rows.append([
            run_id, job.metric, seg_key,
            m["n_points"], 1 if m["n_points"] == 0 else 0,
            m["wape"], m["mape"], m["coverage"], m["bias"],
            now,
        ])
        # persist the realized (actual, forecast) pairs for "past forecast vs actual"
        for pt in backtest_points(seg_ts, vals, forecaster, job.horizon, n_cutoffs=n_cutoffs):
            points.append([
                run_id, job.metric, seg_key, pt["forecast_ts"], pt["horizon_step"],
                pt["y_hat"], pt["p10"], pt["p50"], pt["p90"], pt["y_actual"],
                bt_model, now,
            ])

    # --- запись метрик в forecast_segment + по-точечного бэктеста в forecast_point ---
    if rows:
        client.insert(
            "forecast_segment", rows,
            column_names=[
                "forecast_run_id", "metric_name", "segment_key", "n_points",
                "is_sparse", "wape", "mape", "coverage", "bias", "created_at",
            ],
        )
    if points:
        client.insert(
            "forecast_point", points,
            column_names=[
                "forecast_run_id", "metric_name", "segment_key", "forecast_ts",
                "horizon_step", "y_hat", "p10", "p50", "p90", "y_actual",
                "model_name", "created_at",
            ],
        )
    return run_id
