from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    monkeypatch.setenv("NORN_CONFIG_DIR", "config")
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


def test_forecast_job_covariates_default_empty():
    job = ForecastJob(metric="close", source="t")
    assert job.covariates == [] and job.use_dependencies is False  # plain forecast is the default


def test_forecast_job_covariate_spec():
    from norn_core.contract import CovariateSpec
    job = ForecastJob(metric="close", source="t",
                      covariates=[{"metric": "log_return", "segment": "symbol=BTCUSDT", "lag": 3}])
    assert isinstance(job.covariates[0], CovariateSpec)
    assert job.covariates[0].lag == 3 and job.covariates[0].segment == "symbol=BTCUSDT"


def test_forecast_job_filter_default_and_yaml(tmp_path):
    from norn_core.contract import ForecastJob
    # default: empty
    j = ForecastJob(metric="m", source="s", horizon=3)
    assert j.filter == {}
    # from_yaml parses a filter mapping
    p = tmp_path / "job.yml"
    p.write_text("metric: close\nsource: fct_close\ndimensions: [symbol]\n"
                 "filter: {symbol: BTCUSDT}\nhorizon: 30\n")
    j2 = ForecastJob.from_yaml(str(p))
    assert j2.filter == {"symbol": "BTCUSDT"}


def test_forecast_point_roundtrip():
    pt = ForecastPoint(
        forecast_run_id="run-1",
        metric_name="sales",
        segment_key="region=eu",
        forecast_ts=datetime(2026, 6, 1, tzinfo=UTC),
        horizon_step=1,
        y_hat=10.0,
        p10=8.0,
        p50=10.0,
        p90=12.0,
        model_name="baseline-seasonal-naive",
        created_at=datetime(2026, 5, 29, tzinfo=UTC),
    )
    assert pt.y_actual is None
    assert pt.p90 > pt.p10


def test_forecast_point_rejects_naive_datetimes():
    # naive datetimes get shifted by the machine's UTC offset on ClickHouse
    # insert — the contract boundary must refuse them outright
    with pytest.raises(ValidationError):
        ForecastPoint(
            forecast_run_id="run-1", metric_name="sales", segment_key="region=eu",
            forecast_ts=datetime(2026, 6, 1),  # naive
            horizon_step=1, y_hat=10.0, p10=8.0, p50=10.0, p90=12.0,
            model_name="baseline-seasonal-naive",
            created_at=datetime(2026, 5, 29, tzinfo=UTC),
        )


def test_forecast_job_rejects_zero_seasonality():
    # seasonality=0 would divide by zero inside the baseline's horizon loop
    with pytest.raises(ValidationError):
        ForecastJob(metric="sales", source="t", seasonality=0)
