"""
Benjamin Graham scoring engine.
All formulas from "The Intelligent Investor" (revised edition).

Scoring breakdown (100 points total):
  P/E Ratio              ≤ 15×          15 pts
  P/B Ratio              ≤ 1.5×         10 pts
  P/E × P/B              ≤ 22.5          5 pts
  Graham Number          price ≤ 67%    20 pts
  Current Ratio          ≥ 2.0×         10 pts
  Debt / Equity          ≤ 1.0×         10 pts
  EPS Stability          ≥33% / no loss 15 pts
  Dividend Track Record  20+ yrs        10 pts
  Net-Net (NNWC)         > mktcap        5 pts
"""

import math
import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────────

def _first(records: list, field="value"):
    """Return the most-recent value from a sorted list of records."""
    return records[0][field] if records else None


def _safe(val, fallback=None):
    try:
        v = float(val)
        return v if math.isfinite(v) else fallback
    except (TypeError, ValueError):
        return fallback


# ── Main scoring function ─────────────────────────────────────────────────────

def score(price: float | None, sec: dict) -> dict:
    """
    Given a live price and SEC-parsed data dict, return a full Graham report.
    """

    eps_hist  = sec.get("eps",      [])
    bvps_hist = sec.get("bvps",     [])
    cur_ast   = sec.get("cur_ast",  [])
    cur_lib   = sec.get("cur_lib",  [])
    lt_debt   = sec.get("lt_debt",  [])
    tot_lib   = sec.get("tot_lib",  [])
    equity    = sec.get("equity",   [])
    shares    = sec.get("shares",   [])
    div_hist  = sec.get("dividends",[])

    # ── Core values ──────────────────────────────────────────────────────────
    eps       = _safe(_first(eps_hist))
    bvps      = _safe(_first(bvps_hist))
    ca        = _safe(_first(cur_ast))
    cl        = _safe(_first(cur_lib))
    debt      = _safe(_first(lt_debt))
    total_lib = _safe(_first(tot_lib))
    eq        = _safe(_first(equity))
    sh        = _safe(_first(shares))

    pe = _safe(price / eps)  if price and eps  and eps  > 0 else None
    pb = _safe(price / bvps) if price and bvps and bvps > 0 else None
    cr = _safe(ca / cl)      if ca and cl and cl > 0        else None
    de = _safe(debt / eq)    if debt is not None and eq and eq > 0 else None

    graham_number = (
        math.sqrt(22.5 * eps * bvps)
        if eps and bvps and eps > 0 and bvps > 0 else None
    )
    margin_of_safety = (
        (graham_number - price) / graham_number * 100
        if graham_number and price else None
    )

    # EPS trend
    eps_values = [r["value"] for r in eps_hist if r["value"] is not None]
    eps_years  = len(eps_values)
    loss_years = sum(1 for v in eps_values if v < 0)

    eps_growth = None
    if eps_years >= 5 and eps_values[-1] and eps_values[-1] > 0:
        eps_growth = (eps_values[0] - eps_values[-1]) / abs(eps_values[-1]) * 100

    # Consecutive dividend years: count uninterrupted years ending at the most
    # recent year with a positive dividend.  Stop at the first gap.
    # Example: [2025,2024,2023,2022,2020] → 4 (gap before 2020 breaks the streak).
    div_by_year = {r["year"]: r["value"] for r in div_hist
               if r.get("value") and r["value"] > 0}
    sorted_years = sorted(div_by_year.keys(), reverse=True)
    consecutive = 0
    for i, yr in enumerate(sorted_years):
        if i == 0 or sorted_years[i - 1] - yr == 1:
            consecutive += 1
        else:
            break
    div_years = consecutive

    # NNWC = Current Assets − Total Liabilities
    nnwc     = (ca - total_lib) / 1e6 if ca and total_lib else None
    mkt_cap  = (price * sh)    / 1e6 if price and sh     else None

    # ── Scoring ──────────────────────────────────────────────────────────────

    criteria = []

    # 1. P/E Ratio (15 pts)
    if pe is None:
        pe_score, pe_note = 0, "No earnings data"
    elif pe <= 0:
        pe_score, pe_note = 0, "Negative earnings"
    elif pe <= 15:
        pe_score, pe_note = 15, f"P/E {pe:.1f}× — within Graham's ceiling"
    elif pe <= 20:
        pe_score, pe_note = 8, f"P/E {pe:.1f}× — slightly above ideal"
    else:
        pe_score, pe_note = 0, f"P/E {pe:.1f}× — well beyond Graham's ceiling"

    criteria.append({
        "label": "Price-to-Earnings",
        "requirement": "≤ 15×",
        "actual": f"{pe:.1f}×" if pe else "N/A",
        "score": pe_score,
        "max": 15,
        "note": pe_note
    })

    # 2. P/B Ratio (10 pts)
    if pb is None:
        pb_score, pb_note = 0, "Book value data unavailable"
    elif pb <= 0:
        pb_score, pb_note = 0, "Negative book value"
    elif pb <= 1.5:
        pb_score, pb_note = 10, f"P/B {pb:.2f}× — deep value"
    elif pb <= 2.5:
        pb_score, pb_note = 5, f"P/B {pb:.2f}× — acceptable"
    else:
        pb_score, pb_note = 0, f"P/B {pb:.2f}× — expensive relative to book"

    criteria.append({
        "label": "Price-to-Book",
        "requirement": "≤ 1.5×",
        "actual": f"{pb:.2f}×" if pb else "N/A",
        "score": pb_score,
        "max": 10,
        "note": pb_note
    })

    # 3. P/E × P/B Combined (5 pts)
    pepb = pe * pb if pe and pb else None
    if pepb is None:
        pepb_score, pepb_note = 0, "Cannot calculate"
    elif pepb <= 22.5:
        pepb_score, pepb_note = 5, f"P/E × P/B = {pepb:.1f} — passes Graham's combined test"
    else:
        pepb_score, pepb_note = 0, f"P/E × P/B = {pepb:.1f} — fails combined test (max 22.5)"

    criteria.append({
        "label": "P/E × P/B Combined",
        "requirement": "≤ 22.5",
        "actual": f"{pepb:.1f}" if pepb else "N/A",
        "score": pepb_score,
        "max": 5,
        "note": pepb_note
    })

    # 4. Graham Number & Margin of Safety (20 pts)
    if graham_number is None:
        gn_score, gn_note = 0, "Insufficient data to calculate Graham Number"
    elif not price:
        gn_score, gn_note = 0, f"Graham Number ${graham_number:.2f} — no live price to compare"
    elif price <= graham_number * 0.67:
        gn_score = 20
        gn_note  = f"${price:.2f} ≤ 67% of Graham Number ${graham_number:.2f} — excellent margin"
    elif price <= graham_number:
        gn_score = 10
        gn_note  = f"${price:.2f} below Graham Number ${graham_number:.2f} — some margin"
    else:
        gn_score = 0
        gn_note  = f"${price:.2f} above Graham Number ${graham_number:.2f} — no margin of safety"

    criteria.append({
        "label": "Graham Number & Margin of Safety",
        "requirement": "Price ≤ 67% of Graham Number",
        "actual": f"${graham_number:.2f}" if graham_number else "N/A",
        "score": gn_score,
        "max": 20,
        "note": gn_note,
        "margin_of_safety": round(margin_of_safety, 1) if margin_of_safety else None
    })

    # 5. Current Ratio (10 pts)
    if cr is None:
        cr_score, cr_note = 0, "Current ratio data unavailable"
    elif cr >= 2.0:
        cr_score, cr_note = 10, f"Current ratio {cr:.2f}× — strong liquidity"
    elif cr >= 1.5:
        cr_score, cr_note = 5, f"Current ratio {cr:.2f}× — acceptable"
    else:
        cr_score, cr_note = 0, f"Current ratio {cr:.2f}× — below Graham's minimum"

    criteria.append({
        "label": "Current Ratio",
        "requirement": "≥ 2.0×",
        "actual": f"{cr:.2f}×" if cr else "N/A",
        "score": cr_score,
        "max": 10,
        "note": cr_note
    })

    # 6. Debt / Equity (10 pts)
    if de is None:
        de_score, de_note = 0, "Debt/equity data unavailable"
    elif de <= 0.5:
        de_score, de_note = 10, f"D/E {de:.2f} — very conservative leverage"
    elif de <= 1.0:
        de_score, de_note = 5, f"D/E {de:.2f} — moderate leverage"
    else:
        de_score, de_note = 0, f"D/E {de:.2f} — high leverage, Graham would disapprove"

    criteria.append({
        "label": "Long-Term Debt & Leverage",
        "requirement": "D/E ≤ 1.0×",
        "actual": f"{de:.2f}" if de is not None else "N/A",
        "score": de_score,
        "max": 10,
        "note": de_note
    })

    # 7. EPS Stability & Growth (15 pts)
    if eps_years < 5:
        eps_score = 0
        eps_note  = f"Only {eps_years} year(s) of income data — insufficient"
    elif loss_years > 0:
        eps_score = 0
        eps_note  = f"{loss_years} loss year(s) in last {eps_years}yrs — fails stability test"
    elif eps_growth is not None and eps_growth >= 33:
        eps_score = 15
        eps_note  = f"{eps_growth:.1f}% EPS growth over {eps_years}yrs — excellent"
    elif eps_growth is not None and eps_growth >= 0:
        eps_score = 7
        eps_note  = f"{eps_growth:.1f}% EPS growth over {eps_years}yrs — positive but below 33%"
    else:
        eps_score = 0
        eps_note  = f"Negative EPS growth over {eps_years}yrs"

    criteria.append({
        "label": "EPS Stability & Growth",
        "requirement": "≥ 33% growth, no loss years",
        "actual": f"{eps_growth:.1f}%" if eps_growth is not None else f"{eps_years} yrs",
        "score": eps_score,
        "max": 15,
        "note": eps_note,
        "eps_years": eps_years,
        "loss_years": loss_years
    })

    # 8. Dividend Track Record (10 pts)
    if div_years >= 20:
        dv_score, dv_note = 10, f"{div_years} consecutive dividend years — exemplary"
    elif div_years >= 10:
        dv_score, dv_note = 5, f"{div_years} years of dividends — good but under 20"
    elif div_years > 0:
        dv_score, dv_note = 2, f"Only {div_years} year(s) of dividends on record"
    else:
        dv_score, dv_note = 0, "No dividend history — Graham's defensive investor required 20+ yrs"

    criteria.append({
        "label": "Dividend Track Record",
        "requirement": "20+ consecutive years",
        "actual": f"{div_years} yrs",
        "score": dv_score,
        "max": 10,
        "note": dv_note
    })

    # 9. Net-Net Working Capital (5 pts)
    if nnwc is None or mkt_cap is None:
        nn_score, nn_note = 0, "Insufficient data for NNWC calculation"
    elif nnwc > mkt_cap:
        nn_score = 5
        nn_note  = f"NNWC ${nnwc:,.0f}M > Market Cap ${mkt_cap:,.0f}M — rare Graham net-net!"
    else:
        nn_score = 0
        nn_note  = f"NNWC ${nnwc:,.0f}M < Market Cap ${mkt_cap:,.0f}M — not a net-net"

    criteria.append({
        "label": "Net-Net Working Capital",
        "requirement": "NNWC > Market Cap",
        "actual": f"${nnwc:,.0f}M" if nnwc else "N/A",
        "score": nn_score,
        "max": 5,
        "note": nn_note
    })

    # ── Summary ───────────────────────────────────────────────────────────────
    total_score = sum(c["score"] for c in criteria)
    total_max   = sum(c["max"]   for c in criteria)

    if total_score >= 70:
        grade, grade_label = "A", "Defensive"
    elif total_score >= 50:
        grade, grade_label = "B", "Enterprising"
    elif total_score >= 30:
        grade, grade_label = "C", "Speculative"
    else:
        grade, grade_label = "D", "Avoid"

    return {
        "price":            price,
        "pe":               pe,
        "pb":               pb,
        "eps":              eps,
        "bvps":             bvps,
        "current_ratio":    cr,
        "debt_to_equity":   de,
        "graham_number":    graham_number,
        "margin_of_safety": margin_of_safety,
        "eps_growth":       eps_growth,
        "eps_years":        eps_years,
        "loss_years":       loss_years,
        "div_years":        div_years,
        "nnwc":             nnwc,
        "market_cap":       mkt_cap,
        "total_score":      total_score,
        "total_max":        total_max,
        "grade":            grade,
        "grade_label":      grade_label,
        "criteria":         criteria,
        "eps_history":      eps_hist,
        "div_history":      div_hist,
    }