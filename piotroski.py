"""
Piotroski F-Score — 9-point binary accounting-quality filter.

Reference:
  Piotroski (2000) "Value Investing: The Use of Historical Financial Statement
  Information to Separate Winners from Losers"
  Journal of Accounting Research, Vol.38 Supplement.

Key empirical result: High-F-Score value stocks (F=8-9) outperformed
Low-F-Score value stocks (F=0-2) by 7.5%/yr on average over 20 years.
Most effective when combined with a value filter (low P/B or Graham screen).

Score interpretation:
  8–9   Strong — high financial health, historically strong outperformance
  5–7   Neutral — mixed signals, worth monitoring
  0–4   Weak — deteriorating fundamentals; avoid or consider short

9 binary signals (1 point each):

PROFITABILITY  (4 signals)
  F1  ROA > 0                     (earning on assets)
  F2  Operating Cash Flow > 0     (actual cash generation)
  F3  ROA improving YoY           (getting more profitable)
  F4  Accruals: OCF/Assets > ROA  (cash earnings beat accounting earnings)

LEVERAGE / LIQUIDITY / DILUTION  (3 signals)
  F5  Long-term debt ratio falling YoY   (deleveraging)
  F6  Current ratio improving YoY        (improving liquidity)
  F7  No share dilution                  (no new equity issued)

OPERATING EFFICIENCY  (2 signals)
  F8  Gross margin improving YoY         (pricing power / cost control)
  F9  Asset turnover improving YoY       (more revenue per dollar of assets)

Requires sec_facts keys:
  net_inc, op_cf, total_assets (NEW), cur_ast, cur_lib, lt_debt,
  shares, gross_profit, revenue

Add total_assets to sec_data.py — see patch in that file.
"""

import math


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe(val) -> float | None:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _nth(records: list, n: int = 0) -> float | None:
    """Return the nth non-None value from a list of {value: ...} records."""
    found = 0
    for r in records:
        v = r.get("value")
        if v is not None:
            if found == n:
                return _safe(v)
            found += 1
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def score(sec: dict) -> dict:
    """
    Compute Piotroski F-Score from a sec_facts dict.

    Returns a dict with:
      f_score     int  0-9
      label       str  "strong" | "neutral" | "weak"
      signals     list[dict]  9 binary signal details
      <key ratios> floats for display
    """

    # ── Pull current year (index 0) and prior year (index 1) values ──────────
    net_0  = _nth(sec.get("net_inc",      []), 0)
    net_1  = _nth(sec.get("net_inc",      []), 1)
    ocf_0  = _nth(sec.get("op_cf",        []), 0)
    ast_0  = _nth(sec.get("total_assets", []), 0)
    ast_1  = _nth(sec.get("total_assets", []), 1)
    ca_0   = _nth(sec.get("cur_ast",      []), 0)
    ca_1   = _nth(sec.get("cur_ast",      []), 1)
    cl_0   = _nth(sec.get("cur_lib",      []), 0)
    cl_1   = _nth(sec.get("cur_lib",      []), 1)
    ltd_0  = _nth(sec.get("lt_debt",      []), 0)
    ltd_1  = _nth(sec.get("lt_debt",      []), 1)
    sh_0   = _nth(sec.get("shares",       []), 0)
    sh_1   = _nth(sec.get("shares",       []), 1)
    gp_0   = _nth(sec.get("gross_profit", []), 0)
    gp_1   = _nth(sec.get("gross_profit", []), 1)
    rev_0  = _nth(sec.get("revenue",      []), 0)
    rev_1  = _nth(sec.get("revenue",      []), 1)

    signals = []

    # ════════════════════════════════════════════════════════════════════════
    # PROFITABILITY  (4 signals)
    # ════════════════════════════════════════════════════════════════════════

    # F1: ROA > 0
    roa_0 = (net_0 / ast_0) if (net_0 is not None and ast_0 and ast_0 > 0) else None
    f1 = 1 if (roa_0 is not None and roa_0 > 0) else 0
    signals.append({
        "id":       "F1",
        "label":    "ROA Positive",
        "category": "Profitability",
        "signal":   f1,
        "note":     f"ROA {roa_0*100:.2f}%" if roa_0 is not None else "Insufficient data",
    })

    # F2: Operating Cash Flow > 0
    f2 = 1 if (ocf_0 is not None and ocf_0 > 0) else 0
    signals.append({
        "id":       "F2",
        "label":    "Positive Operating Cash Flow",
        "category": "Profitability",
        "signal":   f2,
        "note":     f"OCF ${ocf_0/1e9:.2f}B" if ocf_0 is not None else "Insufficient data",
    })

    # F3: ROA improving YoY
    roa_1 = (net_1 / ast_1) if (net_1 is not None and ast_1 and ast_1 > 0) else None
    f3 = 1 if (roa_0 is not None and roa_1 is not None and roa_0 > roa_1) else 0
    signals.append({
        "id":       "F3",
        "label":    "Improving ROA",
        "category": "Profitability",
        "signal":   f3,
        "note":     (f"ROA {roa_0*100:.2f}% vs {roa_1*100:.2f}% prior"
                     if roa_0 is not None and roa_1 is not None else "Insufficient data"),
    })

    # F4: Accruals — cash earnings quality
    # Signal fires when OCF/Assets > ROA (more cash than accounting income)
    ocf_roa = (ocf_0 / ast_0) if (ocf_0 is not None and ast_0 and ast_0 > 0) else None
    f4 = 1 if (ocf_roa is not None and roa_0 is not None and ocf_roa > roa_0) else 0
    signals.append({
        "id":       "F4",
        "label":    "Quality Earnings (Low Accruals)",
        "category": "Profitability",
        "signal":   f4,
        "note":     (f"OCF/Assets {ocf_roa*100:.2f}% > ROA {roa_0*100:.2f}%"
                     if ocf_roa is not None and roa_0 is not None else "Insufficient data"),
    })

    # ════════════════════════════════════════════════════════════════════════
    # LEVERAGE / LIQUIDITY / DILUTION  (3 signals)
    # ════════════════════════════════════════════════════════════════════════

    # F5: Long-term debt ratio decreasing
    lev_0 = (ltd_0 / ast_0) if (ltd_0 is not None and ast_0 and ast_0 > 0) else None
    lev_1 = (ltd_1 / ast_1) if (ltd_1 is not None and ast_1 and ast_1 > 0) else None
    f5 = 1 if (lev_0 is not None and lev_1 is not None and lev_0 < lev_1) else 0
    signals.append({
        "id":       "F5",
        "label":    "Decreasing Leverage",
        "category": "Leverage",
        "signal":   f5,
        "note":     (f"LTD/Assets {lev_0*100:.1f}% vs {lev_1*100:.1f}% prior"
                     if lev_0 is not None and lev_1 is not None else "Insufficient data"),
    })

    # F6: Current ratio improving
    cr_0 = (ca_0 / cl_0) if (ca_0 and cl_0 and cl_0 > 0) else None
    cr_1 = (ca_1 / cl_1) if (ca_1 and cl_1 and cl_1 > 0) else None
    f6 = 1 if (cr_0 is not None and cr_1 is not None and cr_0 > cr_1) else 0
    signals.append({
        "id":       "F6",
        "label":    "Improving Liquidity (Current Ratio)",
        "category": "Leverage",
        "signal":   f6,
        "note":     (f"CR {cr_0:.2f}× vs {cr_1:.2f}× prior"
                     if cr_0 is not None and cr_1 is not None else "Insufficient data"),
    })

    # F7: No share dilution (<=1% tolerance for buybacks/options noise)
    f7 = 1 if (sh_0 is not None and sh_1 is not None and sh_0 <= sh_1 * 1.01) else 0
    signals.append({
        "id":       "F7",
        "label":    "No Share Dilution",
        "category": "Leverage",
        "signal":   f7,
        "note":     (f"{sh_0/1e6:.1f}M shares vs {sh_1/1e6:.1f}M prior"
                     if sh_0 is not None and sh_1 is not None else "Insufficient data"),
    })

    # ════════════════════════════════════════════════════════════════════════
    # OPERATING EFFICIENCY  (2 signals)
    # ════════════════════════════════════════════════════════════════════════

    # F8: Improving gross margin
    gm_0 = (gp_0 / rev_0) if (gp_0 is not None and rev_0 and rev_0 > 0) else None
    gm_1 = (gp_1 / rev_1) if (gp_1 is not None and rev_1 and rev_1 > 0) else None
    f8 = 1 if (gm_0 is not None and gm_1 is not None and gm_0 > gm_1) else 0
    signals.append({
        "id":       "F8",
        "label":    "Improving Gross Margin",
        "category": "Efficiency",
        "signal":   f8,
        "note":     (f"GM {gm_0*100:.1f}% vs {gm_1*100:.1f}% prior"
                     if gm_0 is not None and gm_1 is not None else "Insufficient data"),
    })

    # F9: Improving asset turnover
    at_0 = (rev_0 / ast_0) if (rev_0 and ast_0 and ast_0 > 0) else None
    at_1 = (rev_1 / ast_1) if (rev_1 and ast_1 and ast_1 > 0) else None
    f9 = 1 if (at_0 is not None and at_1 is not None and at_0 > at_1) else 0
    signals.append({
        "id":       "F9",
        "label":    "Improving Asset Turnover",
        "category": "Efficiency",
        "signal":   f9,
        "note":     (f"AT {at_0:.2f}× vs {at_1:.2f}× prior"
                     if at_0 is not None and at_1 is not None else "Insufficient data"),
    })

    # ── Summary ───────────────────────────────────────────────────────────────
    total = sum(s["signal"] for s in signals)

    if total >= 8:
        label         = "strong"
        interpretation = "Strong — high financial health; historically associated with outperformance"
    elif total >= 5:
        label         = "neutral"
        interpretation = "Neutral — some positive signals; monitor for change"
    else:
        label         = "weak"
        interpretation = "Weak — deteriorating fundamentals; high risk of underperformance"

    return {
        "f_score":          total,
        "f_score_max":      9,
        "label":            label,
        "interpretation":   interpretation,
        "signals":          signals,
        # Key ratios for display
        "roa":              round(roa_0 * 100, 2) if roa_0 is not None else None,
        "ocf_roa":          round(ocf_roa * 100, 2) if ocf_roa is not None else None,
        "gross_margin":     round(gm_0 * 100, 2)  if gm_0  is not None else None,
        "asset_turnover":   round(at_0, 3)         if at_0  is not None else None,
        "leverage_ratio":   round(lev_0 * 100, 2) if lev_0 is not None else None,
        "current_ratio":    round(cr_0, 2)         if cr_0  is not None else None,
    }
