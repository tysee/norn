from datetime import datetime, timedelta

from typer.testing import CliRunner

from norn_cli.main import app

runner = CliRunner()


def test_deps_command(ch, tmp_path, monkeypatch):
    ch.command("DROP TABLE IF EXISTS mart_metric")
    ch.command(
        "CREATE TABLE mart_metric (ts DateTime, metric_name String, value Float64, "
        "segment_key String) ENGINE = MergeTree ORDER BY (metric_name, segment_key, ts)"
    )
    import numpy as np

    rng = np.random.default_rng(0)
    base = np.sin(np.linspace(0, 20, 230)) + 0.05 * rng.standard_normal(230)
    start = datetime(2025, 1, 1)
    rows = []
    for i in range(200):
        ts = start + timedelta(days=i)
        rows.append([ts, "log_return", float(base[i + 10]), "symbol=BTCUSDT"])
        rows.append([ts, "log_return", float(base[i + 7]), "symbol=TONUSDT"])
    ch.insert("mart_metric", rows,
              column_names=["ts", "metric_name", "value", "segment_key"])

    # Inject a TestModel agent so no real LLM is called.
    import norn_agent.agent as agentmod
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel
    from norn_agent.contract import DependencyDecision

    monkeypatch.setattr(
        agentmod, "build_agent",
        lambda model=None: Agent(TestModel(), output_type=DependencyDecision,
                                 system_prompt=agentmod.SYSTEM_PROMPT),
    )
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", "http://norn:norn@localhost:8123/norn")

    job = tmp_path / "deps.yml"
    job.write_text("source_segment: symbol=BTCUSDT\ntarget_segment: symbol=TONUSDT\nmax_lag: 10\n")
    result = runner.invoke(app, ["deps", str(job)])
    assert result.exit_code == 0, result.output
    assert "deps run_id=" in result.output
