"""
Stock universe: full US equity market (~3,500 stocks).

Sources (all free, no API key):
  Primary   — iShares Russell 3000 ETF (IWV): every US large/mid/small-cap
  Supplement— iShares Russell Microcap ETF (IWC): ~1,400 micro-caps not in R3000
  Supplement— iShares Russell 2000 Value ETF (IWN): ensures value-tilted
              small-caps aren't under-represented
  Fallback  — Wikipedia S&P 500 list if iShares is unreachable

All sources return US-listed equities that file SEC 10-Ks, so they work
natively with the SEC EDGAR pipeline in sec_data.py.

iShares CSV URL pattern (confirmed stable):
  https://www.ishares.com/us/products/{PRODUCT_ID}/{ETF}_/1467271812596.ajax
    ?fileType=csv&fileName={ETF}_holdings&dataType=fund
The CSV has ~10 header rows of metadata, then holdings, then a footer
disclaimer — we skip the header with skiprows and drop the footer rows.
"""

import pandas as pd
import cache
import requests
from io import StringIO

# =========================
# iShares CSV sources
# =========================
# product_id taken from the ishares.com product page URL
# Each entry: (etf_ticker, product_id, friendly_name)
ISHARES_SOURCES = [
    ("IWV", "239714", "Russell 3000 (all US large/mid/small-cap)"),
    ("IWC", "239716", "Russell Microcap (~1,400 micro-caps)"),
    ("IWN", "239712", "Russell 2000 Value (value-tilted small-caps)"),
]

ISHARES_URL = (
    "https://www.ishares.com/us/products/{pid}"
    "/1467271812596.ajax?fileType=csv&fileName={etf}_holdings&dataType=fund"
)

# =========================
# Fallback: Wikipedia S&P 500
# =========================
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
FALLBACK_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG", "BRK.B",
                    "JNJ", "V", "PG", "JPM", "UNH", "HD", "MA", "XOM"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# =========================
# iShares CSV fetcher
# =========================

def _fetch_ishares_tickers(etf: str, product_id: str, name: str) -> list[str]:
    """
    Download an iShares ETF holdings CSV and return US equity tickers.

    The CSV format:
      Rows 0-9  : metadata (fund name, date, AUM, etc.)
      Row 10+   : holdings with columns including 'Ticker', 'Asset Class'
      Last rows : footer disclaimer separated by rows of \xa0 (non-breaking space)

    We filter to Asset Class == 'Equity' to exclude cash, futures, and
    any non-US depositary receipts that slip through.
    """
    url = ISHARES_URL.format(pid=product_id, etf=etf)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()

        # Decode and find where the actual data starts (after metadata header)
        lines = resp.content.decode("utf-8", errors="replace").splitlines()

        # Find the header row (contains "Ticker" or "Name")
        header_idx = None
        for i, line in enumerate(lines):
            if "Ticker" in line and "Name" in line:
                header_idx = i
                break

        if header_idx is None:
            print(f"  ⚠️  [{etf}] Could not find header row in CSV")
            return []

        # Truncate footer: drop rows after any near-empty line past the header
        data_lines = [lines[header_idx]]
        for line in lines[header_idx + 1:]:
            # iShares footers start with a row of just commas / \xa0
            stripped = line.replace("\xa0", "").replace(",", "").strip()
            if not stripped:
                break
            data_lines.append(line)

        df = pd.read_csv(StringIO("\n".join(data_lines)))

        # Normalise column names (iShares sometimes varies capitalisation)
        df.columns = [c.strip() for c in df.columns]

        if "Ticker" not in df.columns:
            print(f"  ⚠️  [{etf}] No 'Ticker' column found. Columns: {list(df.columns)}")
            return []

        # Keep equities only — drops cash rows, money-market instruments, etc.
        if "Asset Class" in df.columns:
            df = df[df["Asset Class"].str.strip() == "Equity"]

        tickers = (
            df["Ticker"]
            .astype(str)
            .str.strip()
            .str.upper()
            .tolist()
        )
        # Remove blanks, "-", and obvious non-tickers
        tickers = [
            t for t in tickers
            if t and t not in ("-", "NAN", "N/A", "CASH")
            and len(t) <= 6
            and t.isalpha()   # pure alpha — drops things like "CASH_USD"
        ]

        print(f"  ✅ [{etf}] {len(tickers):,} equity tickers — {name}")
        return tickers

    except Exception as e:
        print(f"  ⚠️  [{etf}] Failed to load iShares CSV: {e}")
        return []


def _fetch_wikipedia_sp500() -> list[str]:
    """Fallback: S&P 500 from Wikipedia."""
    try:
        resp = requests.get(SP500_WIKI_URL, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        df = tables[0]
        for col in ["Symbol", "Ticker", "Ticker symbol"]:
            if col in df.columns:
                tickers = df[col].astype(str).str.strip().tolist()
                tickers = [t for t in tickers if t and t != "nan"]
                print(f"  ✅ [Fallback] {len(tickers)} S&P 500 tickers from Wikipedia")
                return tickers
    except Exception as e:
        print(f"  ⚠️  [Fallback] Wikipedia S&P 500 failed: {e}")
    return FALLBACK_TICKERS


# =========================
# Individual index getters (cached separately so they can be refreshed
# independently without busting the whole combined universe cache)
# =========================

def get_russell3000() -> list[str]:
    cached = cache.read("universe", "iwv")
    if cached:
        return cached
    tickers = _fetch_ishares_tickers(*ISHARES_SOURCES[0])
    if tickers:
        cache.write("universe", "iwv", tickers)
    return tickers


def get_russell_microcap() -> list[str]:
    cached = cache.read("universe", "iwc")
    if cached:
        return cached
    tickers = _fetch_ishares_tickers(*ISHARES_SOURCES[1])
    if tickers:
        cache.write("universe", "iwc", tickers)
    return tickers


def get_russell2000_value() -> list[str]:
    cached = cache.read("universe", "iwn")
    if cached:
        return cached
    tickers = _fetch_ishares_tickers(*ISHARES_SOURCES[2])
    if tickers:
        cache.write("universe", "iwn", tickers)
    return tickers


# =========================
# Combined Universe
# =========================

def get_universe() -> list[str]:
    cached = cache.read("universe", "combined")
    if cached:
        return cached

    print("\n📋 Loading full US equity universe...")

    r3000  = get_russell3000()
    microcap = get_russell_microcap()
    r2000v   = get_russell2000_value()

    if not r3000:
        print("  ⚠️  Russell 3000 unavailable — falling back to S&P 500")
        r3000 = _fetch_wikipedia_sp500()

    # Merge, preserving order, deduplicating
    combined = list(dict.fromkeys(r3000 + microcap + r2000v))

    print(f"✅ Universe loaded: {len(combined):,} unique US stocks\n")
    cache.write("universe", "combined", combined)
    return combined


def get_cached_universe() -> list[str]:
    """Only stocks with cached SEC data."""
    universe = get_universe()
    return [t for t in universe if cache.read("sec_facts", t) is not None]

# =========================
# Helpers for reading sec_facts records
# =========================

def _first_val(records: list) -> float | None:
    """Return the most-recent value from a list of {year, value} records."""
    for r in records:
        v = r.get("value")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _all_vals(records: list) -> list[float]:
    """Return all non-None values from a list of records, newest first."""
    out = []
    for r in records:
        v = r.get("value")
        if v is not None:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                pass
    return out


# =========================
# STAGE 1 — Fast Graham Prefilter
# =========================

def prefilter_graham_candidates(tickers: list[str]) -> list[str]:
    """
    Loose filter using only fields available without a live price.
    Reads computed ratios where present; falls back to raw records.
    Accepts anything that could plausibly pass the deep screen.
    """
    candidates = []

    for t in tickers:
        data = cache.read("sec_facts", t)
        if not data:
            continue

        try:
            # --- derive values from raw sec_facts records ---
            bvps     = _first_val(data.get("bvps",     []))
            cur_ast  = _first_val(data.get("cur_ast",  []))
            cur_lib  = _first_val(data.get("cur_lib",  []))
            lt_debt  = _first_val(data.get("lt_debt",  []))
            equity   = _first_val(data.get("equity",   []))
            eps_vals = _all_vals(data.get("eps",       []))

            # Need positive book value — no price available so we can't
            # compute P/B, but negative/zero BVPS is an automatic fail
            if bvps is None or bvps <= 0:
                continue

            # Current ratio
            current_ratio = (cur_ast / cur_lib) if cur_ast and cur_lib and cur_lib > 0 else None

            # Debt/equity
            debt_eq = (lt_debt / equity) if lt_debt is not None and equity and equity > 0 else None

            # EPS: require at least one profitable year in recent history
            has_positive_eps = any(v > 0 for v in eps_vals[:5]) if eps_vals else False
            if not has_positive_eps:
                continue

            # Pass if current ratio looks healthy OR leverage is low
            if current_ratio and current_ratio >= 1.5:
                candidates.append(t)
                continue

            if debt_eq is not None and debt_eq <= 0.7:
                candidates.append(t)
                continue

            # Catch anything with very clean earnings history even if
            # balance-sheet data is incomplete
            if len(eps_vals) >= 5 and all(v > 0 for v in eps_vals[:5]):
                candidates.append(t)

        except Exception:
            continue

    print(f"⚡ Prefilter reduced to {len(candidates)} candidates")
    return candidates


# =========================
# STAGE 2 — Strict Graham Screen
# =========================

def graham_deep_screen(tickers: list[str]) -> list[str]:
    """
    Strict filter mirroring Graham's Defensive Investor criteria.
    No live price available, so P/E and P/B checks are skipped here —
    those are handled by graham.score() once price is loaded.
    """
    results = []

    for t in tickers:
        data = cache.read("sec_facts", t)
        if not data:
            continue

        try:
            cur_ast  = _first_val(data.get("cur_ast",  []))
            cur_lib  = _first_val(data.get("cur_lib",  []))
            lt_debt  = _first_val(data.get("lt_debt",  []))
            equity   = _first_val(data.get("equity",   []))
            op_cf    = _first_val(data.get("op_cf",    []))
            capex    = _first_val(data.get("capex",    []))
            eps_vals = _all_vals(data.get("eps",       []))
            div_recs = data.get("dividends", [])

            # Current ratio ≥ 2.0 (Graham minimum)
            current_ratio = (cur_ast / cur_lib) if cur_ast and cur_lib and cur_lib > 0 else None
            if current_ratio is None or current_ratio < 2.0:
                continue

            # Debt/equity ≤ 0.5 (conservative leverage)
            debt_eq = (lt_debt / equity) if lt_debt is not None and equity and equity > 0 else None
            if debt_eq is not None and debt_eq > 0.5:
                continue

            # No loss years in last 5 years
            recent_eps = eps_vals[:5]
            if len(recent_eps) < 5:
                continue
            if any(v <= 0 for v in recent_eps):
                continue

            # Positive free cash flow (operating CF − capex)
            if op_cf is not None:
                fcf = op_cf - abs(capex) if capex is not None else op_cf
                if fcf <= 0:
                    continue
            else:
                continue  # no cash flow data at all

            # At least some dividend history (Graham preferred 20 yrs;
            # we use ≥ 5 here so small-caps aren't universally excluded)
            div_years = sum(1 for r in div_recs if r.get("value") and r["value"] > 0)
            if div_years < 5:
                continue

            results.append(t)

        except Exception:
            continue

    print(f"💎 Graham deep screen: {len(results)} final stocks")
    return results


# =========================
# FULL PIPELINE
# =========================

def get_graham_universe() -> list[str]:
    """
    Two-stage Graham pipeline over the full cached universe.
    Stage 1: loose prefilter (balance sheet health, positive EPS).
    Stage 2: strict Graham criteria (current ratio, D/E, FCF, dividends).
    Returns tickers that pass both stages.
    """
    all_tickers = get_universe()
    cached = [t for t in all_tickers if cache.read("sec_facts", t)]
    candidates = prefilter_graham_candidates(cached)
    winners = graham_deep_screen(candidates)
    return winners