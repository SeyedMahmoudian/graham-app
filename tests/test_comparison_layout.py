"""
Tests for the side-by-side portfolio comparison layout in app.py.

Verifies:
1. _two_col() produces a 2-child flex row.
2. _build_comparison_view() returns winner banner + side-by-side sections
   for stats, charts, holdings, and weak-link analysis without raising.
3. _comparison_stats_row / _comparison_holdings_table / _comparison_weak_link_card
   handle both populated and error/empty inputs gracefully.
"""

import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "codes"))


@pytest.fixture(scope="module")
def app_module():
    """Import codes.app with startup() network calls mocked out."""
    with patch("codes.data.sec_data.get_ticker_map", return_value={}), \
         patch("codes.engine.universe.get_universe", return_value=[]), \
         patch("codes.engine.screener.load_cached_only", return_value=[]):
        import codes.app as app_mod
    return app_mod


def _sim(cagr, spy_cagr, final_value, p50_last):
    return {
        "error": None,
        "backtest": {
            "error": None,
            "dates": ["2024-01-01", "2024-02-01"],
            "portfolio_value": [100, final_value],
            "spy_value": [100, 110],
            "total_invested": 100.0,
            "spy_invested": 100.0,
            "final_value": final_value,
            "final_spy": 110.0,
            "cagr": cagr,
            "spy_cagr": spy_cagr,
            "n_months": 2,
            "holdings_detail": {
                "AAA": {
                    "shares": 10, "original_shares": 10, "split_factor": 1.0,
                    "splits": [], "entry_price": 10.0, "current_price": 12.0,
                    "gain_pct": 20.0, "current_value": 120.0,
                },
            },
        },
        "montecarlo": {
            "error": None,
            "dates": ["2026-01-01", "2027-01-01"],
            "p10": [80, 85],
            "p50": [final_value, p50_last],
            "p90": [120, 130],
            "spy_p10": [105, 108],
            "spy_p50": [110, 115],
            "spy_p90": [115, 122],
        },
        "holdings": {"AAA": {"shares": 10, "price_at_add": 10.0, "name": "AAA Corp"}},
    }


def _cmp_result(winner="A"):
    return {
        "winner": winner,
        "score_a": 30.0,
        "score_b": 10.0,
        "reasons": ["Higher CAGR", "Better alpha vs SPY"],
        "portfolio_a": _sim(cagr=20, spy_cagr=8, final_value=150, p50_last=160),
        "portfolio_b": _sim(cagr=5, spy_cagr=8, final_value=100, p50_last=105),
        "error": None,
    }


class TestTwoCol:
    def test_two_col_has_two_children(self, app_module):
        row = app_module._two_col(
            app_module.html.Div("left"),
            app_module.html.Div("right"),
        )
        assert isinstance(row, app_module.html.Div)
        assert len(row.children) == 2

    def test_two_col_uses_flex(self, app_module):
        row = app_module._two_col(
            app_module.html.Div("left"),
            app_module.html.Div("right"),
        )
        assert row.style.get("display") == "flex"


class TestComparisonStatsRow:
    def test_renders_stats_for_valid_backtest(self, app_module):
        bt = _sim(10, 8, 100, 110)["backtest"]
        out = app_module._comparison_stats_row("A", bt)
        assert out.className == "portfolio-stats-row"
        assert len(out.children) == 6

    def test_handles_backtest_error(self, app_module):
        out = app_module._comparison_stats_row("A", {"error": "no data"})
        assert "no data" in out.children


class TestComparisonHoldingsTable:
    def test_renders_scorecard_when_holdings_present(self, app_module):
        bt = _sim(10, 8, 100, 110)["backtest"]
        out = app_module._comparison_holdings_table(bt)
        assert out.className == "scorecard"

    def test_empty_div_on_error(self, app_module):
        out = app_module._comparison_holdings_table({"error": "boom"})
        assert out.children is None or out.children == []

    def test_empty_div_when_no_holdings_detail(self, app_module):
        out = app_module._comparison_holdings_table(
            {"error": None, "holdings_detail": {}}
        )
        assert out.children is None or out.children == []


class TestComparisonWeakLinkCard:
    def test_renders_card_with_weak_link_data(self, app_module):
        bt = _sim(10, 8, 100, 110)["backtest"]
        wl = {
            "error": None, "gap_cagr": -1.0, "port_cagr": 7.0, "spy_cagr": 8.0,
            "n_years": 5.0,
            "holdings": {
                "AAA": {
                    "weight": 100.0, "stock_cagr": 7.0, "spy_cagr": 8.0,
                    "cagr_vs_spy": -1.0, "drag_bps": -1.0, "swap_delta_pct": 0.5,
                    "verdict": "weak link",
                },
            },
            "ranking": ["AAA"], "weakest": "AAA",
        }
        with patch.object(app_module.portfolio_engine, "load_portfolio",
                          return_value={"holdings": {"AAA": {}}}), \
             patch.object(app_module.portfolio_engine, "analyze_weak_links",
                          return_value=wl):
            out = app_module._comparison_weak_link_card("A", bt)
        assert out.className == "scorecard"

    def test_empty_div_when_portfolio_missing(self, app_module):
        bt = _sim(10, 8, 100, 110)["backtest"]
        with patch.object(app_module.portfolio_engine, "load_portfolio",
                          return_value=None):
            out = app_module._comparison_weak_link_card("A", bt)
        assert out.children is None or out.children == []


class TestBuildComparisonView:
    def _run(self, app_module, winner="A"):
        with patch.object(app_module.portfolio_engine, "load_portfolio",
                          return_value={"holdings": {"AAA": {}}}), \
             patch.object(app_module.portfolio_engine, "analyze_weak_links",
                          return_value={"error": "no history"}):
            return app_module._build_comparison_view(
                "A", "B", _cmp_result(winner),
                [app_module.BLUE, app_module.GREEN],
            )

    def test_returns_seven_sections(self, app_module):
        # winner banner, col headers, stats, bt chart, mc chart, holdings, weak-link
        sections = self._run(app_module)
        assert len(sections) == 7

    def test_winner_banner_mentions_winner(self, app_module):
        sections = self._run(app_module, winner="A")
        assert "A" in str(sections[0])

    def test_no_winner_shows_similar_message(self, app_module):
        sections = self._run(app_module, winner=None)
        assert "perform similarly" in str(sections[0])

    def test_col_headers_show_both_names(self, app_module):
        sections = self._run(app_module)
        headers_str = str(sections[1])
        assert "A" in headers_str and "B" in headers_str
