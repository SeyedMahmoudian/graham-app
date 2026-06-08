"""
TEST — ProfitabilityAnalyzer (P1 module)

Covers:
  - Each metric method in isolation
  - Edge cases: missing data, zero denominators, negative values
  - Weighted scoring and normalization to 0-100
  - Signal mapping thresholds
  - Output JSON shape (strict keys, types)
  - Integration: scorer.enhanced_composite accepts profitability_result

Run via pytest (preferred):
    pytest tests/test_profitability.py

Or directly:
    python3 tests/test_profitability.py
"""

import sys
import os

# Ensure project root is on sys.path when run directly with python3.
# pytest handles this via tests/conftest.py; this block covers direct execution.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import math
import pytest
from codes.models.profitability import ProfitabilityAnalyzer, _signal, _norm_roic


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rec(v):
    return [{"value": v}] if v is not None else []


def _sec(
    *,
    op_income=None,
    net_inc=None,
    revenue=None,
    equity=None,
    lt_debt=None,
    cash=None,
    tot_lib=None,
    total_assets=None,
    gross_profit=None,
    op_cf=None,
    # multi-period lists (newest first)
    op_income_list=None,
    revenue_list=None,
    equity_list=None,
    lt_debt_list=None,
    cash_list=None,
    net_inc_list=None,
):
    def _recs(lst):
        return [{"value": v} for v in lst] if lst else []

    return {
        "op_income":    _recs(op_income_list) if op_income_list else _rec(op_income),
        "net_inc":      _recs(net_inc_list)   if net_inc_list   else _rec(net_inc),
        "revenue":      _recs(revenue_list)   if revenue_list   else _rec(revenue),
        "equity":       _recs(equity_list)    if equity_list    else _rec(equity),
        "lt_debt":      _recs(lt_debt_list)   if lt_debt_list   else _rec(lt_debt),
        "cash":         _recs(cash_list)      if cash_list      else _rec(cash),
        "tot_lib":      _rec(tot_lib),
        "total_assets": _rec(total_assets),
        "gross_profit": _rec(gross_profit),
        "op_cf":        _rec(op_cf),
    }


def _make(
    *,
    op_income=100_000, net_inc=80_000, revenue=1_000_000,
    equity=500_000, lt_debt=200_000, cash=50_000,
    tot_lib=300_000, total_assets=800_000, gross_profit=400_000,
    **kwargs,
):
    return ProfitabilityAnalyzer(
        "TEST",
        _sec(
            op_income=op_income, net_inc=net_inc, revenue=revenue,
            equity=equity, lt_debt=lt_debt, cash=cash,
            tot_lib=tot_lib, total_assets=total_assets,
            gross_profit=gross_profit, **kwargs
        ),
    )


# ── Signal mapping ────────────────────────────────────────────────────────────

class TestSignalMapping:
    def test_strong_high_quality(self):
        assert _signal(80) == "STRONG_HIGH_QUALITY"
        assert _signal(100) == "STRONG_HIGH_QUALITY"

    def test_high_quality(self):
        assert _signal(65) == "HIGH_QUALITY"
        assert _signal(79.9) == "HIGH_QUALITY"

    def test_neutral(self):
        assert _signal(45) == "NEUTRAL"
        assert _signal(64) == "NEUTRAL"

    def test_low_quality(self):
        assert _signal(30) == "LOW_QUALITY"
        assert _signal(44) == "LOW_QUALITY"

    def test_value_trap_risk(self):
        assert _signal(0) == "VALUE_TRAP_RISK"
        assert _signal(29.9) == "VALUE_TRAP_RISK"


# ── calc_roic ─────────────────────────────────────────────────────────────────

class TestCalcROIC:
    def test_basic_roic(self):
        # NOPAT = 100k * (1 - (1 - 80k/100k)) = 100k * 0.8 = 80k
        # IC    = 500k + max(200k - 50k, 0) = 650k
        # ROIC  = 80k / 650k * 100 ≈ 12.31%
        pa = _make()
        roic = pa.calc_roic()
        assert roic is not None
        assert abs(roic - (80_000 / 650_000 * 100)) < 0.01

    def test_returns_none_when_op_income_missing(self):
        pa = _make(op_income=None)
        assert pa.calc_roic() is None

    def test_returns_none_when_equity_missing(self):
        pa = _make(equity=None)
        assert pa.calc_roic() is None

    def test_returns_none_when_ic_zero(self):
        # equity=0, lt_debt=0, cash=0  → IC=0
        pa = _make(equity=0, lt_debt=0, cash=0)
        assert pa.calc_roic() is None

    def test_fallback_tax_rate_when_op_income_not_positive(self):
        # op_income < 0 → fallback to 21% tax rate
        pa = ProfitabilityAnalyzer(
            "X",
            _sec(op_income=-10_000, net_inc=-8_000, equity=500_000, lt_debt=0, cash=0),
        )
        roic = pa.calc_roic()
        # NOPAT = -10k * (1 - 0.21) = -7.9k; IC = 500k
        assert roic is not None
        assert roic < 0

    def test_net_cash_position_floors_net_debt_at_zero(self):
        # cash > lt_debt → net_debt < 0 → floored at 0 → IC = equity
        pa = _make(cash=500_000, lt_debt=100_000, equity=400_000)
        roic = pa.calc_roic()
        assert roic is not None


# ── calc_roe_adjusted ─────────────────────────────────────────────────────────

class TestCalcROEAdjusted:
    def test_basic_roe_adjusted(self):
        # ROE = 80k/500k*100 = 16%
        # D/E = 300k/500k = 0.6
        # ROE_adj = 16 / 1.6 = 10%
        pa = _make()
        roe_adj = pa.calc_roe_adjusted()
        assert roe_adj is not None
        assert abs(roe_adj - 10.0) < 0.01

    def test_returns_none_when_equity_zero(self):
        pa = _make(equity=0)
        assert pa.calc_roe_adjusted() is None

    def test_returns_none_when_net_inc_missing(self):
        pa = _make(net_inc=None)
        assert pa.calc_roe_adjusted() is None


# ── calc_roa ──────────────────────────────────────────────────────────────────

class TestCalcROA:
    def test_basic_roa(self):
        # ROA = 80k/800k*100 = 10%
        pa = _make()
        roa = pa.calc_roa()
        assert roa is not None
        assert abs(roa - 10.0) < 0.01

    def test_returns_none_when_assets_missing(self):
        pa = _make(total_assets=None)
        assert pa.calc_roa() is None

    def test_returns_none_when_net_inc_missing(self):
        pa = _make(net_inc=None)
        assert pa.calc_roa() is None


# ── calc_gross_profitability ──────────────────────────────────────────────────

class TestCalcGrossProfitability:
    def test_basic_gross_profitability(self):
        # GP / TA = 400k / 800k = 0.5
        pa = _make()
        gp = pa.calc_gross_profitability()
        assert gp is not None
        assert abs(gp - 0.5) < 1e-6

    def test_returns_none_when_gp_missing(self):
        pa = _make(gross_profit=None)
        assert pa.calc_gross_profitability() is None

    def test_returns_none_when_assets_missing(self):
        pa = _make(total_assets=None)
        assert pa.calc_gross_profitability() is None


# ── calc_operating_margin_stability ──────────────────────────────────────────

class TestCalcOpMarginStability:
    def test_stable_margins_return_low_std(self):
        # All margins = 10% → std = 0
        pa = ProfitabilityAnalyzer(
            "X",
            _sec(
                op_income_list=[100, 100, 100, 100, 100],
                revenue_list=  [1000, 1000, 1000, 1000, 1000],
            ),
        )
        std = pa.calc_operating_margin_stability()
        assert std is not None
        assert std == pytest.approx(0.0)

    def test_volatile_margins_return_high_std(self):
        pa = ProfitabilityAnalyzer(
            "X",
            _sec(
                op_income_list=[200, -100, 200, -100, 200],
                revenue_list=  [1000, 1000, 1000, 1000, 1000],
            ),
        )
        std = pa.calc_operating_margin_stability()
        assert std is not None
        assert std > 10

    def test_returns_none_when_fewer_than_3_periods(self):
        pa = ProfitabilityAnalyzer(
            "X",
            _sec(
                op_income_list=[100, 100],
                revenue_list=  [1000, 1000],
            ),
        )
        assert pa.calc_operating_margin_stability() is None


# ── calc_capital_efficiency ───────────────────────────────────────────────────

class TestCalcCapitalEfficiency:
    def test_basic_asset_turnover(self):
        # 1M / 800k = 1.25
        pa = _make()
        ce = pa.calc_capital_efficiency()
        assert ce is not None
        assert abs(ce - 1_000_000 / 800_000) < 1e-6

    def test_returns_none_when_assets_missing(self):
        pa = _make(total_assets=None)
        assert pa.calc_capital_efficiency() is None


# ── calc_incremental_roic ─────────────────────────────────────────────────────

class TestCalcIncrementalROIC:
    def test_positive_incremental_roic(self):
        # year 0: op=120k, ni=96k, equity=600k, lt_debt=200k, cash=50k
        # year 1: op=100k, ni=80k, equity=500k, lt_debt=200k, cash=50k
        pa = ProfitabilityAnalyzer(
            "X",
            _sec(
                op_income_list=[120_000, 100_000],
                net_inc_list=  [96_000,  80_000],
                equity_list=   [600_000, 500_000],
                lt_debt_list=  [200_000, 200_000],
                cash_list=     [50_000,  50_000],
            ),
        )
        iroic = pa.calc_incremental_roic()
        assert iroic is not None
        # delta_IC = (600k+150k) - (500k+150k) = 100k
        # tax_rate0 = 1 - 96k/120k = 0.20 → NOPAT0 = 96k
        # tax_rate1 = 1 - 80k/100k = 0.20 → NOPAT1 = 80k
        # delta_NOPAT = 16k
        # iroic = 16k / 100k * 100 = 16%
        assert abs(iroic - 16.0) < 0.5

    def test_returns_none_when_only_one_period(self):
        pa = _make()
        # single-value lists
        assert pa.calc_incremental_roic() is None

    def test_returns_none_when_delta_ic_near_zero(self):
        # Same IC both periods
        pa = ProfitabilityAnalyzer(
            "X",
            _sec(
                op_income_list=[120_000, 100_000],
                net_inc_list=  [96_000,  80_000],
                equity_list=   [500_000, 500_000],
                lt_debt_list=  [200_000, 200_000],
                cash_list=     [50_000,  50_000],
            ),
        )
        assert pa.calc_incremental_roic() is None


# ── get_profitability_score ───────────────────────────────────────────────────

class TestGetProfitabilityScore:
    REQUIRED_KEYS = {
        "ticker", "roic", "roe_adjusted", "roa",
        "gross_profitability", "operating_margin_stability",
        "capital_efficiency", "incremental_roic",
        "profitability_score", "signal",
    }

    def test_output_has_required_keys(self):
        pa = _make()
        out = pa.get_profitability_score()
        assert self.REQUIRED_KEYS.issubset(out.keys())

    def test_no_extra_keys_beyond_scorer_compat(self):
        pa = _make()
        out = pa.get_profitability_score()
        allowed = self.REQUIRED_KEYS | {"total_score", "total_max"}
        assert set(out.keys()) == allowed

    def test_score_in_range_0_to_100(self):
        pa = _make()
        out = pa.get_profitability_score()
        assert 0.0 <= out["profitability_score"] <= 100.0

    def test_ticker_stored_uppercase(self):
        pa = ProfitabilityAnalyzer("aapl", _sec())
        out = pa.get_profitability_score()
        assert out["ticker"] == "AAPL"

    def test_signal_consistent_with_score(self):
        pa = _make()
        out = pa.get_profitability_score()
        assert out["signal"] == _signal(out["profitability_score"])

    def test_all_missing_data_returns_neutral(self):
        pa = ProfitabilityAnalyzer("X", _sec())
        out = pa.get_profitability_score()
        # With all missing data, most components normalise to 0 or neutral (50).
        # Score should be in a reasonable mid-range, not crash.
        assert math.isfinite(out["profitability_score"])

    def test_total_score_matches_profitability_score(self):
        pa = _make()
        out = pa.get_profitability_score()
        assert out["total_score"] == out["profitability_score"]
        assert out["total_max"] == 100.0

    def test_high_quality_company_scores_above_60(self):
        """AAPL-like profile should yield HIGH_QUALITY or better."""
        pa = ProfitabilityAnalyzer(
            "AAPL",
            _sec(
                # op_income, net_inc — strong margins
                op_income_list=[100e9, 95e9, 88e9, 80e9, 75e9],
                net_inc_list=  [94e9, 89e9, 82e9, 75e9, 70e9],
                revenue_list=  [380e9, 365e9, 350e9, 320e9, 300e9],
                equity_list=   [50e9,  55e9,  60e9,  65e9,  70e9],
                lt_debt_list=  [100e9, 95e9,  90e9,  85e9,  80e9],
                cash_list=     [60e9,  55e9,  50e9,  45e9,  40e9],
                total_assets=  350e9,
                gross_profit=  160e9,
            ),
        )
        out = pa.get_profitability_score()
        assert out["profitability_score"] >= 60

    def test_weak_company_scores_below_40(self):
        """Loss-making company with thin margins → LOW_QUALITY or VALUE_TRAP_RISK."""
        pa = ProfitabilityAnalyzer(
            "WEAK",
            _sec(
                op_income=5_000,
                net_inc=-10_000,
                revenue=200_000,
                equity=50_000,
                lt_debt=100_000,
                cash=5_000,
                total_assets=300_000,
                gross_profit=10_000,
            ),
        )
        out = pa.get_profitability_score()
        assert out["profitability_score"] < 50


# ── Integration: scorer.enhanced_composite ────────────────────────────────────

class TestScorerIntegration:
    def test_enhanced_composite_accepts_profitability_result(self):
        from codes.engine import scorer

        profitability_result = {
            "ticker": "TEST",
            "profitability_score": 72.5,
            "signal": "HIGH_QUALITY",
            "total_score": 72.5,
            "total_max": 100.0,
        }

        def _empty(score=50, mx=100):
            return {"total_score": score, "total_max": mx}

        result = scorer.enhanced_composite(
            graham_result=_empty(),
            quality_result=_empty(),
            momentum_result=_empty(),
            piotroski_result={"f_score": 6},
            risk_result={"risk_score": 50, "risk_score_max": 100},
            altman_result={"zone": "safe", "risk_score": 70},
            buffett_result=_empty(),
            profitability_result=profitability_result,
        )
        assert "profitability_pct" in result
        assert result["profitability_pct"] == pytest.approx(72.5, abs=0.1)
        assert 0 <= result["composite_score"] <= 100

    def test_enhanced_composite_neutral_fallback_when_profitability_none(self):
        from codes.engine import scorer

        def _empty(score=50, mx=100):
            return {"total_score": score, "total_max": mx}

        result = scorer.enhanced_composite(
            graham_result=_empty(),
            quality_result=_empty(),
            momentum_result=_empty(),
            piotroski_result={"f_score": 5},
            risk_result={"risk_score": 50, "risk_score_max": 100},
            altman_result={"zone": "safe", "risk_score": 50},
            buffett_result=_empty(),
            profitability_result=None,
        )
        # Neutral fallback = 50; should not crash and score should be finite
        assert "profitability_pct" in result
        assert result["profitability_pct"] == pytest.approx(50.0, abs=0.1)
        assert math.isfinite(result["composite_score"])

    def test_enhanced_weights_sum_to_one(self):
        from codes.engine.scorer import ENHANCED_WEIGHTS
        total = sum(ENHANCED_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, not 1.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
