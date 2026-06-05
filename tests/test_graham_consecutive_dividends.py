"""
Tests for ISSUE-002: Graham consecutive dividend year counting.

Verifies that div_years reflects uninterrupted consecutive years ending
at the most recent dividend year, not a total count of years with dividends.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codes import graham


def _make_sec(div_years_list):
    """Build a minimal sec_facts dict with the given dividend year list."""
    dividends = [{"year": yr, "value": 1.0} for yr in div_years_list]
    return {
        "eps":      [],
        "bvps":     [],
        "cur_ast":  [],
        "cur_lib":  [],
        "lt_debt":  [],
        "tot_lib":  [],
        "equity":   [],
        "shares":   [],
        "dividends": dividends,
    }


def _div_years(div_years_list):
    sec = _make_sec(div_years_list)
    result = graham.score(None, sec)
    return result["div_years"]


# ── Consecutive streak tests ───────────────────────────────────────────────────

def test_all_consecutive():
    assert _div_years([2025, 2024, 2023, 2022, 2021]) == 5


def test_gap_stops_count():
    # Gap before 2020 should yield 4, not 5
    assert _div_years([2025, 2024, 2023, 2022, 2020]) == 4


def test_gap_at_start_stops_immediately():
    # Only 2025 is consecutive from the top; 2023 is a gap
    assert _div_years([2025, 2023, 2022, 2021]) == 1


def test_single_year():
    assert _div_years([2024]) == 1


def test_no_dividends():
    assert _div_years([]) == 0


def test_two_consecutive():
    assert _div_years([2025, 2024]) == 2


def test_two_with_gap():
    assert _div_years([2025, 2023]) == 1


def test_long_streak_with_early_gap():
    # 10 consecutive recent years, then a gap, then older years
    years = list(range(2025, 2015, -1)) + [2010, 2009, 2008]
    assert _div_years(years) == 10


def test_unordered_input_handled():
    # Input order should not matter — sorted internally
    assert _div_years([2022, 2025, 2023, 2024]) == 4


# ── Scoring threshold tests ────────────────────────────────────────────────────

def test_score_20_consecutive_earns_full_points():
    years = list(range(2025, 2005, -1))   # 2025..2006 → 20 years
    sec = _make_sec(years)
    result = graham.score(None, sec)
    # Find dividend criterion
    crit = next(c for c in result["criteria"] if "Dividend" in c["label"])
    assert crit["score"] == 10, f"Expected 10 pts for 20 yrs, got {crit['score']}"


def test_score_15_consecutive_earns_partial():
    years = list(range(2025, 2010, -1))   # 15 years
    sec = _make_sec(years)
    result = graham.score(None, sec)
    crit = next(c for c in result["criteria"] if "Dividend" in c["label"])
    assert crit["score"] == 5


def test_score_gap_breaks_20yr_streak():
    # 19 consecutive + gap = only 19 consecutive → should score 5, not 10
    years = list(range(2025, 2006, -1))   # 2025..2007 = 19 years
    years.append(2004)                    # gap at 2005, then one more year
    sec = _make_sec(years)
    result = graham.score(None, sec)
    crit = next(c for c in result["criteria"] if "Dividend" in c["label"])
    assert crit["score"] == 5, (
        f"19 consecutive years should score 5 pts, got {crit['score']}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
