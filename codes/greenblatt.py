"""
Greenblatt Magic Formula — cross-sectional earnings yield + ROIC ranking.

Reference:
  Greenblatt (2005) "The Little Book That Beats the Market"
  Back-tested to return ~30.8%/yr vs ~12.4% for S&P 500 (1988–2004).

The core idea: find good businesses (high ROIC) trading cheaply (high
earnings yield). Rank every stock on each metric separately, then combine
the ranks. The top-ranked stocks (top decile by Magic Score) have shown
durable outperformance even after the strategy became widely published.

Metrics:
  Earnings Yield = EBIT / Enterprise Value
    Measures how cheaply you're buying the operating earnings stream,
    pre-leverage (so it's comparable across different capital structures).
    EV = Market Cap + Total Debt − Cash

  ROIC = EBIT / Invested Capital
    Measures how efficiently the business converts capital into earnings.
    Invested Capital = Net Working Capital + Net PP&E
    (or fallback: Total Assets − Current Liabilities if PP&E unavailable)

Usage (two-step):
  1. Compute raw metrics for each stock:
       raw = compute_single(price, sec_facts)
  2. Rank across the universe you've built up:
       ranked_list = rank_universe([{symbol, **raw}, ...])

  After rank_universe, each entry has 'magic_score' (0-100).
  A score of 90 means the stock is in the top 10% of both metrics combined.

Requires new sec_facts keys added to sec_data.py:
  ppe_net, cash
"""

import math


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val) -> float | None:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _first(records: list) -> float | None:
    for r in records:
        v = r.get("value")
        if v is not None:
            return _safe(v)
    return None


# ── Enterprise Value helper (single authoritative implementation) ─────────────

def enterprise_value(price: float | None, sec: dict) -> float | None:
    """
    Compute Enterprise Value for a single stock.

    EV = Market Cap + Long-term Debt − Cash & Equivalents

    This is the *only* place EV should be computed in this codebase.
    Import and call this function rather than re-implementing the formula.

    Total Debt = Long-term Debt only (lt_debt).
    Short-term debt / current portion of LTD is excluded because SEC XBRL
    reports often conflate it with accounts payable; using lt_debt alone is
    the standard Greenblatt Magic Formula convention and ensures comparability
    across sectors.

    Returns None when price or shares data is unavailable.
    """
    shares  = _first(sec.get("shares",  []))
    lt_debt = _first(sec.get("lt_debt", []))
    cash    = _first(sec.get("cash",    []))

    mkt_cap    = (price * shares) if (price and shares) else None
    total_debt = (lt_debt or 0.0)
    cash_val   = (cash   or 0.0)

    return (mkt_cap + total_debt - cash_val) if mkt_cap is not None else None


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — compute raw metrics for a single stock
# ══════════════════════════════════════════════════════════════════════════════

def compute_single(price: float | None, sec: dict) -> dict:
    """
    Compute Earnings Yield and ROIC for one stock.

    Returns:
        dict with earnings_yield (%), roic (%), ev, invested_capital.
        earnings_yield / roic are None if data is insufficient.

    Enterprise Value (canonical definition — authoritative for this codebase):
        EV = Market Cap + Long-term Debt − Cash & Equivalents
        All other modules that need EV must call greenblatt.enterprise_value()
        or consume the 'enterprise_value' key from this function's return dict,
        rather than re-implementing the formula independently.
    """
    ebit    = _first(sec.get("op_income", []))     # operating income ≈ EBIT
    shares  = _first(sec.get("shares",    []))
    lt_debt = _first(sec.get("lt_debt",   []))
    cur_lib = _first(sec.get("cur_lib",   []))
    cur_ast = _first(sec.get("cur_ast",   []))
    ppe     = _first(sec.get("ppe_net",   []))      # net PP&E (new field)
    cash    = _first(sec.get("cash",      []))      # cash & equivalents (new field)
    tot_ast = _first(sec.get("total_assets", []))   # fallback if PP&E missing

    # Enterprise Value — single authoritative implementation via enterprise_value()
    # ISSUE-002: do NOT recompute mkt_cap/total_debt/cash_val here; delegate entirely.
    ev = enterprise_value(price, sec)

    # Earnings Yield = EBIT / EV
    earnings_yield = (ebit / ev) if (ebit is not None and ev and ev > 0) else None

    # Invested Capital
    # Primary:  Net Working Capital + Net PP&E
    # Fallback: Total Assets − Current Liabilities
    # cash_val needed locally for NWC only (not for EV — that's in enterprise_value())
    cash_val = (cash or 0.0)
    nwc = (cur_ast - cash_val - cur_lib) if (cur_ast is not None and cur_lib is not None) else None

    if ppe is not None and nwc is not None:
        invested_capital = nwc + ppe
        ic_method = "NWC + PP&E"
    elif ppe is not None:
        invested_capital = ppe
        ic_method = "PP&E only"
    elif nwc is not None and tot_ast is not None:
        # Rough fallback: total assets minus current liabilities
        invested_capital = tot_ast - (cur_lib or 0)
        ic_method = "Assets − CL (fallback)"
    elif nwc is not None:
        invested_capital = nwc
        ic_method = "NWC only (fallback)"
    else:
        invested_capital = None
        ic_method = "N/A"

    roic = (
        ebit / invested_capital
        if (ebit is not None and invested_capital and invested_capital > 0)
        else None
    )

    mkt_cap = (price * shares) if (price and shares) else None

    return {
        "ebit":             ebit,
        "enterprise_value": ev,
        "mkt_cap":          mkt_cap,
        "earnings_yield":   round(earnings_yield * 100, 3) if earnings_yield is not None else None,
        "roic":             round(roic * 100, 3)           if roic           is not None else None,
        "invested_capital": invested_capital,
        "ic_method":        ic_method,
        # Rankings filled in by rank_universe()
        "magic_score":      None,
        "magic_rank":       None,
        "ey_percentile":    None,
        "roic_percentile":  None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — cross-sectional ranking
# ══════════════════════════════════════════════════════════════════════════════

def rank_universe(universe_metrics: list[dict]) -> list[dict]:
    """
    Cross-sectionally rank a list of compute_single() dicts by magic formula.

    Mutates each dict in place to add:
        magic_score     float  0-100  (100 = top combined rank)
        magic_rank      float         combined rank (1 = best)
        ey_percentile   float  0-100
        roic_percentile float  0-100

    Returns the list sorted by magic_score descending.

    Usage:
        items = [{"symbol": t, **greenblatt.compute_single(price, sec)}
                 for t, price, sec in universe]
        ranked = greenblatt.rank_universe(items)
    """
    # Only rank stocks with both metrics (filter positive earnings yield)
    valid = [
        s for s in universe_metrics
        if s.get("earnings_yield") is not None
        and s.get("roic")          is not None
        and s["earnings_yield"]    > 0         # Greenblatt excludes negative EY
        and s["roic"]              > 0
    ]

    n = len(valid)
    if n == 0:
        return universe_metrics

    # Sort by earnings yield descending → assign rank 1 to highest
    ey_sorted = sorted(valid, key=lambda x: x["earnings_yield"], reverse=True)
    for rank, item in enumerate(ey_sorted, start=1):
        item["_ey_rank"] = rank

    # Sort by ROIC descending → assign rank 1 to highest
    roic_sorted = sorted(valid, key=lambda x: x["roic"], reverse=True)
    for rank, item in enumerate(roic_sorted, start=1):
        item["_roic_rank"] = rank

    # Combined: simple sum of ranks (Greenblatt's method)
    for item in valid:
        combined = item["_ey_rank"] + item["_roic_rank"]
        # Convert to 0-100 score: lower combined rank = better = higher score
        item["magic_score"] = 100.0 if n == 1 else round((1 - (combined - 2) / (2 * n - 2)) * 100, 1)
        item["magic_rank"]      = combined
        item["ey_percentile"]   = round((1 - item["_ey_rank"]   / n) * 100, 1)
        item["roic_percentile"] = round((1 - item["_roic_rank"] / n) * 100, 1)
        # Clean temp keys
        del item["_ey_rank"], item["_roic_rank"]

    # Mark stocks that couldn't be ranked
    valid_syms = {s.get("symbol") for s in valid}
    for s in universe_metrics:
        if s.get("symbol") not in valid_syms:
            s["magic_score"] = s["magic_rank"] = None
            s["ey_percentile"] = s["roic_percentile"] = None

    return sorted(
        universe_metrics,
        key=lambda x: x.get("magic_score") or -1,
        reverse=True
    )