"""
Price data client.

Source priority
───────────────
  Real-time quote : Finnhub (live) → Tiingo (EOD close fallback) → Alpha Vantage
  Price history   : Tiingo (primary) → Alpha Vantage (fallback)
  Split history   : Finnhub (explicit) → Tiingo (derived) → Alpha Vantage (derived)

FMP has been removed — both /api/v3/ (legacy, dead Aug 2025) and /stable/
(paid only, 402 on free accounts) are no longer usable on free tier.

─────────────────────────────────────────────────────────────────────────────
Finnhub  — real-time quotes + explicit split history
  Free tier : 60 calls / minute
  Sign up   : https://finnhub.io/register
  Env var   : FINNHUB_API_KEY=your_key
  Install   : pip install finnhub-python

Tiingo  — primary EOD history (20yr+), derived splits, EOD quote fallback
  Free tier : 500 calls / day, 50 calls / hour  (no credit card required)
  Sign up   : https://tiingo.com
  Env var   : TIINGO_API_KEY=your_key
  Auth      : Authorization: Token <key>  header  (no SDK needed)
  Endpoint  : https://api.tiingo.com/tiingo/daily/{ticker}/prices
              ?startDate=YYYY-MM-DD&token=KEY
  Returns   : close (unadjusted) + adjClose (split+dividend adjusted)
  Splits    : derived by detecting adjClose/close ratio jumps across days
              — no dedicated splits endpoint exists on Tiingo

Alpha Vantage  — last-resort fallback for history and splits
  Free tier : 25 calls / day, 5 calls / minute
  Sign up   : https://www.alphavantage.co/support/#api-key
  Env var   : AV_API_KEY=your_key

─────────────────────────────────────────────────────────────────────────────
RATE LIMITING
  Each provider is tracked by a RateLimiter (sliding-window call log).
  Two thresholds apply per window:

    WARN_PCT  (80 %) — warning printed, request still proceeds
    BLOCK_PCT (95 %) — RateLimitError raised, no request made

  Callers should catch RateLimitError:

    from price_client import RateLimitError, get_price

    try:
        price = get_price("NVDA")
    except RateLimitError as e:
        print(e)   # ⚠️  [Tiingo] Approaching daily limit (475/500 calls used …)

  Limits tracked:
    Finnhub       — 60 / minute
    Tiingo        — 50 / hour  AND  500 / day
    Alpha Vantage — 5 / minute  AND  25 / day

─────────────────────────────────────────────────────────────────────────────
BACKWARDS-COMPATIBLE SHIMS
  _fh_rate_limit() and _av_rate_limit() are kept as no-op shims so any
  module that imports them (e.g. EarningsRevision via alpha_vantage_client)
  does not break with AttributeError.  Migrate those callers to use the
  RateLimiter instances directly when convenient.
"""

import os
import time
import collections
import requests
import finnhub
import pandas as pd

from .cache import read, write


# ══════════════════════════════════════════════════════════════════════════════
# Rate-limit infrastructure
# ══════════════════════════════════════════════════════════════════════════════

class RateLimitError(RuntimeError):
    """
    Raised before a request is made when a provider is at or near its ceiling.

    Attributes
    ----------
    provider  : str          e.g. "Tiingo", "Finnhub", "AlphaVantage"
    window    : str          "per-minute", "hourly", or "daily"
    used      : int          calls made in the current window
    limit     : int          hard ceiling for this window
    resets_in : float | None seconds until the window resets (None = daily)
    """
    def __init__(
        self,
        provider: str,
        window: str,
        used: int,
        limit: int,
        resets_in: float | None = None,
    ):
        self.provider  = provider
        self.window    = window
        self.used      = used
        self.limit     = limit
        self.resets_in = resets_in

        reset_str = (
            f"  Resets in ~{int(resets_in)}s."
            if resets_in is not None
            else "  Resets at midnight (UTC)."
        )
        super().__init__(
            f"⚠️  [{provider}] Approaching {window} limit "
            f"({used}/{limit} calls used — processing paused to protect your quota)."
            f"{reset_str}"
        )


class _Window:
    """
    Sliding-window call counter.

    span_seconds : width of the rolling window  (60, 3600, or 86400)
    limit        : hard call ceiling within the window
    warn_pct     : fraction at which a warning is emitted      (default 0.80)
    block_pct    : fraction at which RateLimitError is raised  (default 0.95)
    """

    def __init__(
        self,
        span_seconds: int,
        limit: int,
        warn_pct: float = 0.80,
        block_pct: float = 0.95,
    ):
        self.span     = span_seconds
        self.limit    = limit
        self.warn_at  = int(limit * warn_pct)
        self.block_at = int(limit * block_pct)
        self._calls: collections.deque[float] = collections.deque()

    def _evict(self) -> None:
        cutoff = time.time() - self.span
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

    @property
    def used(self) -> int:
        self._evict()
        return len(self._calls)

    @property
    def resets_in(self) -> float:
        self._evict()
        if not self._calls:
            return 0.0
        return max(0.0, self._calls[0] + self.span - time.time())

    def check(self, provider: str, window_label: str) -> None:
        n = self.used
        if n >= self.block_at:
            raise RateLimitError(
                provider=provider,
                window=window_label,
                used=n,
                limit=self.limit,
                resets_in=self.resets_in if self.span < 86400 else None,
            )
        if n >= self.warn_at:
            ri    = int(self.resets_in)
            label = f"~{ri}s" if self.span < 86400 else "midnight UTC"
            print(
                f"  ⚠️  [{provider}] {window_label} limit warning: "
                f"{n}/{self.limit} calls used.  Resets in {label}."
            )

    def record(self) -> None:
        self._calls.append(time.time())

    def force_fill(self) -> None:
        """Mark window as full (used when the API itself returns a rate-limit error)."""
        now = time.time()
        while len(self._calls) < self.limit:
            self._calls.append(now)


class RateLimiter:
    """
    Composite rate limiter supporting multiple independent windows per provider.

    Usage
    -----
        limiter.check()    # raises RateLimitError if any window near ceiling
        # … make the API call …
        limiter.record()   # log timestamp in all windows

    windows : list of (span_seconds, limit, label)
        e.g. [(3600, 50, "hourly"), (86400, 500, "daily")]
    """

    def __init__(self, provider: str, windows: list[tuple[int, int, str]]):
        self.provider = provider
        self._windows = [
            (_Window(span, lim), label)
            for span, lim, label in windows
        ]

    def check(self) -> None:
        for win, label in self._windows:
            win.check(self.provider, label)

    def record(self) -> None:
        for win, _ in self._windows:
            win.record()

    def force_fill(self) -> None:
        for win, _ in self._windows:
            win.force_fill()

    def status(self) -> list[dict]:
        return [
            {
                "provider":  self.provider,
                "window":    label,
                "used":      win.used,
                "limit":     win.limit,
                "resets_in": round(win.resets_in, 1),
            }
            for win, label in self._windows
        ]


# ── Per-provider limiters ─────────────────────────────────────────────────────
_fh_limiter = RateLimiter(
    provider="Finnhub",
    windows=[(60, 60, "per-minute")],
)

_tiingo_limiter = RateLimiter(
    provider="Tiingo",
    windows=[
        (3600,  50,  "hourly"),
        (86400, 500, "daily"),
    ],
)

_av_limiter = RateLimiter(
    provider="AlphaVantage",
    windows=[
        (60,    5,  "per-minute"),
        (86400, 25, "daily"),
    ],
)


# ══════════════════════════════════════════════════════════════════════════════
# Provider config
# ══════════════════════════════════════════════════════════════════════════════

# Finnhub
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
_fh_client: finnhub.Client | None = (
    finnhub.Client(api_key=FINNHUB_API_KEY) if FINNHUB_API_KEY else None
)

# Tiingo
TIINGO_API_KEY  = os.getenv("TIINGO_API_KEY", "")
TIINGO_BASE_URL = "https://api.tiingo.com/tiingo/daily"
_TIINGO_HEADERS = {
    "Content-Type":  "application/json",
    "Authorization": f"Token {TIINGO_API_KEY}",
}

# Alpha Vantage
AV_API_KEY  = os.getenv("AV_API_KEY", "demo")
AV_BASE_URL = "https://www.alphavantage.co/query"

_TIMEOUT    = 30
_MAX_RETRY  = 3
_RETRY_WAIT = 5


# ══════════════════════════════════════════════════════════════════════════════
# HTTP helper
# ══════════════════════════════════════════════════════════════════════════════

def _get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> dict | list | None:
    """GET with retry.  Rate-limit check/record handled by each caller."""
    for attempt in range(1, _MAX_RETRY + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status == 429:
                print(f"  429 rate-limit from server (attempt {attempt}/{_MAX_RETRY})")
                # Don't retry 429 — surface it so the limiter can handle it
                return None
            print(f"  HTTP {status} error (attempt {attempt}/{_MAX_RETRY}): {e}")
            if attempt < _MAX_RETRY:
                time.sleep(_RETRY_WAIT)
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
# Finnhub  — real-time quotes + explicit split history
# ══════════════════════════════════════════════════════════════════════════════

def _fh_get_price(symbol: str) -> float | None:
    """Live quote from Finnhub SDK.  Raises RateLimitError if near per-minute ceiling."""
    if not _fh_client:
        return None
    _fh_limiter.check()
    try:
        quote = _fh_client.quote(symbol.upper())
        _fh_limiter.record()
        price = quote.get("c")
        if price and float(price) > 0:
            return round(float(price), 2)
    except RateLimitError:
        raise
    except Exception as e:
        print(f"  [Finnhub] quote error for {symbol}: {e}")
    return None


def _fh_get_splits(symbol: str) -> list[dict]:
    """
    Explicit split history from Finnhub.
    Returns [{"date": "YYYY-MM-DD", "ratio": float}, ...] oldest-first.
      ratio > 1.0 → forward split  (e.g. 4.0 = 4-for-1)
      ratio < 1.0 → reverse split  (e.g. 0.1 = 1-for-10)

    NOTE: earnings_surprises(), eps_estimates(), revenue_estimates() do NOT
    exist on the Finnhub free-tier SDK.  Callers must catch AttributeError.
    """
    if not _fh_client:
        return []
    _fh_limiter.check()
    try:
        today  = time.strftime("%Y-%m-%d")
        result = _fh_client.stock_splits(symbol.upper(), _from="2000-01-01", to=today)
        _fh_limiter.record()
        splits = []
        for item in result.get("data") or []:
            from_f = float(item.get("fromFactor", 1) or 1)
            to_f   = float(item.get("toFactor",   1) or 1)
            if from_f > 0 and to_f > 0 and abs(to_f / from_f - 1.0) > 0.001:
                splits.append({
                    "date":  item["date"],
                    "ratio": round(to_f / from_f, 6),
                })
        return sorted(splits, key=lambda x: x["date"])
    except RateLimitError:
        raise
    except Exception as e:
        print(f"  [Finnhub] splits error for {symbol}: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Tiingo  — EOD history (primary), EOD quote (fallback), derived splits
# ══════════════════════════════════════════════════════════════════════════════

def _tiingo_get(url: str, params: dict | None = None) -> dict | list | None:
    """
    Tiingo GET with rate-limit guard.
    Auth is passed via the Authorization header (set at module load).
    Raises RateLimitError if hourly or daily ceiling is near.
    Forces window fill if the server returns 429.
    """
    if not TIINGO_API_KEY:
        return None
    _tiingo_limiter.check()
    result = _get(url, params=params, headers=_TIINGO_HEADERS)
    if result is None:
        # _get returns None on 429 — fill limiter so next check() blocks
        _tiingo_limiter.force_fill()
        raise RateLimitError(
            provider="Tiingo",
            window="hourly (API-enforced)",
            used=_tiingo_limiter._windows[0][0].used,
            limit=_tiingo_limiter._windows[0][0].limit,
            resets_in=_tiingo_limiter._windows[0][0].resets_in,
        )
    _tiingo_limiter.record()
    return result


def _tiingo_get_price(symbol: str) -> float | None:
    """
    Latest EOD close from Tiingo.
    Endpoint: GET /tiingo/daily/{ticker}/prices  (returns last available close)
    NOT a live intraday quote — use Finnhub for that.
    """
    try:
        print(f"  [Tiingo] fetching latest EOD price for {symbol}...")
        url  = f"{TIINGO_BASE_URL}/{symbol.lower()}/prices"
        data = _tiingo_get(url)
        if data and isinstance(data, list) and len(data) > 0:
            price = data[-1].get("close") or data[-1].get("adjClose")
            if price and float(price) > 0:
                return round(float(price), 2)
    except RateLimitError:
        raise
    except Exception as e:
        print(f"  [Tiingo] price error for {symbol}: {e}")
    return None


def _tiingo_get_price_history(symbol: str, years: int = 10) -> pd.DataFrame:
    """
    Daily EOD history from Tiingo, resampled to monthly.

    Endpoint : GET /tiingo/daily/{ticker}/prices
               ?startDate=YYYY-MM-DD  (client-side date param, supported)
    Response : [{"date": ..., "close": float, "adjClose": float, ...}, ...]

    Both raw close and adjClose are stored — we use close (unadjusted) to
    match the existing stack's convention.  adjClose is retained in the
    DataFrame for callers that need split-adjusted values.

    Splits: derived here by detecting adjClose/close ratio jumps > 0.5%
    between consecutive days.  Stored separately via _tiingo_derive_splits().
    """
    try:
        cutoff     = pd.Timestamp.now() - pd.DateOffset(years=years)
        start_date = cutoff.strftime("%Y-%m-%d")

        print(f"  [Tiingo] fetching {years}yr history for {symbol}...")
        url  = f"{TIINGO_BASE_URL}/{symbol.lower()}/prices"
        data = _tiingo_get(url, params={"startDate": start_date})

        if not data or not isinstance(data, list):
            print(f"  [Tiingo] no history returned for {symbol}")
            return pd.DataFrame()

    except RateLimitError:
        raise
    except Exception as e:
        print(f"  [Tiingo] history error for {symbol}: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(data)
    df.columns = [c.strip().lower() for c in df.columns]

    if "date" not in df.columns or "close" not in df.columns:
        print(f"  [Tiingo] unexpected response shape for {symbol}: {list(df.columns)}")
        return pd.DataFrame()

    df["Date"]     = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_localize(None)
    df["Close"]    = pd.to_numeric(df["close"],    errors="coerce")
    df["AdjClose"] = pd.to_numeric(df.get("adjclose", df["close"]), errors="coerce")
    df = df[["Date", "Close", "AdjClose"]].dropna(subset=["Date", "Close"])
    df = df.sort_values("Date").reset_index(drop=True)

    # Client-side filter (belt-and-suspenders — startDate param usually handles it)
    df = df[df["Date"] >= cutoff].reset_index(drop=True)
    print(f"  [Tiingo] {len(df)} daily rows for {symbol}, filtering client-side to {years}yr")

    if df.empty:
        return pd.DataFrame()

    # Resample to monthly (last trading day of each month)
    try:
        df = df.set_index("Date").resample("ME").last().dropna(subset=["Close"]).reset_index()
    except ValueError:
        df = df.set_index("Date").resample("M").last().dropna(subset=["Close"]).reset_index()

    df["Date"]     = df["Date"].dt.strftime("%Y-%m-%d")
    df["Close"]    = df["Close"].round(4)
    df["AdjClose"] = df["AdjClose"].round(4)
    print(f"  [Tiingo] {len(df)} monthly rows for {symbol} ({years}yr)")
    return df


def _tiingo_derive_splits(symbol: str, years: int = 10) -> list[dict]:
    """
    Derive split events from Tiingo daily price history by detecting
    step-changes in the adjClose / close ratio.

    A ratio jump > 0.5% between consecutive trading days indicates a
    corporate action (split or large special dividend) on that date.

    This is an approximation — it cannot distinguish splits from special
    dividends, and the ratio may differ slightly from the true split factor.
    Use Finnhub's explicit split endpoint when exact ratios are needed.

    Returns [{"date": "YYYY-MM-DD", "ratio": float}, ...] oldest-first.
    """
    try:
        cutoff     = pd.Timestamp.now() - pd.DateOffset(years=years)
        start_date = cutoff.strftime("%Y-%m-%d")
        url        = f"{TIINGO_BASE_URL}/{symbol.lower()}/prices"
        data       = _tiingo_get(url, params={"startDate": start_date})

        if not data or not isinstance(data, list):
            return []
    except RateLimitError:
        raise
    except Exception as e:
        print(f"  [Tiingo] derive_splits error for {symbol}: {e}")
        return []

    df = pd.DataFrame(data)
    df.columns = [c.strip().lower() for c in df.columns]

    if "date" not in df.columns or "close" not in df.columns or "adjclose" not in df.columns:
        return []

    df["date"]     = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_localize(None)
    df["close"]    = pd.to_numeric(df["close"],    errors="coerce")
    df["adjclose"] = pd.to_numeric(df["adjclose"], errors="coerce")
    df = df.dropna(subset=["date", "close", "adjclose"])
    df = df[df["close"] > 0].sort_values("date").reset_index(drop=True)

    df["ratio"] = df["adjclose"] / df["close"]
    df["ratio_prev"] = df["ratio"].shift(1)
    df["ratio_chg"]  = (df["ratio"] - df["ratio_prev"]).abs() / df["ratio_prev"].abs()

    # Flag days where the ratio changed by more than 0.5%
    splits_df = df[df["ratio_chg"] > 0.005].copy()

    splits = []
    for _, row in splits_df.iterrows():
        if pd.notna(row["ratio_prev"]) and row["ratio_prev"] > 0:
            factor = round(row["ratio"] / row["ratio_prev"], 6)
            splits.append({
                "date":  row["date"].strftime("%Y-%m-%d"),
                "ratio": factor,
            })

    return sorted(splits, key=lambda x: x["date"])


# ══════════════════════════════════════════════════════════════════════════════
# Alpha Vantage  — last-resort fallback
# ══════════════════════════════════════════════════════════════════════════════

def _av_get(params: dict) -> dict | None:
    """
    Alpha Vantage GET with rate-limit guard.
    Raises RateLimitError on pre-flight check or on API-returned rate-limit body.
    """
    _av_limiter.check()

    data = _get(AV_BASE_URL, params=params)
    if not data:
        return None

    if "Error Message" in data:
        print(f"  [AlphaVantage] API error: {data['Error Message']}")
        return None

    if "Note" in data or "Information" in data:
        msg = data.get("Note") or data.get("Information", "")
        print(f"  [AlphaVantage] API rate-limit response: {msg[:120]}")
        _av_limiter.force_fill()
        raise RateLimitError(
            provider="AlphaVantage",
            window="per-minute (API-enforced)",
            used=_av_limiter._windows[0][0].used,
            limit=_av_limiter._windows[0][0].limit,
            resets_in=60.0,
        )

    _av_limiter.record()
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
    rows   = []
    for date_str, vals in ts.items():
        dt = pd.to_datetime(date_str)
        if dt >= cutoff:
            rows.append({
                "Date":     dt.strftime("%Y-%m-%d"),
                "Close":    float(vals["4. close"]),
                "AdjClose": float(vals["4. close"]),  # AV monthly has no adjClose
            })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df


def _av_get_splits(symbol: str) -> list[dict]:
    data = _av_get({
        "function": "TIME_SERIES_MONTHLY_ADJUSTED",
        "symbol":   symbol.upper(),
        "apikey":   AV_API_KEY,
    })
    if not data:
        return []

    ts     = data.get("Monthly Adjusted Time Series", {})
    splits = []
    for date_str, vals in ts.items():
        try:
            coeff = float(vals.get("8. split coefficient", 1) or 1)
            if abs(coeff - 1.0) > 0.001:
                splits.append({"date": date_str, "ratio": round(coeff, 6)})
        except (ValueError, TypeError):
            continue
    return sorted(splits, key=lambda x: x["date"])


# ══════════════════════════════════════════════════════════════════════════════
# Public API — drop-in replacement, same signatures throughout
# ══════════════════════════════════════════════════════════════════════════════

def get_price(symbol: str) -> float | None:
    """
    Current price.
    Priority: Finnhub (live) → Tiingo (EOD close) → Alpha Vantage (EOD close)

    Finnhub gives a live intraday price during market hours.
    Tiingo and Alpha Vantage give the most recent EOD close.

    Raises RateLimitError if the active provider is near its ceiling.
    RateLimitError from Finnhub is NOT silently swallowed — it propagates
    immediately so the caller can surface it rather than burning Tiingo quota.
    """
    symbol = symbol.upper().strip()

    if _fh_client:
        print(f"  [Finnhub] fetching price for {symbol}...")
        try:
            price = _fh_get_price(symbol)
            if price:
                return price
            print(f"  [Finnhub] no price returned, trying Tiingo...")
        except RateLimitError:
            raise

    if TIINGO_API_KEY:
        try:
            price = _tiingo_get_price(symbol)
            if price:
                return price
            print(f"  [Tiingo] no price returned, trying Alpha Vantage...")
        except RateLimitError:
            raise

    print(f"  [AlphaVantage] fetching price for {symbol}...")
    return _av_get_price(symbol)


def get_price_history(symbol: str, years: int = 10) -> pd.DataFrame:
    """
    Monthly EOD price history for `years` years.
    Priority: Tiingo (primary) → Alpha Vantage (fallback)

    Returns a DataFrame with columns: Date (str YYYY-MM-DD), Close (float),
    AdjClose (float).  Cached after first fetch.

    Raises RateLimitError if the active provider is near its ceiling.
    """
    symbol = symbol.upper().strip()

    cached = read("hist", symbol)
    if cached:
        return pd.DataFrame(cached)

    df = pd.DataFrame()

    if TIINGO_API_KEY:
        try:
            df = _tiingo_get_price_history(symbol, years)
        except RateLimitError:
            raise
        if df.empty:
            print(f"  [Tiingo] no history returned, falling back to Alpha Vantage...")

    if df.empty:
        print(f"  [AlphaVantage] fetching {years}yr history for {symbol}...")
        df = _av_get_price_history(symbol, years)

    if not df.empty:
        write("hist", symbol, df.to_dict("records"))

    return df


def get_splits(symbol: str) -> list[dict]:
    """
    Full split history, sorted oldest-first.
    Each item: {"date": "YYYY-MM-DD", "ratio": float}
      ratio > 1.0 → forward split  (share count × ratio)
      ratio < 1.0 → reverse split  (share count ÷ 1/ratio)

    Priority:
      1. Finnhub  — explicit split dates and exact ratios          (best)
      2. Tiingo   — derived from adjClose/close ratio jumps        (approximate)
      3. Alpha Vantage — derived from split coefficient field      (approximate)

    Cached after first fetch.
    Raises RateLimitError if the active provider is near its ceiling.
    """
    symbol = symbol.upper().strip()

    cached = read("splits", symbol)
    if cached is not None:
        return cached

    splits = []

    if _fh_client:
        print(f"  [Finnhub] fetching split history for {symbol}...")
        try:
            splits = _fh_get_splits(symbol)
        except RateLimitError:
            raise

    if not splits and TIINGO_API_KEY:
        print(f"  [Tiingo] deriving split history for {symbol}...")
        try:
            splits = _tiingo_derive_splits(symbol)
        except RateLimitError:
            raise

    if not splits:
        print(f"  [AlphaVantage] fetching split history for {symbol}...")
        splits = _av_get_splits(symbol)

    write("splits", symbol, splits)
    return splits


# ══════════════════════════════════════════════════════════════════════════════
# Diagnostics
# ══════════════════════════════════════════════════════════════════════════════

def rate_limit_status() -> list[dict]:
    """
    Current call-usage snapshot for all tracked providers.

    Example output:
        [
          {"provider": "Finnhub",      "window": "per-minute", "used": 12,  "limit": 60,  "resets_in": 44.2},
          {"provider": "Tiingo",       "window": "hourly",     "used": 8,   "limit": 50,  "resets_in": 1820.5},
          {"provider": "Tiingo",       "window": "daily",      "used": 31,  "limit": 500, "resets_in": 0.0},
          {"provider": "AlphaVantage", "window": "per-minute", "used": 3,   "limit": 5,   "resets_in": 22.8},
          {"provider": "AlphaVantage", "window": "daily",      "used": 9,   "limit": 25,  "resets_in": 0.0},
        ]
    """
    return _fh_limiter.status() + _tiingo_limiter.status() + _av_limiter.status()


# ══════════════════════════════════════════════════════════════════════════════
# Backwards-compatibility shims
# ══════════════════════════════════════════════════════════════════════════════
# Kept so that any module importing _fh_rate_limit or _av_rate_limit
# (e.g. EarningsRevision via the old alpha_vantage_client) doesn't break
# with AttributeError.  Migrate callers to RateLimiter instances when convenient.

def _fh_rate_limit() -> None:
    """Deprecated — use _fh_limiter.check() / .record() instead."""
    _fh_limiter.check()


def _av_rate_limit() -> None:
    """Deprecated — use _av_limiter.check() / .record() instead."""
    _av_limiter.check()