from fastapi.testclient import TestClient

from norn_forecast.timesfm_worker import create_app


class FakeModel:
    def predict(self, values, horizon, quantiles, dynamic_numerical_covariates=None):
        self.last_cov = dynamic_numerical_covariates
        return [
            {"horizon_step": h, "y_hat": 1.0, "p10": 0.5, "p50": 1.0, "p90": 1.5}
            for h in range(1, horizon + 1)
        ]


def test_health():
    client = TestClient(create_app(FakeModel()))
    assert client.get("/health").json() == {"status": "ok"}


def test_forecast_contract():
    client = TestClient(create_app(FakeModel()))
    resp = client.post("/forecast", json={"values": [1, 2, 3], "horizon": 2})
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 2
    assert rows[0]["p10"] <= rows[0]["p50"] <= rows[0]["p90"]


def test_forecast_passes_covariates_to_model():
    model = FakeModel()
    client = TestClient(create_app(model))
    resp = client.post(
        "/forecast",
        json={
            "values": [1, 2, 3],
            "horizon": 2,
            "dynamic_numerical_covariates": {"btc": [1, 2, 3, 4, 5]},
        },
    )
    assert resp.status_code == 200
    assert model.last_cov == {"btc": [1, 2, 3, 4, 5]}
