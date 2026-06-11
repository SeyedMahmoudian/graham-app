"""
Tests for on-demand screener loading (P5 redesign).

Verifies:
  1. _stub_row returns a row with zero scores and analyzed=False.
  2. _score_cached returns None when no cache entry.
  3. _score_cached scores from cache without any SEC network call.
  4. load_cached_only populates stub rows for uncached symbols.
  5. load_cached_only scores cached symbols without SEC network calls.
  6. load_universe_background scores only cached stocks (no SEC fetches).
  7. update_stock_after_analysis enriches an existing stub row.
"""

import sys
import os
import time
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from codes.engine import screener


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_sec(symbol="AAPL"):
    """Minimal sec_facts dict that graham/quality can score without crashing."""
    return {
        "name": f"{symbol} Inc.", "sector": "Technology",
        "eps": [], "bvps": [], "cur_ast": [], "cur_lib": [],
        "lt_debt": [], "tot_lib": [], "equity": [], "shares": [],
        "dividends": [], "net_inc": [], "revenue": [], "op_income": [],
        "op_cf": [], "capex": [], "gross_profit": [], "total_assets": [],
    }


def _make_analysis(symbol="AAPL"):
    return {
        "name": f"{symbol} Inc.", "sector": "Technology", "price": 150.0,
        "graham":    {"total_score": 60, "total_max": 100, "graham_number": 120.0,
                      "margin_of_safety": 20.0},
        "quality":   {"total_score": 70, "total_max": 100},
        "buffett":   {"intrinsic_value": 180.0},
        "enhanced":  {"composite_score": 65.0, "verdict": "BUY",
                      "verdict_label": "buy", "graham_pct": 60.0,
                      "quality_pct": 70.0},
        "composite": {},
    }


# ══════════════════════════════════════════════════════════════════════════════
# _stub_row
# ══════════════════════════════════════════════════════════════════════════════

class TestStubRow:
    def test_zero_scores(self):
        row = screener._stub_row("NVDA")
        assert row["composite_score"] == 0
        assert row["graham_pct"] == 0
        assert row["quality_pct"] == 0

    def test_analyzed_false(self):
        assert screener._stub_row("NVDA")["analyzed"] is False

    def test_verdict_pending(self):
        assert screener._stub_row("NVDA")["verdict"] == "PENDING"

    def test_symbol_stored(self):
        assert screener._stub_row("NVDA")["symbol"] == "NVDA"

    def test_name_fallback_to_symbol(self):
        row = screener._stub_row("XYZ")
        assert row["name"] == "XYZ"

    def test_custom_name_stored(self):
        row = screener._stub_row("AAPL", name="Apple Inc.")
        assert row["name"] == "Apple Inc."


# ══════════════════════════════════════════════════════════════════════════════
# _score_cached
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreCached:
    def test_returns_none_when_no_cache(self):
        with patch("codes.engine.screener.cache.read", return_value=None):
            assert screener._score_cached("AAPL") is None

    def test_scores_from_cache_no_network(self):
        """When cache exists, no SEC network call should be made."""
        sec = _make_sec("AAPL")
        with patch("codes.engine.screener.cache.read", return_value=sec), \
             patch("codes.data.sec_data.fetch_company_facts") as mock_fetch:
            row = screener._score_cached("AAPL")
            mock_fetch.assert_not_called()
            assert row is not None
            assert row["symbol"] == "AAPL"

    def test_row_has_required_keys(self):
        sec = _make_sec("AAPL")
        with patch("codes.engine.screener.cache.read", return_value=sec):
            row = screener._score_cached("AAPL")
        required = {"symbol", "name", "sector", "graham_pct", "quality_pct",
                    "composite_score", "verdict", "verdict_label", "analyzed"}
        assert required.issubset(row.keys())

    def test_analyzed_false(self):
        sec = _make_sec("AAPL")
        with patch("codes.engine.screener.cache.read", return_value=sec):
            row = screener._score_cached("AAPL")
        assert row["analyzed"] is False

    def test_exception_returns_none(self):
        with patch("codes.engine.screener.cache.read", side_effect=Exception("boom")):
            assert screener._score_cached("AAPL") is None


# ══════════════════════════════════════════════════════════════════════════════
# load_cached_only
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadCachedOnly:
    def _run(self, symbols, cached_set, ticker_map=None):
        """Helper to run load_cached_only with mocked universe."""
        ticker_map = ticker_map or {s: {"name": f"{s} Corp"} for s in symbols}

        def fake_cache_read(kind, key):
            if kind == "sec_facts":
                return _make_sec(key) if key.upper() in cached_set else None
            if kind == "analysis":
                return None
            return None

        with patch("codes.engine.screener.universe.get_universe", return_value=symbols), \
             patch("codes.engine.screener.cache.read", side_effect=fake_cache_read), \
             patch("codes.engine.screener.cache.list_cached_kind", return_value=[]), \
             patch("codes.data.sec_data.get_ticker_map", return_value=ticker_map), \
             patch("codes.data.sec_data.fetch_company_facts") as mock_fetch:
            result = screener.load_cached_only()
            return result, mock_fetch

    def test_no_sec_fetch_for_uncached_symbols(self):
        symbols = ["AAPL", "NVDA", "TSLA"]
        result, mock_fetch = self._run(symbols, cached_set={"AAPL"})
        mock_fetch.assert_not_called()

    def test_all_symbols_appear_in_results(self):
        symbols = ["AAPL", "NVDA", "TSLA"]
        result, _ = self._run(symbols, cached_set={"AAPL"})
        result_syms = {r["symbol"] for r in result}
        assert result_syms == {"AAPL", "NVDA", "TSLA"}

    def test_uncached_symbol_is_stub(self):
        symbols = ["AAPL", "NVDA"]
        result, _ = self._run(symbols, cached_set=set())
        for row in result:
            assert row["analyzed"] is False
            assert row["composite_score"] == 0

    def test_empty_universe_returns_empty(self):
        with patch("codes.engine.screener.universe.get_universe", return_value=[]), \
             patch("codes.data.sec_data.get_ticker_map", return_value={}):
            assert screener.load_cached_only() == []

    def test_no_sec_fetch_when_universe_empty(self):
        with patch("codes.engine.screener.universe.get_universe", return_value=[]), \
             patch("codes.data.sec_data.get_ticker_map", return_value={}), \
             patch("codes.data.sec_data.fetch_company_facts") as mock_fetch:
            screener.load_cached_only()
            mock_fetch.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# load_universe_background — no SEC fetches
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadUniverseBackground:
    def test_no_sec_fetch_for_uncached(self):
        """Background loader must never fetch uncached stocks from SEC."""
        symbols = ["AAPL", "NVDA", "NEW1", "NEW2"]

        def fake_cache_read(kind, key):
            if kind == "sec_facts" and key.upper() in {"AAPL", "NVDA"}:
                return _make_sec(key)
            return None

        import time as _t
        with patch("codes.engine.screener.universe.get_universe", return_value=symbols), \
             patch("codes.engine.screener.cache.read", side_effect=fake_cache_read), \
             patch("codes.engine.screener.cache.list_cached_kind", return_value=[]), \
             patch("codes.data.sec_data.fetch_company_facts") as mock_fetch:
            screener._progress["running"] = False
            screener._progress["results"] = []
            screener.load_universe_background(tickers=symbols)
            # Wait for background thread
            _t.sleep(1.0)
            mock_fetch.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# update_stock_after_analysis
# ══════════════════════════════════════════════════════════════════════════════

class TestUpdateStockAfterAnalysis:
    def _setup_progress(self, symbols):
        screener._progress["results"] = [screener._stub_row(s) for s in symbols]

    def test_enriches_existing_stub(self):
        self._setup_progress(["AAPL", "NVDA"])
        screener.update_stock_after_analysis("AAPL", _make_analysis("AAPL"))
        row = next(r for r in screener._progress["results"] if r["symbol"] == "AAPL")
        assert row["analyzed"] is True
        assert row["price"] == 150.0
        assert row["graham_number"] == 120.0

    def test_other_rows_unchanged(self):
        self._setup_progress(["AAPL", "NVDA"])
        screener.update_stock_after_analysis("AAPL", _make_analysis("AAPL"))
        nvda = next(r for r in screener._progress["results"] if r["symbol"] == "NVDA")
        assert nvda["analyzed"] is False
        assert nvda["composite_score"] == 0

    def test_new_symbol_appended(self):
        self._setup_progress(["AAPL"])
        screener.update_stock_after_analysis("TSLA", _make_analysis("TSLA"))
        syms = {r["symbol"] for r in screener._progress["results"]}
        assert "TSLA" in syms

    def test_composite_score_updated(self):
        self._setup_progress(["AAPL"])
        screener.update_stock_after_analysis("AAPL", _make_analysis("AAPL"))
        row = next(r for r in screener._progress["results"] if r["symbol"] == "AAPL")
        assert row["composite_score"] == 65.0


if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
