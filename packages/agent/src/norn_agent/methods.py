"""
packages/agent/src/norn_agent/methods.py

Statistical evidence methods of the norn platform's dependency subsystem. Each
method receives two aligned (ideally stationary) series and returns a single
DependencyMeasurement — a compact piece of evidence with a lag, relationship
strength, direction and confidence. This evidence does not pass a verdict on its
own: an LLM agent interprets it. The METHODS registry lets the orchestrator pick
methods by name from the job config.

Public functions:
- lagged_cross_correlation(source, target, max_lag) -> DependencyMeasurement —
  finds the lag with the largest absolute cross-correlation; direction = who leads.
- granger(source, target, max_lag, min_points_factor=3, significance=0.05)
  -> DependencyMeasurement — Granger causality test (source -> target);
  score = -log10(p), the significance threshold is passed via the significance parameter.
- METHODS — a {name: callable} registry for selecting methods by DependencyJob.methods.
"""
from __future__ import annotations

import numpy as np

from norn_agent.contract import DependencyMeasurement

# Smallest representable p-value: the F-test can return exactly 0.0 (underflow of
# a tiny p), which would otherwise always pass any positive significance threshold.
# We clamp to this floor so the threshold stays a real lever.
_P_VALUE_FLOOR = 1e-12


def lagged_cross_correlation(
    source: list[float], target: list[float], max_lag: int
) -> DependencyMeasurement:
    # --- align series lengths on the common tail ---
    s = np.asarray(source, dtype=float)
    t = np.asarray(target, dtype=float)
    n = min(s.size, t.size)
    s, t = s[-n:], t[-n:]
    # --- sweep lags and remember the largest absolute correlation ---
    best_lag, best_corr = 0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            a, b = s[:-lag], t[lag:]        # source[t] vs target[t+lag]
        elif lag < 0:
            a, b = s[-lag:], t[:lag]
        else:
            a, b = s, t
        if a.size < 3:
            continue
        corr = np.corrcoef(a, b)[0, 1]
        if np.isnan(corr):
            continue
        if abs(corr) > abs(best_corr):
            best_lag, best_corr = lag, float(corr)
    # --- the sign of the best lag sets the leading series' direction ---
    direction = (
        "source_leads" if best_lag > 0
        else "target_leads" if best_lag < 0
        else "co_move"
    )
    return DependencyMeasurement(
        method="lagged_cross_correlation",
        lag=best_lag,
        score=best_corr,
        direction=direction,
        p_value=None,
        confidence=abs(best_corr),
    )


def granger(
    source: list[float], target: list[float], max_lag: int,
    min_points_factor: int = 3, significance: float = 0.05,
) -> DependencyMeasurement:
    # --- series too short for a reliable test: return a neutral piece of evidence ---
    s = np.asarray(source, dtype=float)
    t = np.asarray(target, dtype=float)
    n = min(s.size, t.size)
    if n < min_points_factor * max_lag:
        return DependencyMeasurement(
            method="granger", lag=0, score=0.0,
            direction="inconclusive", p_value=None, confidence=0.0,
        )
    import contextlib
    import io

    from statsmodels.tools.sm_exceptions import InfeasibleTestError
    from statsmodels.tsa.stattools import grangercausalitytests

    # Column 0 = predicted (target); column 1 = predictor (source).
    data = np.column_stack([t[-n:], s[-n:]])
    # Newer statsmodels prints (verbose deprecated); suppress the chatter.
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = grangercausalitytests(data, maxlag=max_lag)
    except InfeasibleTestError:
        # Perfect VAR fit / singular design: causality undefined. Treat as a
        # maximally significant lead (p≈0) so a perfectly-collinear lead is not
        # reported as "inconclusive".
        return DependencyMeasurement(
            method="granger", lag=1, score=float(-np.log10(1e-12)),
            direction="source_leads", p_value=0.0, confidence=1.0,
        )
    # --- pick the lag with the smallest p-value of the F-test on residuals ---
    best_lag, best_p = 0, 1.0
    for lag, (stats, _) in res.items():
        p = float(stats["ssr_ftest"][1])
        if p < best_p:
            best_p, best_lag = p, lag
    # --- raise an underflowed tiny p of exactly 0 up to the floor (threshold stays a lever) ---
    best_p = max(best_p, _P_VALUE_FLOOR)
    # --- significance threshold -> direction; score = -log10(p) ---
    direction = "source_leads" if best_p < significance else "inconclusive"
    score = float(-np.log10(best_p))  # best_p >= _P_VALUE_FLOOR > 0 (floored above)
    return DependencyMeasurement(
        method="granger", lag=best_lag, score=score,
        direction=direction, p_value=best_p, confidence=1.0 - best_p,
    )


METHODS = {
    "lagged_cross_correlation": lagged_cross_correlation,
    "granger": granger,
}
