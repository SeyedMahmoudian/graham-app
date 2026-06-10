"""
Composite scorer: Graham 40% + Quality 35% + Momentum 25%.

Verdicts:
  >= 70  STRONG BUY   — all pillars aligned
  55-70  BUY          — mostly positive signals
  40-55  WATCH        — mixed signals, monitor
  25-40  HOLD/WEAK    — significant concerns
  < 25   AVOID        — fails on multiple pillars
"""

WEIGHTS = {
    "graham":   0.40,
    "quality":  0.35,
    "momentum": 0.25,
}

VERDICTS = [
    (70, "STRONG BUY",  "strong-buy",  "All three pillars aligned — rare Graham+Quality+Momentum signal"),
    (55, "BUY",         "buy",         "Mostly positive — good value with quality confirmation"),
    (40, "WATCH",       "watch",       "Mixed signals — monitor for entry point"),
    (25, "HOLD/WEAK",   "hold",        "Significant concerns — not a high-conviction idea"),
    (0,  "AVOID",       "avoid",       "Fails on multiple pillars — skip"),
]


def composite(graham_result: dict, quality_result: dict,
              momentum_result: dict) -> dict:
    """
    Combine three scoring results into a final composite score.
    All inputs are the dict returns from their respective score() functions.
    """
    g_score = graham_result.get("total_score", 0)
    g_max   = graham_result.get("total_max", 100)
    q_score = quality_result.get("total_score", 0)
    q_max   = quality_result.get("total_max", 100)
    m_score = momentum_result.get("total_score", 0)
    m_max   = momentum_result.get("total_max", 100)

    # Normalise each to 0-100
    g_pct = (g_score / g_max * 100) if g_max else 0
    q_pct = (q_score / q_max * 100) if q_max else 0
    m_pct = (m_score / m_max * 100) if m_max else 0

    composite_score = (
        g_pct * WEIGHTS["graham"] +
        q_pct * WEIGHTS["quality"] +
        m_pct * WEIGHTS["momentum"]
    )

    # Determine verdict
    verdict = label = description = ""
    for threshold, v, l, d in VERDICTS:
        if composite_score >= threshold:
            verdict, label, description = v, l, d
            break

    # Value trap check: good Graham but bad momentum
    value_trap_warning = (
        g_pct >= 60 and
        m_pct < 30 and
        quality_result.get("roe") is not None and
        quality_result.get("roe", 0) < 10
    )

    return {
        "graham_pct":       round(g_pct, 1),
        "quality_pct":      round(q_pct, 1),
        "momentum_pct":     round(m_pct, 1),
        "composite_score":  round(composite_score, 1),
        "verdict":          verdict,
        "verdict_label":    label,
        "verdict_desc":     description,
        "value_trap_warning": value_trap_warning,
        "weights":          WEIGHTS,
    }


def fundamental_only(graham_result: dict, quality_result: dict) -> dict:
    """
    Score without momentum — used for screener pre-filter.
    Weights re-normalised to Graham 53% / Quality 47%.
    """
    g_score = graham_result.get("total_score", 0)
    g_max   = graham_result.get("total_max", 100)
    q_score = quality_result.get("total_score", 0)
    q_max   = quality_result.get("total_max", 100)

    g_pct = (g_score / g_max * 100) if g_max else 0
    q_pct = (q_score / q_max * 100) if q_max else 0

    score = g_pct * 0.53 + q_pct * 0.47

    return {
        "graham_pct":       round(g_pct, 1),
        "quality_pct":      round(q_pct, 1),
        "momentum_pct":     None,
        "composite_score":  round(score, 1),
        "verdict":          "PENDING",
        "verdict_label":    "pending",
        "verdict_desc":     "Momentum not yet loaded",
        "value_trap_warning": False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Enhanced composite  (6-factor model)
# ══════════════════════════════════════════════════════════════════════════════
#
# Weight breakdown (sums to 1.0):
#   Graham     0.25   Valuation anchor (price vs intrinsic value)
#   Quality    0.22   Business quality (ROE, margins, FCF, revenue growth)
#   Momentum   0.18   Price trend confirmation (moving avg, RS, drawdown)
#   Piotroski  0.18   Accounting health (9-point signal; prevents value traps)
#   Risk       0.10   Risk-adjusted profile (Sharpe, beta, drawdown, vol)
#   Altman     0.07   Bankruptcy risk filter (directly encoded as safety cap)
#
# Altman also acts as a HARD CAP: distress zone stocks cannot exceed 50/100.

ENHANCED_WEIGHTS = {
    "graham":             0.12,   # value anchor
    "buffett":            0.06,   # DCF IV signal
    "quality":            0.10,   # business quality (ROE, margins, FCF, rev growth)
    "momentum":           0.12,   # price trend confirmation
    "piotroski":          0.09,   # accounting health / value-trap filter
    "risk":               0.06,   # risk-adjusted profile
    "altman":             0.03,   # bankruptcy safety cap
    "earnings_revision":  0.12,   # P1 forward momentum factor
    "profitability":      0.12,   # P1 structural quality (ROIC-based)
    "fcf_quality":        0.10,   # P1 cash generation quality
    "capital_allocation": 0.08,   # P2 capital allocation efficiency
    # Sum = 1.00
}

ENHANCED_VERDICTS = [
    (75, "STRONG BUY",  "strong-buy",  "All six pillars aligned — highest-conviction signal"),
    (60, "BUY",         "buy",         "Strong across most factors — good risk/reward"),
    (45, "WATCH",       "watch",       "Mixed signals — monitor for better entry"),
    (30, "HOLD/WEAK",   "hold",        "Significant concerns across multiple factors"),
    (0,  "AVOID",       "avoid",       "Fails on multiple pillars — high risk"),
]


# ── ISSUE-008 resolution: Greenblatt Earnings Yield in composite scoring ──────
#
# Greenblatt's Magic Formula requires CROSS-SECTIONAL ranking: a stock's
# magic_score (0-100) is only meaningful relative to the full universe.
# Therefore it cannot be included in a single-stock composite score —
# including it would always give a neutral 50 (no peer comparison possible).
#
# Current behaviour:
#   greenblatt.compute_single() -> earnings_yield (%) and roic (%) stored in
#   the analysis dict for display purposes only.
#   magic_score is None until rank_universe() is called on the screener universe.
#
# Decision: EXCLUDE from enhanced_composite() weighted sum.
#   Rationale: composite score must be deterministic for a single stock.
#   Including an unnormalised EY % alongside normalised 0-100 pillar scores
#   would distort the weighted sum unpredictably.
#
# Greenblatt data IS surfaced in the analysis result dict (greenblatt_result)
# and displayed in app.py for informational purposes. When the screener runs
# rank_universe() the magic_score becomes available for universe-level sorting.
#
# ISSUE-002: Enterprise Value is computed ONLY in greenblatt.enterprise_value()
# and consumed via greenblatt.compute_single(). scorer.py must never
# re-implement the EV formula independently. Use greenblatt.compute_single()
# or greenblatt.enterprise_value(price, sec) to obtain EV for any need.
# ─────────────────────────────────────────────────────────────────────────────

def enhanced_composite(
    graham_result:    dict,
    quality_result:   dict,
    momentum_result:  dict,
    piotroski_result: dict,
    risk_result:      dict,
    altman_result:    dict,
    buffett_result:   dict | None = None,
    greenblatt_result: dict | None = None,            # display only; not scored (ISSUE-008)
    earnings_revision_result: dict | None = None,     # P1 forward momentum; 12% weight
    profitability_result: dict | None = None,          # P1 structural quality; 12% weight
    fcf_quality_result: dict | None = None,            # P1 cash generation quality; 10% weight
    capital_allocation_result: dict | None = None,     # P2 capital allocation; 8% weight
) -> dict:
    """
    Eleven-factor composite score (fewer when optional results are None for
    backward-compatibility with older cached analyses).

    All input dicts are the return values of their respective score() functions:
      graham_result              → graham.score()
      quality_result             → quality.score()
      momentum_result            → momentum.score()
      piotroski_result           → piotroski.score()
      risk_result                → risk_metrics.score()
      altman_result              → altman.score()
      buffett_result             → buffett.score()          (optional; defaults to neutral 50)
      earnings_revision_result   → earnings_revision.get_revision_score()
                                   (optional; defaults to neutral 50 when not available)
      profitability_result        → profitability.ProfitabilityAnalyzer.get_profitability_score()
                                   (optional; defaults to neutral 50 when not available)
      fcf_quality_result          → fcf_quality.FCFQualityAnalyzer.get_fcf_quality_score()
                                   (optional; defaults to neutral 50 when not available)
      capital_allocation_result   → capital_allocation.CapitalAllocationAnalyzer.get_capital_allocation_score()
                                   (optional; defaults to neutral 50 when not available)

    Returns a dict with composite_score (0-100), verdict, and per-pillar
    percentages, compatible with display in app.py.
    """

    # ── Normalise each pillar to 0-100 pct ───────────────────────────────────
    def _pct(result, score_key="total_score", max_key="total_max"):
        s = result.get(score_key, 0) or 0
        m = result.get(max_key,   100) or 100
        return (s / m * 100) if m else 0

    g_pct  = _pct(graham_result)
    q_pct  = _pct(quality_result)
    m_pct  = _pct(momentum_result)
    f_pct  = ((piotroski_result or {}).get("f_score", 0) or 0) / 9 * 100  # 0-9 scale
    r_pct  = _pct(risk_result, "risk_score", "risk_score_max")
    _a_raw = (altman_result or {}).get("risk_score")
    a_pct  = _a_raw if _a_raw is not None else 50  # neutral fallback only when data absent
    b_pct  = _pct(buffett_result) if buffett_result else 50              # neutral fallback
    er_pct = _pct(earnings_revision_result) if earnings_revision_result else 50  # neutral fallback
    _p_raw = profitability_result.get("profitability_score") if profitability_result else None
    p_pct  = _p_raw if _p_raw is not None else 50  # neutral fallback only when data absent
    _fcf_raw = fcf_quality_result.get("fcf_quality_score") if fcf_quality_result else None
    fcf_pct  = _fcf_raw if _fcf_raw is not None else 50  # neutral fallback only when data absent
    _ca_raw = capital_allocation_result.get("capital_allocation_score") if capital_allocation_result else None
    ca_pct  = _ca_raw if _ca_raw is not None else 50  # neutral fallback only when data absent

    # ── Weighted sum ──────────────────────────────────────────────────────────
    raw_score = (
        g_pct  * ENHANCED_WEIGHTS["graham"]            +
        b_pct  * ENHANCED_WEIGHTS["buffett"]           +
        q_pct  * ENHANCED_WEIGHTS["quality"]           +
        m_pct  * ENHANCED_WEIGHTS["momentum"]          +
        f_pct  * ENHANCED_WEIGHTS["piotroski"]         +
        r_pct  * ENHANCED_WEIGHTS["risk"]              +
        a_pct  * ENHANCED_WEIGHTS["altman"]            +
        er_pct * ENHANCED_WEIGHTS["earnings_revision"]  +
        p_pct  * ENHANCED_WEIGHTS["profitability"]     +
        fcf_pct * ENHANCED_WEIGHTS["fcf_quality"]      +
        ca_pct  * ENHANCED_WEIGHTS["capital_allocation"]
    )

    # ── Altman hard cap — distress zone stocks cannot score above 50 ──────────
    altman_zone = (altman_result or {}).get("zone", "unknown")
    altman_cap_applied = False
    if altman_zone == "distress":
        raw_score = min(raw_score, 50.0)
        altman_cap_applied = True
    elif altman_zone == "grey":
        raw_score = max(0, raw_score - 10)

    composite_score = round(raw_score, 1)

    # ── ISSUE-001: Margin of Safety guard ─────────────────────────────────────
    # Negative MoS means price > intrinsic value estimate.
    # Both negative  → hard cap at HOLD/WEAK (≤ 44.9), dual_mos_warning=True
    # One negative   → cap at WATCH (≤ 59.9),           partial_mos_warning set
    # None (no data) → treated as not negative; no cap applied
    g_mos = graham_result.get("margin_of_safety")
    b_mos = (buffett_result or {}).get("margin_of_safety")

    g_mos_negative = g_mos is not None and g_mos < 0
    b_mos_negative = b_mos is not None and b_mos < 0

    dual_mos_warning    = False
    partial_mos_warning = None   # None | "graham" | "buffett"

    if g_mos_negative and b_mos_negative:
        composite_score  = round(min(composite_score, 44.9), 1)
        dual_mos_warning = True
    elif g_mos_negative:
        composite_score     = round(min(composite_score, 59.9), 1)
        partial_mos_warning = "graham"
    elif b_mos_negative:
        composite_score     = round(min(composite_score, 59.9), 1)
        partial_mos_warning = "buffett"

    # ── Verdict ───────────────────────────────────────────────────────────────
    verdict = label = description = ""
    for threshold, v, l, d in ENHANCED_VERDICTS:
        if composite_score >= threshold:
            verdict, label, description = v, l, d
            break

    # ── Value trap check ─────────────────────────────────────────────────────
    # Good Graham score but weak momentum AND weak Piotroski = classic value trap
    value_trap_warning = (
        g_pct >= 60 and
        m_pct < 30  and
        piotroski_result.get("f_score", 5) <= 3 if piotroski_result else True
    )

    # ── Quality flag: high Piotroski + high Quality + high Buffett ───────────
    compounder_flag = (
        (piotroski_result or {}).get("f_score", 0) >= 7 and
        q_pct >= 65 and
        b_pct >= 60
    )

    return {
        # Pillar percentages
        "graham_pct":               round(g_pct,  1),
        "buffett_pct":              round(b_pct,  1),
        "quality_pct":              round(q_pct,  1),
        "momentum_pct":             round(m_pct,  1),
        "piotroski_pct":            round(f_pct,  1),
        "risk_pct":                 round(r_pct,  1),
        "altman_pct":               round(a_pct,  1),
        "earnings_revision_pct":    round(er_pct, 1),
        "profitability_pct":        round(p_pct,  1),
        "fcf_quality_pct":          round(fcf_pct, 1),
        "capital_allocation_pct":   round(ca_pct,  1),

        # Greenblatt — display only, not in weighted sum (see ISSUE-008)
        "greenblatt_earnings_yield": (
            greenblatt_result.get("earnings_yield") if greenblatt_result else None
        ),
        "greenblatt_fcf_yield": (
            greenblatt_result.get("fcf_yield") if greenblatt_result else None
        ),
        "greenblatt_roic": (
            greenblatt_result.get("roic") if greenblatt_result else None
        ),
        "greenblatt_magic_score": (
            greenblatt_result.get("magic_score") if greenblatt_result else None
        ),

        # Earnings revision signal (display + scoring)
        "earnings_revision_signal": (
            earnings_revision_result.get("signal") if earnings_revision_result else None
        ),

        # Profitability signal (display + scoring)
        "profitability_signal": (
            profitability_result.get("signal") if profitability_result else None
        ),

        # FCF Quality signal (display + scoring)
        "fcf_quality_signal": (
            fcf_quality_result.get("signal") if fcf_quality_result else None
        ),

        # Capital Allocation signal (display + scoring)
        "capital_allocation_signal": (
            capital_allocation_result.get("signal") if capital_allocation_result else None
        ),

        # Score and verdict
        "composite_score":   composite_score,
        "verdict":           verdict,
        "verdict_label":     label,
        "verdict_desc":      description,

        # Flags
        "value_trap_warning": (
            value_trap_warning or altman_zone in ("distress", "grey")
        ),
        "compounder_flag":     compounder_flag,
        "altman_cap_applied":  altman_cap_applied,
        "dual_mos_warning":    dual_mos_warning,
        "partial_mos_warning": partial_mos_warning,

        "weights": ENHANCED_WEIGHTS,
    }