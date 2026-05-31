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


def test_forecast_segment_columns(ch):
    cols = {row[0] for row in ch.query("DESCRIBE TABLE forecast_segment").result_rows}
    assert {
        "forecast_run_id", "metric_name", "segment_key", "n_points",
        "is_sparse", "wape", "mape", "coverage", "bias", "created_at",
    } <= cols


def test_required_tables_lists_contract_tables():
    from norn_integration.schema import required_tables
    assert set(required_tables()) == {
        "forecast_run", "forecast_point", "forecast_segment",
        "metric_dependency", "dependency_explanation",
    }


def test_prepare_schema_true_creates(ch):
    from norn_integration.schema import prepare_schema, required_tables
    for t in required_tables():
        ch.command(f"DROP TABLE IF EXISTS {t}")
    prepare_schema(ch, True)
    for t in required_tables():
        assert str(ch.command(f"EXISTS TABLE {t}")).strip() in ("1", "True")


def test_prepare_schema_false_ok_when_present(ch):
    from norn_integration.schema import apply_schema, prepare_schema
    apply_schema(ch)                       # ensure present
    prepare_schema(ch, False)              # must not raise, must not DDL


def test_prepare_schema_false_raises_when_missing(ch):
    from norn_integration.schema import apply_schema, prepare_schema, ContractSchemaMissing
    import pytest
    apply_schema(ch)
    ch.command("DROP TABLE IF EXISTS forecast_segment")
    try:
        with pytest.raises(ContractSchemaMissing) as e:
            prepare_schema(ch, False)
        assert "forecast_segment" in str(e.value)
    finally:
        apply_schema(ch)                   # restore for other tests


def test_schema_sql_partition_and_ttl():
    from norn_integration.schema import schema_sql, required_tables
    sql12 = schema_sql(12)
    assert sql12.count("PARTITION BY toYYYYMM(created_at)") == 5
    assert sql12.count("TTL created_at + INTERVAL 12 MONTH") == 5
    sql0 = schema_sql(0)
    assert sql0.count("PARTITION BY toYYYYMM(created_at)") == 5
    assert "TTL" not in sql0
    assert "{RETENTION_MONTHS_TTL}" not in sql12 and "{RETENTION_MONTHS_TTL}" not in sql0
    # table names unaffected by retention
    assert set(required_tables()) == {
        "forecast_run", "forecast_point", "forecast_segment",
        "metric_dependency", "dependency_explanation"}


def test_apply_schema_creates_partitioned(ch):
    from norn_integration.schema import apply_schema, required_tables
    for t in required_tables():
        ch.command(f"DROP TABLE IF EXISTS {t}")
    apply_schema(ch, 12)
    ddl = ch.query("SHOW CREATE TABLE forecast_point").result_rows[0][0]
    assert "toYYYYMM(created_at)" in ddl and "TTL" in ddl
    apply_schema(ch, 12)  # idempotent re-apply must not raise
