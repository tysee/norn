# Forecast methodology & evaluation

How the platform forecasts, how we measure it honestly, and how to read the
results on the ETT example instance (Electricity Transformer Temperature —
hourly oil temperature `ot` per dataset).

## How a forecast is built

`norn forecast <job.yml>` (see `runner.py`):

1. **Segments** — `SELECT DISTINCT <dimensions>` from the job's `source`
   (e.g. `fct_ot` by `[dataset, feature]`, yielding segment keys like
   `dataset=ETTh1|feature=ot`).
2. **Context** — the last `context_length` points of the `metric`, chronological.
3. **Forecast** — `forecaster.forecast(values, horizon)` returns, per step,
   `y_hat` + quantiles `p10/p50/p90`. Models: `timesfm-2.5` (TimesFM HTTP worker)
   or `baseline-seasonal-naive`.
4. **Transform** (optional, `transform: log`) — a wrapper logs the (positive)
   target on the way in and exponentiates the point and every quantile on the way
   out. Suits positive multiplicative series (e.g. prices): no negative forecasts,
   multiplicatively symmetric intervals. It falls back to the base forecaster if
   any value is `<= 0`, so it is **not** used for the ETT `ot` jobs (oil
   temperature can be zero or negative) — those run `transform: none`.
5. **Write** — future points to `forecast_point` (`forecast_ts` is tz-aware UTC),
   run summary to `forecast_run`. If the chosen forecaster fails (e.g. the TimesFM
   worker is unreachable) the run is written with `status='failed'` and a clear
   error is raised — there is no silent fallback to the baseline.

`norn calibrate` runs a **rolling-origin backtest**: it rewinds the series by
`horizon` `n_cutoffs` times, forecasts from the past only, and compares to the
held-out truth. Aggregates land in `forecast_segment`; the per-point
`(forecast, actual)` pairs are persisted to `forecast_point` (tagged
`model_name '<model> (backtest)'`, with a `+xreg` marker when covariates are
active) for "past forecast vs actual" charts.

## Metrics

`backtest_metrics` (`calibration.py`) reports, per segment:

- **MAPE / WAPE** — point accuracy (lower = better). `mape = mean|.|` over
  nonzero actuals; `wape = sum|actual − y_hat| / sum|actual|`.
- **Coverage** — share of actuals inside the 80% `p10..p90` interval (target ≈ 80;
  >80 = intervals too wide, <80 = too narrow).
- **Bias** — `mean(y_hat − actual)`; <0 = forecasts run low.

These are surfaced per model family (baseline / timesfm / timesfm+xreg)
side-by-side in the `calibration` mart of the ETT instance, pre-scaled to percent.

## How the baseline sets its intervals

`baseline-seasonal-naive` is a deliberately cheap reference (`baseline.py`):

- **Point** — repeats the value from the previous seasonal cycle
  (`seasonality` steps back; 24 for the hourly ETT jobs, default 7).
- **Intervals** — a normal approximation: `z`-multipliers from the configured
  quantiles (`statistics.NormalDist().inv_cdf`) times a `sigma`. `sigma` is the
  std of period-over-period residuals (`arr[seasonality:] − arr[:-seasonality]`);
  for a perfectly periodic series it falls back to the dispersion of the seasonal
  profile around its own linear trend.
- **Width growth** — the interval widens `~sqrt(cycles ahead)`, so uncertainty
  accumulates with horizon.

The baseline ignores covariates; it is the honest "did the model earn its keep?"
yardstick for every TimesFM run.

## Reading a calibration on ETT

The ETT instance ships three calibratable jobs for the same target so they sit
side-by-side in the `calibration` mart:

- `ot_baseline.yml` — `baseline-seasonal-naive` (runs without the TimesFM worker).
- `ot_timesfm.yml` — `timesfm-2.5` (needs the worker; fails explicitly if it is
  unreachable).
- `ot_timesfm_xreg.yml` — `timesfm-2.5` with `use_dependencies: true`, attaching
  the confirmed leading load features (HUFL/MUFL/… → OT) as TimesFM XReg
  covariates. Run `norn deps` over each file in `forecasts/deps/` first
  (`for f in forecasts/deps/*.yml; do norn deps "$f"; done` — the command takes
  one job at a time), and set
  `NORN_FORECAST_COVARIATES__HORIZON_POLICY=ffill` (under the strict default a
  lead whose lag `<` horizon is dropped).

What to look at when comparing them:

- **Use enough folds.** With a small `n_cutoffs` the metrics are noisy and
  over-optimistic (one lucky window dominates). The config default is **8**
  (`forecast.yml` → `calibration.n_cutoffs`), which averages several rolling
  origins for an honest number.
- **Compare against the baseline.** A TimesFM run only earns its keep where it
  beats `baseline-seasonal-naive` on the same segment and folds. Read the model
  rows next to each other in the `calibration` mart.
- **Skill is concentrated at short horizons.** Point error grows with the
  forecast step; trust the early steps and treat the long tail as a scenario, not
  a number. Use the per-step `backtest_point` mart to see where accuracy decays.
- **Intervals are regime-dependent.** Per-fold coverage swings with the regime:
  during sharp swings actuals blow through the band, in calm stretches they sit
  well inside. Read coverage **averaged over folds**, and remember a single
  static rescale rarely hits 80% out-of-sample when the series is non-stationary.

## Recommendations

- Report and act on **short horizons**; surface the full horizon as an uncertainty
  cone, not a point.
- Use `transform: log` for **positive** multiplicative series (prices); leave it
  `none` for signed series like ETT oil temperature.
- Always read MAPE **alongside the naive baseline** — the model only earns its
  keep where it beats naive.
