from __future__ import annotations

import numpy as np

Z_80 = 1.2816  # z-score for an 80% interval (p10..p90)


def seasonal_naive_forecast(
    values: list[float], horizon: int, seasonality: int = 7
) -> list[dict]:
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        raise ValueError("values must be non-empty")

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
            coef = np.polyfit(x, cycle, 1)
            profile_resid = cycle - np.polyval(coef, x)
            sigma = float(np.std(profile_resid))
            # Snap floating-point noise from the linear fit to exactly zero so a
            # perfectly linear seasonal ramp yields a zero-width interval.
            scale = float(np.max(np.abs(cycle))) or 1.0
            if sigma < 1e-9 * scale:
                sigma = 0.0
    else:
        sigma = 0.0  # too short to estimate seasonal residuals

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
                "p10": base - Z_80 * spread,
                "p90": base + Z_80 * spread,
            }
        )
    return out
