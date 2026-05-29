# Integrating norn with Lightdash

norn does **not** ship or fork Lightdash. norn writes forecast rows to ClickHouse
(`forecast_point`); you point your existing Lightdash project at those tables.

## Steps
1. In your dbt project add a model `actual_vs_forecast` that joins your metric mart
   to `forecast_point` on `(metric_name, segment_key, ts = forecast_ts)`.
2. Expose `y_actual`, `y_hat`, `p10`, `p90`, `error_abs` as Lightdash metrics.
3. Build a chart: actual line + forecast line + p10/p90 band per segment.

## Connection
Lightdash connects to the **same ClickHouse** norn writes to. norn never touches
Lightdash's Postgres. Crypto-specific dashboards live in `norn-crypto-instance`,
not in this repo.
