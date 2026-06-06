-- Metric derived from raw candles. Empty until raw_candles is populated separately.
select
    ts,
    symbol,
    metric_name,
    value
from {{ source('norn', 'raw_candles') }}
