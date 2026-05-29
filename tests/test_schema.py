def test_apply_schema_is_idempotent(ch):
    from norn_integration.schema import apply_schema

    apply_schema(ch)  # second apply must not raise
    tables = {row[0] for row in ch.query("SHOW TABLES").result_rows}
    assert {"forecast_run", "forecast_point"} <= tables


def test_forecast_point_columns(ch):
    cols = {row[0] for row in ch.query("DESCRIBE TABLE forecast_point").result_rows}
    assert {
        "forecast_run_id", "metric_name", "segment_key", "forecast_ts",
        "horizon_step", "y_hat", "p10", "p50", "p90", "y_actual",
        "model_name", "created_at",
    } <= cols
