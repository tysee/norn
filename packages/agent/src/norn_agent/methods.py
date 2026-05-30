"""
packages/agent/src/norn_agent/methods.py

Статистические методы-улики для анализа зависимостей (на стационарных рядах).

Методы:
- lagged_cross_correlation(source, target, max_lag) -> DependencyMeasurement.
- granger(source, target, max_lag) -> DependencyMeasurement.
- METHODS — реестр {name: callable} для выбора по DependencyJob.methods.
"""
from __future__ import annotations

import numpy as np

from norn_agent.contract import DependencyMeasurement


def lagged_cross_correlation(
    source: list[float], target: list[float], max_lag: int
) -> DependencyMeasurement:
    s = np.asarray(source, dtype=float)
    t = np.asarray(target, dtype=float)
    n = min(s.size, t.size)
    s, t = s[-n:], t[-n:]
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
    source: list[float], target: list[float], max_lag: int, min_points_factor: int = 3
) -> DependencyMeasurement:
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
    best_lag, best_p = 0, 1.0
    for lag, (stats, _) in res.items():
        p = float(stats["ssr_ftest"][1])
        if p < best_p:
            best_p, best_lag = p, lag
    direction = "source_leads" if best_p < 0.05 else "inconclusive"
    score = float(-np.log10(best_p)) if best_p > 0 else 0.0
    return DependencyMeasurement(
        method="granger", lag=best_lag, score=score,
        direction=direction, p_value=best_p, confidence=1.0 - best_p,
    )


METHODS = {
    "lagged_cross_correlation": lagged_cross_correlation,
    "granger": granger,
}
