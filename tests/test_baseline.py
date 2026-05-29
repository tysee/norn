from norn_forecast.baseline import seasonal_naive_forecast


def test_perfect_seasonal_series_has_zero_spread():
    # 4 weeks of a clean weekly pattern; seasonal-naive should reproduce it exactly
    values = [1, 2, 3, 4, 5, 6, 7] * 4
    out = seasonal_naive_forecast(values, horizon=7, seasonality=7)
    assert len(out) == 7
    # next day after ...,7 is 1 (start of the weekly cycle)
    assert out[0]["y_hat"] == 1.0
    assert out[0]["p50"] == 1.0
    assert out[0]["p10"] == out[0]["p90"] == 1.0  # zero residual -> zero spread


def test_intervals_widen_with_horizon():
    values = [float(v) for v in [10, 12, 9, 11, 13, 8, 10] * 6]
    out = seasonal_naive_forecast(values, horizon=14, seasonality=7)
    width_first = out[0]["p90"] - out[0]["p10"]
    width_last = out[-1]["p90"] - out[-1]["p10"]
    assert width_last >= width_first > 0
    for row in out:
        assert row["p10"] <= row["p50"] <= row["p90"]


def test_short_series_falls_back_without_error():
    out = seasonal_naive_forecast([5.0, 6.0, 7.0], horizon=3, seasonality=7)
    assert len(out) == 3
    assert all(r["y_hat"] == 7.0 for r in out)  # last value carried forward
