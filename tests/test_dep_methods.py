import numpy as np

from norn_agent.methods import METHODS, granger, lagged_cross_correlation


def test_xcorr_detects_source_leading_by_k():
    base = np.sin(np.linspace(0, 12, 220))
    # source[i] == target[i+3]  => source leads target by 3
    source = base[10:200].tolist()
    target = base[7:197].tolist()
    m = lagged_cross_correlation(source, target, max_lag=10)
    assert m.lag == 3
    assert m.direction == "source_leads"
    assert abs(m.score) > 0.95


def test_xcorr_unrelated_low_corr():
    rng = np.random.default_rng(1)
    m = lagged_cross_correlation(
        rng.standard_normal(300).tolist(), rng.standard_normal(300).tolist(), max_lag=10
    )
    assert abs(m.score) < 0.4


def test_granger_detects_causality():
    rng = np.random.default_rng(0)
    source = rng.standard_normal(400)
    target = np.zeros(400)
    for t in range(2, 400):
        target[t] = 0.7 * source[t - 2] + 0.1 * rng.standard_normal()
    m = granger(source.tolist(), target.tolist(), max_lag=5)
    assert m.p_value is not None and m.p_value < 0.05
    assert m.direction == "source_leads"


def test_granger_short_window_inconclusive():
    m = granger([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], max_lag=5)
    assert m.direction == "inconclusive"


def test_methods_registry():
    assert set(METHODS) == {"lagged_cross_correlation", "granger"}


def test_granger_significance_param_controls_direction():
    import numpy as np
    rng = np.random.default_rng(0)
    source = rng.standard_normal(400)
    target = np.zeros(400)
    for t in range(2, 400):
        target[t] = 0.7 * source[t - 2] + 0.1 * rng.standard_normal()
    # an impossibly strict alpha -> even a real signal is judged inconclusive
    m = granger(source.tolist(), target.tolist(), max_lag=5, significance=1e-12)
    assert m.direction == "inconclusive"
