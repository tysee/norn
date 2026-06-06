-- fct_orders: orders time series per region, shaped for the norn forecast layer.
--
-- This view filters mart_metric to orders rows and re-formats segment_key
-- for the forecast jobs (forecasts/orders_baseline.yml, orders_timesfm.yml).
-- Forecast job config: source=fct_orders, metric=orders, dimensions=[region].
--
-- Platform contract columns (required by the forecast worker):
--   ts           DateTime  — observation timestamp (UTC)
--   region       String    — dimension column matching `dimensions: [region]`
--   orders       Float64   — daily order count (named after metric: orders in the job YAML)
--   segment_key  String    — "region=<value>" (constructed from dimensions)
--
-- The forecast runner queries: SELECT ts, orders AS val FROM fct_orders WHERE region = ...
-- The metric column must be named after `metric` in the job YAML (here: orders).
--
-- segment_key here uses only `region` (not `type`) because the forecast job
-- declares `dimensions: [region]`.  The dependency job uses mart_metric directly
-- where segment_key also encodes `type`.

select
    ts,
    region,
    value                      as orders,
    concat('region=', region)  as segment_key
from {{ ref('mart_metric') }}
where type = 'orders'
