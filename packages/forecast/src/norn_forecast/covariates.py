"""
packages/forecast/src/norn_forecast/covariates.py

Сборка ковариат для прогноза: резолв спецификаций (явные из job + опц. из подтверждённых
зависимостей) и построение выровненного по таймстемпам массива лидера на контекст+горизонт.

Методы:
- resolve_covariate_specs(client, job, target_segment) -> list[CovariateSpec]
- covariate_series(client, mart, metric, segment, n) -> (ts[], vals[]) — ряд лидера из long-mart
- build_covariate_array(target_ts, source_ts, source_vals, lag, horizon, step, policy) -> list[float] | None
"""
from __future__ import annotations

from datetime import datetime, timedelta

from clickhouse_connect.driver.client import Client

from norn_core.clickhouse import _safe_identifier
from norn_core.contract import CovariateSpec, ForecastJob


def covariate_series(client: Client, mart: str, metric: str, segment: str, n: int):
    """Most-recent n points of the leader series from the long mart (ts, value)."""
    _safe_identifier(mart)
    rows = client.query(
        f"SELECT ts, value FROM (SELECT ts, value FROM {mart} "
        "WHERE metric_name=%(m)s AND segment_key=%(s)s ORDER BY ts DESC "
        f"LIMIT {int(n)}) ORDER BY ts",
        parameters={"m": metric, "s": segment},
    ).result_rows
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def resolve_covariate_specs(client: Client, job: ForecastJob, target_segment: str) -> list[CovariateSpec]:
    specs = list(job.covariates)
    if job.use_dependencies:
        rows = client.query(
            "SELECT d.source_segment, d.metric_name, d.lag FROM dependency_explanation d "
            "WHERE d.target_segment=%(t)s AND d.is_real=1 AND d.direction='source_leads'",
            parameters={"t": target_segment},
        ).result_rows
        for src, metric, lag in rows:
            specs.append(CovariateSpec(metric=metric, segment=src, lag=int(lag)))
    return specs


def build_covariate_array(
    target_ts: list[datetime], source_ts: list[datetime], source_vals: list[float],
    lag: int, horizon: int, step: timedelta, policy: str,
) -> list[float] | None:
    if policy == "strict" and lag < horizon:
        return None  # leader does not cover the whole horizon with known actuals
    smap = {ts: float(v) for ts, v in zip(source_ts, source_vals)}
    full_ts = list(target_ts) + [target_ts[-1] + step * h for h in range(1, horizon + 1)]
    out: list[float] = []
    last_known: float | None = None
    for ts in full_ts:
        src_at = ts - step * lag
        if src_at in smap:
            last_known = smap[src_at]
            out.append(last_known)
        elif policy == "ffill" and last_known is not None:
            out.append(last_known)
        else:
            return None  # gap in leader history -> covariate unusable
    return out
