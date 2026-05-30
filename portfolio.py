"""
Portfolio builder engine.

Storage layout (via cache.py):
  cache key: ("portfolio", "index")   → list of portfolio names
  cache key: ("portfolio", name)      → portfolio dict (see schema below)

Portfolio schema:
  {
    "name":     str,
    "created":  ISO timestamp,
    "holdings": {
      "AAPL": {
        "shares":       10,           # shares at time of purchase (pre-split)
        "price_at_add": 182.50,       # price when added (used for cost basis)
        "name":         "Apple Inc.",
        "added_date":   "2024-01-15", # anchors split lookups; present on new holdings
      },
      ...
    }
  }

Simulation output:
  backtest  — monthly portfolio value vs SPY over 10 years (actual prices)
  montecarlo — 2-year forward projection: 1,000 paths → p10/p50/p90 bands
"""

import math
import time
import datetime
import numpy as np
import pandas as pd

import cache
import alpha_vantage_client

MAX_HOLDINGS  = 10
MIN_SHARES    = 5
MC_PATHS      = 1000
MC_YEARS      = 2
MC_MONTHS     = MC_YEARS * 12

# ── In-process split cache (lives for the server session, refreshed every 6h) ─
# Avoids re-fetching the same symbol's splits on every backtest call.
_splits_memo: dict[str, tuple[float, list]] = {}
_SPLITS_TTL  = 6 * 3600   # seconds


def _splits_since(symbol: str, since_date: str) -> list[dict]:
    """
    Return splits for `symbol` that occurred on or after `since_date` (YYYY-MM-DD).
    Uses an in-process memo so the same symbol isn't re-fetched within a session.
    """
    now = time.time()
    if symbol in _splits_memo:
        ts, data = _splits_memo[symbol]
        if now - ts < _SPLITS_TTL:
            splits = data
        else:
            splits = alpha_vantage_client.get_splits(symbol)
            _splits_memo[symbol] = (now, splits)
    else:
        splits = alpha_vantage_client.get_splits(symbol)
        _splits_memo[symbol] = (now, splits)

    return [s for s in splits if s["date"] >= since_date]


def _split_factor_at(splits: list[dict], as_of: pd.Timestamp) -> float:
    """
    Cumulative split factor from a pre-filtered splits list (already
    filtered to since_date) up to and including `as_of` date.

    Example: 2:1 split on 2020-06-01, then 3:1 on 2022-01-15.
      as_of = 2021-12-31  →  factor = 2.0
      as_of = 2023-01-01  →  factor = 6.0
    """
    factor = 1.0
    for split in splits:
        if pd.Timestamp(split["date"]) <= as_of:
            factor *= split["ratio"]
    return factor


def get_cumulative_split_factor(symbol: str, since_date: str) -> float:
    """
    Public helper: cumulative split factor for `symbol` from `since_date`
    up to today.  Multiply the original share count by this number to get
    the current split-adjusted share count.
    """
    splits = _splits_since(symbol, since_date)
    today  = pd.Timestamp.today().normalize()
    return _split_factor_at(splits, today)

# ══════════════════════════════════════════════════════════════════════════════
# Storage helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_index() -> list[str]:
    return cache.read("portfolio", "index") or []


def _save_index(names: list[str]) -> None:
    cache.write("portfolio", "index", names)


def list_portfolios() -> list[str]:
    return _load_index()


def load_portfolio(name: str) -> dict | None:
    return cache.read("portfolio", f"p_{name}")


def save_portfolio(portfolio: dict) -> None:
    name = portfolio["name"]
    cache.write("portfolio", f"p_{name}", portfolio)
    idx = _load_index()
    if name not in idx:
        idx.append(name)
        _save_index(idx)


def delete_portfolio(name: str) -> None:
    cache.clear("portfolio", f"p_{name}")
    idx = [n for n in _load_index() if n != name]
    _save_index(idx)


def create_portfolio(name: str) -> dict:
    p = {
        "name":     name,
        "created":  datetime.datetime.now().isoformat(),
        "holdings": {},
    }
    save_portfolio(p)
    return p


# ══════════════════════════════════════════════════════════════════════════════
# Holdings management
# ══════════════════════════════════════════════════════════════════════════════

def add_holding(name: str, symbol: str, shares: int,
                current_price: float, company_name: str) -> tuple[dict, str]:
    """
    Add a holding to a portfolio.
    Returns (updated_portfolio, error_message).
    error_message is "" on success.
    """
    p = load_portfolio(name)
    if p is None:
        return {}, f'Portfolio "{name}" not found'

    symbol = symbol.upper().strip()

    if symbol in p["holdings"]:
        return p, f"{symbol} is already in this portfolio"

    if len(p["holdings"]) >= MAX_HOLDINGS:
        return p, f"Portfolio is full ({MAX_HOLDINGS} stocks maximum)"

    if shares < MIN_SHARES:
        return p, f"Minimum {MIN_SHARES} shares per stock (got {shares})"

    p["holdings"][symbol] = {
        "shares":       shares,
        "price_at_add": current_price,
        "name":         company_name,
        "added_date":   datetime.date.today().isoformat(),  # used to anchor split lookups
    }
    save_portfolio(p)
    return p, ""


def remove_holding(name: str, symbol: str) -> tuple[dict, str]:
    p = load_portfolio(name)
    if p is None:
        return {}, f'Portfolio "{name}" not found'
    symbol = symbol.upper().strip()
    if symbol not in p["holdings"]:
        return p, f"{symbol} is not in this portfolio"
    del p["holdings"][symbol]
    save_portfolio(p)
    return p, ""


# ══════════════════════════════════════════════════════════════════════════════
# Simulation helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_history(symbol: str) -> pd.DataFrame:
    """Return monthly price history as a DataFrame with Date + Close."""
    df = alpha_vantage_client.get_price_history(symbol, years=10)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["Date"]  = pd.to_datetime(df["Date"])
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna().sort_values("Date").reset_index(drop=True)
    return df


def _align_histories(histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Inner-join all symbol histories on Date, keeping only dates present
    in every series.  Returns a wide DataFrame: Date + one col per symbol.
    """
    dfs = []
    for sym, df in histories.items():
        dfs.append(df.set_index("Date")["Close"].rename(sym))

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, axis=1).dropna()
    combined = combined.reset_index()
    combined = combined.rename(columns={"index": "Date"})
    return combined.sort_values("Date").reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
# Backtest
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(portfolio: dict) -> dict:
    """
    Compute monthly portfolio value vs SPY over 10 years.

    Entry: shares × price at the FIRST date available in the aligned history.
    This is the same entry date for every holding and for SPY, making the
    comparison fair.

    Returns:
      {
        "dates":           [str, ...],          # monthly ISO dates
        "portfolio_value": [float, ...],        # $ value each month
        "spy_value":       [float, ...],        # $ value if same $ → SPY
        "total_invested":  float,               # shares × first-month price
        "spy_invested":    float,               # same dollar amount
        "final_value":     float,
        "final_spy":       float,
        "cagr":            float,               # annualised % return
        "spy_cagr":        float,
        "holdings_detail": { SYM: {shares, entry_price, current_price, gain_pct} }
        "error":           str | None,
      }
    """
    holdings = portfolio.get("holdings", {})
    if not holdings:
        return {"error": "Portfolio is empty"}

    symbols = list(holdings.keys())

    # Load all histories + SPY
    print(f"  [Backtest] loading {len(symbols)+1} price histories...")
    histories = {}
    for sym in symbols + ["SPY"]:
        h = _load_history(sym)
        if not h.empty:
            histories[sym] = h

    missing = [s for s in symbols if s not in histories]
    if missing:
        print(f"  [Backtest] ⚠️  No price history for: {missing}")

    available = [s for s in symbols if s in histories]
    if not available or "SPY" not in histories:
        return {"error": "Not enough price history to run backtest"}

    # Align on common dates
    to_align = {s: histories[s] for s in available + ["SPY"]}
    wide = _align_histories(to_align)
    if wide.empty or len(wide) < 6:
        return {"error": "Insufficient overlapping price history"}

    # Pre-fetch split histories for every available symbol.
    # Falls back to the portfolio created date for legacy holdings that
    # pre-date the added_date field.
    port_created = portfolio.get("created", "2000-01-01")[:10]
    splits_by_sym: dict[str, list[dict]] = {}
    for sym in available:
        h        = holdings[sym]
        since    = h.get("added_date") or port_created
        splits_by_sym[sym] = _splits_since(sym, since)

    # Entry values at first date — apply any splits that already happened
    # between the holding's added_date and the first date in price history.
    entry_row = wide.iloc[0]
    entry_date = entry_row["Date"]
    port_entry_value = sum(
        holdings[s]["shares"]
        * _split_factor_at(splits_by_sym[s], entry_date)
        * float(entry_row[s])
        for s in available if s in wide.columns
    )

    spy_entry_price  = float(entry_row["SPY"])
    spy_shares_equiv = port_entry_value / spy_entry_price  # same $ in SPY

    # Monthly values — share count adjusts forward as each split date passes.
    dates = wide["Date"].dt.strftime("%Y-%m-%d").tolist()
    port_values = []
    spy_values  = []

    for _, row in wide.iterrows():
        row_date = row["Date"]
        pv = sum(
            holdings[s]["shares"]
            * _split_factor_at(splits_by_sym[s], row_date)
            * float(row[s])
            for s in available if s in wide.columns
        )
        port_values.append(round(pv, 2))
        spy_values.append(round(spy_shares_equiv * float(row["SPY"]), 2))

    # CAGR
    n_years = len(wide) / 12
    def _cagr(start, end, years):
        if start <= 0 or end <= 0 or years <= 0:
            return 0.0
        return (math.pow(end / start, 1 / years) - 1) * 100

    port_cagr = _cagr(port_values[0], port_values[-1], n_years)
    spy_cagr  = _cagr(spy_values[0],  spy_values[-1],  n_years)

    # Per-holding detail — shares and current_value use fully split-adjusted count.
    last_row  = wide.iloc[-1]
    last_date = last_row["Date"]
    holdings_detail = {}
    for s in available:
        if s not in wide.columns:
            continue
        ep = float(entry_row[s])
        cp = float(last_row[s])

        # Cumulative split factor from purchase date through end of backtest
        factor     = _split_factor_at(splits_by_sym[s], last_date)
        adj_shares = holdings[s]["shares"] * factor

        gain = (cp - ep) / ep * 100 if ep > 0 else 0

        holdings_detail[s] = {
            "shares":          round(adj_shares),        # split-adjusted count
            "original_shares": holdings[s]["shares"],    # as-stored (pre-split)
            "split_factor":    round(factor, 4),         # e.g. 20.0 for one 20:1 split
            "splits":          splits_by_sym[s],         # list of individual split events
            "entry_price":     round(ep, 2),
            "current_price":   round(cp, 2),
            "gain_pct":        round(gain, 1),
            "current_value":   round(adj_shares * cp, 2),
        }

    return {
        "dates":            dates,
        "portfolio_value":  port_values,
        "spy_value":        spy_values,
        "total_invested":   round(port_entry_value, 2),
        "spy_invested":     round(port_entry_value, 2),
        "final_value":      round(port_values[-1], 2),
        "final_spy":        round(spy_values[-1], 2),
        "cagr":             round(port_cagr, 2),
        "spy_cagr":         round(spy_cagr, 2),
        "n_months":         len(wide),
        "holdings_detail":  holdings_detail,
        "error":            None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Monte Carlo projection
# ══════════════════════════════════════════════════════════════════════════════

def run_montecarlo(portfolio: dict, backtest: dict) -> dict:
    """
    2-year forward Monte Carlo using per-stock historical monthly return
    distribution (mean + std).

    Starting value = backtest final portfolio value (or sum of shares × current
    price if backtest failed).

    Returns:
      {
        "dates":   [str, ...],   # monthly dates from today forward
        "p10":     [float, ...], # 10th-percentile path
        "p50":     [float, ...], # median path
        "p90":     [float, ...], # 90th-percentile path
        "spy_p10": [float, ...],
        "spy_p50": [float, ...],
        "spy_p90": [float, ...],
        "start_value": float,
        "error": str | None,
      }
    """
    holdings = portfolio.get("holdings", {})
    if not holdings:
        return {"error": "Portfolio is empty"}

    if backtest.get("error"):
        # Fall back to current prices if backtest didn't run.
        # shares × price_at_add is the original invested amount and remains
        # correct after splits — the total value doesn't change on split day,
        # only the per-share price and count change.
        start_value = sum(
            h["shares"] * h["price_at_add"]
            for h in holdings.values()
        )
    else:
        start_value = backtest["final_value"]

    if start_value <= 0:
        return {"error": "Cannot project from zero or negative portfolio value"}

    # Compute per-symbol monthly return stats from history
    symbols = list(holdings.keys())
    sym_stats: dict[str, tuple[float, float]] = {}  # sym → (mean_monthly, std_monthly)
    weights:   dict[str, float] = {}

    total_val = 0.0
    for sym, h in holdings.items():
        price = h.get("price_at_add", 0)
        val   = h["shares"] * price
        total_val += val

    for sym in symbols:
        df = _load_history(sym)
        if df.empty or len(df) < 12:
            # Use a conservative default: 0.6% monthly / 5% std
            sym_stats[sym] = (0.006, 0.050)
        else:
            rets = df["Close"].pct_change().dropna()
            sym_stats[sym] = (float(rets.mean()), float(rets.std()))

        price = holdings[sym].get("price_at_add", 1)
        val   = holdings[sym]["shares"] * price
        weights[sym] = val / total_val if total_val > 0 else 1 / len(symbols)

    # Portfolio blended monthly stats (weighted)
    port_mean = sum(weights[s] * sym_stats[s][0] for s in symbols)
    port_std  = math.sqrt(sum(
        (weights[s] * sym_stats[s][1]) ** 2 for s in symbols
    ))  # simplified: ignores correlation (conservative)

    # SPY stats
    spy_df = _load_history("SPY")
    if spy_df.empty or len(spy_df) < 12:
        spy_mean, spy_std = 0.008, 0.040
    else:
        spy_rets = spy_df["Close"].pct_change().dropna()
        spy_mean = float(spy_rets.mean())
        spy_std  = float(spy_rets.std())

    spy_start = backtest.get("final_spy", start_value) if not backtest.get("error") else start_value

    # Generate date range
    today = datetime.date.today()
    future_dates = []
    d = today
    for _ in range(MC_MONTHS + 1):
        future_dates.append(d.strftime("%Y-%m-%d"))
        # Advance one month
        m = d.month + 1
        y = d.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        d = d.replace(year=y, month=m, day=min(d.day, 28))

    rng = np.random.default_rng(seed=42)

    def _simulate(start: float, mean: float, std: float) -> np.ndarray:
        """Run MC_PATHS simulations, return array (MC_PATHS, MC_MONTHS+1)."""
        paths = np.empty((MC_PATHS, MC_MONTHS + 1))
        paths[:, 0] = start
        shocks = rng.normal(mean, std, size=(MC_PATHS, MC_MONTHS))
        for t in range(1, MC_MONTHS + 1):
            paths[:, t] = paths[:, t - 1] * (1 + shocks[:, t - 1])
        return paths

    port_paths = _simulate(start_value, port_mean, port_std)
    spy_paths  = _simulate(spy_start,   spy_mean,  spy_std)

    def _percentile_paths(paths: np.ndarray, pct: int) -> list[float]:
        return [round(float(np.percentile(paths[:, t], pct)), 2)
                for t in range(MC_MONTHS + 1)]

    return {
        "dates":       future_dates,
        "p10":         _percentile_paths(port_paths, 10),
        "p50":         _percentile_paths(port_paths, 50),
        "p90":         _percentile_paths(port_paths, 90),
        "spy_p10":     _percentile_paths(spy_paths,  10),
        "spy_p50":     _percentile_paths(spy_paths,  50),
        "spy_p90":     _percentile_paths(spy_paths,  90),
        "start_value": round(start_value, 2),
        "error":       None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Full simulation (backtest + Monte Carlo)
# ══════════════════════════════════════════════════════════════════════════════

def run_simulation(portfolio_name: str) -> dict:
    """
    Load a portfolio by name, run backtest + Monte Carlo.
    Results are cached for 24h (price data doesn't change intraday).
    """
    p = load_portfolio(portfolio_name)
    if p is None:
        return {"error": f'Portfolio "{portfolio_name}" not found'}

    cached = cache.read("port_sim", portfolio_name)
    if cached:
        return cached

    bt = run_backtest(p)
    mc = run_montecarlo(p, bt)

    result = {
        "portfolio_name": portfolio_name,
        "backtest":       bt,
        "montecarlo":     mc,
        "holdings":       p["holdings"],
    }

    # Cache for 6h — price history doesn't change that fast
    cache.write("port_sim", portfolio_name, result)
    return result


def invalidate_simulation_cache(portfolio_name: str) -> None:
    """Call whenever holdings change so next run fetches fresh data."""
    cache.clear("port_sim", portfolio_name)