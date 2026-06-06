# tests/test_montecarlo.py

import numpy as np

from codes.portfolio import MC_PATHS


def test_geometric_drift_matches_expected_growth():
    start = 100.0

    mu_arith = 0.01      # 1% monthly
    sigma = 0.05

    mu_geo = mu_arith - sigma**2 / 2

    rng = np.random.default_rng(42)

    months = 120
    paths = np.full((50000,), start)

    for _ in range(months):
        z = rng.normal(size=len(paths))
        paths *= np.exp(mu_geo + sigma * z)

    simulated = paths.mean()

    expected = start * np.exp(mu_arith * months)

    assert abs(simulated - expected) / expected < 0.03
def test_zero_volatility_growth():
    start = 100.0
    mu_arith = 0.01
    sigma = 0.0

    mu_geo = mu_arith - sigma**2 / 2

    value = start

    for _ in range(24):
        value *= np.exp(mu_geo)

    expected = start * np.exp(mu_arith * 24)

    assert abs(value - expected) < 1e-8