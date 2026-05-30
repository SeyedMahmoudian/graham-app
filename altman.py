"""
Altman Z-Score — bankruptcy risk and financial distress prediction.

Reference:
  Altman (1968) "Financial Ratios, Discriminant Analysis and the Prediction
  of Corporate Bankruptcy"
  Journal of Finance, Vol. 23, No. 4.

The Z-Score has been shown to predict bankruptcy 1–2 years in advance with
~72–80% accuracy. Critical use-case here: filtering value traps — stocks
that look cheap on P/E or P/B but are heading toward financial distress.

Three model variants:
  Original (public manufacturers):   Z  = 1.2X1 + 1.4X2 + 3.3X3 + 0.6X4 + 1.0X5
  Z' (private firms):                Z' = 0.717X1 + 0.847X2 + 3.107X3 + 0.420X4 + 0.998X5
  Z'' (non-manufacturers/services):  Z'' = 6.56X1 + 3.26X2 + 6.72X3 + 1.05X4

We default to the original public model; Z'' is applied when a company
has no net PP&E data (typical of service/financial/tech firms).

Components:
  X1 = Working Capital / Total Assets           (short-term liquidity)
  X2 = Retained Earnings / Total Assets         (accumulated profitability)
  X3 = EBIT / Total Assets                      (core operating efficiency)
  X4 = Market Cap / Total Liabilities           (leverage buffer)
  X5 = Revenue / Total Assets                   (asset efficiency)
                                                 [omitted in Z'']

Safe zones:
  Original:  Z > 2.99  safe | 1.81–2.99  grey | < 1.81  distress
  Z'':       Z > 2.60  safe | 1.10–2.60  grey | < 1.10  distress

Requires new sec_facts keys added to sec_data.py:
  total_assets, retained_earnings
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


# ── Main ──────────────────────────────────────────────────────────────────────

def score(price: float | None, sec: dict) -> dict:
    """
    Compute Altman Z-Score.

    Args:
        price:  Current market price per share (None → X4 unavailable).
        sec:    sec_facts dict.

    Returns dict with z_score, zone, zone_label, components, and a scoring
    sub-dict (risk_score 0–100) compatible with enhanced_composite in scorer.py.
    """
    # ── Inputs ────────────────────────────────────────────────────────────────
    cur_ast  = _first(sec.get("cur_ast",          []))
    cur_lib  = _first(sec.get("cur_lib",          []))
    tot_ast  = _first(sec.get("total_assets",     []))
    ret_earn = _first(sec.get("retained_earnings",[]))
    op_inc   = _first(sec.get("op_income",        []))
    shares   = _first(sec.get("shares",           []))
    tot_lib  = _first(sec.get("tot_lib",          []))
    revenue  = _first(sec.get("revenue",          []))
    ppe      = _first(sec.get("ppe_net",          []))   # None → Z'' model

    mkt_cap = (price * shares) if (price and shares) else None

    # Working capital
    wc = (cur_ast - cur_lib) if (cur_ast is not None and cur_lib is not None) else None

    # ── Compute components ────────────────────────────────────────────────────
    x1 = (wc      / tot_ast) if (wc is not None   and tot_ast and tot_ast > 0) else None
    x2 = (ret_earn / tot_ast) if (ret_earn is not None and tot_ast and tot_ast > 0) else None
    x3 = (op_inc  / tot_ast) if (op_inc is not None   and tot_ast and tot_ast > 0) else None
    x4 = (mkt_cap / tot_lib) if (mkt_cap and tot_lib and tot_lib > 0)              else None
    x5 = (revenue / tot_ast) if (revenue and tot_ast and tot_ast > 0)              else None

    # Choose model: Z'' for service/non-manufacturer (no PP&E), else original
    use_zpp = (ppe is None or ppe == 0)   # True → Z'' non-manufacturer model

    # Count available components
    if use_zpp:
        components_used = [x for x in [x1, x2, x3, x4] if x is not None]
        model = "Z''"
    else:
        components_used = [x for x in [x1, x2, x3, x4, x5] if x is not None]
        model = "Original"

    n_available = len(components_used)
    min_required = 3

    z_score = None
    if n_available >= min_required:
        if use_zpp:
            # Z'' = 6.56X1 + 3.26X2 + 6.72X3 + 1.05X4
            z_score = (
                (6.56 * x1 if x1 is not None else 0) +
                (3.26 * x2 if x2 is not None else 0) +
                (6.72 * x3 if x3 is not None else 0) +
                (1.05 * x4 if x4 is not None else 0)
            )
        else:
            # Original: Z = 1.2X1 + 1.4X2 + 3.3X3 + 0.6X4 + 1.0X5
            z_score = (
                (1.2 * x1 if x1 is not None else 0) +
                (1.4 * x2 if x2 is not None else 0) +
                (3.3 * x3 if x3 is not None else 0) +
                (0.6 * x4 if x4 is not None else 0) +
                (1.0 * x5 if x5 is not None else 0)
            )
        z_score = round(z_score, 3)

    # ── Zone classification ───────────────────────────────────────────────────
    if use_zpp:
        safe_thresh, grey_thresh = 2.60, 1.10
    else:
        safe_thresh, grey_thresh = 2.99, 1.81

    if z_score is None:
        zone, zone_label, color = "unknown", "Unknown", "gray"
        note = f"Insufficient data ({n_available}/{4 if use_zpp else 5} components)"
        risk_penalty = 0
    elif z_score > safe_thresh:
        zone, zone_label, color = "safe", "Safe Zone", "green"
        note = f"Z={z_score:.2f} ({model}) — low bankruptcy risk"
        risk_penalty = 0
    elif z_score >= grey_thresh:
        zone, zone_label, color = "grey", "Grey Zone", "amber"
        note = f"Z={z_score:.2f} ({model}) — elevated risk, monitor closely"
        risk_penalty = 15
    else:
        zone, zone_label, color = "distress", "Distress Zone", "red"
        note = f"Z={z_score:.2f} ({model}) — high bankruptcy risk — value trap risk!"
        risk_penalty = 35

    # ── Altman risk sub-score (for enhanced_composite) ────────────────────────
    if z_score is None:
        risk_score = 50    # neutral when we can't calculate
    elif use_zpp:
        # Scale Z'' 0–6 → 0–100
        risk_score = min(100, max(0, round(z_score / 6.0 * 100)))
    else:
        # Scale Z  0–4+ → 0–100
        risk_score = min(100, max(0, round(z_score / 4.0 * 100)))

    return {
        "z_score":         z_score,
        "model":           model,
        "zone":            zone,
        "zone_label":      zone_label,
        "color":           color,
        "note":            note,
        "risk_penalty":    risk_penalty,   # points to subtract from composite
        "risk_score":      risk_score,     # 0-100 for enhanced_composite
        "n_available":     n_available,
        "components": {
            "x1_working_capital":     round(x1, 4) if x1 is not None else None,
            "x2_retained_earnings":   round(x2, 4) if x2 is not None else None,
            "x3_ebit_ratio":          round(x3, 4) if x3 is not None else None,
            "x4_equity_liabilities":  round(x4, 4) if x4 is not None else None,
            "x5_asset_turnover":      round(x5, 4) if x5 is not None else None,
        },
    }
