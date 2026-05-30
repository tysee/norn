"""
packages/agent/src/norn_agent/analyze.py

Оркестратор одного прохода анализа зависимостей — точка сборки слоя в платформе
norn. Связывает витрину метрик (ClickHouse mart_metric), статистические методы и
LLM-агента: достаёт свежие ряды двух сегментов, выравнивает их по общему времени,
прогоняет методы-улики, фиксирует улики в metric_dependency, а структурированное
решение агента — в dependency_explanation. Знает о прошлом прогоне, чтобы агент
оценивал дрейф зависимости.

Публичные функции:
- analyze_dependencies(job, client, agent=None) -> str — выполняет полный проход
  для одной job и возвращает analysis_run_id, связывающий все записанные строки.

Внутренние помощники:
- _prior_measurements — улики последнего прошлого прогона для той же тройки
  метрика/source/target (для drift-aware суждения).
- _series — чтение последних context_length точек ряда для (метрика, сегмент).
- _align — выравнивание двух рядов по общим временным меткам + окно наблюдения.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from clickhouse_connect.driver.client import Client

from norn_agent.agent import judge_dependencies
from norn_agent.contract import DependencyJob, DependencyMeasurement
from norn_agent.methods import METHODS


def _prior_measurements(client: Client, job: DependencyJob) -> list[DependencyMeasurement]:
    """Measurements from the most recent PRIOR run for this metric/source/target."""
    # --- найти id последнего прошлого прогона для этой тройки ---
    run = client.query(
        "SELECT analysis_run_id FROM metric_dependency "
        "WHERE metric_name=%(m)s AND source_segment=%(s)s AND target_segment=%(t)s "
        "ORDER BY created_at DESC LIMIT 1",
        parameters={"m": job.metric, "s": job.source_segment, "t": job.target_segment},
    ).result_rows
    if not run:
        return []
    # --- поднять улики того прогона и восстановить модели измерений ---
    rows = client.query(
        "SELECT method, lag, score, direction, p_value, confidence FROM metric_dependency "
        "WHERE analysis_run_id=%(r)s",
        parameters={"r": run[0][0]},
    ).result_rows
    return [
        DependencyMeasurement(
            method=r[0], lag=r[1], score=r[2], direction=r[3], p_value=r[4], confidence=r[5]
        )
        for r in rows
    ]


def _series(client: Client, mart: str, metric: str, segment: str, context_length: int):
    rows = client.query(
        f"SELECT ts, value FROM (SELECT ts, value FROM {mart} "
        "WHERE metric_name=%(m)s AND segment_key=%(s)s ORDER BY ts DESC "
        f"LIMIT {context_length}) ORDER BY ts",
        parameters={"m": metric, "s": segment},
    ).result_rows
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def _align(src_ts, src_vals, tgt_ts, tgt_vals):
    # --- индекс target по времени для поиска общих меток ---
    tmap = dict(zip(tgt_ts, tgt_vals))
    # --- пересечение по ts, отсортированное по времени ---
    common = sorted(
        ((ts, sv, tmap[ts]) for ts, sv in zip(src_ts, src_vals) if ts in tmap),
        key=lambda x: x[0],
    )
    # --- разложить на параллельные ряды и вычислить окно наблюдения ---
    src = [c[1] for c in common]
    tgt = [c[2] for c in common]
    window = (common[0][0], common[-1][0]) if common else (datetime(1970, 1, 1), datetime(1970, 1, 1))
    return src, tgt, window


def analyze_dependencies(job: DependencyJob, client: Client, agent=None) -> str:
    # --- подставить незаданные тюнинги job из конфига и выдать id прогона ---
    job = job.resolved()
    run_id = str(uuid.uuid4())
    # --- extract: прочитать ряды обоих сегментов и выровнять по общему времени ---
    s_ts, s_v = _series(client, job.mart, job.metric, job.source_segment, job.context_length)
    t_ts, t_v = _series(client, job.mart, job.metric, job.target_segment, job.context_length)
    src, tgt, (w0, w1) = _align(s_ts, s_v, t_ts, t_v)

    # --- compute: прогнать выбранные методы-улики (granger получает тюнинги из конфига) ---
    from norn_core.config import get_settings

    a = get_settings().agent
    measurements = []
    for name in job.methods:
        if name == "granger":
            measurements.append(
                METHODS[name](src, tgt, job.max_lag,
                               min_points_factor=a.granger_min_points_factor,
                               significance=a.granger_significance)
            )
        else:
            measurements.append(METHODS[name](src, tgt, job.max_lag))

    # Look up the previous run BEFORE inserting this run's rows (drift-aware judging).
    prior = _prior_measurements(client, job)

    # --- write-back: зафиксировать улики методов в metric_dependency ---
    dep_rows = [
        [run_id, job.metric, job.source_segment, job.target_segment, m.method,
         m.lag, m.score, m.direction, m.p_value, m.confidence, w0, w1, datetime.now(UTC)]
        for m in measurements
    ]
    client.insert(
        "metric_dependency", dep_rows,
        column_names=[
            "analysis_run_id", "metric_name", "source_segment", "target_segment",
            "method", "lag", "score", "direction", "p_value", "confidence",
            "window_start", "window_end", "created_at",
        ],
    )

    # --- judge: отдать улики (и прошлые) агенту за решением о реальности ---
    meta = {
        "source_segment": job.source_segment,
        "target_segment": job.target_segment,
        "metric_name": job.metric,
    }
    decision = judge_dependencies(measurements, meta, prior_measurements=prior, agent=agent)
    from norn_core.config import get_settings

    model_name = get_settings().agent.model
    # --- write-back: сохранить объяснения агента в dependency_explanation ---
    exp_rows = [
        [run_id, job.metric, r.source_segment, r.target_segment, r.lag, r.direction,
         1 if r.is_real else 0, r.confidence, r.explanation, r.caveats, r.change_note,
         model_name, datetime.now(UTC)]
        for r in decision.relations
    ]
    if exp_rows:
        client.insert(
            "dependency_explanation", exp_rows,
            column_names=[
                "analysis_run_id", "metric_name", "source_segment", "target_segment",
                "lag", "direction", "is_real", "confidence", "explanation", "caveats",
                "change_note", "llm_model", "created_at",
            ],
        )
    return run_id
