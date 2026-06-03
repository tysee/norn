# Forecast methodology & evaluation

How the platform forecasts, how we measure it honestly, and what the crypto
instance (daily BTC/TON close) taught us.

## How a forecast is built

`norn forecast <job.yml>` (see `runner.py`):

1. **Segments** — `SELECT DISTINCT <dimensions>` from the job's `source`
   (e.g. `fct_close` by `symbol`).
2. **Context** — the last `context_length` points of the `metric`, chronological.
3. **Forecast** — `forecaster.forecast(values, horizon)` returns, per step,
   `y_hat` + quantiles `p10/p50/p90`. Models: `timesfm-2.5` (TimesFM HTTP worker)
   or `baseline-seasonal-naive`.
4. **Transform** (optional, `transform: log`) — a wrapper logs the (positive)
   target on the way in and exponentiates the point and every quantile on the way
   out. Suits prices: no negative forecasts, multiplicatively symmetric intervals.
5. **Write** — future points to `forecast_point` (`forecast_ts` is tz-aware UTC),
   run summary to `forecast_run`.

`norn calibrate` runs a **rolling-origin backtest**: it rewinds the series by
`horizon` `n_cutoffs` times, forecasts from the past only, and compares to the
held-out truth. Aggregates land in `forecast_segment`; the per-point
`(forecast, actual)` pairs are persisted to `forecast_point` (tagged
`model_name '<model> (backtest)'`) for "past forecast vs actual" charts.

## Metrics

- **MAPE / WAPE** — point accuracy (lower = better).
- **Coverage** — share of actuals inside the 80% `p10..p90` interval (target ≈ 80;
  >80 = intervals too wide, <80 = too narrow).
- **Pinball loss** — quantile quality.
- **Bias** — `mean(forecast − actual)`; <0 = forecasts run low.

## What the crypto backtest showed (daily BTC/TON close)

- **Use enough folds.** With `n_cutoffs: 3` the metrics were noisy and
  over-optimistic (e.g. BTC MAPE 5.4%, coverage 98.9%). At `n_cutoffs: 8` the
  honest numbers are ~9% (BTC) / ~14% (TON) MAPE. Default is now **8**.
- **Skill is concentrated in the first ~7 days.** MAPE roughly doubles by day 30
  (BTC 4.8% → 11.8%; TON 8.8% → 17.4%). At h1–7 TimesFM beats seasonal-naive by
  ~30%; by h30 it converges to naive. **Trust short-horizon point forecasts;
  treat the long tail as a scenario, not a number.** (See the "MAPE by horizon"
  dashboard tile.)
- **Log-space helps, modestly.** On a matched 8-fold A/B, `transform: log` lowers
  short-horizon MAPE (BTC −3.7%, TON −2.1% at h1–7) and is neutral-to-better on
  full-horizon MAPE — now the default for the crypto close jobs.
- **Intervals are regime-dependent.** Per-fold coverage swings wildly
  (sd ≈ 30%): in trends/crashes actuals blow through the band, in calm periods
  they sit well inside. Static interval rescaling (conformal offset, single
  volatility scale) did **not** robustly hit 80% out-of-sample — the dominant
  effect is non-stationarity. Averaged over folds the model's own intervals are
  reasonable (BTC ~80%, TON ~88%).

## Recommendations

- Report and act on **short horizons**; surface the full horizon as an uncertainty
  cone, not a point.
- Keep `transform: log` for positive price series.
- Always read MAPE **alongside the naive baseline** — the model only earns its
  keep where it beats naive (short horizon here).
