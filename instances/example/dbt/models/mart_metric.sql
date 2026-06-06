-- mart_metric: long-format store for the norn platform.
--
-- Platform contract columns (required by the dependency-analysis agent):
--   ts           DateTime  — observation timestamp (UTC)
--   metric_name  String    — name of the metric (same value for all rows in this mart)
--   value        Float64   — observed value
--   segment_key  String    — canonical segment identifier, e.g. "region=north|type=orders"
--
-- This template unpivots two columns (visits, orders) from raw_example into the
-- long format.  The `type` dimension encodes which column each row holds.
-- The dependency job (forecasts/deps/visits_orders.yml) reads this mart to test
-- whether visits leads orders within the same region.
--
-- TODO: replace the SELECT below with a query over your actual raw table.
-- Key rules:
--   1. ts must be tz-aware UTC (or cast to UTC) — naive datetimes can skew inserts.
--   2. segment_key must be deterministic and stable across runs.
--   3. metric_name must match the `metric` field in your dependency job YAMLs.
--   4. If your raw source has duplicates, deduplicate with FINAL (ReplacingMergeTree)
--      or a ROW_NUMBER() window before unpivoting.

with base as (
    -- TODO: replace raw_example with your actual source table reference.
    -- Remove or adjust the FINAL clause based on your table engine.
    select
        ts,
        region,
        visits,
        orders
    from {{ source('raw_example', 'raw_example') }} final
)
select
    ts,
    region,
    type,
    'count'                                            as metric_name,
    value,
    concat('region=', region, '|type=', type)          as segment_key
from base
-- Unpivot visits and orders into rows.
-- TODO: if your ClickHouse version supports UNPIVOT syntax, prefer it.
array join
    ['visits', 'orders'] as type,
    [visits,   orders  ] as value
