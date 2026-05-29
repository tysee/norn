CREATE TABLE IF NOT EXISTS forecast_run (
    forecast_run_id String,
    forecast_job    String,
    status          String,
    model_name      String,
    model_version   String,
    started_at      DateTime,
    finished_at     Nullable(DateTime),
    segments_total  UInt32,
    segments_skipped UInt32,
    error           Nullable(String)
) ENGINE = MergeTree ORDER BY (forecast_run_id, started_at);

CREATE TABLE IF NOT EXISTS forecast_point (
    forecast_run_id String,
    metric_name     String,
    segment_key     String,
    forecast_ts     DateTime,
    horizon_step    UInt16,
    y_hat           Float64,
    p10             Float64,
    p50             Float64,
    p90             Float64,
    y_actual        Nullable(Float64),
    model_name      String,
    created_at      DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (metric_name, segment_key, forecast_ts);

CREATE TABLE IF NOT EXISTS forecast_segment (
    forecast_run_id String,
    metric_name     String,
    segment_key     String,
    n_points        UInt32,
    is_sparse       UInt8,
    wape            Float64,
    mape            Float64,
    coverage        Float64,
    bias            Float64,
    created_at      DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (metric_name, segment_key, forecast_run_id);
