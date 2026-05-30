def test_dependency_tables(ch):
    tables = {row[0] for row in ch.query("SHOW TABLES").result_rows}
    assert {"metric_dependency", "dependency_explanation"} <= tables
    dep_cols = {r[0] for r in ch.query("DESCRIBE TABLE metric_dependency").result_rows}
    assert {"analysis_run_id", "source_segment", "target_segment", "method",
            "lag", "score", "direction", "p_value", "confidence"} <= dep_cols
    exp_cols = {r[0] for r in ch.query("DESCRIBE TABLE dependency_explanation").result_rows}
    assert {"analysis_run_id", "source_segment", "target_segment", "lag",
            "is_real", "confidence", "explanation", "caveats", "llm_model"} <= exp_cols
