from datetime import datetime
from pathlib import Path

from norn_core.contract import ForecastJob, ForecastPoint, Grain


def test_forecast_job_tunables_default_none():
    job = ForecastJob(metric="sales", source="analytics.mart_metric")
    assert job.grain is Grain.daily
    assert job.horizon is None
    assert job.context_length is None
    assert job.seasonality is None
    assert job.dimensions == []
    assert job.model == "baseline-seasonal-naive"


def test_forecast_job_resolved_fills_from_settings(monkeypatch):
    import os
    os.environ["NORN_CONFIG_DIR"] = "config"
    job = ForecastJob(metric="sales", source="t").resolved()
    assert job.horizon == 30 and job.context_length == 512 and job.seasonality == 7


def test_forecast_job_explicit_overrides_config():
    job = ForecastJob(metric="sales", source="t", horizon=7).resolved()
    assert job.horizon == 7  # explicit wins


def test_forecast_job_from_yaml(tmp_path: Path):
    p = tmp_path / "job.yml"
    p.write_text(
        "metric: sales\n"
        "source: analytics.mart_metric\n"
        "grain: daily\n"
        "dimensions: [region]\n"
        "horizon: 7\n"
        "seasonality: 7\n"
    )
    job = ForecastJob.from_yaml(p)
    assert job.metric == "sales"
    assert job.dimensions == ["region"]
    assert job.horizon == 7


def test_forecast_point_roundtrip():
    pt = ForecastPoint(
        forecast_run_id="run-1",
        metric_name="sales",
        segment_key="region=eu",
        forecast_ts=datetime(2026, 6, 1),
        horizon_step=1,
        y_hat=10.0,
        p10=8.0,
        p50=10.0,
        p90=12.0,
        model_name="baseline-seasonal-naive",
        created_at=datetime(2026, 5, 29),
    )
    assert pt.y_actual is None
    assert pt.p90 > pt.p10
