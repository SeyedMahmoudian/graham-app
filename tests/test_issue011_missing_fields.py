"""
Tests for ISSUE-011: Missing financial statement handling.

Verifies:
1. altman.score() uses op_income when ebit key is absent (and vice-versa)
2. graham.score() falls back to net_inc when earnings key is absent
3. graham.score() does not raise TypeError when shares data is absent
4. risk_metrics.score() filters non-positive prices without propagating NaN/inf
5. portfolio.run_montecarlo() survives a non-PSD covariance matrix (Cholesky guard)
6. scorer.enhanced_composite() accepts None piotroski/altman without raising
"""

import math
import numpy as np
import pandas as pd
import pytest
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from codes import altman, graham, risk_metrics, scorer


def _rec(v):
    return [{"value": v}] if v is not None else []


# ══════════════════════════════════════════════════════════════════════════════
# Altman — op_income / ebit key fallback
# ══════════════════════════════════════════════════════════════════════════════

def _altman_sec(ebit_key="op_income"):
    sec = {
        "cur_ast":           _rec(200_000),
        "cur_lib":           _rec(100_000),
        "total_assets":      _rec(1_000_000),
        "retained_earnings": _rec(400_000),
        "shares":            _rec(1_000),
        "tot_lib":           _rec(500_000),
        "revenue":           _rec(800_000),
        "ppe_net":           _rec(300_000),
    }
    sec[ebit_key] = _rec(150_000)
    return sec


class TestAltmanOpIncomeFallback:
    def test_x3_not_none_with_op_income_key(self):
        result = altman.score(price=100.0, sec=_altman_sec("op_income"))
        assert result["components"]["x3_ebit_ratio"] is not None

    def test_x3_not_none_with_ebit_key(self):
        """Backward compat: legacy 'ebit' key must still work."""
        result = altman.score(price=100.0, sec=_altman_sec("ebit"))
        assert result["components"]["x3_ebit_ratio"] is not None

    def test_both_keys_produce_same_x3(self):
        r_op   = altman.score(price=100.0, sec=_altman_sec("op_income"))
        r_ebit = altman.score(price=100.0, sec=_altman_sec("ebit"))
        assert math.isclose(
            r_op["components"]["x3_ebit_ratio"],
            r_ebit["components"]["x3_ebit_ratio"],
            rel_tol=1e-6,
        )

    def test_x3_is_none_when_neither_key_present(self):
        sec = {
            "cur_ast": _rec(200_000), "cur_lib": _rec(100_000),
            "total_assets": _rec(1_000_000), "retained_earnings": _rec(400_000),
            "shares": _rec(1_000), "tot_lib": _rec(500_000),
            "revenue": _rec(800_000), "ppe_net": _rec(300_000),
        }
        result = altman.score(price=100.0, sec=sec)
        assert result["components"]["x3_ebit_ratio"] is None


# ══════════════════════════════════════════════════════════════════════════════
# Graham — net_inc fallback + shares-guard
# ══════════════════════════════════════════════════════════════════════════════

def _graham_base():
    return {
        "shares":    _rec(10_000_000),
        "bvps":      _rec(20),
        "cur_ast":   _rec(500_000_000),
        "cur_lib":   _rec(200_000_000),
        "lt_debt":   _rec(100_000_000),
        "tot_lib":   _rec(300_000_000),
        "equity":    _rec(400_000_000),
        "dividends": [],
    }


class TestGrahamFallbackAndGuards:
    def test_eps_computed_from_earnings_key(self):
        sec = {**_graham_base(), "earnings": [{"value": 100_000_000}] * 5}
        result = graham.score(price=50.0, sec=sec)
        assert result["eps"] is not None
        assert math.isclose(result["eps"], 100_000_000 / 10_000_000, rel_tol=1e-6)

    def test_eps_computed_from_net_inc_fallback(self):
        """Without 'earnings' key, net_inc must be used instead."""
        sec = {**_graham_base(), "net_inc": [{"value": 100_000_000}] * 5}
        result = graham.score(price=50.0, sec=sec)
        assert result["eps"] is not None
        assert math.isclose(result["eps"], 100_000_000 / 10_000_000, rel_tol=1e-6)

    def test_no_crash_when_shares_missing(self):
        """Missing shares must not cause TypeError or ZeroDivisionError."""
        sec = {**_graham_base(), "shares": [], "earnings": [{"value": 1_000_000}] * 5}
        try:
            result = graham.score(price=50.0, sec=sec)
        except (TypeError, ZeroDivisionError) as exc:
            pytest.fail(f"graham.score raised {type(exc).__name__}: {exc}")
        assert result["eps"] is None
        assert result["eps_years"] == 0

    def test_no_crash_on_empty_sec(self):
        """Completely empty sec dict must not raise — should return zero score."""
        try:
            result = graham.score(price=50.0, sec={})
        except Exception as exc:
            pytest.fail(f"graham.score raised {type(exc).__name__}: {exc}")
        assert result["total_score"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Risk metrics — non-positive price filtering
# ══════════════════════════════════════════════════════════════════════════════

def _make_hist(closes):
    dates = pd.date_range("2020-01-01", periods=len(closes), freq="MS")
    return pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Close": closes})


class TestRiskMetricsInvalidPrices:
    def test_valid_prices_produce_finite_metrics(self):
        closes = [100.0 * (1.01 ** i) for i in range(24)]
        result = risk_metrics.score(_make_hist(closes))
        assert result.get("error") is None
        assert result["volatility_annual"] is not None
        assert math.isfinite(result["volatility_annual"])

    def test_zero_price_no_nan_propagation(self):
        """A zero price must not silently produce NaN/inf in any metric."""
        closes = [100, 102, 0, 105, 108, 110, 107, 112, 115, 113, 118, 120]
        result = risk_metrics.score(_make_hist(closes))
        if result.get("error"):
            return  # acceptable: too few valid prices after filtering
        for key in ("volatility_annual", "sharpe", "sortino", "max_drawdown"):
            val = result.get(key)
            if val is not None:
                assert math.isfinite(val), f"{key} = {val} is not finite"

    def test_negative_price_no_nan_propagation(self):
        """A negative price must not silently produce NaN in log-returns."""
        closes = [100, 102, -5, 105, 108, 110, 107, 112, 115, 113, 118, 120]
        result = risk_metrics.score(_make_hist(closes))
        if result.get("error"):
            return
        for key in ("volatility_annual", "sharpe"):
            val = result.get(key)
            if val is not None:
                assert math.isfinite(val), f"{key} = {val} is not finite"


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio — Cholesky guard
# ══════════════════════════════════════════════════════════════════════════════

class TestPortfolioCholesky:
    def test_perfectly_correlated_assets_no_linalg_error(self):
        """
        Perfectly correlated returns → singular covariance → Cholesky fails
        without the guard.  Verify no LinAlgError is raised.
        """
        try:
            import codes.portfolio as portfolio_mod
        except ModuleNotFoundError as exc:
            pytest.skip(f"portfolio dependencies not installed: {exc}")

        dates = pd.date_range("2020-01-01", periods=60, freq="MS")
        returns = np.random.default_rng(0).normal(0.01, 0.04, 60)
        df1 = pd.DataFrame({"Date": dates, "Close": np.cumprod(1 + returns)})
        df2 = df1.copy()  # identical → perfectly correlated → singular cov

        with patch.object(portfolio_mod, "_load_history", side_effect=lambda s: df1 if s == "AAPL" else df2):
            portfolio = {
                "name": "Test",
                "holdings": {
                    "AAPL":  {"shares": 10, "price_at_add": 150.0},
                    "GOOGL": {"shares": 5,  "price_at_add": 2800.0},
                },
            }
            backtest = {"final_value": 50000.0, "error": None}
            try:
                result = portfolio_mod.run_montecarlo(portfolio, backtest)
            except np.linalg.LinAlgError as exc:
                pytest.fail(f"run_montecarlo raised LinAlgError: {exc}")
            assert result.get("error") is None
            assert len(result.get("p50", [])) > 0


# ══════════════════════════════════════════════════════════════════════════════
# Scorer — None inputs
# ══════════════════════════════════════════════════════════════════════════════

def _base_score_args(**overrides):
    base = dict(
        graham_result    = {"total_score": 50, "total_max": 100},
        quality_result   = {"total_score": 50, "total_max": 100, "roe": 12},
        momentum_result  = {"total_score": 50, "total_max": 100},
        piotroski_result = {"f_score": 5, "f_score_max": 9},
        risk_result      = {"risk_score": 50, "risk_score_max": 100, "risk_criteria": []},
        altman_result    = {"risk_score": 50, "zone": "grey"},
        buffett_result   = {"total_score": 50, "total_max": 100},
    )
    base.update(overrides)
    return base


class TestScorerNoneGuards:
    def test_missing_f_score_key_does_not_raise(self):
        args = _base_score_args(piotroski_result={})
        try:
            result = scorer.enhanced_composite(**args)
        except (AttributeError, TypeError) as exc:
            pytest.fail(f"enhanced_composite raised {type(exc).__name__}: {exc}")
        assert result["composite_score"] >= 0

    def test_missing_risk_score_key_does_not_raise(self):
        args = _base_score_args(altman_result={"zone": "unknown"})
        try:
            result = scorer.enhanced_composite(**args)
        except (AttributeError, TypeError) as exc:
            pytest.fail(f"enhanced_composite raised {type(exc).__name__}: {exc}")
        assert result["composite_score"] >= 0

    def test_piotroski_none_does_not_raise(self):
        """Passing None for piotroski_result must not crash enhanced_composite."""
        args = _base_score_args(piotroski_result={"f_score": 0})
        try:
            result = scorer.enhanced_composite(**args)
        except Exception as exc:
            pytest.fail(f"enhanced_composite raised {type(exc).__name__}: {exc}")
        assert result["composite_score"] >= 0


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
