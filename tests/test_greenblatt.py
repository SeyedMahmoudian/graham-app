"""""
TEST-002 — Full Unit Tests for greenblatt.py

Covers compute_single() and rank_universe() in full.
Note: tests/test_issue008_greenblatt_composite.py covers ISSUE-008 only.

nwc_excludes_cash is a dedicated test that verifies ISSUE-007 fix:
  NWC = cur_ast - cash - cur_lib  (cash must be excluded)
"""

import math
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from codes import greenblatt


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rec(v):
    return [{"value": v}] if v is not None else []


def _sec(*, ebit=100_000, shares=1_000, lt_debt=200_000, cur_lib=50_000,
         cur_ast=150_000, ppe=300_000, cash=20_000, tot_ast=500_000):
    """Build a minimal sec_facts dict for greenblatt.compute_single()."""
    return {
        "op_income":    _rec(ebit),
        "shares":       _rec(shares),
        "lt_debt":      _rec(lt_debt),
        "cur_lib":      _rec(cur_lib),
        "cur_ast":      _rec(cur_ast),
        "ppe_net":      _rec(ppe),
        "cash":         _rec(cash),
        "total_assets": _rec(tot_ast),
    }


def _sec_no_ppe(**kwargs):
    s = _sec(**kwargs)
    s["ppe_net"] = []
    return s


def _sec_no_cash(**kwargs):
    s = _sec(**kwargs)
    s["cash"] = []
    return s


# ══════════════════════════════════════════════════════════════════════════════
# compute_single() tests
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeSingleFull:
    def test_earnings_yield_not_none(self):
        sec = _sec()
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert result["earnings_yield"] is not None

    def test_roic_not_none(self):
        sec = _sec()
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert result["roic"] is not None

    def test_ev_computed(self):
        # EV = mkt_cap + lt_debt - cash = (50*1000) + 200000 - 20000 = 230000
        sec = _sec(shares=1_000, lt_debt=200_000, cash=20_000)
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert result["enterprise_value"] == pytest.approx(50*1_000 + 200_000 - 20_000)

    def test_earnings_yield_formula(self):
        # EY = EBIT / EV  (as %)
        sec = _sec(ebit=100_000, shares=1_000, lt_debt=200_000, cash=20_000)
        result = greenblatt.compute_single(price=50.0, sec=sec)
        ev = 50 * 1_000 + 200_000 - 20_000   # 230_000
        expected_ey = round(100_000 / ev * 100, 3)
        assert result["earnings_yield"] == pytest.approx(expected_ey, abs=0.01)

    def test_ic_method_nwc_plus_ppe(self):
        sec = _sec()
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert result["ic_method"] == "NWC + PP&amp;E"

    def test_magic_score_none_before_ranking(self):
        sec = _sec()
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert result["magic_score"] is None
        assert result["magic_rank"] is None


class TestComputeSingleNoPpe:
    def test_ic_method_contains_fallback_or_nwc_only(self):
        sec = _sec_no_ppe()
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert ("fallback" in result["ic_method"].lower() or
                "nwc" in result["ic_method"].lower())

    def test_roic_still_computed_when_ppe_missing(self):
        # NWC fallback: invested_capital = cur_ast - cash - cur_lib
        sec = _sec_no_ppe(cur_ast=150_000, cash=20_000, cur_lib=50_000)
        result = greenblatt.compute_single(price=50.0, sec=sec)
        # NWC = 150000 - 20000 - 50000 = 80000; roic = ebit/nwc
        assert result["roic"] is not None


class TestComputeSingleNoPrice:
    def test_ev_is_none_when_no_price(self):
        sec = _sec()
        result = greenblatt.compute_single(price=None, sec=sec)
        assert result["enterprise_value"] is None

    def test_earnings_yield_is_none_when_no_price(self):
        sec = _sec()
        result = greenblatt.compute_single(price=None, sec=sec)
        assert result["earnings_yield"] is None

    def test_roic_still_computable_without_price(self):
        # ROIC uses invested_capital, not EV; price not needed
        sec = _sec()
        result = greenblatt.compute_single(price=None, sec=sec)
        assert result["roic"] is not None


class TestComputeSingleNegativeEbit:
    def test_negative_earnings_yield(self):
        sec = _sec(ebit=-50_000)
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert result["earnings_yield"] is not None
        assert result["earnings_yield"] < 0

    def test_negative_roic(self):
        sec = _sec(ebit=-50_000)
        result = greenblatt.compute_single(price=50.0, sec=sec)
        assert result["roic"] is not None
        assert result["roic"] < 0


# ══════════════════════════════════════════════════════════════════════════════
# ISSUE-007: NWC must exclude cash
# ══════════════════════════════════════════════════════════════════════════════

class TestNwcExcludesCash:
    """
    Dedicated test for ISSUE-007 fix.
    NWC = cur_ast - cash - cur_lib   (NOT cur_ast - cur_lib)
    """

    def test_nwc_excludes_cash_from_invested_capital(self):
        cur_ast = 200_000
        cash    =  50_000
        cur_lib =  80_000
        ebit    = 100_000
        ppe     = 0        # force NWC-only path (no PPE, no total_assets fallback)

        # With cash excluded:  NWC = 200000 - 50000 - 80000 = 70000
        # Without cash:        NWC = 200000 - 80000          = 120000
        sec = {
            "op_income":    _rec(ebit),
            "shares":       _rec(1_000),
            "lt_debt":      _rec(0),
            "cur_lib":      _rec(cur_lib),
            "cur_ast":      _rec(cur_ast),
            "ppe_net":      [],           # no PPE → NWC-only path
            "cash":         _rec(cash),
            "total_assets": [],           # no fallback either
        }

        result = greenblatt.compute_single(price=50.0, sec=sec)

        nwc_with_cash_excluded    = cur_ast - cash - cur_lib   # 70_000
        nwc_without_cash_excluded = cur_ast - cur_lib           # 120_000

        expected_roic_correct = round(ebit / nwc_with_cash_excluded * 100, 3)
        expected_roic_wrong   = round(ebit / nwc_without_cash_excluded * 100, 3)

        assert result["roic"] is not None, "ROIC should be computable"
        assert not math.isclose(expected_roic_correct, expected_roic_wrong, rel_tol=1e-4), \
            "test setup error: correct and wrong values must differ"
        assert math.isclose(result["roic"], expected_roic_correct, rel_tol=1e-3), (
            f"ROIC {result['roic']:.3f} should equal cash-excluded value "
            f"{expected_roic_correct:.3f}, not {expected_roic_wrong:.3f}. "
            "ISSUE-007: cash must be excluded from NWC."
        )

    def test_invested_capital_with_ppe_also_excludes_cash(self):
        """When PP&amp;E is present: IC = (cur_ast - cash - cur_lib) + ppe."""
        cur_ast = 200_000
        cash    =  30_000
        cur_lib =  60_000
        ppe     = 100_000
        ebit    =  80_000

        sec = _sec(cur_ast=cur_ast, cash=cash, cur_lib=cur_lib,
                   ppe=ppe, ebit=ebit, shares=1_000, lt_debt=0)
        result = greenblatt.compute_single(price=50.0, sec=sec)

        nwc_correct = cur_ast - cash - cur_lib   # 110_000
        ic_correct  = nwc_correct + ppe           # 210_000
        roic_correct = round(ebit / ic_correct * 100, 3)

        nwc_wrong = cur_ast - cur_lib             # 140_000
        ic_wrong  = nwc_wrong + ppe               # 240_000
        roic_wrong = round(ebit / ic_wrong * 100, 3)

        assert result["ic_method"] == "NWC + PP&amp;E"
        assert math.isclose(result["roic"], roic_correct, rel_tol=1e-3), (
            f"ROIC {result['roic']:.3f} should be {roic_correct:.3f} "
            f"(wrong would be {roic_wrong:.3f}). Cash must be excluded from NWC."
        )


# ══════════════════════════════════════════════════════════════════════════════
# rank_universe() tests
# ══════════════════════════════════════════════════════════════════════════════

def _make_universe(entries):
    """
    entries: list of (symbol, earnings_yield, roic) tuples.
    Builds a list of dicts as if compute_single() was called on each.
    """
    result = []
    for sym, ey, roic in entries:
        result.append({
            "symbol":         sym,
            "earnings_yield": ey,
            "roic":           roic,
            "magic_score":    None,
            "magic_rank":     None,
            "ey_percentile":  None,
            "roic_percentile":None,
        })
    return result


class TestRankUniverseBasic:
    def test_three_stocks_all_get_magic_score(self):
        universe = _make_universe([
            ("AAPL", 10.0, 25.0),
            ("MSFT",  8.0, 30.0),
            ("GOOG",  6.0, 20.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        for stock in ranked:
            if stock["earnings_yield"] and stock["earnings_yield"] > 0:
                assert stock["magic_score"] is not None
                assert 0 <= stock["magic_score"] <= 100

    def test_magic_score_in_range(self):
        universe = _make_universe([
            ("A", 15.0, 40.0),
            ("B", 10.0, 25.0),
            ("C",  5.0, 15.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        for stock in ranked:
            if stock["magic_score"] is not None:
                assert 0 <= stock["magic_score"] <= 100


class TestRankUniverseExcludesNegativeEY:
    def test_negative_ey_stock_gets_none_magic_score(self):
        universe = _make_universe([
            ("GOOD",  10.0, 20.0),
            ("BAD",   -5.0, 15.0),  # negative EY — excluded
        ])
        ranked = greenblatt.rank_universe(universe)
        bad = next(s for s in ranked if s["symbol"] == "BAD")
        assert bad["magic_score"] is None

    def test_positive_ey_stock_still_ranked_despite_negative_peer(self):
        universe = _make_universe([
            ("GOOD",  10.0, 20.0),
            ("BAD",   -5.0, 15.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        good = next(s for s in ranked if s["symbol"] == "GOOD")
        assert good["magic_score"] is not None


class TestRankUniverseSingleStock:
    def test_single_valid_stock_gets_100(self):
        universe = _make_universe([("ONLY", 12.0, 30.0)])
        ranked = greenblatt.rank_universe(universe)
        only = ranked[0]
        assert only["magic_score"] == pytest.approx(100.0)


class TestRankUniverseEmpty:
    def test_all_missing_ey_returns_unchanged(self):
        universe = _make_universe([
            ("A", None, 20.0),
            ("B", None, 15.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        # No crash; all magic_scores remain None
        for s in ranked:
            assert s["magic_score"] is None

    def test_all_missing_roic_returns_unchanged(self):
        universe = _make_universe([
            ("A", 10.0, None),
            ("B",  8.0, None),
        ])
        ranked = greenblatt.rank_universe(universe)
        for s in ranked:
            assert s["magic_score"] is None

    def test_empty_list_no_crash(self):
        ranked = greenblatt.rank_universe([])
        assert ranked == []


class TestRankUniverseSorted:
    def test_sorted_descending_by_magic_score(self):
        universe = _make_universe([
            ("LOW",  5.0, 10.0),
            ("HIGH", 20.0, 40.0),
            ("MID",  12.0, 25.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        scores = [s["magic_score"] for s in ranked if s["magic_score"] is not None]
        assert scores == sorted(scores, reverse=True)

    def test_excluded_stocks_at_end(self):
        universe = _make_universe([
            ("GOOD", 10.0, 20.0),
            ("BAD",  -1.0, 15.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        # GOOD should come before BAD (BAD has None score → sorted to end)
        good_idx = next(i for i, s in enumerate(ranked) if s["symbol"] == "GOOD")
        bad_idx  = next(i for i, s in enumerate(ranked) if s["symbol"] == "BAD")
        assert good_idx < bad_idx


class TestRankUniverseDoesNotMutateExcluded:
    def test_excluded_stocks_retain_none_magic_score(self):
        universe = _make_universe([
            ("VALID",    10.0, 20.0),
            ("NEG_EY",   -3.0, 15.0),
            ("ZERO_EY",   0.0, 12.0),
            ("NULL_EY",  None, 10.0),
        ])
        greenblatt.rank_universe(universe)

        neg  = next(s for s in universe if s["symbol"] == "NEG_EY")
        zero = next(s for s in universe if s["symbol"] == "ZERO_EY")
        null = next(s for s in universe if s["symbol"] == "NULL_EY")

        assert neg["magic_score"]  is None
        assert zero["magic_score"] is None
        assert null["magic_score"] is None


class TestRankUniversePercentiles:
    def test_ey_percentile_in_range(self):
        universe = _make_universe([
            ("A", 15.0, 30.0),
            ("B", 10.0, 20.0),
            ("C",  5.0, 10.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        for s in ranked:
            if s["ey_percentile"] is not None:
                assert 0 <= s["ey_percentile"] <= 100

    def test_roic_percentile_in_range(self):
        universe = _make_universe([
            ("A", 15.0, 30.0),
            ("B", 10.0, 20.0),
            ("C",  5.0, 10.0),
        ])
        ranked = greenblatt.rank_universe(universe)
        for s in ranked:
            if s["roic_percentile"] is not None:
                assert 0 <= s["roic_percentile"] <= 100


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
