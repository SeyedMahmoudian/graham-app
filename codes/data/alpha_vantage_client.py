"""
Price data client.

Source priority:
  Real-time quote : Finnhub → FMP → Alpha Vantage
  Price history   : FMP (primary) → Alpha Vantage (fallback)
  Split history   : Finnhub → Alpha Vantage

FMP (Financial Modeling Prep) free tier: unlimited historical daily prices.
Get your free key at: https://financialmodelingprep.com/developer/docs
Set env var: FMP_API_KEY=your_key

Finnhub free tier: 60 calls/min, real-time quotes only.
Get your free key at: https://finnhub.io/register
Set env var: FINNHUB_API_KEY=your_key
Install SDK:  pip install finnhub-python

Alpha Vantage (last-resort fallback): 25 calls/day, 5 calls/min.
Get your free key at: https://www.alphavantage.co/support/#api-key
Set env var: AV_API_KEY=your_key

NOTE: Finnhub stock_candles() is NOT used — it returns 403 for many symbols
on the free tier.  All historical data goes through FMP.
"""

import os
import time
import requests
import finnhub
import pandas as pd

from .cache import read, write

# ── Finnhub config ────────────────────────────────────────────────────────────
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
# Free tier: 60 calls/min → 1 call/sec safe ceiling
_FH_MIN_INTERVAL = 1.1   # seconds between Finnhub calls
_fh_last_call    = 0.0

# Instantiate SDK client once at module load (None if no key set)
_fh_client: finnhub.Client | None = (
    finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None
)

# ── FMP (Financial Modeling Prep) — primary history source ───────────────────
FMP_API_KEY  = os.getenv("FMP_API_KEY", "")
FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"

# ── Alpha Vantage — last-resort fallback ──────────────────────────────────────
AV_API_KEY       = os.getenv("AV_API_KEY", "demo")
AV_BASE_URL      = "https://www.alphavantage.co/query"
_AV_MIN_INTERVAL = 12    # 5 calls/min on free tier
_av_last_call    = 0.0

# ── Shared retry config ───────────────────────────────────────────────────────
_TIMEOUT    = 30
_MAX_RETRY  = 3
_RETRY_WAIT = 5


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _fh_rate_limit():
    global _fh_last_call
    wait = _FH_MIN_INTERVAL - (time.time() - _fh_last_call)
    if wait > 0:
        time.sleep(wait)
    _fh_last_call = time.time()


def _av_rate_limit():
    global _av_last_call
    wait = _AV_MIN_INTERVAL - (time.time() - _av_last_call)
    if wait > 0:
        time.sleep(wait)
    _av_last_call = time.time()


def _get(url: str, params: dict, rate_fn) -> dict | None:
    """GET with retry — used only by the Alpha Vantage layer."""
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            rate_fn()
            r = requests.get(url, params=params, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            print(f"  Timeout (attempt {attempt}/{_MAX_RETRY}), retrying in {_RETRY_WAIT}s...")
            if attempt < _MAX_RETRY:
                time.sleep(_RETRY_WAIT)
        except requests.exceptions.ConnectionError as e:
            print(f"  Connection error (attempt {attempt}/{_MAX_RETRY}): {e}")
            if attempt < _MAX_RETRY:
                time.sleep(_RETRY_WAIT)
        except Exception as e:
            print(f"  Unexpected error (attempt {attempt}/{_MAX_RETRY}): {e}")
            if attempt < _MAX_RETRY:
                time.sleep(_RETRY_WAIT)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Finnhub implementation  (uses finnhub-python SDK, no raw HTTP)
# ══════════════════════════════════════════════════════════════════════════════

def _fh_get_price(symbol: str) -> float | None:
    if not _fh_client:
        return None
    try:
        _fh_rate_limit()
        quote = _fh_client.quote(symbol.upper())
        price = quote.get("c")  # current price
        if price and float(price) > 0:
            return round(float(price), 2)
    except Exception as e:
        print(f"  [Finnhub SDK] quote error for {symbol}: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# FMP implementation  (primary for all historical data)
# ══════════════════════════════════════════════════════════════════════════════

def _fmp_get_price(symbol: str) -> float | None:
    """Real-time quote from FMP (used as Finnhub fallback)."""
    if not FMP_API_KEY:
        return None
    try:
        url  = f"{FMP_BASE_URL}/quote-short/{symbol.upper()}"
        resp = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list):
            price = data[0].get("price")
            if price and float(price) > 0:
                return round(float(price), 2)
    except Exception as e:
        print(f"  [FMP] quote error for {symbol}: {e}")
    return None


def _fmp_get_price_history(symbol: str, years: int = 10) -> pd.DataFrame:
    """
    Fetch daily price history from FMP and resample to monthly.
    Endpoint: /api/v3/historical-price-full/{symbol}?from=YYYY-MM-DD&apikey=...
    No rate-limit concerns on FMP free tier for this endpoint.
    """
    if not FMP_API_KEY:
        return pd.DataFrame()

    cutoff = (pd.Timestamp.now() - pd.DateOffset(years=years)).strftime("%Y-%m-%d")
    url    = f"{FMP_BASE_URL}/historical-price-full/{symbol.upper()}"
    try:
        resp = requests.get(
            url,
            params={"apikey": FMP_API_KEY, "from": cutoff},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [FMP] price history error for {symbol}: {e}")
        return pd.DataFrame()

    historical = data.get("historical", [])
    if not historical:
        print(f"  [FMP] no historical data for {symbol}")
        return pd.DataFrame()

    df = (
        pd.DataFrame(historical)[["date", "close"]]
        .rename(columns={"date": "Date", "close": "Close"})
    )
    df["Date"]  = pd.to_datetime(df["Date"])
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna().sort_values("Date").reset_index(drop=True)

    # Resample to monthly (last trading day of each month)
    try:
        df = df.set_index("Date").resample("ME").last().dropna().reset_index()
    except ValueError:
        # pandas < 2.2 uses "M" instead of "ME"
        df = df.set_index("Date").resample("M").last().dropna().reset_index()

    df["Date"]  = df["Date"].dt.strftime("%Y-%m-%d")
    df["Close"] = df["Close"].round(4)
    print(f"  [FMP] {len(df)} monthly rows for {symbol} ({years}yr)")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Alpha Vantage fallback implementation
# ══════════════════════════════════════════════════════════════════════════════

def _av_get(params: dict) -> dict | None:
    data = _get(AV_BASE_URL, params, _av_rate_limit)
    if not data:
        return None
    if "Error Message" in data:
        print(f"  AV API error: {data['Error Message']}")
        return None
    if "Note" in data or "Information" in data:
        msg = data.get("Note") or data.get("Information", "")
        print(f"  AV rate limit: {msg[:80]}...")
        time.sleep(60)
        return None
    return data


def _av_get_price(symbol: str) -> float | None:
    data = _av_get({
        "function": "GLOBAL_QUOTE",
        "symbol":   symbol.upper(),
        "apikey":   AV_API_KEY,
    })
    if not data:
        return None
    price_str = data.get("Global Quote", {}).get("05. price")
    if price_str:
        try:
            p = float(price_str)
            return round(p, 2) if p > 0 else None
        except (ValueError, TypeError):
            pass
    return None


def _av_get_price_history(symbol: str, years: int = 10) -> pd.DataFrame:
    data = _av_get({
        "function": "TIME_SERIES_MONTHLY",
        "symbol":   symbol.upper(),
        "apikey":   AV_API_KEY,
    })
    if not data:
        return pd.DataFrame()

    ts = data.get("Monthly Time Series", {})
    if not ts:
        return pd.DataFrame()

    cutoff = pd.Timestamp.now() - pd.DateOffset(years=years)
    rows = []
    for date_str, vals in ts.items():
        dt = pd.to_datetime(date_str)
        if dt >= cutoff:
            rows.append({"Date": dt.strftime("%Y-%m-%d"), "Close": float(vals["4. close"])})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# Public API  —  same signatures as before, drop-in replacement
# ══════════════════════════════════════════════════════════════════════════════

def get_price(symbol: str) -> float | None:
    """Current price: Finnhub → FMP → Alpha Vantage."""
    symbol = symbol.upper().strip()

    if _fh_client:
        print(f"  [Finnhub] fetching price for {symbol}...")
        price = _fh_get_price(symbol)
        if price:
            return price
        print(f"  [Finnhub] no price, trying FMP...")

    if FMP_API_KEY:
        print(f"  [FMP] fetching price for {symbol}...")
        price = _fmp_get_price(symbol)
        if price:
            return price
        print(f"  [FMP] no price, falling back to Alpha Vantage...")

    print(f"  [AlphaVantage] fetching price for {symbol}...")
    return _av_get_price(symbol)


def get_price_history(symbol: str, years: int = 10) -> pd.DataFrame:
    """
    Monthly price history for `years` years.
    FMP primary → Alpha Vantage fallback.
    Finnhub stock_candles() is NOT used (403 errors on free tier).
    Result is cached.
    """
    symbol = symbol.upper().strip()

    cached = read("hist", symbol)
    if cached:
        return pd.DataFrame(cached)

    df = pd.DataFrame()

    if FMP_API_KEY:
        print(f"  [FMP] fetching {years}yr history for {symbol}...")
        df = _fmp_get_price_history(symbol, years)
        if df.empty:
            print(f"  [FMP] no history returned, falling back to Alpha Vantage...")

    if df.empty:
        print(f"  [AlphaVantage] fetching {years}yr history for {symbol}...")
        df = _av_get_price_history(symbol, years)

    if not df.empty:
        write("hist", symbol, df.to_dict("records"))

    return df


# ══════════════════════════════════════════════════════════════════════════════
# Split history
# ══════════════════════════════════════════════════════════════════════════════

def _fh_get_splits(symbol: str) -> list[dict]:
    """
    Fetch full split history from Finnhub SDK.
    Returns [{"date": "YYYY-MM-DD", "ratio": float}, ...] oldest-first.
      ratio > 1.0  →  forward split  (e.g. 20.0 = 20-for-1: shares multiply by 20)
      ratio < 1.0  →  reverse split  (e.g. 0.1  = 1-for-10: shares divide by 10)
    """
    if not _fh_client:
        return []
    try:
        today = time.strftime("%Y-%m-%d")
        _fh_rate_limit()
        result = _fh_client.stock_splits(symbol.upper(), _from="2000-01-01", to=today)
        splits = []
        for item in result.get("data") or []:
            from_f = float(item.get("fromFactor", 1) or 1)
            to_f   = float(item.get("toFactor",   1) or 1)
            if from_f > 0 and to_f > 0 and abs(to_f / from_f - 1.0) > 0.001:
                splits.append({
                    "date":  item["date"],             # "YYYY-MM-DD"
                    "ratio": round(to_f / from_f, 6),  # e.g. 20.0 for 20:1 split
                })
        return sorted(splits, key=lambda x: x["date"])
    except Exception as e:
        print(f"  [Finnhub SDK] splits error for {symbol}: {e}")
        return []


def _av_get_splits(symbol: str) -> list[dict]:
    """
    Alpha Vantage fallback: detect splits from TIME_SERIES_MONTHLY_ADJUSTED
    via the '8. split coefficient' field.  Values != 1.0 mean a split occurred
    in that month.  Date is approximate (last trading day of the split month).
    """
    data = _av_get({
        "function": "TIME_SERIES_MONTHLY_ADJUSTED",
        "symbol":   symbol.upper(),
        "apikey":   AV_API_KEY,
    })
    if not data:
        return []
    ts = data.get("Monthly Adjusted Time Series", {})
    splits = []
    for date_str, vals in ts.items():
        try:
            coeff = float(vals.get("8. split coefficient", 1) or 1)
            if abs(coeff - 1.0) > 0.001:
                splits.append({"date": date_str, "ratio": round(coeff, 6)})
        except (ValueError, TypeError):
            continue
    return sorted(splits, key=lambda x: x["date"])


def get_splits(symbol: str) -> list[dict]:
    """
    Full split history for a symbol, sorted oldest-first.
    Each item: {"date": "YYYY-MM-DD", "ratio": float}
      ratio > 1.0  →  forward split  (share count multiplied by ratio)
      ratio < 1.0  →  reverse split  (share count divided by 1/ratio)

    Finnhub primary, Alpha Vantage fallback.
    Cached for 6 months — splits are rare; clear cache manually after a split
    if needed before the next natural expiry.
    """
    symbol = symbol.upper().strip()

    cached = read("splits", symbol)
    if cached is not None:
        return cached

    splits = []

    if _fh_client:
        print(f"  [Finnhub] fetching split history for {symbol}...")
        splits = _fh_get_splits(symbol)

    if not splits:
        print(f"  [AlphaVantage] fetching split history for {symbol}...")
        splits = _av_get_splits(symbol)

    write("splits", symbol, splits)
    return splits