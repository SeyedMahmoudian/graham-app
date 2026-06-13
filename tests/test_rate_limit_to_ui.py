"""
Test that a hard RateLimitError raised by api_fetcher.get_price() during
analyze_stock() is converted into result["error"], which run_analysis()
renders in status-msg (existing UI error path) instead of crashing the
callback. Warning-level (80%) prints are untouched — only the hard
block-threshold RateLimitError is surfaced.
"""

import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "codes"))

from data.api_fetcher import RateLimitError

# codes.app runs startup() at import time (sec_data.get_ticker_map(),
# universe.get_universe(), screener.load_cached_only()), which makes
# network calls. Stub those out before import so this test is isolated
# from network/SEC availability — unrelated to the change under test.
with patch("codes.data.sec_data.get_ticker_map", return_value={}), \
     patch("codes.engine.universe.get_universe", return_value=[]), \
     patch("codes.engine.screener.load_cached_only", return_value=[]):
    import codes.app as app


def _make_sec_facts(symbol="AAPL"):
    return {
        "name": f"{symbol} Inc.",
        "sector": "Technology",
        "shares": [{"value": "1000000"}],
    }


def test_rate_limit_error_surfaced_as_result_error():
    sec_facts = _make_sec_facts("AAPL")
    err = RateLimitError(
        provider="Tiingo", window="hourly", used=48, limit=50, resets_in=120.0,
    )

    with patch.object(app, "_analysis_cache", {}), \
         patch("codes.app.cache.read", return_value=None), \
         patch("codes.app.sec_data.get_financials", return_value=sec_facts), \
         patch("codes.app.quality.score", return_value={"total_score": 0, "total_max": 100, "criteria": []}), \
         patch("codes.app.api_fetcher.get_price", side_effect=err):

        result = app.analyze_stock("AAPL")

    assert "error" in result
    assert "Tiingo" in result["error"]
    assert "Approaching hourly limit" in result["error"]


def test_rate_limit_error_does_not_propagate_as_exception():
    """analyze_stock must not raise — run_analysis relies on dict-shaped errors."""
    sec_facts = _make_sec_facts("MSFT")
    err = RateLimitError(
        provider="AlphaVantage", window="per-minute", used=5, limit=5, resets_in=30.0,
    )

    with patch.object(app, "_analysis_cache", {}), \
         patch("codes.app.cache.read", return_value=None), \
         patch("codes.app.sec_data.get_financials", return_value=sec_facts), \
         patch("codes.app.quality.score", return_value={"total_score": 0, "total_max": 100, "criteria": []}), \
         patch("codes.app.api_fetcher.get_price", side_effect=err):

        try:
            result = app.analyze_stock("MSFT")
        except RateLimitError:
            assert False, "RateLimitError must be caught inside analyze_stock"

    assert result == {"error": str(err)}
