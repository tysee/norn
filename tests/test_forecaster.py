import json

import httpx

from norn_core.contract import ForecastJob
from norn_forecast.baseline import seasonal_naive_forecast
from norn_forecast.forecaster import (
    BaselineForecaster,
    LogTransformForecaster,
    TimesFMForecaster,
    make_forecaster,
)


class _EchoMean:
    """Base forecaster returning the arithmetic mean of its inputs as y_hat/p50 and
    mean±1 as the band — lets us assert the log/exp round-trip exactly."""

    def forecast(self, values, horizon, covariates=None):
        m = sum(values) / len(values)
        return [
            {"horizon_step": h, "y_hat": m, "p10": m - 1, "p50": m, "p90": m + 1}
            for h in range(1, horizon + 1)
        ]


def test_log_transform_forecaster_logs_in_exps_out():
    import math

    values = [math.e**k for k in range(1, 5)]  # log -> [1,2,3,4], mean 2.5
    rows = LogTransformForecaster(_EchoMean()).forecast(values, horizon=2)
    assert rows[0]["y_hat"] == math.exp(2.5)
    assert rows[0]["p10"] == math.exp(1.5)
    assert rows[0]["p90"] == math.exp(3.5)


def test_log_transform_forecaster_falls_back_on_nonpositive():
    # A non-positive value can't be log'd -> pass through the base forecaster as-is.
    rows = LogTransformForecaster(_EchoMean()).forecast([1.0, -2.0, 3.0], horizon=1)
    assert rows[0]["y_hat"] == (1.0 - 2.0 + 3.0) / 3  # raw mean, not exp'd


def test_make_forecaster_wraps_in_log_when_requested():
    job = ForecastJob(metric="v", source="t", model="timesfm-2.5", transform="log")
    f = make_forecaster(job, timesfm_url="http://worker:9100")
    assert isinstance(f, LogTransformForecaster)


def test_baseline_forecaster_matches_function():
    values = [float(v) for v in [10, 12, 9, 11, 13, 8, 10] * 4]
    got = BaselineForecaster(seasonality=7).forecast(values, horizon=7)
    expected = seasonal_naive_forecast(values, horizon=7, seasonality=7)
    assert got == expected


def test_make_forecaster_defaults_to_baseline():
    job = ForecastJob(metric="v", source="t", seasonality=7)
    assert isinstance(make_forecaster(job), BaselineForecaster)


def test_make_forecaster_selects_timesfm():
    job = ForecastJob(metric="v", source="t", model="timesfm-2.5")
    f = make_forecaster(job, timesfm_url="http://worker:9100")
    assert isinstance(f, TimesFMForecaster)


def test_timesfm_forecaster_posts_and_parses():
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert body["horizon"] == 3
        assert body["values"] == [1.0, 2.0, 3.0]
        rows = [
            {"horizon_step": h, "y_hat": 2.0, "p10": 1.0, "p50": 2.0, "p90": 3.0}
            for h in range(1, 4)
        ]
        return httpx.Response(200, json={"rows": rows})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    f = TimesFMForecaster("http://worker:9100", client=client)
    rows = f.forecast([1.0, 2.0, 3.0], horizon=3)
    assert len(rows) == 3
    assert rows[0]["p50"] == 2.0 and rows[0]["p90"] >= rows[0]["p10"]


def test_timesfm_forecaster_url_from_settings(monkeypatch):
    import norn_forecast.forecaster as fc
    from norn_core.contract import ForecastJob

    monkeypatch.delenv("NORN_TIMESFM_URL", raising=False)
    monkeypatch.setenv("NORN_CONFIG_DIR", "config")
    monkeypatch.setenv("NORN_FORECAST_TIMESFM__WORKER_URL", "http://worker-from-settings:9100")
    job = ForecastJob(metric="close", source="t", model="timesfm-2.5")
    f = fc.make_forecaster(job)
    assert isinstance(f, fc.TimesFMForecaster)
    assert f._base == "http://worker-from-settings:9100"


def test_timesfm_forecaster_quantiles_from_settings(monkeypatch):
    import json
    import httpx
    import norn_forecast.forecaster as fc
    from norn_core.contract import ForecastJob

    monkeypatch.setenv("NORN_CONFIG_DIR", "config")
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"rows": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    job = ForecastJob(metric="close", source="t", model="timesfm-2.5")
    f = fc.make_forecaster(job)
    f._client = client  # inject mock transport
    f.forecast([1.0, 2.0, 3.0], horizon=1)
    assert seen["quantiles"] == [0.1, 0.5, 0.9]  # from config, not a hardcoded literal


def test_baseline_ignores_covariates():
    from norn_forecast.forecaster import BaselineForecaster
    vals = [float(v) for v in [10, 12, 9, 11, 13, 8, 10] * 4]
    f = BaselineForecaster(seasonality=7)
    assert f.forecast(vals, 5, covariates={"x": [0.0] * 100}) == f.forecast(vals, 5)


def test_timesfm_sends_covariates_only_when_present():
    import json, httpx
    from norn_forecast.forecaster import TimesFMForecaster
    seen = {}
    def handler(req):
        seen.clear(); seen.update(json.loads(req.content))
        return httpx.Response(200, json={"rows": []})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    f = TimesFMForecaster("http://w", client=client)
    f.forecast([1.0, 2.0, 3.0], 1)                       # plain
    assert seen.get("dynamic_numerical_covariates", {}) == {}
    f.forecast([1.0, 2.0, 3.0], 1, covariates={"btc": [1.0, 2.0, 3.0, 4.0]})
    assert seen["dynamic_numerical_covariates"] == {"btc": [1.0, 2.0, 3.0, 4.0]}


def test_log_transform_rejects_covariates_on_log_path():
    # raw-scale covariates with a log-space target would silently corrupt the
    # XReg regression -> explicit failure instead (review F-26)
    import pytest

    with pytest.raises(ValueError, match="log"):
        LogTransformForecaster(_EchoMean()).forecast(
            [1.0, 2.0, 3.0], horizon=1, covariates={"x": [1.0] * 4}
        )


def test_log_transform_close_delegates_to_base():
    class _Closable(_EchoMean):
        closed = False

        def close(self):
            self.closed = True

    base = _Closable()
    LogTransformForecaster(base).close()
    assert base.closed is True
