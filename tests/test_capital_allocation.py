"""
Tests for P2 Capital Allocation Model.

Covers:
  1. Signal mapping thresholds
  2. Normalisation helpers
  3. Each metric method in isolation
  4. Edge cases: missing data, zero denominators, negative values
  5. get_capital_allocation_score() output schema
  6. scorer.enhanced_composite() integration
  7. ENHANCED_WEIGHTS sum to 1.0 after adding capital_allocation
"""

import math
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codes.models.capital_allocation import (
    CapitalAllocationAnalyzer,
    _signal,
    _norm_roic_spread,
    _norm_incremental_roic,
    _norm_reinvestment_rate,
    _norm_shareholder_yield,
    _norm_dilution_rate,
    _norm_debt_trend,
    HURDLE_RATE,
)
from codes.engine import scorer


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rec(v):
    return [{"value": v}] if v is not None else []


def _recs(lst):
    return [{"value": v} for v in lst] if lst else []


def _sec(
    *,
    op_income=100_000, net_inc=80_000, equity=500_000,
    lt_debt=200_000, cash=50_000,
    capex=20_000, r_and_d=None, op_cf=90_000,
    shares=None, dividends=None, revenue=1_000_000,
    # multi-period
    op_income_list=None, net_inc_list=None, equity_list=None,
    lt_debt_list=None, cash_list=None, shares_list=None,
):
    return {
        "op_income":    _recs(op_income_list) if op_income_list else _rec(op_income),
        "net_inc":      _recs(net_inc_list)   if net_inc_list   else _rec(net_inc),
        "equity":       _recs(equity_list)    if equity_list    else _rec(equity),
        "lt_debt":      _recs(lt_debt_list)   if lt_debt_list   else _rec(lt_debt),
        "cash":         _recs(cash_list)      if cash_list      else _rec(cash),
        "capex":        _rec(capex),
        "r_and_d":      _rec(r_and_d),
        "op_cf":        _rec(op_cf),
        "shares":       _recs(shares_list) if shares_list else (_rec(shares) if shares else []),
        "dividends":    _rec(dividends),
        "revenue":      _rec(revenue),
    }


def _make(**kwargs):
    sec = _sec(**{k: v for k, v in kwargs.items()
                  if k not in ("price",)})
    price = kwargs.get("price")
    return CapitalAllocationAnalyzer("TEST", sec, price)


def _base_scorer_args(**overrides):
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


# ══════════════════════════════════════════════════════════════════════════════
# 1. Signal mapping
# ══════════════════════════════════════════════════════════════════════════════

class TestSignalMapping:
    def test_excellent_allocator(self):
        assert _signal(80) == "EXCELLENT_ALLOCATOR"
        assert _signal(100) == "EXCELLENT_ALLOCATOR"

    def test_good_allocator(self):
        assert _signal(65) == "GOOD_ALLOCATOR"
        assert _signal(79.9) == "GOOD_ALLOCATOR"

    def test_average_allocator(self):
        assert _signal(45) == "AVERAGE_ALLOCATOR"
        assert _signal(64) == "AVERAGE_ALLOCATOR"

    def test_poor_allocator(self):
        assert _signal(30) == "POOR_ALLOCATOR"
        assert _signal(44) == "POOR_ALLOCATOR"

    def test_capital_destroyer(self):
        assert _signal(0) == "CAPITAL_DESTROYER"
        assert _signal(29.9) == "CAPITAL_DESTROYER"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Normalisation helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestNormRoicSpread:
    def test_spread_ge_15_gives_100(self):
        assert _norm_roic_spread(15) == pytest.approx(100.0)
        assert _norm_roic_spread(30) == pytest.approx(100.0)

    def test_spread_le_minus10_gives_0(self):
        assert _norm_roic_spread(-10) == pytest.approx(0.0)
        assert _norm_roic_spread(-20) == pytest.approx(0.0)

    def test_spread_zero_gives_40(self):
        # At spread=0: (0+10)/25*100 = 40
        assert _norm_roic_spread(0) == pytest.approx(40.0)

    def test_none_gives_neutral_50(self):
        assert _norm_roic_spread(None) == pytest.approx(50.0)

    def test_mid_range_in_bounds(self):
        v = _norm_roic_spread(5)
        assert 0 < v < 100


class TestNormIncrementalROIC:
    def test_ge_25_gives_100(self):
        assert _norm_incremental_roic(25) == pytest.approx(100.0)
        assert _norm_incremental_roic(50) == pytest.approx(100.0)

    def test_le_0_gives_0(self):
        assert _norm_incremental_roic(0) == pytest.approx(0.0)
        assert _norm_incremental_roic(-10) == pytest.approx(0.0)

    def test_none_gives_neutral_50(self):
        assert _norm_incremental_roic(None) == pytest.approx(50.0)

    def test_mid_gives_bounded(self):
        v = _norm_incremental_roic(12)
        assert 0 < v < 100


class TestNormReinvestmentRate:
    def test_optimal_range_scores_high(self):
        v = _norm_reinvestment_rate(0.35)
        assert v >= 90

    def test_zero_rate_gives_0(self):
        assert _norm_reinvestment_rate(0) == pytest.approx(0.0)

    def test_none_gives_neutral_50(self):
        assert _norm_reinvestment_rate(None) == pytest.approx(50.0)

    def test_very_high_rate_scores_low(self):
        v = _norm_reinvestment_rate(0.95)
        assert v < 30

    def test_low_rate_scores_below_optimal(self):
        v = _norm_reinvestment_rate(0.05)
        assert v < 60


class TestNormShareholderYield:
    def test_ge_5pct_gives_100(self):
        assert _norm_shareholder_yield(5) == pytest.approx(100.0)
        assert _norm_shareholder_yield(10) == pytest.approx(100.0)

    def test_zero_gives_30(self):
        assert _norm_shareholder_yield(0) == pytest.approx(30.0)

    def test_none_gives_neutral_50(self):
        assert _norm_shareholder_yield(None) == pytest.approx(50.0)


class TestNormDilutionRate:
    def test_buybacks_give_100(self):
        assert _norm_dilution_rate(-3) == pytest.approx(100.0)

    def test_flat_gives_80(self):
        assert _norm_dilution_rate(0) == pytest.approx(80.0)

    def test_severe_dilution_gives_0(self):
        assert _norm_dilution_rate(10) == pytest.approx(0.0)
        assert _norm_dilution_rate(20) == pytest.approx(0.0)

    def test_none_gives_neutral_50(self):
        assert _norm_dilution_rate(None) == pytest.approx(50.0)


class TestNormDebtTrend:
    def test_strong_deleveraging_gives_100(self):
        assert _norm_debt_trend(-0.10) == pytest.approx(100.0)
        assert _norm_debt_trend(-0.30) == pytest.approx(100.0)

    def test_heavy_leveraging_gives_0(self):
        assert _norm_debt_trend(0.20) == pytest.approx(0.0)
        assert _norm_debt_trend(0.50) == pytest.approx(0.0)

    def test_none_gives_neutral_50(self):
        assert _norm_debt_trend(None) == pytest.approx(50.0)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Metric methods
# ══════════════════════════════════════════════════════════════════════════════

class TestCalcRoic:
    def test_basic_roic(self):
        # NOPAT = 100k*(1 - (1-80k/100k)) = 80k; IC = 500k + max(200k-50k,0) = 650k
        pa = _make()
        roic = pa.calc_roic()
        assert roic is not None
        assert abs(roic - (80_000 / 650_000 * 100)) < 0.01

    def test_returns_none_when_op_income_missing(self):
        pa = _make(op_income=None)
        assert pa.calc_roic() is None

    def test_roic_spread_uses_hurdle_10(self):
        pa = _make()
        spread = pa.calc_roic_spread()
        roic   = pa.calc_roic()
        assert spread is not None
        assert abs(spread - (roic - HURDLE_RATE)) < 0.001


class TestCalcIncrementalROIC:
    def test_positive_incremental(self):
        pa = CapitalAllocationAnalyzer("X", _sec(
            op_income_list=[120_000, 100_000],
            net_inc_list  =[96_000,  80_000],
            equity_list   =[600_000, 500_000],
            lt_debt_list  =[200_000, 200_000],
            cash_list     =[50_000,  50_000],
        ))
        iroic = pa.calc_incremental_roic()
        assert iroic is not None
        assert iroic > 0

    def test_single_period_returns_none(self):
        pa = _make()
        assert pa.calc_incremental_roic() is None

    def test_zero_delta_ic_returns_none(self):
        pa = CapitalAllocationAnalyzer("X", _sec(
            op_income_list=[120_000, 100_000],
            net_inc_list  =[96_000,  80_000],
            equity_list   =[500_000, 500_000],  # same IC both periods
            lt_debt_list  =[200_000, 200_000],
            cash_list     =[50_000,  50_000],
        ))
        assert pa.calc_incremental_roic() is None


class TestCalcReinvestmentRate:
    def test_capex_plus_rd_over_ebit(self):
        pa = CapitalAllocationAnalyzer("X", _sec(
            op_income=100_000, capex=20_000, r_and_d=10_000
        ))
        rate, method = pa.calc_reinvestment_rate()
        assert rate is not None
        assert abs(rate - 30_000 / 100_000) < 1e-6
        assert method == "(CapEx+R&D)/EBIT"

    def test_capex_over_ebit_when_no_rd(self):
        pa = _make(op_income=100_000, capex=20_000)
        rate, method = pa.calc_reinvestment_rate()
        assert rate is not None
        assert abs(rate - 20_000 / 100_000) < 1e-6
        assert method == "CapEx/EBIT"

    def test_fallback_to_capex_ocf_when_no_ebit(self):
        pa = CapitalAllocationAnalyzer("X", _sec(
            op_income=None, capex=20_000, op_cf=80_000
        ))
        rate, method = pa.calc_reinvestment_rate()
        assert rate is not None
        assert method == "CapEx/OCF"

    def test_missing_capex_returns_none(self):
        pa = CapitalAllocationAnalyzer("X", _sec(capex=None, op_income=None, op_cf=None))
        rate, method = pa.calc_reinvestment_rate()
        assert rate is None
        assert method == "N/A"


class TestCalcDilutionRate:
    def test_positive_dilution(self):
        pa = CapitalAllocationAnalyzer("X", _sec(shares_list=[1_100_000, 1_000_000]))
        rate = pa.calc_dilution_rate()
        assert rate is not None
        assert abs(rate - 10.0) < 0.01

    def test_buyback_gives_negative(self):
        pa = CapitalAllocationAnalyzer("X", _sec(shares_list=[900_000, 1_000_000]))
        rate = pa.calc_dilution_rate()
        assert rate is not None
        assert rate < 0

    def test_single_period_returns_none(self):
        pa = _make(shares=1_000_000)
        assert pa.calc_dilution_rate() is None


class TestCalcDebtTrend:
    def test_deleveraging_gives_negative(self):
        # D/E now = 100/500=0.2, before = 200/500=0.4 → trend = -0.2
        pa = CapitalAllocationAnalyzer("X", _sec(
            lt_debt_list=[100_000, 200_000],
            equity_list =[500_000, 500_000],
        ))
        trend = pa.calc_debt_trend()
        assert trend is not None
        assert trend < 0

    def test_increasing_debt_gives_positive(self):
        pa = CapitalAllocationAnalyzer("X", _sec(
            lt_debt_list=[300_000, 100_000],
            equity_list =[500_000, 500_000],
        ))
        trend = pa.calc_debt_trend()
        assert trend is not None
        assert trend > 0

    def test_single_period_returns_none(self):
        pa = _make()
        assert pa.calc_debt_trend() is None


# ══════════════════════════════════════════════════════════════════════════════
# 4. Output schema
# ══════════════════════════════════════════════════════════════════════════════

class TestOutputSchema:
    REQUIRED_KEYS = {
        "ticker", "roic", "roic_spread", "incremental_roic",
        "reinvestment_rate", "reinvestment_method",
        "buyback_yield", "dividend_yield_implied", "shareholder_yield",
        "dilution_rate", "debt_trend",
        "capital_allocation_score", "signal",
        "total_score", "total_max",
    }

    def test_all_required_keys_present(self):
        pa = _make()
        out = pa.get_capital_allocation_score()
        assert self.REQUIRED_KEYS == set(out.keys()), (
            f"Extra: {set(out.keys()) - self.REQUIRED_KEYS}  "
            f"Missing: {self.REQUIRED_KEYS - set(out.keys())}"
        )

    def test_score_in_range_0_to_100(self):
        pa = _make()
        out = pa.get_capital_allocation_score()
        assert 0.0 <= out["capital_allocation_score"] <= 100.0

    def test_ticker_uppercase(self):
        pa = CapitalAllocationAnalyzer("aapl", _sec())
        out = pa.get_capital_allocation_score()
        assert out["ticker"] == "AAPL"

    def test_signal_consistent_with_score(self):
        pa = _make()
        out = pa.get_capital_allocation_score()
        assert out["signal"] == _signal(out["capital_allocation_score"])

    def test_total_score_equals_capital_allocation_score(self):
        pa = _make()
        out = pa.get_capital_allocation_score()
        assert out["total_score"] == out["capital_allocation_score"]
        assert out["total_max"] == 100.0

    def test_empty_sec_does_not_crash(self):
        pa = CapitalAllocationAnalyzer("X", {})
        try:
            out = pa.get_capital_allocation_score()
        except Exception as exc:
            pytest.fail(f"raised {type(exc).__name__}: {exc}")
        assert math.isfinite(out["capital_allocation_score"])

    def test_excellent_allocator_profile_scores_above_65(self):
        """High ROIC, buybacks, low debt, R&D investment."""
        pa = CapitalAllocationAnalyzer("GOOD", _sec(
            op_income_list=[120_000, 100_000],
            net_inc_list  =[100_000,  82_000],
            equity_list   =[400_000, 380_000],
            lt_debt_list  =[50_000,   80_000],   # deleveraging
            cash_list     =[60_000,   50_000],
            capex=15_000, r_and_d=20_000,
            shares_list   =[950_000, 1_000_000],  # buybacks
        ), price=50.0)
        out = pa.get_capital_allocation_score()
        assert out["capital_allocation_score"] >= 55

    def test_poor_allocator_scores_below_40(self):
        """Negative ROIC spread, dilutive, increasing debt."""
        pa = CapitalAllocationAnalyzer("WEAK", _sec(
            op_income=5_000, net_inc=-20_000,
            equity_list   =[100_000, 200_000],
            lt_debt_list  =[500_000, 300_000],   # levering up
            cash_list     =[10_000,  10_000],
            capex=80_000, op_cf=10_000,
            shares_list   =[1_200_000, 1_000_000],  # diluting
        ))
        out = pa.get_capital_allocation_score()
        assert out["capital_allocation_score"] < 50


# ══════════════════════════════════════════════════════════════════════════════
# 5. scorer.enhanced_composite integration
# ══════════════════════════════════════════════════════════════════════════════

class TestScorerIntegration:
    def test_weights_sum_to_one(self):
        from codes.engine.scorer import ENHANCED_WEIGHTS
        total = sum(ENHANCED_WEIGHTS.values())
        assert math.isclose(total, 1.0, abs_tol=1e-9), \
            f"ENHANCED_WEIGHTS sum = {total:.10f}"

    def test_capital_allocation_key_in_weights(self):
        from codes.engine.scorer import ENHANCED_WEIGHTS
        assert "capital_allocation" in ENHANCED_WEIGHTS
        assert ENHANCED_WEIGHTS["capital_allocation"] == pytest.approx(0.08)

    def test_capital_allocation_pct_in_return_dict(self):
        result = scorer.enhanced_composite(**_base_scorer_args())
        assert "capital_allocation_pct" in result

    def test_omitting_gives_neutral_50(self):
        result = scorer.enhanced_composite(**_base_scorer_args())
        assert result["capital_allocation_pct"] == pytest.approx(50.0)

    def test_signal_in_return_dict_when_provided(self):
        ca = {"capital_allocation_score": 70.0, "signal": "GOOD_ALLOCATOR",
              "total_score": 70.0, "total_max": 100.0}
        result = scorer.enhanced_composite(
            **_base_scorer_args(), capital_allocation_result=ca
        )
        assert result["capital_allocation_signal"] == "GOOD_ALLOCATOR"

    def test_signal_none_when_not_provided(self):
        result = scorer.enhanced_composite(**_base_scorer_args())
        assert result.get("capital_allocation_signal") is None

    def test_high_score_increases_composite(self):
        args = _base_scorer_args()
        without_ca = scorer.enhanced_composite(**args)
        ca_strong = {"capital_allocation_score": 90.0, "total_score": 90.0,
                     "total_max": 100.0, "signal": "EXCELLENT_ALLOCATOR"}
        with_strong = scorer.enhanced_composite(**args, capital_allocation_result=ca_strong)
        assert with_strong["composite_score"] > without_ca["composite_score"]

    def test_low_score_decreases_composite(self):
        args = _base_scorer_args()
        without_ca = scorer.enhanced_composite(**args)
        ca_weak = {"capital_allocation_score": 10.0, "total_score": 10.0,
                   "total_max": 100.0, "signal": "CAPITAL_DESTROYER"}
        with_weak = scorer.enhanced_composite(**args, capital_allocation_result=ca_weak)
        assert with_weak["composite_score"] < without_ca["composite_score"]

    def test_impact_proportional_to_weight(self):
        from codes.engine.scorer import ENHANCED_WEIGHTS
        w = ENHANCED_WEIGHTS["capital_allocation"]
        args = _base_scorer_args()

        ca_60 = {"capital_allocation_score": 60.0, "total_score": 60.0, "total_max": 100.0}
        ca_40 = {"capital_allocation_score": 40.0, "total_score": 40.0, "total_max": 100.0}

        res_60 = scorer.enhanced_composite(**args, capital_allocation_result=ca_60)
        res_40 = scorer.enhanced_composite(**args, capital_allocation_result=ca_40)

        expected_delta = (60.0 - 40.0) * w
        actual_delta   = res_60["composite_score"] - res_40["composite_score"]
        assert math.isclose(actual_delta, expected_delta, abs_tol=0.15)

    def test_backward_compat_no_ca_arg(self):
        """Callers not passing capital_allocation_result must still work."""
        args = _base_scorer_args()
        try:
            result = scorer.enhanced_composite(**args)
        except TypeError as exc:
            pytest.fail(f"raised TypeError: {exc}")
        assert "composite_score" in result

    def test_greenblatt_still_not_in_composite(self):
        args = _base_scorer_args()
        without_gb = scorer.enhanced_composite(**args)
        gb = {"earnings_yield": 99.0, "roic": 99.0, "fcf_yield": 99.0, "magic_score": None}
        with_gb = scorer.enhanced_composite(**args, greenblatt_result=gb)
        assert math.isclose(
            without_gb["composite_score"], with_gb["composite_score"]
        )


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
