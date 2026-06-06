-- actual-vs-forecast: actual from mart_metric vs forecast from forecast_point.
select
    f.metric_name,
    f.segment_key,
    f.forecast_ts as ts,
    f.y_hat,
    f.p10,
    f.p90,
    -- ClickHouse LEFT JOIN fills unmatched rows with type defaults (0), not
    -- NULL (join_use_nulls=0). Future forecast points have no actual yet —
    -- detect the no-match case via the String default ('') and emit NULLs so
    -- charts show a gap instead of a line dropping to zero.
    if(m.metric_name = '', cast(null as Nullable(Float64)), m.value) as y_actual,
    if(m.metric_name = '', cast(null as Nullable(Float64)), abs(m.value - f.y_hat)) as error_abs
from {{ source('norn', 'forecast_point') }} as f
left join {{ ref('mart_metric') }} as m
    on  m.metric_name = f.metric_name
    and m.segment_key = f.segment_key
    and m.ts          = f.forecast_ts
