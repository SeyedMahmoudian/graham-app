"""
Tests for ISSUE-002: FMP replaces Finnhub stock_candles for price history.

Verifies:
1. _fh_get_price_history does not exist (candles removed)
2. _fmp_get_price_history exists and parses FMP response correctly
3. get_price_history uses FMP when FMP_API_KEY is set, not Finnhub candles
4. get_price_history falls back to Alpha Vantage when FMP returns empty
5. No calls to stock_candles() are made anywhere in the module
"""

import importlib
import inspect
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import codes.data.alpha_vantage_client as client


# ── 1. Candle method removed ──────────────────────────────────────────────────

def test_fh_get_price_history_removed():
    """Finnhub candle function must no longer exist in the module."""
    assert not hasattr(client, "_fh_get_price_history"), (
        "_fh_get_price_history still exists — Finnhub candles must be removed"
    )


def test_no_stock_candles_call_in_source():
    """No actual call to stock_candles() must exist (doc mentions are OK)."""
    src = inspect.getsource(client)
    # Allow mentions in comments/docstrings explaining its removal,
    # but a live call would look like: _fh_client.stock_candles(
    assert "_fh_client.stock_candles(" not in src, (
        "Live stock_candles() call found — must be removed per ISSUE-002"
    )


# ── 2. FMP function exists and parses correctly ───────────────────────────────

def test_fmp_get_price_history_exists():
    assert hasattr(client, "_fmp_get_price_history"), (
        "_fmp_get_price_history is missing — FMP implementation required"
    )


def test_fmp_get_price_history_parses_response():
    """_fmp_get_price_history returns a DataFrame with Date and Close columns."""
    mock_historical = [
        {"date": f"2024-{m:02d}-28", "close": 100.0 + m}
        for m in range(1, 13)
    ]
    mock_response = {"historical": mock_historical}

    with patch.object(client, "FMP_API_KEY", "testkey"), \
         patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_response,
            raise_for_status=lambda: None,
        )
        df = client._fmp_get_price_history("AAPL", years=1)

    assert not df.empty, "Expected non-empty DataFrame from FMP"
    assert "Date" in df.columns
    assert "Close" in df.columns
    assert len(df) > 0


def test_fmp_get_price_history_returns_empty_without_key():
    """Without FMP_API_KEY, returns empty DataFrame immediately."""
    with patch.object(client, "FMP_API_KEY", ""):
        df = client._fmp_get_price_history("AAPL")
    assert df.empty


def test_fmp_get_price_history_handles_empty_historical():
    """FMP response with empty 'historical' list returns empty DataFrame."""
    with patch.object(client, "FMP_API_KEY", "testkey"), \
         patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"historical": []},
            raise_for_status=lambda: None,
        )
        df = client._fmp_get_price_history("ISRG", years=10)
    assert df.empty


def test_fmp_get_price_history_handles_network_error():
    """Network error must return empty DataFrame, not raise."""
    import requests as req
    with patch.object(client, "FMP_API_KEY", "testkey"), \
         patch("requests.get", side_effect=req.exceptions.ConnectionError("timeout")):
        df = client._fmp_get_price_history("ISRG", years=10)
    assert df.empty


# ── 3. get_price_history uses FMP when key is set ────────────────────────────

def test_get_price_history_uses_fmp_when_key_set():
    """When FMP_API_KEY is set, FMP is called; Finnhub candles are NOT called."""
    mock_historical = [{"date": f"2023-{m:02d}-28", "close": 150.0} for m in range(1, 13)]

    with patch.object(client, "FMP_API_KEY", "testkey"), \
         patch.object(client, "_fmp_get_price_history",
                      return_value=pd.DataFrame({"Date": ["2023-01-31"], "Close": [150.0]})) as fmp_mock, \
         patch("codes.data.alpha_vantage_client.read", return_value=None), \
         patch("codes.data.alpha_vantage_client.write"):
        df = client.get_price_history("AAPL", years=1)
        fmp_mock.assert_called_once_with("AAPL", 1)

    assert not df.empty


# ── 4. Fallback to Alpha Vantage when FMP returns empty ───────────────────────

def test_get_price_history_falls_back_to_av_when_fmp_empty():
    """When FMP returns empty, Alpha Vantage is used as fallback."""
    av_df = pd.DataFrame({"Date": ["2023-01-31"], "Close": [150.0]})

    with patch.object(client, "FMP_API_KEY", "testkey"), \
         patch.object(client, "_fmp_get_price_history", return_value=pd.DataFrame()), \
         patch.object(client, "_av_get_price_history", return_value=av_df) as av_mock, \
         patch("codes.data.alpha_vantage_client.read", return_value=None), \
         patch("codes.data.alpha_vantage_client.write"):
        df = client.get_price_history("ISRG", years=1)
        av_mock.assert_called_once()

    assert not df.empty


def test_get_price_history_uses_av_when_no_fmp_key():
    """Without FMP_API_KEY, falls directly to Alpha Vantage."""
    av_df = pd.DataFrame({"Date": ["2023-01-31"], "Close": [150.0]})

    with patch.object(client, "FMP_API_KEY", ""), \
         patch.object(client, "_av_get_price_history", return_value=av_df) as av_mock, \
         patch("codes.data.alpha_vantage_client.read", return_value=None), \
         patch("codes.data.alpha_vantage_client.write"):
        df = client.get_price_history("ISRG", years=1)
        av_mock.assert_called_once()

    assert not df.empty


# ── 5. FMP quote function exists ──────────────────────────────────────────────

def test_fmp_get_price_exists():
    assert hasattr(client, "_fmp_get_price"), (
        "_fmp_get_price is missing — FMP quote fallback required"
    )


def test_fmp_get_price_parses_response():
    with patch.object(client, "FMP_API_KEY", "testkey"), \
         patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"price": 185.5}],
            raise_for_status=lambda: None,
        )
        price = client._fmp_get_price("AAPL")
    assert price == pytest.approx(185.5)


def test_fmp_get_price_returns_none_without_key():
    with patch.object(client, "FMP_API_KEY", ""):
        price = client._fmp_get_price("AAPL")
    assert price is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
