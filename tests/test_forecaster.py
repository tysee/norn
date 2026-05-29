import json

import httpx

from norn_core.contract import ForecastJob
from norn_forecast.baseline import seasonal_naive_forecast
from norn_forecast.forecaster import (
    BaselineForecaster,
    TimesFMForecaster,
    make_forecaster,
)


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
