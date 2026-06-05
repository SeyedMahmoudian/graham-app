"""
Quality scoring engine — 100 points total.

Criteria:
  Return on Equity (ROE)      ≥ 15%         25 pts
  EPS Growth Consistency      4/5 up years  20 pts
  Operating Margin            ≥ 15%         20 pts
  Free Cash Flow              positive      20 pts
  Revenue Growth              positive 5yr  15 pts
"""

import math


def _first(records: list, field="value"):
    return records[0][field] if records else None


def _safe(val):
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _values(records: list) -> list[float]:
    return [r["value"] for r in records if r.get("value") is not None]


def score(sec: dict) -> dict:
    """
    Quality scoring from SEC fundamentals only — no price needed.
    Returns score dict compatible with scorer.py composite logic.
    """
    net_inc   = _values(sec.get("net_inc",      []))
    equity    = _values(sec.get("equity",        []))
    revenue   = _values(sec.get("revenue",       []))
    op_income = _values(sec.get("op_income",     []))
    op_cf     = _values(sec.get("op_cf",         []))
    capex     = _values(sec.get("capex",         []))
    eps_hist  = _values(sec.get("eps",           []))

    criteria = []

    # ── 1. Return on Equity — 25 pts ─────────────────────────────────────────
    roe = None
    if net_inc and equity and equity[0] and equity[0] > 0:
        roe = net_inc[0] / equity[0] * 100

    if roe is None:
        roe_score, roe_note = 0, "Insufficient data to calculate ROE"
    elif roe >= 20:
        roe_score, roe_note = 25, f"ROE {roe:.1f}% — excellent return on equity"
    elif roe >= 15:
        roe_score, roe_note = 18, f"ROE {roe:.1f}% — good return on equity"
    elif roe >= 10:
        roe_score, roe_note = 10, f"ROE {roe:.1f}% — acceptable but below ideal"
    else:
        roe_score, roe_note = 0, f"ROE {roe:.1f}% — poor capital efficiency"

    criteria.append({
        "label":       "Return on Equity",
        "requirement": "≥ 15%",
        "actual":      f"{roe:.1f}%" if roe is not None else "N/A",
        "score":       roe_score,
        "max":         25,
        "note":        roe_note,
    })

    # ── 2. EPS Growth Consistency — 20 pts ───────────────────────────────────
    up_years = 0
    total_years = 0
    if len(eps_hist) >= 2:
        # eps_hist is newest-first; check year-over-year changes
        # Reverse to get oldest-first for comparison
        eps_chron = list(reversed(eps_hist))
        for i in range(1, len(eps_chron)):
            total_years += 1
            if eps_chron[i] > eps_chron[i - 1]:
                up_years += 1

    if total_years < 4:
        eps_score, eps_note = 0, f"Only {total_years} year(s) of EPS data — insufficient"
    elif up_years >= total_years:
        eps_score, eps_note = 20, f"EPS up every year ({up_years}/{total_years}) — exceptional consistency"
    elif up_years / total_years >= 0.8:
        eps_score, eps_note = 15, f"EPS up {up_years}/{total_years} years — strong consistency"
    elif up_years / total_years >= 0.6:
        eps_score, eps_note = 8, f"EPS up {up_years}/{total_years} years — moderate consistency"
    else:
        eps_score, eps_note = 0, f"EPS up only {up_years}/{total_years} years — inconsistent earnings"

    criteria.append({
        "label":       "EPS Growth Consistency",
        "requirement": "Up 4 of last 5 years",
        "actual":      f"{up_years}/{total_years} yrs" if total_years else "N/A",
        "score":       eps_score,
        "max":         20,
        "note":        eps_note,
    })

    # ── 3. Operating Margin — 20 pts ─────────────────────────────────────────
    op_margin = None
    if op_income and revenue and revenue[0] and revenue[0] > 0:
        op_margin = op_income[0] / revenue[0] * 100

    if op_margin is None:
        om_score, om_note = 0, "Insufficient data to calculate operating margin"
    elif op_margin >= 20:
        om_score, om_note = 20, f"Operating margin {op_margin:.1f}% — strong pricing power"
    elif op_margin >= 15:
        om_score, om_note = 14, f"Operating margin {op_margin:.1f}% — healthy margins"
    elif op_margin >= 10:
        om_score, om_note = 8,  f"Operating margin {op_margin:.1f}% — adequate"
    elif op_margin >= 0:
        om_score, om_note = 3,  f"Operating margin {op_margin:.1f}% — thin margins"
    else:
        om_score, om_note = 0,  f"Operating margin {op_margin:.1f}% — operating at a loss"

    criteria.append({
        "label":       "Operating Margin",
        "requirement": "≥ 15%",
        "actual":      f"{op_margin:.1f}%" if op_margin is not None else "N/A",
        "score":       om_score,
        "max":         20,
        "note":        om_note,
    })

    # ── 4. Free Cash Flow — 20 pts ────────────────────────────────────────────
    # FCF = Operating CF - CapEx
    # CapEx from SEC is usually reported as a positive outflow
    fcf = None
    if op_cf and capex:
        fcf = op_cf[0] - abs(capex[0])
    elif op_cf:
        fcf = op_cf[0]  # No capex data, use operating CF as proxy

    fcf_margin = None
    if fcf is not None and revenue and revenue[0] and revenue[0] > 0:
        fcf_margin = fcf / revenue[0] * 100

    if fcf is None:
        fcf_score, fcf_note = 0, "Insufficient data to calculate FCF"
    elif fcf > 0 and fcf_margin and fcf_margin >= 15:
        fcf_score, fcf_note = 20, f"FCF margin {fcf_margin:.1f}% — strong cash generation"
    elif fcf > 0 and fcf_margin and fcf_margin >= 8:
        fcf_score, fcf_note = 13, f"FCF margin {fcf_margin:.1f}% — good cash generation"
    elif fcf > 0:
        fcf_score, fcf_note = 7,  f"Positive FCF ${fcf/1e9:.2f}B — cash generative"
    else:
        fcf_score, fcf_note = 0,  f"Negative FCF ${fcf/1e9:.2f}B — burning cash"

    criteria.append({
        "label":       "Free Cash Flow",
        "requirement": "Positive FCF",
        "actual":      f"${fcf/1e9:.2f}B" if fcf is not None else "N/A",
        "score":       fcf_score,
        "max":         20,
        "note":        fcf_note,
    })

    # ── 5. Revenue Growth — 15 pts ────────────────────────────────────────────
    rev_growth = None
    if len(revenue) >= 5 and revenue[-1] and revenue[-1] > 0:
        rev_growth = (revenue[0] - revenue[-1]) / abs(revenue[-1]) * 100

    if rev_growth is None:
        rg_score, rg_note = 0, "Insufficient revenue history"
    elif rev_growth >= 50:
        rg_score, rg_note = 15, f"Revenue grew {rev_growth:.1f}% over 5yrs — strong growth"
    elif rev_growth >= 20:
        rg_score, rg_note = 10, f"Revenue grew {rev_growth:.1f}% over 5yrs — solid growth"
    elif rev_growth >= 0:
        rg_score, rg_note = 5,  f"Revenue grew {rev_growth:.1f}% over 5yrs — modest growth"
    else:
        rg_score, rg_note = 0,  f"Revenue declined {rev_growth:.1f}% over 5yrs — shrinking"

    criteria.append({
        "label":       "Revenue Growth (5yr)",
        "requirement": "Positive 5-year growth",
        "actual":      f"{rev_growth:.1f}%" if rev_growth is not None else "N/A",
        "score":       rg_score,
        "max":         15,
        "note":        rg_note,
    })

    # ── Summary ───────────────────────────────────────────────────────────────
    total_score = sum(c["score"] for c in criteria)
    total_max   = sum(c["max"]   for c in criteria)

    return {
        "roe":          roe,
        "op_margin":    op_margin,
        "fcf":          fcf,
        "fcf_margin":   fcf_margin,
        "rev_growth":   rev_growth,
        "up_years":     up_years,
        "total_years":  total_years,
        "total_score":  total_score,
        "total_max":    total_max,
        "criteria":     criteria,
    }
