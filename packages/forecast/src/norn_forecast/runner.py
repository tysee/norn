"""
packages/forecast/src/norn_forecast/runner.py

The forecast-job executor of the norn platform — orchestration of a single run. Expands
a job into a set of segments, pulls the last context_length points of the series from ClickHouse,
runs them through the chosen forecaster and materializes the result into the contract tables:
future points into forecast_point, the run summary into forecast_run. These tables are then
read by the MCP tools that the agent uses.

Methods:
- run_job(job, client, forecaster=None) -> str — executes the whole run,
  returns run_id.
Internal helpers:
- _segments(client, job) -> list[dict] — list of segments (DISTINCT over dimensions).
- _segment_key(dims) -> str — stable string key of a segment ("all" when no dims).
- _series(client, job, dims) -> (timestamps, values) — the latest points of a segment's series
  in chronological order.
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
    resolve_covariate_specs_bulk,
)
from norn_forecast.forecaster import Forecaster, make_forecaster


def _close_forecaster(forecaster: Forecaster) -> None:
    """Close a locally-created forecaster (it may own an httpx connection pool)."""
    close = getattr(forecaster, "close", None)
    if close is not None:
        close()


def _is_rejected_job_error(exc: ValueError) -> bool:
    """Validation errors that reject the job before it becomes a forecast run."""
    return str(exc).startswith("Unsafe SQL identifier:")


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
    # --- run setup: id, forecaster, time step, list of segments ---
    job = job.resolved()
    run_id = str(uuid.uuid4())
    # close a forecaster we created ourselves (it may own an httpx pool); an
    # injected one belongs to the caller (e.g. a long-lived scheduler instance)
    owns_forecaster = forecaster is None
    forecaster = forecaster or make_forecaster(job)
    try:
        return _run_job(job, client, forecaster, run_id)
    finally:
        if owns_forecaster:
            _close_forecaster(forecaster)


def _run_job(job: ForecastJob, client: Client, forecaster: Forecaster, run_id: str) -> str:
    started = datetime.now(UTC)
    step = _STEP[job.grain]
    policy = get_settings().forecast.covariates.horizon_policy
    segments = _segments(client, job)
    # one dependency query for all segments instead of one per segment
    spec_by_seg = resolve_covariate_specs_bulk(
        client, job, [_segment_key(d) for d in segments]
    )
    points: list[list] = []
    used_covariates = False
    skipped = 0  # segments with no data in the source mart

    # --- per segment: series -> forecast -> rows of future points ---
    # The whole forecasting loop is under try: on a forecaster failure (e.g. the TimesFM
    # worker is unavailable) we write forecast_run with status='failed' (the run becomes visible
    # in the contract, there is an audit trail) and raise a clear error — with no silent fallback.
    try:
        for dims in segments:
            ts, vals = _series(client, job, dims)
            if not vals:
                skipped += 1
                continue
            seg_key = _segment_key(dims)
            last_ts = ts[-1]
            # ClickHouse DateTime comes back naive-UTC; tag it UTC so the insert
            # stores the true instant. Otherwise clickhouse-connect treats the naive
            # forecast_ts as LOCAL time and shifts it by the machine's UTC offset,
            # breaking the exact-ts join from forecast to realized actuals.
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=UTC)
            # --- covariates: leader series from the long mart, aligned over context+horizon ---
            # the leader is taken from the long mart (metric_name+segment_key), independent of job.source
            # (this is the wide per-metric mart of the target). We pull context_length+lag points so that
            # the shift (t - lag) is covered; value-binding is parameterized, the mart goes through identifier.
            covs: dict[str, list[float]] = {}
            for spec in spec_by_seg[seg_key]:
                s_ts, s_vals = covariate_series(
                    client, spec.mart, spec.metric, spec.segment,
                    job.context_length + spec.lag,
                )
                arr = build_covariate_array(ts, s_ts, s_vals, spec.lag, job.horizon, step, policy)
                if arr is not None:
                    covs[f"{spec.segment}:{spec.metric}@lag{spec.lag}"] = arr
            # no covariates -> call without covariates -> a plain forecast (default, unchanged);
            # this keeps compatibility with forecasters whose forecast() knows nothing about covariates.
            if covs:
                used_covariates = True
                fc = forecaster.forecast(vals, job.horizon, covariates=covs)
            else:
                fc = forecaster.forecast(vals, job.horizon)
            now = datetime.now(UTC)
            # future timestamp = last actual + step * horizon step number
            for row in fc:
                points.append([
                    run_id, job.metric, seg_key,
                    last_ts + step * row["horizon_step"],
                    row["horizon_step"], row["y_hat"],
                    row["p10"], row["p50"], row["p90"],
                    None, job.model, now,
                ])
    except ValueError as e:
        # input/identifier validation (e.g. an unsafe source) — fail fast,
        # this is not a "failed run" but a rejected job; we do not write forecast_run.
        if _is_rejected_job_error(e):
            raise
        client.insert(
            "forecast_run",
            [[run_id, job.metric, "failed", job.model, "v0",
              started, datetime.now(UTC), len(segments), 0, str(e)]],
            column_names=[
                "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
                "started_at", "finished_at", "segments_total", "segments_skipped", "error",
            ],
        )
        raise RuntimeError(f"forecast run {run_id} failed (model={job.model}): {e}") from e
    except Exception as e:
        # segments_skipped=0: 'skipped' means "no data in the source", not "failed" —
        # status='failed' + error carry the failure semantics (see jobs.md).
        client.insert(
            "forecast_run",
            [[run_id, job.metric, "failed", job.model, "v0",
              started, datetime.now(UTC), len(segments), 0, str(e)]],
            column_names=[
                "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
                "started_at", "finished_at", "segments_total", "segments_skipped", "error",
            ],
        )
        raise RuntimeError(f"forecast run {run_id} failed (model={job.model}): {e}") from e

    # --- write the forecast into forecast_point ---
    if points:
        client.insert(
            "forecast_point", points,
            column_names=[
                "forecast_run_id", "metric_name", "segment_key", "forecast_ts",
                "horizon_step", "y_hat", "p10", "p50", "p90", "y_actual",
                "model_name", "created_at",
            ],
        )

    # --- run summary into forecast_run (always, even with no points) ---
    # mark an XReg run in model_version so a run with covariates is distinguishable
    model_version = "v0+xreg" if used_covariates else "v0"
    client.insert(
        "forecast_run",
        [[run_id, job.metric, "success", job.model, model_version,
          started, datetime.now(UTC), len(segments), skipped, None]],
        column_names=[
            "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
            "started_at", "finished_at", "segments_total", "segments_skipped", "error",
        ],
    )
    return run_id
