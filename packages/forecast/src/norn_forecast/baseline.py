"""
packages/forecast/src/norn_forecast/baseline.py

Baseline forecaster for the norn platform: seasonal-naive with intervals from
configurable quantiles (normal approximation, statistics.NormalDist).
It repeats the value from the previous seasonal cycle as the point forecast and
estimates the interval width from the spread of period-over-period residuals. A
lightweight stub without torch — it keeps the whole forecast pipeline (runner,
calibration, MCP) working until the heavy TimesFM model is wired in, and serves
as a cheap reference baseline in backtests.

Functions:
- seasonal_naive_forecast(values, horizon, seasonality) -> list[dict] —
  point forecast y_hat=p50 and p10/p90 bounds for each horizon step;
  uncertainty grows ~sqrt(number of seasonal cycles ahead).
"""
from __future__ import annotations

import statistics

import numpy as np

# Relative threshold: a profile residual std below it is treated as an exact zero
# (suppresses float noise from the linear fit on a perfectly seasonal series).
_ZERO_EPS_REL = 1e-9


def seasonal_naive_forecast(
    values: list[float], horizon: int, seasonality: int = 7,
    quantiles: tuple[float, float, float] = (0.1, 0.5, 0.9),
) -> list[dict]:
    # --- input validation ---
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        raise ValueError("values must be non-empty")

    # z-multipliers from the configurable quantiles (normal approximation)
    z_low = statistics.NormalDist().inv_cdf(quantiles[0])
    z_high = statistics.NormalDist().inv_cdf(quantiles[2])

    # --- estimate the uncertainty scale (sigma) ---
    if n > seasonality:
        # Period-over-period residuals capture how much each seasonal cycle
        # deviates from the previous one.
        resid = arr[seasonality:] - arr[:-seasonality]
        sigma = float(np.std(resid)) if resid.size else 0.0
        # For a perfectly periodic series the seasonal residuals are all zero,
        # so fall back to the dispersion of the seasonal profile around its own
        # linear trend. This is zero only for a clean linear ramp within the
        # cycle and positive for any irregular seasonal shape.
        if sigma == 0.0:
            cycle = arr[-seasonality:]
            x = np.arange(cycle.size, dtype=float)
            fitted = np.polynomial.Polynomial.fit(x, cycle, deg=1)
            profile_resid = cycle - fitted(x)
            sigma = float(np.std(profile_resid))
            # Snap floating-point noise from the linear fit to exactly zero so a
            # perfectly linear seasonal ramp yields a zero-width interval.
            scale = float(np.max(np.abs(cycle))) or 1.0
            if sigma < _ZERO_EPS_REL * scale:
                sigma = 0.0
    else:
        sigma = 0.0  # too short to estimate seasonal residuals

    # --- assemble the forecast rows over the horizon ---
    out: list[dict] = []
    for h in range(1, horizon + 1):
        if n >= seasonality:
            idx = n - seasonality + ((h - 1) % seasonality)
            base = float(arr[idx])
        else:
            base = float(arr[-1])  # fallback: carry last value forward
        cycles = (h - 1) // seasonality + 1
        spread = sigma * np.sqrt(cycles)
        out.append(
            {
                "horizon_step": h,
                "y_hat": base,
                "p50": base,
                "p10": base + z_low * spread,   # z_low < 0
                "p90": base + z_high * spread,
            }
        )
    return out
