"""
Tests for Options Signal Engine (PROJECT_MAP.md P4 — Options Layer).

Covers:
  1. Directional bias (CALL/PUT/NEUTRAL) from regime + momentum
  2. IV regime classification (level + trend)
  3. Expected move sizing
  4. Strike/expiry recommendation direction
  5. Risk score (theta + IV + liquidity)
  6. Edge score and IV-favorability weighting
  7. get_options_signal() output schema and edge cases
"""

import math
import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codes.models.options_signal_engine import (
    OptionsSignalEngine,
    calc_momentum,
    calc_monthly_volatility,
    _norm_momentum,
    _signal,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hist(closes):
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="MS")
    return pd.DataFrame({"Date": dates, "Close": closes})


def _rising(n=12, start=100.0, pct=1.02):
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * pct)
    return prices


def _falling(n=12, start=100.0, pct=0.98):
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * pct)
    return prices


# ══════════════════════════════════════════════════════════════════════════════
# 1. Pure helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcMomentum:
    def test_rising_series_positive(self):
        m = calc_momentum(_hist(_rising(6)))
        assert m is not None and m > 0

    def test_falling_series_negative(self):
        m = calc_momentum(_hist(_falling(6)))
        assert m is not None and m < 0

    def test_insufficient_history_returns_none(self):
        assert calc_momentum(_hist([100, 102]), lookback_months=3) is None

    def test_none_input_returns_none(self):
        assert calc_momentum(None) is None


class TestNormMomentum:
    def test_plus_20_gives_100(self):
        assert _norm_momentum(0.20) == pytest.approx(100.0)

    def test_minus_20_gives_0(self):
        assert _norm_momentum(-0.20) == pytest.approx(0.0)

    def test_zero_gives_50(self):
        assert _norm_momentum(0.0) == pytest.approx(50.0)

    def test_none_gives_none(self):
        assert _norm_momentum(None) is None

    def test_extreme_clipped(self):
        assert _norm_momentum(1.0) == pytest.approx(100.0)
        assert _norm_momentum(-1.0) == pytest.approx(0.0)


class TestCalcMonthlyVolatility:
    def test_constant_prices_zero_vol(self):
        v = calc_monthly_volatility(_hist([100] * 6))
        assert v == pytest.approx(0.0, abs=1e-9)

    def test_varying_prices_positive_vol(self):
        v = calc_monthly_volatility(_hist([100, 105, 98, 110, 95, 103]))
        assert v is not None and v > 0

    def test_too_short_returns_none(self):
        assert calc_monthly_volatility(_hist([100, 101])) is None

    def test_none_returns_none(self):
        assert calc_monthly_volatility(None) is None


class TestSignalThresholds:
    def test_strong_edge_at_60(self):
        assert _signal(60) == "STRONG_EDGE"
        assert _signal(100) == "STRONG_EDGE"

    def test_watch_at_40(self):
        assert _signal(40) == "WATCH"
        assert _signal(59.9) == "WATCH"

    def test_avoid_below_40(self):
        assert _signal(0) == "AVOID"
        assert _signal(39.9) == "AVOID"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Directional bias
# ══════════════════════════════════════════════════════════════════════════════

class TestDirectionalBias:
    def test_bullish_regime_and_momentum_gives_call(self):
        eng = OptionsSignalEngine(
            "TEST", price_hist=_hist(_rising(6)),
            regime_result={"market_trend_score": 80},
        )
        bias, conf = eng.calc_directional_bias()
        assert bias == "CALL"
        assert conf > 0

    def test_bearish_regime_and_momentum_gives_put(self):
        eng = OptionsSignalEngine(
            "TEST", price_hist=_hist(_falling(6)),
            regime_result={"market_trend_score": 20},
        )
        bias, conf = eng.calc_directional_bias()
        assert bias == "PUT"
        assert conf > 0

    def test_midrange_gives_neutral(self):
        eng = OptionsSignalEngine(
            "TEST", price_hist=_hist([100] * 6),
            regime_result={"market_trend_score": 50},
        )
        bias, conf = eng.calc_directional_bias()
        assert bias == "NEUTRAL"

    def test_no_data_gives_neutral_zero_confidence(self):
        eng = OptionsSignalEngine("TEST")
        bias, conf = eng.calc_directional_bias()
        assert bias == "NEUTRAL"
        assert conf == 0.0

    def test_only_regime_available_still_works(self):
        eng = OptionsSignalEngine("TEST", regime_result={"market_trend_score": 90})
        bias, conf = eng.calc_directional_bias()
        assert bias == "CALL"
        assert conf > 0

    def test_only_momentum_available_still_works(self):
        eng = OptionsSignalEngine("TEST", price_hist=_hist(_rising(6, pct=1.05)))
        bias, conf = eng.calc_directional_bias()
        assert bias in ("CALL", "NEUTRAL")


# ══════════════════════════════════════════════════════════════════════════════
# 3. IV regime
# ══════════════════════════════════════════════════════════════════════════════

class TestIvRegime:
    def test_high_iv_level(self):
        eng = OptionsSignalEngine("TEST", regime_result={"volatility_percentile": 90})
        level, _ = eng.calc_iv_regime()
        assert level == "HIGH"

    def test_low_iv_level(self):
        eng = OptionsSignalEngine("TEST", regime_result={"volatility_percentile": 10})
        level, _ = eng.calc_iv_regime()
        assert level == "LOW"

    def test_normal_iv_level(self):
        eng = OptionsSignalEngine("TEST", regime_result={"volatility_percentile": 50})
        level, _ = eng.calc_iv_regime()
        assert level == "NORMAL"

    def test_unknown_iv_level_when_missing(self):
        eng = OptionsSignalEngine("TEST")
        level, _ = eng.calc_iv_regime()
        assert level == "UNKNOWN"

    def test_expanding_trend(self):
        eng = OptionsSignalEngine("TEST", regime_result={"vol_20d": 30, "vol_60d": 20})
        _, trend = eng.calc_iv_regime()
        assert trend == "EXPANDING"

    def test_contracting_trend(self):
        eng = OptionsSignalEngine("TEST", regime_result={"vol_20d": 10, "vol_60d": 20})
        _, trend = eng.calc_iv_regime()
        assert trend == "CONTRACTING"

    def test_stable_trend(self):
        eng = OptionsSignalEngine("TEST", regime_result={"vol_20d": 20, "vol_60d": 20})
        _, trend = eng.calc_iv_regime()
        assert trend == "STABLE"

    def test_unknown_trend_when_missing(self):
        eng = OptionsSignalEngine("TEST")
        _, trend = eng.calc_iv_regime()
        assert trend == "UNKNOWN"


# ══════════════════════════════════════════════════════════════════════════════
# 4. Expected move
# ══════════════════════════════════════════════════════════════════════════════

class TestExpectedMove:
    def test_none_when_no_history(self):
        eng = OptionsSignalEngine("TEST", current_price=100.0)
        pct, dollar = eng.calc_expected_move(30)
        assert pct is None and dollar is None

    def test_positive_move_for_volatile_series(self):
        eng = OptionsSignalEngine(
            "TEST", price_hist=_hist([100, 105, 98, 110, 95, 103]),
            current_price=103.0,
        )
        pct, dollar = eng.calc_expected_move(30)
        assert pct is not None and pct > 0
        assert dollar is not None and dollar > 0

    def test_longer_horizon_scales_up_move(self):
        hist = _hist([100, 105, 98, 110, 95, 103])
        eng = OptionsSignalEngine("TEST", price_hist=hist, current_price=103.0)
        pct_30, _ = eng.calc_expected_move(30)
        pct_90, _ = eng.calc_expected_move(90)
        assert pct_90 > pct_30

    def test_zero_vol_gives_zero_move(self):
        eng = OptionsSignalEngine("TEST", price_hist=_hist([100] * 6), current_price=100.0)
        pct, dollar = eng.calc_expected_move(30)
        assert pct == pytest.approx(0.0, abs=1e-9)
        assert dollar == pytest.approx(0.0, abs=1e-6)


# ══════════════════════════════════════════════════════════════════════════════
# 5. Strike / expiry recommendation
# ══════════════════════════════════════════════════════════════════════════════

class TestStrikeExpiryRecommendation:
    def test_call_strike_above_current_price(self):
        eng = OptionsSignalEngine("TEST", current_price=100.0)
        rec = eng.recommend_strike_expiry("CALL", move_pct=0.10, horizon_days=30)
        assert rec["strike"] > 100.0
        assert rec["expiry_days"] == 30

    def test_put_strike_below_current_price(self):
        eng = OptionsSignalEngine("TEST", current_price=100.0)
        rec = eng.recommend_strike_expiry("PUT", move_pct=0.10, horizon_days=30)
        assert rec["strike"] < 100.0

    def test_neutral_gives_atm_strike(self):
        eng = OptionsSignalEngine("TEST", current_price=100.0)
        rec = eng.recommend_strike_expiry("NEUTRAL", move_pct=0.10, horizon_days=30)
        assert rec["strike"] == pytest.approx(100.0)

    def test_missing_price_gives_none_strike(self):
        eng = OptionsSignalEngine("TEST")
        rec = eng.recommend_strike_expiry("CALL", move_pct=0.10, horizon_days=30)
        assert rec["strike"] is None

    def test_missing_move_pct_gives_atm(self):
        eng = OptionsSignalEngine("TEST", current_price=100.0)
        rec = eng.recommend_strike_expiry("CALL", move_pct=None, horizon_days=30)
        assert rec["strike"] == pytest.approx(100.0)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Risk score
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskScore:
    def test_short_expiry_high_iv_high_risk(self):
        eng = OptionsSignalEngine("TEST")
        score = eng.calc_risk_score("HIGH", horizon_days=7)
        assert score >= 60

    def test_long_expiry_low_iv_lower_risk(self):
        eng = OptionsSignalEngine("TEST")
        score = eng.calc_risk_score("LOW", horizon_days=60)
        assert score <= 40

    def test_risk_score_bounded(self):
        eng = OptionsSignalEngine("TEST")
        for iv in ("HIGH", "NORMAL", "LOW", "UNKNOWN"):
            for days in (7, 30, 60):
                score = eng.calc_risk_score(iv, horizon_days=days)
                assert 0 <= score <= 100


# ══════════════════════════════════════════════════════════════════════════════
# 7. Edge score
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeScore:
    def test_low_iv_boosts_edge(self):
        eng = OptionsSignalEngine("TEST")
        edge_low = eng.calc_edge_score(50.0, "LOW", "STABLE")
        edge_normal = eng.calc_edge_score(50.0, "NORMAL", "STABLE")
        assert edge_low > edge_normal

    def test_high_iv_reduces_edge(self):
        eng = OptionsSignalEngine("TEST")
        edge_high = eng.calc_edge_score(50.0, "HIGH", "STABLE")
        edge_normal = eng.calc_edge_score(50.0, "NORMAL", "STABLE")
        assert edge_high < edge_normal

    def test_contracting_iv_boosts_edge(self):
        eng = OptionsSignalEngine("TEST")
        edge_contract = eng.calc_edge_score(50.0, "NORMAL", "CONTRACTING")
        edge_stable = eng.calc_edge_score(50.0, "NORMAL", "STABLE")
        assert edge_contract > edge_stable

    def test_edge_bounded_0_100(self):
        eng = OptionsSignalEngine("TEST")
        edge = eng.calc_edge_score(100.0, "LOW", "CONTRACTING")
        assert 0 <= edge <= 100


# ══════════════════════════════════════════════════════════════════════════════
# 8. get_options_signal() — schema & integration
# ══════════════════════════════════════════════════════════════════════════════

class TestGetOptionsSignal:
    REQUIRED_KEYS = {
        "ticker", "bias", "bias_confidence", "iv_level", "iv_trend",
        "expected_move_pct", "expected_move_dollar", "recommended_strike",
        "recommended_expiry_days", "risk_score", "edge_score", "signal",
        "total_score", "total_max",
    }

    def test_output_has_required_keys(self):
        eng = OptionsSignalEngine(
            "aapl", price_hist=_hist(_rising(6)),
            regime_result={"market_trend_score": 75, "volatility_percentile": 20,
                           "vol_20d": 15, "vol_60d": 20},
            current_price=150.0,
        )
        out = eng.get_options_signal()
        assert self.REQUIRED_KEYS == set(out.keys())

    def test_ticker_uppercased(self):
        eng = OptionsSignalEngine("aapl")
        out = eng.get_options_signal()
        assert out["ticker"] == "AAPL"

    def test_neutral_bias_gives_no_trade(self):
        eng = OptionsSignalEngine("TEST", regime_result={"market_trend_score": 50},
                                  price_hist=_hist([100] * 6))
        out = eng.get_options_signal()
        assert out["bias"] == "NEUTRAL"
        assert out["signal"] == "NO_TRADE"

    def test_strong_bullish_low_iv_gives_buy_call(self):
        eng = OptionsSignalEngine(
            "TEST", price_hist=_hist(_rising(6, pct=1.05)),
            regime_result={"market_trend_score": 90, "volatility_percentile": 10,
                           "vol_20d": 10, "vol_60d": 20},
            current_price=120.0,
        )
        out = eng.get_options_signal()
        assert out["bias"] == "CALL"
        assert out["signal"] == "BUY_CALL"

    def test_strong_bearish_gives_put_bias(self):
        eng = OptionsSignalEngine(
            "TEST", price_hist=_hist(_falling(6, pct=0.95)),
            regime_result={"market_trend_score": 10, "volatility_percentile": 10,
                           "vol_20d": 10, "vol_60d": 20},
            current_price=80.0,
        )
        out = eng.get_options_signal()
        assert out["bias"] == "PUT"
        assert out["signal"] == "BUY_PUT"

    def test_total_score_equals_edge_score(self):
        eng = OptionsSignalEngine("TEST")
        out = eng.get_options_signal()
        assert out["total_score"] == out["edge_score"]
        assert out["total_max"] == 100.0

    def test_no_data_does_not_crash(self):
        eng = OptionsSignalEngine("X")
        try:
            out = eng.get_options_signal()
        except Exception as exc:
            pytest.fail(f"get_options_signal raised {type(exc).__name__}: {exc}")
        assert out["bias"] == "NEUTRAL"
        assert out["signal"] == "NO_TRADE"
        assert math.isfinite(out["risk_score"])
        assert math.isfinite(out["edge_score"])

    def test_custom_horizon_reflected_in_expiry(self):
        eng = OptionsSignalEngine("TEST", current_price=100.0,
                                  price_hist=_hist([100, 102, 99, 105, 101, 103]))
        out = eng.get_options_signal(horizon_days=60)
        assert out["recommended_expiry_days"] == 60


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
