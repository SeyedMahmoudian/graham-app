"""Tests for ISSUE-001: Dividend history lookback extended to 30 years."""
from sec_data import _annual_df, _try_concepts


def _make_facts(n_years: int) -> dict:
    """Minimal facts dict with n_years of 10-K dividend entries."""
    entries = [
        {
            "form":  "10-K",
            "fy":    2024 - i,
            "val":   1_000_000 * (i + 1),
            "end":   f"{2024 - i}-12-31",
            "filed": f"{2025 - i}-02-15",
        }
        for i in range(n_years)
    ]
    return {
        "us-gaap": {
            "PaymentsOfDividendsCommonStock": {"units": {"USD": entries}}
        }
    }


def test_annual_df_returns_all_years_up_to_30():
    """_annual_df with years=30 returns all available rows up to 30."""
    n = 30
    facts = _make_facts(n)
    df = _annual_df(facts, "PaymentsOfDividendsCommonStock", years=30)
    assert len(df) == n, f"Expected {n} rows, got {len(df)}"


def test_try_concepts_returns_more_than_25_years():
    """_try_concepts with years=30 returns history beyond the old 25-year cap."""
    n = 30
    facts = _make_facts(n)
    df = _try_concepts(facts, ["PaymentsOfDividendsCommonStock"], years=30)
    assert len(df) > 25, f"Expected >25 rows with years=30, got {len(df)}"


def test_old_10_year_cap_would_have_truncated():
    """Regression guard: years=10 truncates to 10 rows (old primary behaviour)."""
    n = 30
    facts = _make_facts(n)
    df = _annual_df(facts, "PaymentsOfDividendsCommonStock", years=10)
    assert len(df) == 10, f"Expected 10 rows with old cap, got {len(df)}"


def test_graham_20yr_dividend_requirement_reachable():
    """Graham requires 20 consecutive dividend years — 30yr window must cover it."""
    n = 30
    facts = _make_facts(n)
    df = _try_concepts(facts, ["PaymentsOfDividendsCommonStock"], years=30)
    assert len(df) >= 20, f"Expected at least 20 rows for Graham check, got {len(df)}"
