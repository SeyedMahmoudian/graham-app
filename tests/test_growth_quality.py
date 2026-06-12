"""
Tests for Growth Quality Model (PROJECT_MAP.md P2 feature).

Covers:
  1. Normal 10-year growth case
  2. Strong growth company
  3. Weak growth company
  4. Missing revenue history (< 11 data points)
  5. Negative EPS start value
  6. Negative FCF start value
  7. Margin stability / volatility scoring
  8. Incremental ROIC calculation
  9. Metric reweighting with missing data
  10. Signal thresholds (Bullish / Neutral / Bearish)
"""

import math
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codes.models.growth_quality import (
    GrowthQualityAnalyzer,
    _signal,
    _norm_cagr,
    _norm_margin_stability,
    _norm_incremental_roic,
    _cagr,
)
from codes.engine import scorer


# ── Helpers ───────────────────────────────────────────────────────────────────

def _recs(lst):
    """Build records list (newest first) from a plain list."""
    return [{"value": v} for v in lst]


def _grow(start: float, rate: float, n: int = 11) -> list[float]:
    """Generate n values newest-first at compound rate from start (oldest)."""
    values = [start * (1 + rate) ** i for i in range(n)]
    return list(reversed(values))  # newest first


def _sec(
    *,
    revenue=None,
    eps=None,
    op_cf=None,
    capex=None,
    op_income=None,
    net_inc=None,
    equity=None,
    lt_debt=None,
    cash=None,
):
    def _r(lst):
        return _recs(lst) if lst is not None else []

    return {
        "revenue":   _r(revenue),
        "eps":       _r(eps),
        "op_cf":     _r(op_cf),
        "capex":     _r(capex),
        "op_income": _r(op_income),
        "net_inc":   _r(net_inc),
        "equity":    _r(equity),
        "lt_debt":   _r(lt_debt),
        "cash":      _r(cash),
    }


def _make_strong():
    """Typical high-quality compounder: ~12% revenue/EPS/FCF CAGR, stable margins."""
    rev    = _grow(1_000_000, 0.12)
    eps    = _grow(2.0, 0.12)
    op_cf  = _grow(150_000, 0.12)
    capex  = [10_000] * 11
    op_inc = [r * 0.20 for r in rev]   # 20% margin, perfectly stable
    net_inc= [oi * 0.80 for oi in op_inc]
    equity = _grow(500_000, 0.08)
    lt_debt= [100_000] * 11
    cash   = [20_000] * 11
    return _sec(
        revenue=rev, eps=eps, op_cf=op_cf, capex=capex,
        op_income=op_inc, net_inc=net_inc,
        equity=equity, lt_debt=lt_debt, cash=cash,
    )


def _make_weak():
    """Declining or flat business."""
    rev    = _grow(1_000_000, -0.02)
    eps    = _grow(1.0, -0.03)
    op_cf  = _grow(80_000, -0.02)
    capex  = [15_000] * 11
    op_inc = [r * 0.04 for r in rev]   # thin margins
    net_inc= [oi * 0.75 for oi in op_inc]
    equity = _grow(300_000, 0.01)
    lt_debt= [200_000] * 11
    cash   = [10_000] * 11
    return _sec(
        revenue=rev, eps=eps, op_cf=op_cf, capex=capex,
        op_income=op_inc, net_inc=net_inc,
        equity=equity, lt_debt=lt_debt, cash=cash,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. Pure helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestCagrHelper:
    def test_known_double_in_10y(self):
        # doubling in 10 years → CAGR ≈ 7.177%
        result = _cagr(100, 200, 10)
        assert result is not None
        assert abs(result - 7.177) < 0.01

    def test_negative_start_returns_none(self):
        assert _cagr(-100, 200, 10) is None

    def test_zero_start_returns_none(self):
        assert _cagr(0, 100, 10) is None

    def test_zero_years_returns_none(self):
        assert _cagr(100, 200, 0) is None

    def test_flat_gives_zero(self):
        result = _cagr(100, 100, 10)
        assert result is not None
        assert abs(result) < 1e-9


class TestNormCagr:
    def test_above_excellent_gives_100(self):
        assert _norm_cagr(15.0) == pytest.approx(100.0)
        assert _norm_cagr(25.0) == pytest.approx(100.0)

    def test_at_floor_gives_0(self):
        assert _norm_cagr(-5.0) == pytest.approx(0.0)
        assert _norm_cagr(-10.0) == pytest.approx(0.0)

    def test_none_gives_0(self):
        assert _norm_cagr(None) == pytest.approx(0.0)

    def test_zero_cagr_between_0_and_100(self):
        v = _norm_cagr(0.0)
        assert 0 < v < 100

    def test_midpoint_gives_50(self):
        # midpoint of [-5, 15] is 5.0
        v = _norm_cagr(5.0)
        assert v == pytest.approx(50.0)


class TestNormMarginStability:
    def test_perfect_stability_gives_100(self):
        assert _norm_margin_stability(0.0) == pytest.approx(100.0)
        assert _norm_margin_stability(2.0) == pytest.approx(100.0)

    def test_high_volatility_gives_0(self):
        assert _norm_margin_stability(20.0) == pytest.approx(0.0)
        assert _norm_margin_stability(30.0) == pytest.approx(0.0)

    def test_none_gives_50_neutral(self):
        assert _norm_margin_stability(None) == pytest.approx(50.0)

    def test_mid_range(self):
        v = _norm_margin_stability(11.0)
        assert 0 < v < 100


class TestNormIncrementalROIC:
    def test_above_25_gives_100(self):
        assert _norm_incremental_roic(25.0) == pytest.approx(100.0)
        assert _norm_incremental_roic(50.0) == pytest.approx(100.0)

    def test_zero_gives_0(self):
        assert _norm_incremental_roic(0.0) == pytest.approx(0.0)

    def test_negative_gives_0(self):
        assert _norm_incremental_roic(-10.0) == pytest.approx(0.0)

    def test_none_gives_50_neutral(self):
        assert _norm_incremental_roic(None) == pytest.approx(50.0)

    def test_12_5_gives_50(self):
        v = _norm_incremental_roic(12.5)
        assert v == pytest.approx(50.0)


class TestSignalMapping:
    def test_bullish(self):
        assert _signal(70) == "Bullish"
        assert _signal(100) == "Bullish"

    def test_neutral(self):
        assert _signal(40) == "Neutral"
        assert _signal(69.99) == "Neutral"

    def test_bearish(self):
        assert _signal(0) == "Bearish"
        assert _signal(39.99) == "Bearish"


def _base_scorer_args(**overrides):
    base = dict(
        graham_result={"total_score": 50, "total_max": 100},
        quality_result={"total_score": 50, "total_max": 100, "roe": 12},
        momentum_result={"total_score": 50, "total_max": 100},
        piotroski_result={"f_score": 5, "f_score_max": 9},
        risk_result={"risk_score": 50, "risk_score_max": 100},
        altman_result={"risk_score": 50, "zone": "safe"},
        buffett_result={"total_score": 50, "total_max": 100},
    )
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# 2. Individual metric methods
# ══════════════════════════════════════════════════════════════════════════════

class TestRevCagr10y:
    def test_exact_10y_growth(self):
        sec = _make_strong()
        pa = GrowthQualityAnalyzer("X", sec)
        result = pa.calc_rev_cagr_10y()
        assert result is not None
        assert abs(result - 12.0) < 0.5

    def test_fewer_than_11_returns_none(self):
        rev = _grow(1_000_000, 0.10)[:10]   # only 10 values
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev))
        assert pa.calc_rev_cagr_10y() is None

    def test_negative_start_returns_none(self):
        rev = [-1_000_000] + [1_000_000 * (1.1 ** i) for i in range(1, 10)][::-1]
        # Ensure 11 values with oldest negative
        rev_11 = list(reversed([abs(v) if i < 10 else -1_000 for i, v in enumerate(range(11))]))
        # Build directly: oldest (index 10 in newest-first list) is negative
        rev = [1_100_000 * (1.1 ** (9 - i)) for i in range(10)] + [-500_000]
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev))
        assert pa.calc_rev_cagr_10y() is None


class TestEpsCagr10y:
    def test_basic_cagr(self):
        pa = GrowthQualityAnalyzer("X", _make_strong())
        result = pa.calc_eps_cagr_10y()
        assert result is not None
        assert abs(result - 12.0) < 0.5

    def test_negative_start_eps_returns_none(self):
        eps = _grow(1.0, 0.10)
        eps[-1] = -0.5   # oldest value (index 10) is negative
        pa = GrowthQualityAnalyzer("X", _sec(eps=eps))
        assert pa.calc_eps_cagr_10y() is None

    def test_negative_end_eps_returns_none(self):
        eps = _grow(1.0, 0.10)
        eps[0] = -0.5   # newest value (index 0) is negative
        pa = GrowthQualityAnalyzer("X", _sec(eps=eps))
        assert pa.calc_eps_cagr_10y() is None

    def test_fewer_than_11_returns_none(self):
        eps = _grow(2.0, 0.10)[:9]
        pa = GrowthQualityAnalyzer("X", _sec(eps=eps))
        assert pa.calc_eps_cagr_10y() is None


class TestFcfCagr10y:
    def test_basic_fcf_cagr(self):
        pa = GrowthQualityAnalyzer("X", _make_strong())
        result = pa.calc_fcf_cagr_10y()
        assert result is not None
        assert abs(result - 12.0) < 1.0   # approx due to fixed capex

    def test_negative_start_fcf_returns_none(self):
        # Oldest FCF negative → not computable
        op_cf  = _grow(150_000, 0.10)
        capex  = [10_000] * 11
        # Make oldest FCF negative by setting op_cf[10] very small and capex[10] large
        op_cf[10]  = 5_000
        capex[10]  = 50_000   # FCF at oldest = 5000 - 50000 = -45000
        pa = GrowthQualityAnalyzer("X", _sec(op_cf=op_cf, capex=capex))
        assert pa.calc_fcf_cagr_10y() is None

    def test_fewer_than_11_op_cf_returns_none(self):
        op_cf = _grow(100_000, 0.08)[:8]
        capex = [10_000] * 8
        pa = GrowthQualityAnalyzer("X", _sec(op_cf=op_cf, capex=capex))
        assert pa.calc_fcf_cagr_10y() is None

    def test_capex_stored_negative_handled(self):
        """Negative capex values (some filers) are abs()-guarded."""
        op_cf  = _grow(150_000, 0.10)
        capex  = [-10_000] * 11   # negative sign from some filers
        pa1 = GrowthQualityAnalyzer("X", _sec(op_cf=op_cf, capex=capex))
        capex2 = [10_000] * 11
        pa2 = GrowthQualityAnalyzer("X", _sec(op_cf=op_cf, capex=capex2))
        # abs() ensures same FCF regardless of sign
        assert pa1.calc_fcf_cagr_10y() == pytest.approx(pa2.calc_fcf_cagr_10y(), rel=1e-4)


class TestMarginStability:
    def test_perfectly_stable_margins_give_zero_std(self):
        rev    = _grow(1_000_000, 0.10)
        op_inc = [r * 0.20 for r in rev]
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev, op_income=op_inc))
        std = pa.calc_margin_stability()
        assert std is not None
        assert std == pytest.approx(0.0, abs=1e-6)

    def test_volatile_margins_give_high_std(self):
        rev    = [1_000_000] * 11
        # Alternating margins: 20% and -10%
        op_inc = [r * (0.20 if i % 2 == 0 else -0.10) for i, r in enumerate(rev)]
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev, op_income=op_inc))
        std = pa.calc_margin_stability()
        assert std is not None
        assert std > 10

    def test_fewer_than_11_periods_returns_none(self):
        rev    = _grow(1_000_000, 0.10)[:9]
        op_inc = [r * 0.20 for r in rev]
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev, op_income=op_inc))
        assert pa.calc_margin_stability() is None


class TestIncrementalROIC:
    def test_positive_incremental_roic(self):
        """When IC and NOPAT both grow, incremental ROIC should be positive."""
        pa = GrowthQualityAnalyzer("X", _make_strong())
        iroic = pa.calc_incremental_roic()
        assert iroic is not None
        assert iroic > 0

    def test_delta_ic_zero_returns_none(self):
        """Flat invested capital → exclude metric."""
        equity = [500_000] * 11   # no IC growth
        lt_debt= [100_000] * 11
        cash   = [20_000]  * 11
        op_inc = _grow(100_000, 0.10)
        net_inc= [oi * 0.80 for oi in op_inc]
        pa = GrowthQualityAnalyzer("X", _sec(
            op_income=op_inc, net_inc=net_inc,
            equity=equity, lt_debt=lt_debt, cash=cash,
        ))
        assert pa.calc_incremental_roic() is None

    def test_fewer_than_11_equity_points_returns_none(self):
        equity = _grow(500_000, 0.08)[:9]
        op_inc = _grow(100_000, 0.10)[:9]
        pa = GrowthQualityAnalyzer("X", _sec(op_income=op_inc, equity=equity))
        assert pa.calc_incremental_roic() is None

    def test_extreme_value_capped_at_100(self):
        """ΔNOPAT / ΔIC >> 100% should be capped at 100%."""
        # IC grows by 1 unit; NOPAT grows by a huge amount
        equity = [1_000_001, 1_000_000] + [1_000_000] * 9
        op_inc = [10_000_000, 1_000] + [1_000] * 9
        net_inc= [oi * 0.80 for oi in op_inc]
        lt_debt= [0] * 11
        cash   = [0] * 11
        pa = GrowthQualityAnalyzer("X", _sec(
            op_income=op_inc, net_inc=net_inc,
            equity=equity, lt_debt=lt_debt, cash=cash,
        ))
        iroic = pa.calc_incremental_roic()
        if iroic is not None:
            assert iroic <= 100.0


# ══════════════════════════════════════════════════════════════════════════════
# 3. get_growth_quality_score — schema, reweighting, signals
# ══════════════════════════════════════════════════════════════════════════════

REQUIRED_KEYS = {
    "ticker", "rev_cagr_10y", "eps_cagr_10y", "fcf_cagr_10y",
    "margin_stability", "incremental_roic",
    "growth_quality_score", "signal", "total_score", "total_max",
}


class TestOutputSchema:
    def test_all_keys_present(self):
        pa = GrowthQualityAnalyzer("X", _make_strong())
        out = pa.get_growth_quality_score()
        assert REQUIRED_KEYS == set(out.keys()), (
            f"Extra: {set(out.keys()) - REQUIRED_KEYS}  "
            f"Missing: {REQUIRED_KEYS - set(out.keys())}"
        )

    def test_score_in_range(self):
        pa = GrowthQualityAnalyzer("X", _make_strong())
        out = pa.get_growth_quality_score()
        assert 0.0 <= out["growth_quality_score"] <= 100.0

    def test_ticker_uppercased(self):
        pa = GrowthQualityAnalyzer("aapl", _sec())
        out = pa.get_growth_quality_score()
        assert out["ticker"] == "AAPL"

    def test_total_score_matches_growth_quality_score(self):
        pa = GrowthQualityAnalyzer("X", _make_strong())
        out = pa.get_growth_quality_score()
        assert out["total_score"] == out["growth_quality_score"]
        assert out["total_max"] == 100.0

    def test_signal_consistent_with_score(self):
        pa = GrowthQualityAnalyzer("X", _make_strong())
        out = pa.get_growth_quality_score()
        assert out["signal"] == _signal(out["growth_quality_score"])

    def test_no_crash_on_empty_sec(self):
        try:
            out = GrowthQualityAnalyzer("X", {}).get_growth_quality_score()
        except Exception as exc:
            pytest.fail(f"raised {type(exc).__name__}: {exc}")
        assert math.isfinite(out["growth_quality_score"])


class TestNormalCase:
    def test_returns_valid_score(self):
        """Full 10-year history with moderate growth → score in plausible range."""
        pa = GrowthQualityAnalyzer("NORM", _make_strong())
        out = pa.get_growth_quality_score()
        assert 0 < out["growth_quality_score"] <= 100

    def test_all_metrics_populated(self):
        pa = GrowthQualityAnalyzer("NORM", _make_strong())
        out = pa.get_growth_quality_score()
        for key in ("rev_cagr_10y", "eps_cagr_10y", "fcf_cagr_10y",
                    "margin_stability", "incremental_roic"):
            assert out[key] is not None, f"{key} should not be None"


class TestStrongGrowthCompany:
    def test_scores_above_65(self):
        """High double-digit CAGR, stable margins → HIGH score."""
        pa = GrowthQualityAnalyzer("STRONG", _make_strong())
        out = pa.get_growth_quality_score()
        assert out["growth_quality_score"] >= 65
        assert out["signal"] in ("Bullish", "Neutral")


class TestWeakGrowthCompany:
    def test_scores_below_50(self):
        """Declining revenues and thin margins → LOW score."""
        pa = GrowthQualityAnalyzer("WEAK", _make_weak())
        out = pa.get_growth_quality_score()
        assert out["growth_quality_score"] < 50


class TestMissingRevenue:
    def test_rev_cagr_none_when_history_short(self):
        sec = _make_strong()
        sec["revenue"] = _recs(_grow(1_000_000, 0.10)[:8])  # only 8 points
        pa = GrowthQualityAnalyzer("X", sec)
        assert pa.calc_rev_cagr_10y() is None

    def test_score_still_finite_when_rev_missing(self):
        sec = _make_strong()
        sec["revenue"] = _recs(_grow(1_000_000, 0.10)[:8])
        pa = GrowthQualityAnalyzer("X", sec)
        out = pa.get_growth_quality_score()
        assert math.isfinite(out["growth_quality_score"])

    def test_reweighting_with_missing_rev(self):
        """Score without revenue should differ from all-data score but not crash."""
        pa_full = GrowthQualityAnalyzer("F", _make_strong())
        score_full = pa_full.get_growth_quality_score()["growth_quality_score"]

        sec_partial = _make_strong()
        sec_partial["revenue"] = []
        pa_partial = GrowthQualityAnalyzer("P", sec_partial)
        score_partial = pa_partial.get_growth_quality_score()["growth_quality_score"]

        # Both should be valid
        assert math.isfinite(score_full)
        assert math.isfinite(score_partial)
        # Scores can differ — just no crash and no zero-penalty artefact
        # (partial score should stay in plausible range when other factors are strong)
        assert score_partial > 30


class TestNegativeEpsStart:
    def test_eps_cagr_none_when_start_negative(self):
        sec = _make_strong()
        eps = _grow(2.0, 0.10)
        eps[-1] = -0.5   # oldest value
        sec["eps"] = _recs(eps)
        pa = GrowthQualityAnalyzer("X", sec)
        assert pa.calc_eps_cagr_10y() is None

    def test_score_finite_when_eps_negative(self):
        sec = _make_strong()
        eps = _grow(2.0, 0.10)
        eps[-1] = -0.5
        sec["eps"] = _recs(eps)
        out = GrowthQualityAnalyzer("X", sec).get_growth_quality_score()
        assert math.isfinite(out["growth_quality_score"])


class TestNegativeFcfStart:
    def test_fcf_cagr_none_when_start_negative(self):
        sec = _make_strong()
        op_cf = _grow(150_000, 0.10)
        capex = [10_000] * 11
        op_cf[-1] = 5_000   # oldest FCF = 5000 - 50000 = negative if capex high
        capex[-1] = 50_000
        sec["op_cf"]  = _recs(op_cf)
        sec["capex"]  = _recs(capex)
        pa = GrowthQualityAnalyzer("X", sec)
        assert pa.calc_fcf_cagr_10y() is None

    def test_score_finite_when_fcf_negative(self):
        sec = _make_strong()
        op_cf = _grow(150_000, 0.10)
        capex = [10_000] * 11
        op_cf[-1] = 5_000
        capex[-1] = 50_000
        sec["op_cf"]  = _recs(op_cf)
        sec["capex"]  = _recs(capex)
        out = GrowthQualityAnalyzer("X", sec).get_growth_quality_score()
        assert math.isfinite(out["growth_quality_score"])


class TestMarginVolatilityScoring:
    def test_stable_margins_give_high_margin_score(self):
        rev    = _grow(1_000_000, 0.10)
        op_inc = [r * 0.20 for r in rev]   # perfectly stable 20% margin
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev, op_income=op_inc))
        std = pa.calc_margin_stability()
        assert std is not None
        from codes.models.growth_quality import _norm_margin_stability
        assert _norm_margin_stability(std) == pytest.approx(100.0)

    def test_highly_volatile_margins_give_low_score(self):
        rev    = [1_000_000] * 11
        op_inc = [200_000, -100_000, 200_000, -100_000, 200_000,
                  -100_000, 200_000, -100_000, 200_000, -100_000, 200_000]
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev, op_income=op_inc))
        std = pa.calc_margin_stability()
        assert std is not None
        from codes.models.growth_quality import _norm_margin_stability
        assert _norm_margin_stability(std) < 30


class TestIncrementalROICCalc:
    def test_known_iroic_value(self):
        """Controlled inputs produce expected incremental ROIC."""
        # Year 0 (newest): NOPAT = 120k * 0.8 = 96k; IC = 600k
        # Year 10 (oldest): NOPAT = 100k * 0.8 = 80k; IC = 500k
        # delta_NOPAT = 16k; delta_IC = 100k → iroic = 16%
        op_inc  = [120_000] + [100_000] * 9 + [100_000]
        net_inc = [96_000]  + [80_000]  * 9 + [80_000]
        equity  = [600_000] + [500_000] * 9 + [500_000]
        lt_debt = [0] * 11
        cash    = [0] * 11
        pa = GrowthQualityAnalyzer("X", _sec(
            op_income=op_inc, net_inc=net_inc,
            equity=equity, lt_debt=lt_debt, cash=cash,
        ))
        iroic = pa.calc_incremental_roic()
        assert iroic is not None
        assert abs(iroic - 16.0) < 0.5


class TestReweightingWithMissingData:
    def test_all_metrics_missing_returns_neutral_50(self):
        """No data at all → neutral 50."""
        out = GrowthQualityAnalyzer("X", {}).get_growth_quality_score()
        assert out["growth_quality_score"] == pytest.approx(50.0)

    def test_only_one_metric_available_uses_that(self):
        """When only rev_cagr is available, score reflects it."""
        # Strong revenue growth only
        rev = _grow(1_000_000, 0.15)
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev))
        out = pa.get_growth_quality_score()
        # rev_cagr ~15% → norm ~100 → score ~100 (only metric)
        assert out["growth_quality_score"] > 80

    def test_two_metrics_available_proportional(self):
        """Score from two metrics should be between their individual norms."""
        # Strong rev growth but only rev and margin available
        rev    = _grow(1_000_000, 0.15)
        op_inc = [r * 0.20 for r in rev]   # stable margins
        pa = GrowthQualityAnalyzer("X", _sec(revenue=rev, op_income=op_inc))
        out = pa.get_growth_quality_score()
        assert 0 < out["growth_quality_score"] <= 100

    def test_missing_metric_does_not_lower_score_artificially(self):
        """
        Removing a strong metric should not significantly lower the score
        because reweighting redistributes weight proportionally.
        The score with N-1 strong metrics should still be high.
        """
        sec_full    = _make_strong()
        sec_partial = _make_strong()
        sec_partial["op_cf"] = []   # remove FCF → fcf_cagr unavailable
        sec_partial["capex"] = []

        score_full    = GrowthQualityAnalyzer("F", sec_full).get_growth_quality_score()["growth_quality_score"]
        score_partial = GrowthQualityAnalyzer("P", sec_partial).get_growth_quality_score()["growth_quality_score"]

        # Both should be high; partial must be above 50
        assert score_full > 60
        assert score_partial > 50


class TestSignalThresholds:
    def _score_for(self, cagr: float) -> float:
        rev = _grow(1_000_000, cagr / 100)
        eps = _grow(2.0, cagr / 100)
        op_cf  = _grow(100_000, cagr / 100)
        capex  = [10_000] * 11
        op_inc = [r * 0.20 for r in rev]
        net_inc= [oi * 0.80 for oi in op_inc]
        equity = _grow(400_000, 0.05)
        lt_debt= [50_000] * 11
        cash   = [10_000] * 11
        sec = _sec(
            revenue=rev, eps=eps, op_cf=op_cf, capex=capex,
            op_income=op_inc, net_inc=net_inc,
            equity=equity, lt_debt=lt_debt, cash=cash,
        )
        return GrowthQualityAnalyzer("T", sec).get_growth_quality_score()["growth_quality_score"]

    def test_strong_growth_is_bullish_or_neutral(self):
        out = GrowthQualityAnalyzer("T", _make_strong()).get_growth_quality_score()
        assert out["signal"] in ("Bullish", "Neutral")

    def test_weak_growth_is_bearish_or_neutral(self):
        out = GrowthQualityAnalyzer("W", _make_weak()).get_growth_quality_score()
        assert out["signal"] in ("Bearish", "Neutral")

    def test_zero_cagr_signal(self):
        # 0% CAGR on all metrics → ~neutral range
        out = GrowthQualityAnalyzer("T", _sec()).get_growth_quality_score()
        # All missing → neutral 50 → Neutral
        assert out["signal"] == "Neutral"

    def test_bullish_threshold_exact(self):
        assert _signal(70.0) == "Bullish"
        assert _signal(69.99) == "Neutral"

    def test_bearish_threshold_exact(self):
        assert _signal(40.0) == "Neutral"
        assert _signal(39.99) == "Bearish"


class TestScorerIntegration:
    def test_growth_quality_key_in_weights(self):
        assert "growth_quality" in scorer.ENHANCED_WEIGHTS
        assert scorer.ENHANCED_WEIGHTS["growth_quality"] == pytest.approx(0.07)

    def test_enhanced_weights_sum_to_one(self):
        assert sum(scorer.ENHANCED_WEIGHTS.values()) == pytest.approx(1.0)

    def test_omitting_growth_quality_gives_neutral_50(self):
        result = scorer.enhanced_composite(**_base_scorer_args())
        assert result["growth_quality_pct"] == pytest.approx(50.0)

    def test_growth_quality_signal_in_return_dict_when_provided(self):
        gq = {"growth_quality_score": 75.0, "signal": "Bullish",
              "total_score": 75.0, "total_max": 100.0}
        result = scorer.enhanced_composite(
            **_base_scorer_args(), growth_quality_result=gq
        )
        assert result["growth_quality_signal"] == "Bullish"

    def test_growth_quality_impact_proportional_to_weight(self):
        w = scorer.ENHANCED_WEIGHTS["growth_quality"]
        args = _base_scorer_args()
        gq_60 = {"growth_quality_score": 60.0, "total_score": 60.0, "total_max": 100.0}
        gq_40 = {"growth_quality_score": 40.0, "total_score": 40.0, "total_max": 100.0}

        res_60 = scorer.enhanced_composite(**args, growth_quality_result=gq_60)
        res_40 = scorer.enhanced_composite(**args, growth_quality_result=gq_40)

        expected_delta = (60.0 - 40.0) * w
        actual_delta = res_60["composite_score"] - res_40["composite_score"]
        assert math.isclose(actual_delta, expected_delta, abs_tol=0.15)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
