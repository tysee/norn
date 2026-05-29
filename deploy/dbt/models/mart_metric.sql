-- Метрика из сырых свечей. Пустая, пока raw_candles не наполнят отдельно.
select
    ts,
    symbol,
    metric_name,
    value
from {{ source('norn', 'raw_candles') }}
