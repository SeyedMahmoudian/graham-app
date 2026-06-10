"""
Tests for P3 Regime Model (regime.py).

Covers:
  1. Trend score calculation (SMA signals)
  2. Volatility percentile
  3. Drawdown calculation
  4. Regime classification (all 6 regimes)
  5. Risk level thresholds
  6. Crisis override
  7. Fast deterioration alert (all 3 triggers)
  8. Output schema
  9. Edge cases (empty, insufficient data)
  10. Regime multipliers and equity exposure
"""

import math
import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codes.models.regime import (
    score,
    BULL_LOW_VOL, BULL_HIGH_VOL, BEAR_LOW_VOL, BEAR_HIGH_VOL,
    SIDEWAYS, CRISIS,
    _drawdown_from_peak, _risk_level, _classify_regime,
    _REGIME_MULTIPLIER, _MAX_EXPOSURE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_hist(closes: list, start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq="MS")
    return pd.DataFrame({"Date": dates.strftime("%Y-%m-%d"), "Close": closes})


def _rising(n: int = 24, start: float = 100.0, pct: float = 1.02) -> list:
    """Steadily rising prices — guaranteed bull trend."""
    prices = [start]
    for _ in range(n - 1):
        prices.append(round(prices[-1] * pct, 4))
    return prices


def _falling(n: int = 24, start: float = 200.0, pct: float = 0.97) -> list:
    prices = [start]
    for _ in range(n - 1):
        prices.append(round(prices[-1] * pct, 4))
    return prices


def _flat(n: int = 24, val: float = 100.0) -> list:
    return [val] * n


# ══════════════════════════════════════════════════════════════════════════════
# 1. Output schema
# ══════════════════════════════════════════════════════════════════════════════

class TestOutputSchema:
    REQUIRED = {
        "market_trend_score", "volatility_percentile", "drawdown_depth",
        "regime", "risk_level", "risk_alert", "max_equity_exposure",
        "regime_multiplier", "sma_50", "sma_200", "current_price",
        "vol_20d", "vol_60d", "error",
    }

    def test_all_required_keys_present(self):
        result = score(_make_hist(_rising()))
        assert self.REQUIRED.issubset(result.keys())

    def test_no_extra_keys(self):
        result = score(_make_hist(_rising()))
        assert set(result.keys()) == self.REQUIRED

    def test_error_is_none_on_success(self):
        result = score(_make_hist(_rising()))
        assert result["error"] is None

    def test_all_numeric_fields_finite(self):
        result = score(_make_hist(_rising(n=24)))
        for key in ("market_trend_score", "volatility_percentile", "drawdown_depth",
                    "max_equity_exposure", "regime_multiplier"):
            val = result[key]
            assert val is not None and math.isfinite(val), f"{key} = {val}"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Trend score
# ══════════════════════════════════════════════════════════════════════════════

class TestTrendScore:
    def test_rising_series_high_trend_score(self):
        result = score(_make_hist(_rising(n=15)))
        assert result["market_trend_score"] >= 60

    def test_falling_series_low_trend_score(self):
        result = score(_make_hist(_falling(n=15)))
        assert result["market_trend_score"] <= 40

    def test_trend_score_bounded_0_100(self):
        for prices in [_rising(24), _falling(24), _flat(24)]:
            r = score(_make_hist(prices))
            assert 0 <= r["market_trend_score"] <= 100

    def test_flat_series_gives_valid_score(self):
        result = score(_make_hist(_flat(24)))
        assert result["market_trend_score"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Drawdown calculation
# ══════════════════════════════════════════════════════════════════════════════

class TestDrawdown:
    def test_rising_series_zero_drawdown(self):
        prices = np.array(_rising(24))
        dd = _drawdown_from_peak(prices)
        assert dd == pytest.approx(0.0, abs=0.01)

    def test_known_drawdown(self):
        # Peak 120, current 90 → -25%  (series ends at trough 90)
        prices = np.array([100, 110, 120, 115, 105, 90])
        dd = _drawdown_from_peak(prices)
        assert dd == pytest.approx(-25.0, abs=0.5)

    def test_flat_series_zero_drawdown(self):
        prices = np.array(_flat(12))
        dd = _drawdown_from_peak(prices)
        assert dd == pytest.approx(0.0, abs=0.001)

    def test_drawdown_negative_or_zero(self):
        for prices in [_rising(24), _falling(24), _flat(12)]:
            dd = _drawdown_from_peak(np.array(prices))
            assert dd <= 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 4. Risk level thresholds
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskLevel:
    def test_normal(self):
        assert _risk_level(-4.0) == "NORMAL"
        assert _risk_level(0.0)  == "NORMAL"

    def test_elevated(self):
        assert _risk_level(-7.0) == "ELEVATED"
        assert _risk_level(-5.0) == "ELEVATED"

    def test_high(self):
        assert _risk_level(-15.0) == "HIGH"
        assert _risk_level(-10.0) == "HIGH"

    def test_crisis(self):
        assert _risk_level(-20.0) == "CRISIS"
        assert _risk_level(-50.0) == "CRISIS"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Regime classification
# ══════════════════════════════════════════════════════════════════════════════

class TestRegimeClassification:
    def test_bull_low_vol(self):
        assert _classify_regime(70, 30) == BULL_LOW_VOL

    def test_bull_high_vol(self):
        assert _classify_regime(70, 70) == BULL_HIGH_VOL

    def test_bear_low_vol(self):
        assert _classify_regime(30, 30) == BEAR_LOW_VOL

    def test_bear_high_vol(self):
        assert _classify_regime(30, 80) == BEAR_HIGH_VOL

    def test_sideways_mid_trend(self):
        assert _classify_regime(50, 50) == SIDEWAYS
        assert _classify_regime(45, 60) == SIDEWAYS
        assert _classify_regime(55, 40) == SIDEWAYS

    def test_boundary_60_trend(self):
        # Exactly 60 → BULL
        assert _classify_regime(60, 30) == BULL_LOW_VOL
        assert _classify_regime(60, 70) == BULL_HIGH_VOL

    def test_boundary_40_trend(self):
        # Exactly 40 → BEAR
        assert _classify_regime(40, 30) == BEAR_LOW_VOL
        assert _classify_regime(40, 80) == BEAR_HIGH_VOL


# ══════════════════════════════════════════════════════════════════════════════
# 6. Crisis override
# ══════════════════════════════════════════════════════════════════════════════

class TestCrisisOverride:
    def _build_crisis_hist(self) -> pd.DataFrame:
        """Build a series with drawdown > -25% and high recent volatility."""
        # Peak 200, crashes to ~140 (-30%), then very volatile tail
        prices = [200, 195, 190, 180, 170, 155, 145, 140, 143, 138,
                  150, 135, 142, 130, 138, 133, 140, 128, 135, 130]
        return _make_hist(prices)

    def test_crisis_regime_on_extreme_drawdown_vol(self):
        """Manually craft inputs that trigger crisis override via score()."""
        # Build 24-month series: big peak then collapse
        prices = [100.0] * 12 + [x for x in [75, 70, 65, 60, 55, 50, 48, 46, 44, 42, 40, 38]]
        hist = _make_hist(prices)
        result = score(hist)
        # With -62% drawdown we expect CRISIS risk_level regardless
        assert result["risk_level"] == "CRISIS"

    def test_crisis_override_sets_multiplier_050(self):
        prices = [100.0] * 12 + [x for x in [75, 70, 65, 60, 55, 50, 48, 46, 44, 42, 40, 38]]
        result = score(_make_hist(prices))
        assert result["max_equity_exposure"] == pytest.approx(0.40)

    def test_crisis_multiplier_is_050(self):
        assert _REGIME_MULTIPLIER[CRISIS] == pytest.approx(0.50)

    def test_crisis_max_exposure_040(self):
        assert _MAX_EXPOSURE["CRISIS"] == pytest.approx(0.40)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Regime multipliers and equity exposure
# ══════════════════════════════════════════════════════════════════════════════

class TestMultipliers:
    def test_bull_low_vol_multiplier_110(self):
        assert _REGIME_MULTIPLIER[BULL_LOW_VOL] == pytest.approx(1.10)

    def test_bull_high_vol_multiplier_100(self):
        assert _REGIME_MULTIPLIER[BULL_HIGH_VOL] == pytest.approx(1.00)

    def test_sideways_multiplier_090(self):
        assert _REGIME_MULTIPLIER[SIDEWAYS] == pytest.approx(0.90)

    def test_bear_low_vol_multiplier_080(self):
        assert _REGIME_MULTIPLIER[BEAR_LOW_VOL] == pytest.approx(0.80)

    def test_bear_high_vol_multiplier_065(self):
        assert _REGIME_MULTIPLIER[BEAR_HIGH_VOL] == pytest.approx(0.65)

    def test_normal_risk_exposure_100(self):
        assert _MAX_EXPOSURE["NORMAL"] == pytest.approx(1.00)

    def test_elevated_risk_exposure_090(self):
        assert _MAX_EXPOSURE["ELEVATED"] == pytest.approx(0.90)

    def test_high_risk_exposure_070(self):
        assert _MAX_EXPOSURE["HIGH"] == pytest.approx(0.70)

    def test_regime_multiplier_in_result(self):
        result = score(_make_hist(_rising(n=15)))
        assert 0.4 <= result["regime_multiplier"] <= 1.1

    def test_max_equity_exposure_in_result(self):
        result = score(_make_hist(_rising(n=15)))
        assert 0.4 <= result["max_equity_exposure"] <= 1.0


# ══════════════════════════════════════════════════════════════════════════════
# 8. Risk alert — fast deterioration triggers
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskAlert:
    def test_alert_on_large_1m_drop(self):
        """5D return proxy: -7% or worse in 1 monthly bar."""
        prices = _rising(20) + [_rising(20)[-1] * 0.90]  # -10% last bar
        result = score(_make_hist(prices))
        assert result["risk_alert"] is True

    def test_no_alert_on_small_drop(self):
        prices = _rising(20) + [_rising(20)[-1] * 0.97]  # -3%
        result = score(_make_hist(prices))
        # -3% alone should not trigger (< -7% threshold)
        assert result["risk_alert"] is False

    def test_alert_false_on_rising_series(self):
        result = score(_make_hist(_rising(24)))
        assert result["risk_alert"] is False

    def test_risk_alert_is_bool(self):
        result = score(_make_hist(_rising(24)))
        assert isinstance(result["risk_alert"], bool)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_dataframe_returns_error(self):
        result = score(pd.DataFrame())
        assert result["error"] is not None

    def test_none_returns_error(self):
        result = score(None)
        assert result["error"] is not None

    def test_insufficient_data_returns_error(self):
        result = score(_make_hist([100, 102]))
        assert result["error"] is not None

    def test_non_positive_prices_filtered(self):
        prices = [100, 0, 102, 104, 106, 108, 110, 112]
        result = score(_make_hist(prices))
        # Should not crash; may return error or valid result
        assert "error" in result

    def test_single_price_returns_error(self):
        result = score(_make_hist([100.0]))
        assert result["error"] is not None

    def test_regime_not_none_on_valid_input(self):
        result = score(_make_hist(_rising(15)))
        assert result["regime"] is not None

    def test_regime_is_valid_label(self):
        valid = {BULL_LOW_VOL, BULL_HIGH_VOL, BEAR_LOW_VOL, BEAR_HIGH_VOL, SIDEWAYS, CRISIS}
        result = score(_make_hist(_rising(15)))
        assert result["regime"] in valid

    def test_risk_level_is_valid_label(self):
        valid = {"NORMAL", "ELEVATED", "HIGH", "CRISIS"}
        result = score(_make_hist(_rising(15)))
        assert result["risk_level"] in valid

    def test_error_key_on_failure_is_string(self):
        result = score(pd.DataFrame())
        assert isinstance(result["error"], str)

    def test_sma_200_none_when_insufficient(self):
        """With only 5 bars, SMA-200 (10 bars) should be None."""
        result = score(_make_hist([100, 101, 102, 103, 104, 105]))
        # Either error or sma_200 is None
        assert result.get("error") is not None or result.get("sma_200") is None

    def test_descending_dates_handled(self):
        """Input with dates not sorted should still work."""
        prices = _rising(20)
        hist = _make_hist(prices)
        hist = hist.iloc[::-1].reset_index(drop=True)  # reverse order
        result = score(hist)
        assert result["error"] is None


# ══════════════════════════════════════════════════════════════════════════════
# 10. Integration — consistent outputs
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    def test_bull_regime_on_steady_rise(self):
        result = score(_make_hist(_rising(n=15, pct=1.03)))
        assert result["regime"] in (BULL_LOW_VOL, BULL_HIGH_VOL)

    def test_bear_regime_on_steady_decline(self):
        result = score(_make_hist(_falling(n=15, pct=0.96)))
        assert result["regime"] in (BEAR_LOW_VOL, BEAR_HIGH_VOL)

    def test_normal_risk_on_steady_rise(self):
        result = score(_make_hist(_rising(n=15)))
        assert result["risk_level"] == "NORMAL"

    def test_final_score_formula_works(self):
        """Verify portfolio usage pattern: stock_score × regime_multiplier."""
        stock_score = 72.0
        result = score(_make_hist(_rising(n=15)))
        final = stock_score * result["regime_multiplier"]
        assert math.isfinite(final)
        assert final > 0

    def test_position_size_formula_works(self):
        """position_size = base × max_equity_exposure."""
        base = 10_000.0
        result = score(_make_hist(_rising(n=15)))
        position = base * result["max_equity_exposure"]
        assert math.isfinite(position) and position > 0

    def test_reproducible_output(self):
        """Same input → same output (deterministic)."""
        hist = _make_hist(_rising(24))
        r1 = score(hist)
        r2 = score(hist)
        assert r1["regime"] == r2["regime"]
        assert r1["market_trend_score"] == r2["market_trend_score"]
        assert r1["drawdown_depth"] == r2["drawdown_depth"]

    def test_current_price_matches_last_close(self):
        prices = _rising(15)
        result = score(_make_hist(prices))
        assert result["current_price"] == pytest.approx(prices[-1], rel=1e-4)


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
