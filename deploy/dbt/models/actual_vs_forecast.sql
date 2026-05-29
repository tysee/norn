-- actual-vs-forecast: факт из mart_metric vs прогноз из forecast_point.
select
    f.metric_name,
    f.segment_key,
    f.forecast_ts as ts,
    f.y_hat,
    f.p10,
    f.p90,
    m.value      as y_actual,
    abs(m.value - f.y_hat) as error_abs
from {{ source('norn', 'forecast_point') }} as f
left join {{ ref('mart_metric') }} as m
    on  m.metric_name = f.metric_name
    and m.symbol      = f.segment_key
    and m.ts          = f.forecast_ts
