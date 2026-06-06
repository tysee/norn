from pathlib import Path

from norn_agent.contract import (
    DependencyDecision,
    DependencyJob,
    DependencyMeasurement,
    DependencyRelation,
)


def test_job_tunables_default_none_and_resolved(tmp_path: Path, monkeypatch):
    # metric is required (platform is domain-agnostic — no default metric)
    job = DependencyJob(source_segment="symbol=BTCUSDT", target_segment="symbol=TONUSDT", metric="log_return")
    assert job.metric == "log_return"
    assert job.mart == "mart_metric"
    assert job.max_lag is None and job.context_length is None and job.methods is None
    monkeypatch.setenv("NORN_CONFIG_DIR", "config")
    r = job.resolved()
    assert r.max_lag == 10 and r.context_length == 512
    assert r.methods == ["lagged_cross_correlation", "granger"]

    p = tmp_path / "deps.yml"
    p.write_text(
        "source_segment: symbol=BTCUSDT\n"
        "target_segment: symbol=TONUSDT\n"
        "metric: log_return\n"
        "max_lag: 5\n"
    )
    loaded = DependencyJob.from_yaml(p)
    assert loaded.max_lag == 5 and loaded.source_segment == "symbol=BTCUSDT"


def test_measurement_and_decision_shapes():
    m = DependencyMeasurement(
        method="lagged_cross_correlation", lag=3, score=0.8,
        direction="source_leads", p_value=None, confidence=0.8,
    )
    assert m.lag == 3 and m.p_value is None

    dec = DependencyDecision(relations=[
        DependencyRelation(
            source_segment="symbol=BTCUSDT", target_segment="symbol=TONUSDT",
            metric_name="log_return", lag=3, direction="source_leads",
            is_real=True, confidence=0.7, explanation="BTC leads TON", caveats="corr != causation",
        )
    ])
    assert dec.relations[0].is_real is True
