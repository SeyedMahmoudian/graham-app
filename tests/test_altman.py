"""
TEST-001 — Altman Z-Score partial-score normalisation (ISSUE-006)

Verifies that missing components do not artificially depress the Z-score.
"""

import math
import pytest
from codes import altman


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sec(*, x1_wc=None, x1_ca=None, x1_cl=None,
         x2_re=None, x3_oi=None, x5_rev=None,
         tot_ast=1_000_000, tot_lib=500_000,
         shares=1_000, ppe=None):
    """Build a minimal sec_facts dict for altman.score()."""
    def _rec(v):
        return [{"value": v}] if v is not None else []

    # Working-capital inputs
    ca = x1_ca if x1_ca is not None else (tot_ast * x1_wc + (x1_cl or 0)) if x1_wc is not None else None
    cl = x1_cl

    return {
        "cur_ast":          _rec(ca),
        "cur_lib":          _rec(cl),
        "total_assets":     _rec(tot_ast),
        "retained_earnings":_rec(x2_re),
        "op_income":        _rec(x3_oi),
        "shares":           _rec(shares),
        "tot_lib":          _rec(tot_lib),
        "revenue":          _rec(x5_rev),
        "ppe_net":          _rec(ppe),
    }


# ── Original model (PP&E present) ────────────────────────────────────────────

class TestOriginalModelAllComponents:
    def test_all_five_components_fraction_is_one(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   x5_rev=800_000, tot_ast=1_000_000,
                   tot_lib=500_000, shares=1_000, ppe=300_000)
        result = altman.score(price=50.0, sec=sec)
        assert result["available_fraction"] == pytest.approx(1.0)
        assert result["z_score"] is not None

    def test_score_matches_manual_calculation(self):
        # X1=0.1, X2=0.4, X3=0.15, X4=0.2 (price=100, shares=1000, tot_lib=500000), X5=0.8
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   x5_rev=800_000, tot_ast=1_000_000,
                   tot_lib=500_000, shares=1_000, ppe=300_000)
        result = altman.score(price=100.0, sec=sec)
        x1, x2, x3 = 0.1, 0.4, 0.15
        x4 = (100.0 * 1_000) / 500_000  # 0.2
        x5 = 0.8
        expected = round(1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5, 3)
        assert result["z_score"] == pytest.approx(expected, abs=0.001)


class TestOriginalModelMissingX4:
    """X4 requires market price; when price=None it should be absent."""

    def test_missing_x4_inflates_score_vs_zero_substitution(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   x5_rev=800_000, tot_ast=1_000_000,
                   tot_lib=500_000, ppe=300_000)
        result_no_price  = altman.score(price=None,  sec=sec)
        result_with_price = altman.score(price=100.0, sec=sec)

        # Without normalisation, missing X4 would always lower the score;
        # with normalisation the no-price score should be ≥ what you'd get
        # if X4 were forced to 0.
        x1, x2, x3, x5 = 0.1, 0.4, 0.15, 0.8
        naive = round(1.2*x1 + 1.4*x2 + 3.3*x3 + 0.0 + 1.0*x5, 3)
        assert result_no_price["z_score"] > naive

    def test_available_fraction_lt_one_when_x4_missing(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   x5_rev=800_000, tot_ast=1_000_000,
                   tot_lib=500_000, ppe=300_000)
        result = altman.score(price=None, sec=sec)
        assert result["available_fraction"] < 1.0

    def test_available_fraction_correct_value(self):
        """avail_w = 1.2+1.4+3.3+0+1.0 = 7.5 - 0.6 = 6.9; total_w = 7.5"""
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   x5_rev=800_000, tot_ast=1_000_000,
                   tot_lib=500_000, ppe=300_000)
        result = altman.score(price=None, sec=sec)
        expected_frac = round((1.2 + 1.4 + 3.3 + 1.0) / (1.2 + 1.4 + 3.3 + 0.6 + 1.0), 4)
        assert result["available_fraction"] == pytest.approx(expected_frac, abs=1e-4)


# ── Z'' model (no PP&E) ───────────────────────────────────────────────────────

class TestZppModelAllComponents:
    def test_all_four_components_fraction_is_one(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   tot_ast=1_000_000, tot_lib=500_000,
                   shares=1_000)   # ppe=None → Z''
        result = altman.score(price=50.0, sec=sec)
        assert result["model"] == "Z''"
        assert result["available_fraction"] == pytest.approx(1.0)

    def test_score_matches_manual_zpp(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   tot_ast=1_000_000, tot_lib=500_000,
                   shares=1_000)
        result = altman.score(price=100.0, sec=sec)
        x1 = 0.1
        x2 = 0.4
        x3 = 0.15
        x4 = (100.0 * 1_000) / 500_000
        expected = round(6.56*x1 + 3.26*x2 + 6.72*x3 + 1.05*x4, 3)
        assert result["z_score"] == pytest.approx(expected, abs=0.001)


class TestZppModelMissingX4:
    def test_missing_x4_normalised_not_zero_penalised(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   tot_ast=1_000_000, tot_lib=500_000)
        result = altman.score(price=None, sec=sec)
        # naive (X4=0) score
        x1, x2, x3 = 0.1, 0.4, 0.15
        naive = round(6.56*x1 + 3.26*x2 + 6.72*x3, 3)
        assert result["z_score"] is not None
        assert result["z_score"] > naive

    def test_available_fraction_correct_zpp(self):
        """avail_w = 6.56+3.26+6.72 = 16.54; total_w = 17.59"""
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   tot_ast=1_000_000, tot_lib=500_000)
        result = altman.score(price=None, sec=sec)
        expected_frac = round((6.56 + 3.26 + 6.72) / (6.56 + 3.26 + 6.72 + 1.05), 4)
        assert result["available_fraction"] == pytest.approx(expected_frac, abs=1e-4)


# ── Insufficient data ─────────────────────────────────────────────────────────

class TestInsufficientData:
    def test_fewer_than_min_required_returns_none(self):
        # Only X1 available — below min_required=3
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   tot_ast=1_000_000)
        result = altman.score(price=None, sec=sec)
        assert result["z_score"] is None
        assert result["zone"] == "unknown"
        assert result["risk_score"] == 50   # neutral

    def test_empty_sec_returns_none(self):
        result = altman.score(price=None, sec={})
        assert result["z_score"] is None


# ── Zone boundaries ───────────────────────────────────────────────────────────

class TestZoneBoundaries:
    def _score_with_z(self, z_target: float, model: str = "original") -> dict:
        """Craft inputs so Z ≈ z_target with all components present."""
        # All five components present, drive score via X3 (EBIT/Assets)
        # Z = 1.2*0.1 + 1.4*0.1 + 3.3*X3 + 0.6*0.2 + 1.0*0.5
        # 3.3*X3 = z_target - (0.12 + 0.14 + 0.12 + 0.5)
        fixed = 1.2*0.1 + 1.4*0.1 + 0.6*0.2 + 1.0*0.5
        x3_needed = (z_target - fixed) / 3.3
        oi = x3_needed * 1_000_000
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=100_000, x3_oi=oi,
                   x5_rev=500_000, tot_ast=1_000_000,
                   tot_lib=500_000, shares=1_000, ppe=300_000)
        return altman.score(price=100.0, sec=sec)

    def test_safe_zone(self):
        r = self._score_with_z(3.5)
        assert r["zone"] == "safe"

    def test_grey_zone(self):
        r = self._score_with_z(2.0)
        assert r["zone"] == "grey"

    def test_distress_zone(self):
        r = self._score_with_z(1.0)
        assert r["zone"] == "distress"


# ── Return dict keys ──────────────────────────────────────────────────────────

class TestReturnShape:
    def test_all_expected_keys_present(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   x5_rev=800_000, tot_ast=1_000_000,
                   tot_lib=500_000, shares=1_000, ppe=300_000)
        result = altman.score(price=50.0, sec=sec)
        required = {
            "z_score", "model", "zone", "zone_label", "color", "note",
            "risk_penalty", "risk_score", "n_available", "available_fraction",
            "components",
        }
        assert required.issubset(result.keys())

    def test_components_sub_keys(self):
        sec = _sec(x1_ca=200_000, x1_cl=100_000,
                   x2_re=400_000, x3_oi=150_000,
                   x5_rev=800_000, tot_ast=1_000_000,
                   tot_lib=500_000, shares=1_000, ppe=300_000)
        result = altman.score(price=50.0, sec=sec)
        comp_keys = {
            "x1_working_capital", "x2_retained_earnings", "x3_ebit_ratio",
            "x4_equity_liabilities", "x5_asset_turnover",
        }
        assert comp_keys == set(result["components"].keys())
