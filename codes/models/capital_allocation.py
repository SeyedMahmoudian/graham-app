"""
Capital Allocation Model — P2 module.

Measures how effectively management allocates capital for long-term value creation.
Prioritises predictive power over academic purity.

Metrics & weights:
  ROIC Spread (ROIC − 10% hurdle)   25%  — value creation above cost of capital
  Incremental ROIC                   25%  — marginal return on NEW capital
  Reinvestment Rate                  20%  — (CapEx + R&D) / EBIT; falls back to CapEx/OCF
  Buyback + Dividend Yield           15%  — capital returned to shareholders
  Dilution Rate                      10%  — share count growth (negative = dilutive)
  Debt Allocation Trend               5%  — leverage trajectory (deleveraging = good)

Signal mapping:
  >= 80  → EXCELLENT_ALLOCATOR
  65–79  → GOOD_ALLOCATOR
  45–64  → AVERAGE_ALLOCATOR
  30–44  → POOR_ALLOCATOR
  < 30   → CAPITAL_DESTROYER

Output schema (strict):
  {
    "ticker":                   str,
    "roic":                     float | None,
    "roic_spread":              float | None,   # ROIC − 10%
    "incremental_roic":         float | None,
    "reinvestment_rate":        float | None,
    "reinvestment_method":      str,            # "(CapEx+R&D)/EBIT" | "CapEx/OCF"
    "buyback_yield":            float | None,
    "dividend_yield_implied":   float | None,
    "shareholder_yield":        float | None,   # buyback + dividend yield
    "dilution_rate":            float | None,   # YoY share count growth %
    "debt_trend":               float | None,   # change in lt_debt/equity ratio
    "capital_allocation_score": float,
    "signal":                   str,
    "total_score":              float,          # scorer.py compat
    "total_max":                float,          # 100.0
  }

Integration:
  from codes.models.capital_allocation import CapitalAllocationAnalyzer
  result = CapitalAllocationAnalyzer(ticker, sec_facts, price).get_capital_allocation_score()
  Composite weight: 8%
"""

from __future__ import annotations

import math
import statistics
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val: Any) -> float | None:
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
    out: list[float] = []
    for r in records:
        v = _safe(r.get("value"))
        if v is not None:
            out.append(v)
            if len(out) >= n:
                break
    return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ── Signal mapping ────────────────────────────────────────────────────────────

def _signal(score: float) -> str:
    if score >= 80:
        return "EXCELLENT_ALLOCATOR"
    if score >= 65:
        return "GOOD_ALLOCATOR"
    if score >= 45:
        return "AVERAGE_ALLOCATOR"
    if score >= 30:
        return "POOR_ALLOCATOR"
    return "CAPITAL_DESTROYER"


# ── Normalisation helpers ─────────────────────────────────────────────────────

HURDLE_RATE = 10.0  # % — fixed hurdle rate for value creation assessment

def _norm_roic_spread(spread_pct: float | None) -> float:
    """
    ROIC spread = ROIC − 10% hurdle.
    Spread ≥ +15pp → 100, spread ≤ −10pp → 0.
    Neutral (50) at spread = 0 (ROIC exactly at hurdle).
    """
    if spread_pct is None:
        return 50.0
    # Linear mapping: [-10, +15] → [0, 100], centre at 0 → ~40
    # Use sigmoid-style: centre at 5% spread (modest value creation = neutral)
    if spread_pct >= 15:
        return 100.0
    if spread_pct <= -10:
        return 0.0
    # Map [-10, 15] → [0, 100]
    return _clamp((spread_pct + 10) / 25 * 100, 0, 100)


def _norm_incremental_roic(iroic_pct: float | None) -> float:
    """
    Incremental ROIC = ΔNOPAT / ΔIC.
    ≥ 25% → 100, ≤ 0% → 0. Neutral (50) at ~12.5%.
    """
    if iroic_pct is None:
        return 50.0
    capped = _clamp(iroic_pct, -50, 200)
    if capped >= 25:
        return 100.0
    if capped <= 0:
        return 0.0
    return _clamp(capped / 25 * 100, 0, 100)


def _norm_reinvestment_rate(rate: float | None) -> float:
    """
    Reinvestment rate = (CapEx + R&D) / EBIT.
    Optimal range: 20–60%.
    < 10% → underinvesting (may signal harvest mode or lack of opportunities).
    > 80% → overinvesting / consuming most earnings.
    Score peaks at ~30–40% reinvestment.
    """
    if rate is None:
        return 50.0
    if rate < 0:
        return 0.0
    # Bell-curve scoring: peak at 35%, drops off both sides
    if 0.20 <= rate <= 0.60:
        # Score from 60 to 100 within optimal range
        mid = 0.35
        half_width = 0.25
        dist = abs(rate - mid) / half_width
        return _clamp(100 - dist * 40, 60, 100)
    elif rate < 0.20:
        # Underinvesting: 0.0 → 60
        return _clamp(rate / 0.20 * 60, 0, 60)
    else:
        # Overinvesting: 0.60 → 60, 1.0+ → 0
        return _clamp((1.0 - rate) / 0.40 * 60, 0, 60)


def _norm_shareholder_yield(yield_pct: float | None) -> float:
    """
    Combined buyback + dividend yield (%).
    ≥ 5% → 100, ≤ 0% → 30 (neutral — not all great companies pay dividends).
    """
    if yield_pct is None:
        return 50.0
    if yield_pct >= 5:
        return 100.0
    if yield_pct <= 0:
        return 30.0
    return _clamp(30 + yield_pct / 5 * 70, 30, 100)


def _norm_dilution_rate(growth_pct: float | None) -> float:
    """
    Share count YoY growth %.
    Negative growth (buybacks) → 100.
    0% (flat) → 80.
    +5% dilution → 20.
    > +10% dilution → 0.
    """
    if growth_pct is None:
        return 50.0
    if growth_pct <= -2:   # meaningful buybacks
        return 100.0
    if growth_pct <= 0:    # flat to slight reduction
        return 80.0
    if growth_pct >= 10:   # severe dilution
        return 0.0
    # Linear: [0, 10] → [80, 0]
    return _clamp(80 - growth_pct / 10 * 80, 0, 80)


def _norm_debt_trend(trend: float | None) -> float:
    """
    Change in lt_debt/equity ratio YoY.
    Negative (deleveraging) → good → higher score.
    Δ ≤ −0.10 → 100, Δ ≥ +0.20 → 0.
    """
    if trend is None:
        return 50.0
    if trend <= -0.10:
        return 100.0
    if trend >= 0.20:
        return 0.0
    # [-0.10, 0.20] → [100, 0]
    return _clamp((0.20 - trend) / 0.30 * 100, 0, 100)


# ── Class ─────────────────────────────────────────────────────────────────────

class CapitalAllocationAnalyzer:
    """
    Compute capital allocation quality score from SEC financials.

    Args:
        ticker:     Stock ticker.
        financials: sec_facts dict from sec_data.fetch_company_facts().
        price:      Current market price per share (optional; needed for yields).
    """

    _WEIGHTS = {
        "roic_spread":        0.25,
        "incremental_roic":   0.25,
        "reinvestment_rate":  0.20,
        "shareholder_yield":  0.15,
        "dilution_rate":      0.10,
        "debt_trend":         0.05,
    }

    def __init__(self, ticker: str, financials: dict, price: float | None = None) -> None:
        self.ticker = ticker.upper().strip()
        self._f     = financials
        self._price = price

    # ── ROIC (reused from profitability logic for consistency) ────────────────

    def _calc_roic_from_vals(self, op_inc: float | None, net_inc: float | None,
                              equity: float | None, lt_debt: float | None,
                              cash: float | None) -> float | None:
        if op_inc is None or equity is None:
            return None
        if net_inc is not None and op_inc > 0:
            tax_rate = _clamp(1 - net_inc / op_inc, 0, 0.50)
        else:
            tax_rate = 0.21
        nopat    = op_inc * (1 - tax_rate)
        net_debt = (lt_debt or 0) - (cash or 0)
        ic       = equity + max(net_debt, 0)
        if ic <= 0:
            return None
        return nopat / ic * 100

    def calc_roic(self) -> float | None:
        return self._calc_roic_from_vals(
            _first(self._f.get("op_income", [])),
            _first(self._f.get("net_inc",   [])),
            _first(self._f.get("equity",    [])),
            _first(self._f.get("lt_debt",   [])),
            _first(self._f.get("cash",       [])),
        )

    def calc_roic_spread(self) -> float | None:
        roic = self.calc_roic()
        if roic is None:
            return None
        return roic - HURDLE_RATE

    # ── Incremental ROIC ──────────────────────────────────────────────────────

    def calc_incremental_roic(self) -> float | None:
        """ΔNOPAT / ΔIC over the most recent two periods."""
        op_inc_vals  = _values(self._f.get("op_income", []), n=2)
        net_inc_vals = _values(self._f.get("net_inc",   []), n=2)
        equity_vals  = _values(self._f.get("equity",    []), n=2)
        lt_debt_vals = _values(self._f.get("lt_debt",   []), n=2)
        cash_vals    = _values(self._f.get("cash",       []), n=2)

        if len(op_inc_vals) < 2 or len(equity_vals) < 2:
            return None

        def _nopat(idx: int) -> float | None:
            oi = op_inc_vals[idx]  if idx < len(op_inc_vals)  else None
            ni = net_inc_vals[idx] if idx < len(net_inc_vals) else None
            if oi is None:
                return None
            tax = _clamp(1 - ni / oi, 0, 0.50) if (ni is not None and oi > 0) else 0.21
            return oi * (1 - tax)

        def _ic(idx: int) -> float | None:
            eq = equity_vals[idx]  if idx < len(equity_vals)  else None
            ld = lt_debt_vals[idx] if idx < len(lt_debt_vals) else None
            ca = cash_vals[idx]    if idx < len(cash_vals)    else None
            if eq is None:
                return None
            nd = (ld or 0) - (ca or 0)
            ic = eq + max(nd, 0)
            return ic if ic > 0 else None

        n0, n1 = _nopat(0), _nopat(1)
        i0, i1 = _ic(0),    _ic(1)
        if None in (n0, n1, i0, i1):
            return None
        delta_ic = i0 - i1  # type: ignore[operator]
        if abs(delta_ic) < 1:
            return None
        return (n0 - n1) / delta_ic * 100  # type: ignore[operator]

    # ── Reinvestment Rate ─────────────────────────────────────────────────────

    def calc_reinvestment_rate(self) -> tuple[float | None, str]:
        """
        Returns (rate, method_label).
        Preferred: (CapEx + R&D) / EBIT.
        Fallback:  CapEx / Operating CF.
        """
        ebit    = _first(self._f.get("op_income", []))
        capex   = _first(self._f.get("capex",     []))
        r_and_d = _first(self._f.get("r_and_d",   []))
        op_cf   = _first(self._f.get("op_cf",     []))

        capex_abs = abs(capex) if capex is not None else None

        # Primary path: (CapEx + R&D) / EBIT
        if ebit is not None and abs(ebit) > 1 and capex_abs is not None:
            if r_and_d is not None:
                rate = (capex_abs + abs(r_and_d)) / abs(ebit)
                return rate, "(CapEx+R&D)/EBIT"
            else:
                # R&D unavailable — still use CapEx/EBIT for comparability
                rate = capex_abs / abs(ebit)
                return rate, "CapEx/EBIT"

        # Fallback: CapEx / Operating CF
        if op_cf is not None and op_cf > 0 and capex_abs is not None:
            return capex_abs / op_cf, "CapEx/OCF"

        return None, "N/A"

    # ── Shareholder Yield ─────────────────────────────────────────────────────

    def calc_buyback_yield(self) -> float | None:
        """
        Buyback yield = share count reduction × price / market cap.
        Approximated as: -YoY_share_change / shares_now (fraction returned).
        Requires price for proper yield; without price returns share-reduction %.
        """
        shares_vals = _values(self._f.get("shares", []), n=2)
        if len(shares_vals) < 2:
            return None
        s_now, s_prev = shares_vals[0], shares_vals[1]
        if s_prev <= 0:
            return None
        reduction_pct = (s_prev - s_now) / s_prev * 100  # positive = buyback
        return reduction_pct  # % of shares retired

    def calc_dividend_yield_implied(self) -> float | None:
        """
        Implied dividend yield from total dividend payments / market cap.
        Market cap = price × shares. Falls back to div/share if no price.
        """
        div_vals    = _values(self._f.get("dividends", []), n=1)
        shares_vals = _values(self._f.get("shares",    []), n=1)
        if not div_vals or not shares_vals:
            return None
        total_div = div_vals[0]
        shares    = shares_vals[0]
        if shares <= 0 or total_div <= 0:
            return None
        if self._price and self._price > 0:
            mkt_cap = self._price * shares
            return total_div / mkt_cap * 100
        return None

    def calc_shareholder_yield(self) -> float | None:
        """Combined buyback yield + dividend yield (both as %)."""
        buyback = self.calc_buyback_yield() or 0.0
        div     = self.calc_dividend_yield_implied() or 0.0
        result  = buyback + div
        return result if result != 0.0 else None

    # ── Dilution Rate ─────────────────────────────────────────────────────────

    def calc_dilution_rate(self) -> float | None:
        """YoY share count growth (%). Negative = buybacks (good)."""
        shares_vals = _values(self._f.get("shares", []), n=2)
        if len(shares_vals) < 2:
            return None
        s_now, s_prev = shares_vals[0], shares_vals[1]
        if s_prev <= 0:
            return None
        return (s_now - s_prev) / s_prev * 100

    # ── Debt Allocation Trend ─────────────────────────────────────────────────

    def calc_debt_trend(self) -> float | None:
        """
        Change in lt_debt/equity ratio YoY.
        Negative = deleveraging (good capital discipline).
        """
        lt_debt_vals = _values(self._f.get("lt_debt", []), n=2)
        equity_vals  = _values(self._f.get("equity",  []), n=2)
        if len(lt_debt_vals) < 2 or len(equity_vals) < 2:
            return None
        ld0, ld1 = lt_debt_vals[0], lt_debt_vals[1]
        eq0, eq1 = equity_vals[0],  equity_vals[1]
        if eq0 <= 0 or eq1 <= 0:
            return None
        ratio_now  = ld0 / eq0
        ratio_prev = ld1 / eq1
        return ratio_now - ratio_prev

    # ── Composite score ───────────────────────────────────────────────────────

    def get_capital_allocation_score(self) -> dict:
        """
        Compute and return strict JSON-compatible dict.
        """
        roic          = self.calc_roic()
        roic_spread   = self.calc_roic_spread()
        incr_roic     = self.calc_incremental_roic()
        rr, rr_method = self.calc_reinvestment_rate()
        buyback       = self.calc_buyback_yield()
        div_yield     = self.calc_dividend_yield_implied()
        sh_yield      = self.calc_shareholder_yield()
        dilution      = self.calc_dilution_rate()
        debt_trend    = self.calc_debt_trend()

        # Normalised sub-scores
        n_spread  = _norm_roic_spread(roic_spread)
        n_iroic   = _norm_incremental_roic(incr_roic)
        n_rr      = _norm_reinvestment_rate(rr)
        n_shy     = _norm_shareholder_yield(sh_yield)
        n_dil     = _norm_dilution_rate(dilution)
        n_debt    = _norm_debt_trend(debt_trend)

        w = self._WEIGHTS
        raw = (
            n_spread * w["roic_spread"]       +
            n_iroic  * w["incremental_roic"]  +
            n_rr     * w["reinvestment_rate"] +
            n_shy    * w["shareholder_yield"] +
            n_dil    * w["dilution_rate"]     +
            n_debt   * w["debt_trend"]
        )

        score = round(_clamp(raw, 0, 100), 2)

        def _r(v: float | None, d: int = 4) -> float | None:
            return round(v, d) if v is not None else None

        return {
            "ticker":                   self.ticker,
            "roic":                     _r(roic, 2),
            "roic_spread":              _r(roic_spread, 2),
            "incremental_roic":         _r(incr_roic, 2),
            "reinvestment_rate":        _r(rr, 4),
            "reinvestment_method":      rr_method,
            "buyback_yield":            _r(buyback, 2),
            "dividend_yield_implied":   _r(div_yield, 2),
            "shareholder_yield":        _r(sh_yield, 2),
            "dilution_rate":            _r(dilution, 2),
            "debt_trend":               _r(debt_trend, 4),
            "capital_allocation_score": score,
            "signal":                   _signal(score),
            # scorer.py compatibility
            "total_score": score,
            "total_max":   100.0,
        }
