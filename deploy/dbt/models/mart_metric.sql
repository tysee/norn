-- Long metric store in the platform contract shape (ts, metric_name, value,
-- segment_key). Empty until raw_metric is populated by your instance.
select
    ts,
    metric_name,
    value,
    segment_key
from {{ source('norn', 'raw_metric') }}
