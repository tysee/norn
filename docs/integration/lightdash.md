# Integrating norn with Lightdash

> **Platform invariant.** norn is a vendor-neutral, domain-agnostic forecasting
> platform: it writes multi-segment metric forecasts to your warehouse via a
> generic contract (`forecast_point` / `forecast_segment`). The platform ships no
> built-in domain defaults (no metrics, dimensions, dashboards, or model choices);
> domain specifics live in an instance repo (the open-source example instance is
> `norn-ett-instance`, attached as a submodule at `instances/ett`). Any concrete
> metric or segment below is a **labeled example**, not a platform requirement.

norn does **not** ship or fork Lightdash. norn writes forecast rows to ClickHouse
(`forecast_point`); you point your existing Lightdash project at those tables.

## Steps

1. In your dbt project add a model `actual_vs_forecast` that joins your metric mart
   to `forecast_point` on `(metric_name, segment_key, ts = forecast_ts)`. The metric
   mart and `metric_name` values are domain-specific and come from your instance
   (e.g. the ETT example instance exposes the metric `ot` — oil temperature — over
   segments like `dataset=ETTh1|feature=ot`, joining the `mart_metric`/`fct_ot`
   marts to `forecast_point`; see `instances/ett/dbt/models/actual_vs_forecast.sql`).
2. Expose `y_actual`, `y_hat`, `p10`, `p90`, `error_abs` as Lightdash metrics.
3. Build a chart: actual line + forecast line + p10/p90 band per segment.

## Connection

Lightdash connects to the **same ClickHouse** norn writes to. norn never touches
Lightdash's Postgres. Domain-specific dashboards live in the corresponding instance
repo (e.g. the ETT example instance, `norn-ett-instance` at `instances/ett`), not in
this platform repo.
