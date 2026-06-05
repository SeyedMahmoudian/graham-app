"""
Unit tests for ISSUE-001: Sortino Ratio denominator correctness.

Verifies downside deviation is computed over ALL N observations,
not only the downside subset.
"""

import math
import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import risk_metrics


def _make_hist(closes: list) -> pd.DataFrame:
    dates = pd.date_range("2015-01-01", periods=len(closes), freq="MS")
    return pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Close": closes})


def _prices_from_normal(seed: int, n: int, mean: float, std: float) -> list:
    """Build price series from normally distributed monthly returns."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(mean, std, size=n)
    prices = [100.0]
    for r in rets:
        prices.append(prices[-1] * (1 + r))
    return prices


def test_sortino_uses_total_n_not_downside_n():
    """Result matches total-N formula, not downside-only-N formula."""
    rf_monthly = risk_metrics.RISK_FREE_RATE / risk_metrics.MONTHS_PER_YEAR

    # Use normally distributed returns so downside returns have real spread.
    prices = _prices_from_normal(seed=42, n=60, mean=0.008, std=0.04)
    hist = _make_hist(prices)

    result = risk_metrics.score(hist)
    sortino_actual = result["sortino"]
    assert sortino_actual is not None

    log_rets = np.log(np.array(prices[1:]) / np.array(prices[:-1]))
    n = len(log_rets)
    annual_ret = (prices[-1] / prices[0]) ** (risk_metrics.MONTHS_PER_YEAR / n) - 1

    # correct: total-N denominator
    down_sq = np.minimum(log_rets - rf_monthly, 0.0) ** 2
    down_std_correct = math.sqrt(float(np.sum(down_sq) / n)) * math.sqrt(risk_metrics.MONTHS_PER_YEAR)
    sortino_correct = (annual_ret - risk_metrics.RISK_FREE_RATE) / down_std_correct

    # wrong: downside-only-N denominator
    d_only = log_rets[log_rets < rf_monthly]
    down_std_wrong = float(np.std(d_only, ddof=1)) * math.sqrt(risk_metrics.MONTHS_PER_YEAR)
    sortino_wrong = (annual_ret - risk_metrics.RISK_FREE_RATE) / down_std_wrong

    assert not math.isclose(sortino_correct, sortino_wrong, rel_tol=1e-4), \
        "test setup error: correct and wrong values must differ"

    # score() rounds to 3 dp — use rel_tol=1e-3 to absorb rounding
    assert math.isclose(sortino_actual, sortino_correct, rel_tol=1e-3), (
        f"got {sortino_actual:.6f}, expected {sortino_correct:.6f} "
        f"(wrong would be {sortino_wrong:.6f})"
    )


def test_sortino_all_positive_returns_is_none():
    """All returns above rf → downside variance = 0 → sortino = None."""
    prices = [100.0 * (1.05 ** i) for i in range(25)]
    result = risk_metrics.score(_make_hist(prices))
    assert result["sortino"] is None


def test_sortino_total_n_larger_than_downside_n():
    """Total-N Sortino is larger (less pessimistic) than downside-N Sortino."""
    rf_monthly = risk_metrics.RISK_FREE_RATE / risk_metrics.MONTHS_PER_YEAR

    # Normally distributed returns ensure non-zero std on the downside subset.
    prices = _prices_from_normal(seed=7, n=60, mean=0.005, std=0.05)
    log_rets = np.log(np.array(prices[1:]) / np.array(prices[:-1]))
    n = len(log_rets)
    annual_ret = (prices[-1] / prices[0]) ** (risk_metrics.MONTHS_PER_YEAR / n) - 1

    down_sq = np.minimum(log_rets - rf_monthly, 0.0) ** 2
    sortino_total_n = (annual_ret - risk_metrics.RISK_FREE_RATE) / (
        math.sqrt(float(np.sum(down_sq) / n)) * math.sqrt(risk_metrics.MONTHS_PER_YEAR)
    )
    d_only = log_rets[log_rets < rf_monthly]
    sortino_down_n = (annual_ret - risk_metrics.RISK_FREE_RATE) / (
        float(np.std(d_only, ddof=1)) * math.sqrt(risk_metrics.MONTHS_PER_YEAR)
    )

    # total-N divides the same sum by a larger N → smaller variance → higher ratio
    assert sortino_total_n > sortino_down_n, (
        f"total-N {sortino_total_n:.4f} should exceed downside-N {sortino_down_n:.4f}"
    )
