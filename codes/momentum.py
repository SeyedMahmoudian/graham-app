"""
Momentum scoring engine — 100 points total.

Criteria:
  200-day Moving Average   price > MA     30 pts
  12-month Return          > 0%           30 pts
  Relative Strength        vs SPY 12mo    25 pts
  3-month Drawdown         not down >20%  15 pts
"""

import pandas as pd
import numpy as np


def score(price_hist: pd.DataFrame, spy_hist: pd.DataFrame, symbol: str) -> dict:
    """
    score() accepts price history DataFrames (Date, Close columns).
    Returns momentum score dict compatible with scorer.py.
    """
    criteria = []

    if price_hist is None or price_hist.empty:
        return _empty_score("No price history available")

    # Ensure sorted chronologically
    hist = price_hist.copy()
    hist["Date"] = pd.to_datetime(hist["Date"])
    hist = hist.sort_values("Date").reset_index(drop=True)

    if len(hist) < 6:
        return _empty_score("Insufficient price history (need 6+ months)")

    current_price = hist["Close"].iloc[-1]

    # ── 200-day Moving Average (use monthly data: ~10 months ≈ 200 days) ─────
    ma_periods = min(10, len(hist))
    ma200 = hist["Close"].tail(ma_periods).mean()
    above_ma = current_price > ma200

    if above_ma:
        pct_above = (current_price - ma200) / ma200 * 100
        ma_score, ma_note = 30, f"Price ${current_price:.2f} is {pct_above:.1f}% above 200-day MA ${ma200:.2f}"
    else:
        pct_below = (ma200 - current_price) / ma200 * 100
        ma_score, ma_note = 0, f"Price ${current_price:.2f} is {pct_below:.1f}% below 200-day MA ${ma200:.2f}"

    criteria.append({
        "label":       "200-Day Moving Average",
        "requirement": "Price above MA",
        "actual":      f"MA ${ma200:.2f}",
        "score":       ma_score,
        "max":         30,
        "note":        ma_note,
    })

    # ── 12-Month Return ───────────────────────────────────────────────────────
    return_12m = None
    if len(hist) >= 12:
        price_12m_ago = hist["Close"].iloc[-12]
        if price_12m_ago > 0:
            return_12m = (current_price - price_12m_ago) / price_12m_ago * 100

    if return_12m is None:
        r12_score, r12_note = 0, "Insufficient history for 12-month return"
    elif return_12m >= 20:
        r12_score, r12_note = 30, f"12-month return +{return_12m:.1f}% — strong momentum"
    elif return_12m >= 10:
        r12_score, r12_note = 20, f"12-month return +{return_12m:.1f}% — positive momentum"
    elif return_12m >= 0:
        r12_score, r12_note = 10, f"12-month return +{return_12m:.1f}% — flat"
    else:
        r12_score, r12_note = 0,  f"12-month return {return_12m:.1f}% — negative momentum"

    criteria.append({
        "label":       "12-Month Return",
        "requirement": "> 0%",
        "actual":      f"{return_12m:+.1f}%" if return_12m is not None else "N/A",
        "score":       r12_score,
        "max":         30,
        "note":        r12_note,
    })

    # ── Relative Strength vs SPY ──────────────────────────────────────────────
    rs_score, rs_note = 0, "SPY history not available for comparison"

    if spy_hist is not None and not spy_hist.empty:
        spy = spy_hist.copy()
        spy["Date"] = pd.to_datetime(spy["Date"])
        spy = spy.sort_values("Date").reset_index(drop=True)

        spy_return_12m = None
        if len(spy) >= 12:
            spy_12m_ago = spy["Close"].iloc[-12]
            spy_now     = spy["Close"].iloc[-1]
            if spy_12m_ago > 0:
                spy_return_12m = (spy_now - spy_12m_ago) / spy_12m_ago * 100

        if return_12m is not None and spy_return_12m is not None:
            alpha = return_12m - spy_return_12m
            if alpha >= 10:
                rs_score, rs_note = 25, f"Outperforming SPY by {alpha:.1f}% — strong relative strength"
            elif alpha >= 0:
                rs_score, rs_note = 15, f"Outperforming SPY by {alpha:.1f}% — in line or slightly ahead"
            elif alpha >= -10:
                rs_score, rs_note = 5,  f"Underperforming SPY by {abs(alpha):.1f}% — slight lag"
            else:
                rs_score, rs_note = 0,  f"Underperforming SPY by {abs(alpha):.1f}% — weak relative strength"

    criteria.append({
        "label":       "Relative Strength vs SPY",
        "requirement": "Outperforming SPY",
        "actual":      "See note",
        "score":       rs_score,
        "max":         25,
        "note":        rs_note,
    })

    # ── 3-Month Drawdown ─────────────────────────────────────────────────────
    return_3m = None
    if len(hist) >= 3:
        price_3m_ago = hist["Close"].iloc[-3]
        if price_3m_ago > 0:
            return_3m = (current_price - price_3m_ago) / price_3m_ago * 100

    if return_3m is None:
        dd_score, dd_note = 0, "Insufficient history for 3-month check"
    elif return_3m >= 0:
        dd_score, dd_note = 15, f"3-month return +{return_3m:.1f}% — no drawdown concern"
    elif return_3m >= -10:
        dd_score, dd_note = 8,  f"3-month return {return_3m:.1f}% — minor pullback"
    elif return_3m >= -20:
        dd_score, dd_note = 3,  f"3-month return {return_3m:.1f}% — significant drawdown"
    else:
        dd_score, dd_note = 0,  f"3-month return {return_3m:.1f}% — severe drawdown — avoid catching falling knife"

    criteria.append({
        "label":       "3-Month Drawdown Check",
        "requirement": "Not down > 20%",
        "actual":      f"{return_3m:+.1f}%" if return_3m is not None else "N/A",
        "score":       dd_score,
        "max":         15,
        "note":        dd_note,
    })

    total_score = sum(c["score"] for c in criteria)
    total_max   = sum(c["max"]   for c in criteria)

    return {
        "price":        current_price,
        "ma200":        ma200,
        "above_ma":     above_ma,
        "return_12m":   return_12m,
        "return_3m":    return_3m,
        "total_score":  total_score,
        "total_max":    total_max,
        "criteria":     criteria,
    }


def _empty_score(reason: str) -> dict:
    criteria = [
        {"label": c, "requirement": "", "actual": "N/A",
         "score": 0, "max": m, "note": reason}
        for c, m in [
            ("200-Day Moving Average", 30),
            ("12-Month Return", 30),
            ("Relative Strength vs SPY", 25),
            ("3-Month Drawdown Check", 15),
        ]
    ]
    return {
        "price":       None,
        "ma200":       None,
        "above_ma":    None,
        "return_12m":  None,
        "return_3m":   None,
        "total_score": 0,
        "total_max":   100,
        "criteria":    criteria,
        "error":       reason,
    }
