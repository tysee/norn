"""
packages/forecast/src/norn_forecast/runner.py

Исполнитель forecast-job платформы norn — оркестрация одного прогона. Раскрывает
job в набор сегментов, тянет последние context_length точек ряда из ClickHouse,
прогоняет через выбранный форкастер и материализует результат в контракт-таблицы:
будущие точки в forecast_point, сводку прогона в forecast_run. Эти таблицы затем
читают MCP-инструменты, которыми пользуется агент.

Методы:
- run_job(job, client, forecaster=None) -> str — выполняет весь прогон,
  возвращает run_id.
Внутренние помощники:
- _segments(client, job) -> list[dict] — список сегментов (DISTINCT по dimensions).
- _segment_key(dims) -> str — стабильный строковый ключ сегмента ("all" без dims).
- _series(client, job, dims) -> (timestamps, values) — последние точки ряда сегмента
  в хронологическом порядке.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from clickhouse_connect.driver.client import Client

from norn_core.contract import ForecastJob, Grain
from norn_forecast.forecaster import Forecaster, make_forecaster

_STEP = {Grain.daily: timedelta(days=1), Grain.hourly: timedelta(hours=1)}


def _segments(client: Client, job: ForecastJob) -> list[dict]:
    if not job.dimensions:
        return [{}]
    cols = ", ".join(job.dimensions)
    rows = client.query(
        f"SELECT DISTINCT {cols} FROM {job.source} ORDER BY {cols}"
    ).result_rows
    return [dict(zip(job.dimensions, r)) for r in rows]


def _segment_key(dims: dict) -> str:
    if not dims:
        return "all"
    return "|".join(f"{k}={dims[k]}" for k in dims)


def _series(client: Client, job: ForecastJob, dims: dict) -> tuple[list[datetime], list[float]]:
    where = " AND ".join(f"{k} = %({k})s" for k in dims) or "1 = 1"
    rows = client.query(
        f"SELECT ts, val FROM ("
        f"SELECT ts, {job.metric} AS val FROM {job.source} WHERE {where} "
        f"ORDER BY ts DESC LIMIT {job.context_length}"
        f") ORDER BY ts",
        parameters=dims,
    ).result_rows
    ts = [r[0] for r in rows]
    vals = [float(r[1]) for r in rows]
    return ts, vals


def run_job(job: ForecastJob, client: Client, forecaster: Forecaster | None = None) -> str:
    # --- подготовка прогона: id, форкастер, шаг времени, список сегментов ---
    job = job.resolved()
    run_id = str(uuid.uuid4())
    forecaster = forecaster or make_forecaster(job)
    started = datetime.now(UTC)
    step = _STEP[job.grain]
    segments = _segments(client, job)
    points: list[list] = []

    # --- по каждому сегменту: ряд -> прогноз -> строки будущих точек ---
    for dims in segments:
        ts, vals = _series(client, job, dims)
        if not vals:
            continue
        seg_key = _segment_key(dims)
        last_ts = ts[-1]
        fc = forecaster.forecast(vals, job.horizon)
        now = datetime.now(UTC)
        # будущая метка времени = последняя фактическая + шаг * номер горизонта
        for row in fc:
            points.append([
                run_id, job.metric, seg_key,
                last_ts + step * row["horizon_step"],
                row["horizon_step"], row["y_hat"],
                row["p10"], row["p50"], row["p90"],
                None, job.model, now,
            ])

    # --- запись прогноза в forecast_point ---
    if points:
        client.insert(
            "forecast_point", points,
            column_names=[
                "forecast_run_id", "metric_name", "segment_key", "forecast_ts",
                "horizon_step", "y_hat", "p10", "p50", "p90", "y_actual",
                "model_name", "created_at",
            ],
        )

    # --- сводка прогона в forecast_run (всегда, даже без точек) ---
    client.insert(
        "forecast_run",
        [[run_id, job.metric, "success", job.model, "v0",
          started, datetime.now(UTC), len(segments), 0, None]],
        column_names=[
            "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
            "started_at", "finished_at", "segments_total", "segments_skipped", "error",
        ],
    )
    return run_id
