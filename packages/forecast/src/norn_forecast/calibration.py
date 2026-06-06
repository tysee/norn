"""
packages/forecast/src/norn_forecast/calibration.py

Rolling-origin calibration of norn platform forecasts. It rewinds the series
back by one horizon several times (cutoffs), builds a forecast from the past
only, and compares it against the real hold-out: how often the actual falls
inside the p10..p90 interval (coverage) and how accurate the central estimate is
(wape/mape/bias). This measures trust in the forecaster per segment; the metrics
are written to the contract table forecast_segment, where the agent reads them
through the MCP tool get_calibration.

Functions:
- backtest_metrics(values, forecaster, horizon, n_cutoffs, ts=None,
  covariates_for=None) -> dict — coverage/wape/mape/bias/n_points for a single
  series (pure function, no I/O). covariates_for(ctx_ts) returns the XReg
  covariates for each cutoff — so calibration of an xreg job measures EXACTLY the
  xreg model.
- calibrate_job(job, client, forecaster=None) -> str — run over all segments of
  the job, writing metrics to forecast_segment; returns the calibration run_id.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Callable

import numpy as np
from clickhouse_connect.driver.client import Client

from norn_core.contract import ForecastJob
from norn_forecast.covariates import (
    build_covariate_array,
    covariate_series,
    resolve_covariate_specs,
)
from norn_forecast.forecaster import Forecaster, make_forecaster
from norn_forecast.runner import _STEP, _segment_key, _segments, _series

# Callback type: a cutoff's context ts -> dict of XReg covariates (or None).
CovariatesFor = Callable[[list], dict[str, list[float]] | None]


def _cutoff_forecast(
    forecaster: Forecaster, ctx: list[float], horizon: int,
    ts, cut: int, covariates_for: CovariatesFor | None,
) -> list[dict]:
    """One rolling-origin forecast at a cutoff, with covariates when provided.

    Without covariates the call stays as before (compatible with forecasters
    whose forecast() does not know the covariates kwarg) — plain-calibration
    behavior is unchanged.
    """
    covs = covariates_for(ts[:cut]) if (covariates_for and ts) else None
    if covs:
        return forecaster.forecast(ctx, horizon, covariates=covs)
    return forecaster.forecast(ctx, horizon)


def backtest_metrics(
    values: list[float], forecaster: Forecaster, horizon: int, n_cutoffs: int = 3,
    ts: list | None = None, covariates_for: CovariatesFor | None = None,
) -> dict:
    # --- rolling-origin run: accumulate (actual, forecast) pairs over all cutoffs ---
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
        fc = _cutoff_forecast(forecaster, ctx, horizon, ts, cut, covariates_for)
        for row, a in zip(fc, truth):
            actual.append(a)
            yhat.append(row["y_hat"])
            lo.append(row["p10"])
            hi.append(row["p90"])

    if not actual:
        return {"coverage": 0.0, "wape": 0.0, "mape": 0.0, "bias": 0.0, "n_points": 0}

    # --- aggregated quality metrics over the accumulated pairs ---
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
    ts: list, values: list[float], forecaster: Forecaster, horizon: int, n_cutoffs: int = 3,
    covariates_for: CovariatesFor | None = None,
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
        for row, a, t in zip(
            _cutoff_forecast(forecaster, ctx, horizon, ts, cut, covariates_for), truth, truth_ts
        ):
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

    # --- run parameters and forecaster selection ---
    n_cutoffs = get_settings().forecast.calibration.n_cutoffs
    forecaster = forecaster or make_forecaster(job)
    run_id = str(uuid.uuid4())

    # --- per-segment calibration: series from ClickHouse -> metrics (+ per-point pairs) ---
    step = _STEP[job.grain]
    policy = get_settings().forecast.covariates.horizon_policy
    rows: list[list] = []
    points: list[list] = []
    now = datetime.now(UTC)
    for dims in _segments(client, job):
        seg_ts, vals = _series(client, job, dims)
        if not vals:
            continue
        seg_key = _segment_key(dims)
        # --- XReg: the same covariates as the live run (explicit + confirmed leads).
        # We pull the leader series for the full segment length (+lag) ONCE; at each
        # cutoff we trim it to the end of the context — in prod the leader's future is
        # unknown, and a backtest without this trim would have a lookahead leak.
        sources = [
            (spec, covariate_series(client, spec.mart, spec.metric, spec.segment,
                                    len(seg_ts) + spec.lag))
            for spec in resolve_covariate_specs(client, job, seg_key)
        ]

        def covariates_for(ctx_ts: list) -> dict[str, list[float]] | None:
            covs: dict[str, list[float]] = {}
            for spec, (s_ts, s_vals) in sources:
                known = [(t, v) for t, v in zip(s_ts, s_vals) if t <= ctx_ts[-1]]
                if not known:
                    continue
                k_ts, k_vals = (list(x) for x in zip(*known))
                arr = build_covariate_array(
                    ctx_ts, k_ts, k_vals, spec.lag, job.horizon, step, policy
                )
                if arr is not None:
                    covs[f"{spec.segment}:{spec.metric}@lag{spec.lag}"] = arr
            return covs or None

        cov_cb = covariates_for if sources else None
        m = backtest_metrics(vals, forecaster, job.horizon, n_cutoffs=n_cutoffs,
                             ts=seg_ts, covariates_for=cov_cb)
        rows.append([
            run_id, job.metric, seg_key,
            m["n_points"], 1 if m["n_points"] == 0 else 0,
            m["wape"], m["mape"], m["coverage"], m["bias"],
            now,
        ])
        # tag so the live-forecast view ignores these; '+xreg' keeps the xreg
        # calibration distinguishable from the plain model's (e2e finding: they
        # used to be byte-identical because covariates were silently dropped)
        bt_model = f"{job.model}{'+xreg' if sources else ''} (backtest)"
        # persist the realized (actual, forecast) pairs for "past forecast vs actual"
        for pt in backtest_points(seg_ts, vals, forecaster, job.horizon,
                                  n_cutoffs=n_cutoffs, covariates_for=cov_cb):
            points.append([
                run_id, job.metric, seg_key, pt["forecast_ts"], pt["horizon_step"],
                pt["y_hat"], pt["p10"], pt["p50"], pt["p90"], pt["y_actual"],
                bt_model, now,
            ])

    # --- write metrics to forecast_segment + per-point backtest to forecast_point ---
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
