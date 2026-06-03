"""
Buffett Quality & Value scoring engine — 100 points total.

Warren Buffett's approach: buy great businesses at fair prices.
Where Graham hunts for statistically cheap stocks, Buffett looks for
companies with durable competitive advantages — businesses he can hold
"for ever" because their economics keep improving.

Sources:
  Berkshire Hathaway Annual Letters (1977–present)
  "The Warren Buffett Way" — Hagstrom (2013)
  "Warren Buffett and the Interpretation of Financial Statements" — Buffett/Clark

Scoring breakdown (100 points):
  Consistent ROE ≥ 15%        5+ years   20 pts  (durable moat proxy)
  Debt-to-Earnings            < 3yr NI   10 pts  (financial fortress test)
  Net Profit Margin           ≥ 10%      15 pts  (pricing power)
  EPS Growth (7-yr CAGR)      ≥ 7%/yr   15 pts  (earnings power trend)
  Owner Earnings (FCF)        +, growing 15 pts  (true cash generation)
  ROIC                        ≥ 12%      10 pts  (capital allocation)
  Intrinsic Value vs Price    DCF margin 15 pts  (margin of safety)

Intrinsic Value method:
  Two-stage DCF on owner earnings (FCF or EPS as proxy).
  Stage 1: 10-year projection at historical CAGR (capped at 15%).
  Stage 2: terminal value at 3% perpetual growth.
  Discount rate: 12% (Buffett's minimum hurdle rate).
  IV per share uses FCF/share if positive, else falls back to EPS.
"""

import math


# ── Constants ─────────────────────────────────────────────────────────────────

DISCOUNT_RATE = 0.12    # Buffett's minimum hurdle; conservative
TERMINAL_RATE = 0.03    # long-term perpetual growth (≈ nominal GDP)
GROWTH_CAP    = 0.15    # cap historical growth to avoid extrapolating anomalies
DCF_YEARS     = 10


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val) -> float | None:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _first(records: list) -> float | None:
    for r in records:
        v = _safe(r.get("value"))
        if v is not None:
            return v
    return None


def _values(records: list, n: int = 10) -> list[float]:
    """Return up to n most-recent non-None values, newest first."""
    out = []
    for r in records:
        v = _safe(r.get("value"))
        if v is not None:
            out.append(v)
            if len(out) >= n:
                break
    return out


def _by_year(records: list) -> dict[int, float]:
    result = {}
    for r in records:
        yr = r.get("year")
        v  = _safe(r.get("value"))
        if yr is not None and v is not None:
            result[int(yr)] = v
    return result


def _roe_series(net_inc_records, equity_records, n: int = 7) -> list[float]:
    """ROE year-by-year for up to n years, newest first."""
    ni = _by_year(net_inc_records)
    eq = _by_year(equity_records)
    years = sorted(set(ni) & set(eq), reverse=True)[:n]
    return [ni[y] / eq[y] * 100 for y in years if eq[y] and eq[y] > 0]


def _cagr(start: float, end: float, years: float) -> float | None:
    if not start or not end or not years or years <= 0:
        return None
    if start <= 0 or end <= 0:
        return None
    return (math.pow(end / start, 1 / years) - 1) * 100


def _dcf(owner_earnings_ps: float, growth: float) -> float | None:
    """
    Two-stage DCF per share.
      Stage 1: FCF/EPS grows at `growth` for DCF_YEARS, discounted at DISCOUNT_RATE.
      Stage 2: Gordon Growth terminal value at TERMINAL_RATE, discounted back.
    """
    if owner_earnings_ps <= 0:
        return None
    if DISCOUNT_RATE <= TERMINAL_RATE:
        return None

    pv1 = sum(
        owner_earnings_ps * math.pow(1 + growth, t) / math.pow(1 + DISCOUNT_RATE, t)
        for t in range(1, DCF_YEARS + 1)
    )

    terminal_cf = owner_earnings_ps * math.pow(1 + growth, DCF_YEARS) * (1 + TERMINAL_RATE)
    pv2 = (terminal_cf / (DISCOUNT_RATE - TERMINAL_RATE)) / math.pow(1 + DISCOUNT_RATE, DCF_YEARS)

    return pv1 + pv2


# ── Main ──────────────────────────────────────────────────────────────────────

def score(price: float | None, sec: dict) -> dict:
    """
    Compute Buffett quality & value score.

    Args:
        price:  Current market price per share (None → IV criterion skipped).
        sec:    sec_facts dict from sec_data.fetch_company_facts().

    Returns a dict with total_score (0-100), total_max (100), criteria list,
    and key metrics ready for display.
    """
    net_inc_recs = sec.get("net_inc",   [])
    equity_recs  = sec.get("equity",    [])
    revenue_recs = sec.get("revenue",   [])
    lt_debt_recs = sec.get("lt_debt",   [])
    op_cf_recs   = sec.get("op_cf",     [])
    capex_recs   = sec.get("capex",     [])
    op_inc_recs  = sec.get("op_income", [])
    eps_recs     = sec.get("eps",       [])
    shares_recs  = sec.get("shares",    [])
    cash_recs    = sec.get("cash",      [])

    net_inc = _first(net_inc_recs)
    equity  = _first(equity_recs)
    revenue = _first(revenue_recs)
    lt_debt = _first(lt_debt_recs)
    op_cf   = _first(op_cf_recs)
    capex   = _first(capex_recs)
    op_inc  = _first(op_inc_recs)
    eps     = _first(eps_recs)
    shares  = _first(shares_recs)
    cash    = _first(cash_recs) or 0.0

    eps_vals    = _values(eps_recs,     10)
    ni_vals     = _values(net_inc_recs,  7)
    op_cf_vals  = _values(op_cf_recs,    7)
    capex_vals  = _values(capex_recs,    7)

    criteria = []

    # ── 1. Consistent ROE ≥ 15% for 5+ years — 20 pts ───────────────────────
    # The single most reliable moat indicator Buffett uses: sustained high ROE
    # means the business has some structural advantage protecting its returns.
    roe_series = _roe_series(net_inc_recs, equity_recs, n=7)
    n_roe      = len(roe_series)
    roe_now    = roe_series[0] if roe_series else None
    n_above15  = sum(1 for r in roe_series if r >= 15)

    if n_roe < 3:
        roe_score = 0
        roe_note  = f"Only {n_roe} year(s) of ROE history — insufficient"
    elif n_roe >= 5 and n_above15 == n_roe:
        roe_score = 20
        roe_note  = (f"ROE ≥ 15% in all {n_roe} years "
                     f"({roe_now:.1f}% latest) — hallmark of a durable moat")
    elif n_above15 >= max(n_roe - 1, 1) and n_roe >= 4:
        roe_score = 14
        roe_note  = (f"ROE ≥ 15% in {n_above15}/{n_roe} years "
                     f"({roe_now:.1f}% latest) — strong but one soft year")
    elif n_above15 / max(n_roe, 1) >= 0.6:
        roe_score = 7
        roe_note  = (f"ROE ≥ 15% in {n_above15}/{n_roe} years "
                     f"({roe_now:.1f}% latest) — inconsistent; moat likely narrow")
    else:
        roe_score = 0
        roe_note  = (f"ROE ≥ 15% in only {n_above15}/{n_roe} years "
                     f"({roe_now:.1f}% latest) — no evidence of durable advantage")

    criteria.append({
        "label":       "Consistent ROE",
        "requirement": "≥ 15% for 5+ years",
        "actual":      f"{roe_now:.1f}%" if roe_now is not None else "N/A",
        "score":       roe_score,
        "max":         20,
        "note":        roe_note,
    })

    # ── 2. Debt-to-Earnings (payback period) — 10 pts ────────────────────────
    # Buffett's test: could the business pay off ALL its net debt from
    # earnings in under 5 years?  Net debt = long-term debt minus cash.
    net_debt = (lt_debt or 0.0) - cash
    avg_ni   = (sum(ni_vals[:3]) / 3) if len(ni_vals) >= 3 else net_inc

    if avg_ni is None or avg_ni <= 0:
        de_score  = 0
        de_note   = "No positive average earnings — cannot compute debt payback"
        de_years  = None
    else:
        de_years = net_debt / avg_ni
        if de_years <= 0:
            de_score = 10
            de_note  = (f"Net cash position (${abs(net_debt)/1e9:.1f}B) — "
                        "financial fortress, Buffett's ideal")
        elif de_years <= 2:
            de_score = 10
            de_note  = (f"Net debt repayable in {de_years:.1f}yr — "
                        "very low leverage")
        elif de_years <= 4:
            de_score = 6
            de_note  = (f"Net debt repayable in {de_years:.1f}yr — "
                        "acceptable; manageable leverage")
        elif de_years <= 7:
            de_score = 2
            de_note  = (f"Net debt repayable in {de_years:.1f}yr — "
                        "elevated leverage; business recovery capacity limited")
        else:
            de_score = 0
            de_note  = (f"Net debt repayable in {de_years:.1f}yr — "
                        "high leverage; Buffett would avoid")

    criteria.append({
        "label":       "Debt-to-Earnings",
        "requirement": "Net debt payable in < 3yr of earnings",
        "actual":      f"{de_years:.1f}yr" if de_years is not None else "N/A",
        "score":       de_score,
        "max":         10,
        "note":        de_note,
    })

    # ── 3. Net Profit Margin ≥ 10%, stable — 15 pts ──────────────────────────
    # High margins mean pricing power — the hallmark of a moat business.
    # Buffett: "The single most important decision in evaluating a business
    # is pricing power."
    ni_by_yr  = _by_year(net_inc_recs)
    rev_by_yr = _by_year(revenue_recs)
    common_yrs = sorted(set(ni_by_yr) & set(rev_by_yr), reverse=True)[:7]
    margins = [
        ni_by_yr[y] / rev_by_yr[y] * 100
        for y in common_yrs
        if rev_by_yr[y] and rev_by_yr[y] > 0
    ]

    npm_now   = margins[0] if margins else (
        (net_inc / revenue * 100) if net_inc and revenue and revenue > 0 else None
    )
    n_above10 = sum(1 for m in margins if m >= 10)

    if npm_now is None:
        npm_score = 0
        npm_note  = "Insufficient data to compute net profit margin"
    elif npm_now >= 20 and len(margins) >= 3 and n_above10 / len(margins) >= 0.8:
        npm_score = 15
        npm_note  = (f"Net margin {npm_now:.1f}% ({n_above10}/{len(margins)} yrs ≥ 10%) "
                     "— exceptional, sustained pricing power")
    elif npm_now >= 15:
        npm_score = 11
        npm_note  = f"Net margin {npm_now:.1f}% — strong"
    elif npm_now >= 10:
        npm_score = 7
        npm_note  = f"Net margin {npm_now:.1f}% — meets Buffett's floor"
    elif npm_now >= 5:
        npm_score = 3
        npm_note  = f"Net margin {npm_now:.1f}% — thin; limited pricing power"
    else:
        npm_score = 0
        npm_note  = f"Net margin {npm_now:.1f}% — below acceptable floor"

    criteria.append({
        "label":       "Net Profit Margin",
        "requirement": "≥ 10%, stable",
        "actual":      f"{npm_now:.1f}%" if npm_now is not None else "N/A",
        "score":       npm_score,
        "max":         15,
        "note":        npm_note,
    })

    # ── 4. EPS Growth 7-yr CAGR ≥ 7%/yr — 15 pts ────────────────────────────
    # Buffett wants to see earning power growing predictably.  7% is his
    # informal floor — roughly doubling every 10 years.
    n_eps       = len(eps_vals)
    eps_cagr    = None
    if n_eps >= 5 and eps_vals[-1] and eps_vals[-1] > 0 and eps_vals[0] > 0:
        eps_cagr = _cagr(eps_vals[-1], eps_vals[0], n_eps - 1)

    if eps_cagr is None:
        eg_score = 0
        eg_note  = f"Only {n_eps} year(s) of positive EPS — insufficient"
    elif eps_cagr >= 15:
        eg_score = 15
        eg_note  = f"EPS CAGR {eps_cagr:.1f}%/yr over {n_eps}yr — exceptional"
    elif eps_cagr >= 10:
        eg_score = 11
        eg_note  = f"EPS CAGR {eps_cagr:.1f}%/yr over {n_eps}yr — strong"
    elif eps_cagr >= 7:
        eg_score = 7
        eg_note  = (f"EPS CAGR {eps_cagr:.1f}%/yr over {n_eps}yr — "
                    "meets Buffett's minimum; doubles every ~10yr")
    elif eps_cagr >= 3:
        eg_score = 3
        eg_note  = f"EPS CAGR {eps_cagr:.1f}%/yr — below Buffett's floor"
    else:
        eg_score = 0
        eg_note  = f"EPS CAGR {eps_cagr:.1f}%/yr — declining earnings power"

    criteria.append({
        "label":       "EPS Growth (7-yr CAGR)",
        "requirement": "≥ 7%/yr compound",
        "actual":      f"{eps_cagr:.1f}%/yr" if eps_cagr is not None else "N/A",
        "score":       eg_score,
        "max":         15,
        "note":        eg_note,
    })

    # ── 5. Owner Earnings (FCF) positive & growing — 15 pts ─────────────────
    # Buffett's 1986 letter: "Owner earnings = net income + D&A − maintenance capex".
    # We proxy with: Operating Cash Flow − CapEx (standard FCF).
    n_pairs   = min(len(op_cf_vals), len(capex_vals))
    fcf_series = [op_cf_vals[i] - abs(capex_vals[i]) for i in range(n_pairs)]
    if not fcf_series and op_cf is not None:
        fcf_series = [op_cf - (abs(capex) if capex else 0)]

    fcf_now  = fcf_series[0]  if fcf_series else None
    n_fcf    = len(fcf_series)
    n_pos    = sum(1 for f in fcf_series if f > 0)
    fcf_cagr = None
    if n_fcf >= 3 and fcf_series[-1] and fcf_series[-1] > 0 and fcf_series[0] > 0:
        fcf_cagr = _cagr(fcf_series[-1], fcf_series[0], n_fcf - 1)

    if fcf_now is None:
        oe_score = 0
        oe_note  = "Insufficient data to compute owner earnings"
    elif fcf_now > 0 and n_pos == n_fcf and fcf_cagr and fcf_cagr >= 7:
        oe_score = 15
        oe_note  = (f"FCF ${fcf_now/1e9:.1f}B growing at {fcf_cagr:.1f}%/yr — "
                    "compounding cash machine")
    elif fcf_now > 0 and n_pos >= n_fcf - 1:
        oe_score = 9
        oe_note  = (f"FCF ${fcf_now/1e9:.1f}B, positive in {n_pos}/{n_fcf} years — "
                    "reliable cash generation")
    elif fcf_now > 0:
        oe_score = 5
        oe_note  = (f"FCF positive (${fcf_now/1e9:.1f}B) but history mixed "
                    f"({n_pos}/{n_fcf} positive years)")
    else:
        oe_score = 0
        oe_note  = f"Negative FCF ${fcf_now/1e9:.1f}B — business consuming cash"

    criteria.append({
        "label":       "Owner Earnings (FCF)",
        "requirement": "Positive & growing",
        "actual":      f"${fcf_now/1e9:.1f}B" if fcf_now is not None else "N/A",
        "score":       oe_score,
        "max":         15,
        "note":        oe_note,
    })

    # ── 6. ROIC ≥ 12% — 10 pts ───────────────────────────────────────────────
    # ROIC = Operating Income / Invested Capital  where IC = Equity + Net Debt.
    # Measures how well management allocates each dollar of capital.
    ic      = (equity or 0) + max(net_debt, 0)   # use max(net_debt, 0) — net cash ≠ negative IC
    roic    = (op_inc / ic * 100) if (op_inc and ic and ic > 0) else None

    if roic is None:
        roic_score = 0
        roic_note  = "Insufficient data to compute ROIC"
    elif roic >= 20:
        roic_score = 10
        roic_note  = f"ROIC {roic:.1f}% — exceptional capital allocation"
    elif roic >= 15:
        roic_score = 7
        roic_note  = f"ROIC {roic:.1f}% — strong"
    elif roic >= 12:
        roic_score = 4
        roic_note  = f"ROIC {roic:.1f}% — clears Buffett's 12% hurdle"
    elif roic >= 8:
        roic_score = 2
        roic_note  = f"ROIC {roic:.1f}% — mediocre capital returns"
    else:
        roic_score = 0
        roic_note  = f"ROIC {roic:.1f}% — poor capital allocation"

    criteria.append({
        "label":       "Return on Invested Capital",
        "requirement": "≥ 12%",
        "actual":      f"{roic:.1f}%" if roic is not None else "N/A",
        "score":       roic_score,
        "max":         10,
        "note":        roic_note,
    })

    # ── 7. Intrinsic Value vs Price (DCF) — 15 pts ───────────────────────────
    # Two-stage DCF on owner earnings per share.
    # Prefer FCF/share; fall back to EPS.
    #
    # Plausibility guard: some companies (REITs, partnerships) co-file LP/OP
    # unit counts under the same XBRL concept as common shares, producing a
    # wildly inflated FCF/share (e.g. SPG with 8,000 LP units vs ~325M common
    # shares gives FCF/share > $500,000).  If FCF/share exceeds 50× the live
    # price — or 50× the latest EPS when no price is available — treat the
    # shares figure as unreliable and fall back to EPS directly.
    iv      = None
    iv_base = None

    if shares and shares > 0:
        fcf_ps  = (fcf_now / shares) if fcf_now and fcf_now > 0 else None
        eps_ps  = eps if eps and eps > 0 else None

        # Sanity-check fcf_ps: it should be in the same ballpark as EPS/price.
        # A factor-of-50 ceiling catches LP-unit contamination while still
        # allowing genuinely high FCF/share companies.
        if fcf_ps is not None:
            reference = price if price else (eps_ps * 15 if eps_ps else None)
            if reference and fcf_ps > reference * 50:
                print(f"  [Buffett IV] FCF/share ${fcf_ps:,.2f} is >50× reference "
                      f"${reference:.2f} — shares count ({shares:,.0f}) looks like "
                      "LP/OP units; falling back to EPS for DCF")
                fcf_ps = None   # discard; use eps_ps instead

        base    = fcf_ps or eps_ps
        b_label = "FCF/share" if fcf_ps else ("EPS" if eps_ps else None)

        if base and base > 0:
            g_raw  = (eps_cagr / 100) if eps_cagr is not None else 0.05
            g_rate = max(0.0, min(g_raw, GROWTH_CAP))
            iv     = _dcf(base, g_rate)
            iv_base = b_label

    iv_margin = ((iv - price) / iv * 100) if (iv and price) else None

    if iv is None or not price:
        iv_score = 0
        iv_note  = ("Insufficient data to estimate intrinsic value"
                    if iv is None else "No live price to compare against intrinsic value")
    elif price <= iv * 0.60:
        iv_score = 15
        iv_note  = (f"Price ${price:.2f} ≤ 60% of IV ${iv:.2f} ({iv_base}) — "
                    "excellent margin of safety")
    elif price <= iv * 0.80:
        iv_score = 10
        iv_note  = (f"Price ${price:.2f} ≤ 80% of IV ${iv:.2f} ({iv_base}) — "
                    "fair price for a great business")
    elif price <= iv:
        iv_score = 5
        iv_note  = (f"Price ${price:.2f} just below IV ${iv:.2f} ({iv_base}) — "
                    "thin margin; still reasonable")
    elif price <= iv * 1.20:
        iv_score = 2
        iv_note  = (f"Price ${price:.2f} slightly above IV ${iv:.2f} ({iv_base}) — "
                    "paying a small premium")
    else:
        iv_score = 0
        iv_note  = (f"Price ${price:.2f} well above IV ${iv:.2f} ({iv_base}) — "
                    "overvalued on 12% DCF")

    criteria.append({
        "label":              "Intrinsic Value vs Price",
        "requirement":        "Price ≤ 80% of DCF intrinsic value",
        "actual":             f"IV ${iv:.2f} ({iv_base})" if iv else "N/A",
        "score":              iv_score,
        "max":                15,
        "note":               iv_note,
        "intrinsic_value":    round(iv, 2) if iv else None,
        "margin_of_safety":   round(iv_margin, 1) if iv_margin is not None else None,
    })

    # ── Summary ───────────────────────────────────────────────────────────────
    total_score = sum(c["score"] for c in criteria)
    total_max   = sum(c["max"]   for c in criteria)

    if total_score >= 75:
        grade, grade_label = "A", "Wide Moat"
    elif total_score >= 55:
        grade, grade_label = "B", "Narrow Moat"
    elif total_score >= 35:
        grade, grade_label = "C", "No Clear Moat"
    else:
        grade, grade_label = "D", "Avoid"

    return {
        "price":             price,
        "roe_series":        [round(r, 1) for r in roe_series],
        "roe_latest":        round(roe_now, 1) if roe_now is not None else None,
        "n_roe_above15":     n_above15,
        "n_roe_years":       n_roe,
        "net_margin":        round(npm_now, 1) if npm_now is not None else None,
        "eps_cagr":          round(eps_cagr, 1) if eps_cagr is not None else None,
        "fcf_latest":        round(fcf_now / 1e9, 2) if fcf_now is not None else None,
        "fcf_cagr":          round(fcf_cagr, 1) if fcf_cagr is not None else None,
        "roic":              round(roic, 1) if roic is not None else None,
        "intrinsic_value":   round(iv, 2) if iv else None,
        "margin_of_safety":  round(iv_margin, 1) if iv_margin is not None else None,
        "iv_base":           iv_base,
        "de_years":          round(de_years, 1) if de_years is not None else None,
        "total_score":       total_score,
        "total_max":         total_max,
        "grade":             grade,
        "grade_label":       grade_label,
        "criteria":          criteria,
    }