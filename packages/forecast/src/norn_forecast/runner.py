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

from norn_core.clickhouse import _safe_identifier
from norn_core.config import get_settings
from norn_core.contract import ForecastJob, Grain
from norn_forecast.covariates import (
    build_covariate_array,
    covariate_series,
    resolve_covariate_specs,
)
from norn_forecast.forecaster import Forecaster, make_forecaster

_STEP = {Grain.daily: timedelta(days=1), Grain.hourly: timedelta(hours=1)}


def _segments(client: Client, job: ForecastJob) -> list[dict]:
    if not job.dimensions:
        return [{}]
    source = _safe_identifier(job.source)
    cols = ", ".join(_safe_identifier(d) for d in job.dimensions)
    fclause, fparams = _filter_clause(job)
    where = f"WHERE {fclause} " if fclause else ""
    rows = client.query(
        f"SELECT DISTINCT {cols} FROM {source} {where}ORDER BY {cols}",
        parameters=fparams,
    ).result_rows
    return [dict(zip(job.dimensions, r)) for r in rows]


def _segment_key(dims: dict) -> str:
    if not dims:
        return "all"
    return "|".join(f"{k}={dims[k]}" for k in dims)


def _filter_clause(job: ForecastJob) -> tuple[str, dict]:
    """SQL WHERE fragment + bound params for job.filter (column names safe, values bound)."""
    parts, params = [], {}
    for i, (col, val) in enumerate(job.filter.items()):
        key = f"f{i}"
        parts.append(f"{_safe_identifier(col)} = %({key})s")
        params[key] = val
    return " AND ".join(parts), params


def _series(client: Client, job: ForecastJob, dims: dict) -> tuple[list[datetime], list[float]]:
    source = _safe_identifier(job.source)
    metric = _safe_identifier(job.metric)
    fclause, fparams = _filter_clause(job)
    conds = [f"{_safe_identifier(k)} = %({k})s" for k in dims]
    if fclause:
        conds.append(fclause)
    where = " AND ".join(conds) or "1 = 1"
    rows = client.query(
        f"SELECT ts, val FROM ("
        f"SELECT ts, {metric} AS val FROM {source} WHERE {where} "
        f"ORDER BY ts DESC LIMIT {job.context_length}"
        f") ORDER BY ts",
        parameters={**dims, **fparams},
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
    policy = get_settings().forecast.covariates.horizon_policy
    segments = _segments(client, job)
    points: list[list] = []
    used_covariates = False

    # --- по каждому сегменту: ряд -> прогноз -> строки будущих точек ---
    # Весь цикл прогнозирования под try: при сбое форкастера (напр. TimesFM-воркер
    # недоступен) пишем forecast_run со status='failed' (прогон становится виден в
    # контракте, есть аудит) и поднимаем понятную ошибку — без тихого фолбека.
    try:
        for dims in segments:
            ts, vals = _series(client, job, dims)
            if not vals:
                continue
            seg_key = _segment_key(dims)
            last_ts = ts[-1]
            # ClickHouse DateTime comes back naive-UTC; tag it UTC so the insert
            # stores the true instant. Otherwise clickhouse-connect treats the naive
            # forecast_ts as LOCAL time and shifts it by the machine's UTC offset,
            # breaking the exact-ts join from forecast to realized actuals.
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=UTC)
            # --- ковариаты: ряд лидера из long-mart, выровненный на контекст+горизонт ---
            # лидер берётся из long-mart (metric_name+segment_key), независимо от job.source
            # (это широкая per-metric витрина цели). Тянем context_length+lag точек, чтобы
            # сдвиг (t - lag) был покрыт; value-binding параметризован, mart — через identifier.
            covs: dict[str, list[float]] = {}
            for spec in resolve_covariate_specs(client, job, seg_key):
                s_ts, s_vals = covariate_series(
                    client, spec.mart, spec.metric, spec.segment,
                    job.context_length + spec.lag,
                )
                arr = build_covariate_array(ts, s_ts, s_vals, spec.lag, job.horizon, step, policy)
                if arr is not None:
                    covs[f"{spec.segment}:{spec.metric}@lag{spec.lag}"] = arr
            # без ковариат -> вызов без covariates -> обычный прогноз (дефолт, без изменений);
            # это сохраняет совместимость с форкастерами, чей forecast() не знает про ковариаты.
            if covs:
                used_covariates = True
                fc = forecaster.forecast(vals, job.horizon, covariates=covs)
            else:
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
    except ValueError:
        # input/identifier validation (напр. небезопасный source) — fail fast,
        # это не "сбойный прогон", а отвергнутый job; не пишем forecast_run.
        raise
    except Exception as e:
        client.insert(
            "forecast_run",
            [[run_id, job.metric, "failed", job.model, "v0",
              started, datetime.now(UTC), len(segments), len(segments), str(e)]],
            column_names=[
                "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
                "started_at", "finished_at", "segments_total", "segments_skipped", "error",
            ],
        )
        raise RuntimeError(f"forecast run {run_id} failed (model={job.model}): {e}") from e

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
    # отмечаем XReg-прогон в model_version, чтобы прогон с ковариатами был отличим
    model_version = "v0+xreg" if used_covariates else "v0"
    client.insert(
        "forecast_run",
        [[run_id, job.metric, "success", job.model, model_version,
          started, datetime.now(UTC), len(segments), 0, None]],
        column_names=[
            "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
            "started_at", "finished_at", "segments_total", "segments_skipped", "error",
        ],
    )
    return run_id
