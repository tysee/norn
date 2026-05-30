"""
packages/agent/src/norn_agent/analyze.py

Оркестрация анализа зависимостей: чтение свежих рядов из mart_metric, прогон
методов-улик, запись metric_dependency, суждение агента -> dependency_explanation.

Методы:
- analyze_dependencies(job, client, agent=None) -> str (analysis_run_id).
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
    run = client.query(
        "SELECT analysis_run_id FROM metric_dependency "
        "WHERE metric_name=%(m)s AND source_segment=%(s)s AND target_segment=%(t)s "
        "ORDER BY created_at DESC LIMIT 1",
        parameters={"m": job.metric, "s": job.source_segment, "t": job.target_segment},
    ).result_rows
    if not run:
        return []
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
    tmap = dict(zip(tgt_ts, tgt_vals))
    common = sorted(
        ((ts, sv, tmap[ts]) for ts, sv in zip(src_ts, src_vals) if ts in tmap),
        key=lambda x: x[0],
    )
    src = [c[1] for c in common]
    tgt = [c[2] for c in common]
    window = (common[0][0], common[-1][0]) if common else (datetime(1970, 1, 1), datetime(1970, 1, 1))
    return src, tgt, window


def analyze_dependencies(job: DependencyJob, client: Client, agent=None) -> str:
    job = job.resolved()
    run_id = str(uuid.uuid4())
    s_ts, s_v = _series(client, job.mart, job.metric, job.source_segment, job.context_length)
    t_ts, t_v = _series(client, job.mart, job.metric, job.target_segment, job.context_length)
    src, tgt, (w0, w1) = _align(s_ts, s_v, t_ts, t_v)

    measurements = [METHODS[name](src, tgt, job.max_lag) for name in job.methods]

    # Look up the previous run BEFORE inserting this run's rows (drift-aware judging).
    prior = _prior_measurements(client, job)

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

    meta = {
        "source_segment": job.source_segment,
        "target_segment": job.target_segment,
        "metric_name": job.metric,
    }
    decision = judge_dependencies(measurements, meta, prior_measurements=prior, agent=agent)
    from norn_core.config import get_settings

    model_name = get_settings(refresh=True).agent.model
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
