"""
Factor Momentum Model — P4 module.

Combines price momentum (3M/6M/12M returns) with fundamental momentum
(earnings trend, ROIC trend) into a single 0-100 score.

Metrics & weights:
  3-Month Return     20%  — short-term price momentum
  6-Month Return     25%  — medium-term price momentum
  12-Month Return    25%  — long-term price momentum
  Earnings Momentum  15%  — trend in EPS over recent years
  ROIC Trend Slope   15%  — direction of capital-efficiency over recent years

Signal mapping:
  >= 70  → Bullish
  40-69  → Neutral
  < 40   → Bearish

Output schema:
  {
    "ticker":               str,
    "return_3m":            float | None,
    "return_6m":            float | None,
    "return_12m":           float | None,
    "earnings_momentum":    float | None,   # % slope per year
    "roic_trend_slope":     float | None,   # pp per year
    "factor_momentum_score": float,         # 0-100
    "signal":               str,            # Bullish | Neutral | Bearish
    "total_score":          float,          # scorer.py compat
    "total_max":            float,          # 100.0
  }

Integration:
  from codes.models.factor_momentum import FactorMomentumAnalyzer
  result = FactorMomentumAnalyzer(ticker, price_hist, sec_facts).get_factor_momentum_score()
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val: Any) -> float | None:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
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


def _linear_slope(values: list) -> float | None:
    """
    OLS slope of a chronological (oldest→newest) sequence, normalised by |mean|.
    Returns % change per period; positive = rising trend.
    """
    n = len(values)
    if n < 2:
        return None
    mean_v = sum(values) / n
    if abs(mean_v) < 1e-10:
        return None
    x_mean = (n - 1) / 2.0
    num = sum((i - x_mean) * (v - mean_v) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den < 1e-10:
        return None
    return (num / den) / abs(mean_v) * 100.0


# ── Signal mapping ────────────────────────────────────────────────────────────

def _signal(score: float) -> str:
    if score >= 70:
        return "Bullish"
    if score >= 40:
        return "Neutral"
    return "Bearish"


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _norm_return(pct: float | None, excellent: float = 20.0, floor: float = -20.0) -> float:
    """Linear map of a return % to 0-100. None → neutral 50."""
    if pct is None:
        return 50.0
    span = excellent - floor
    if span <= 0:
        return 100.0 if pct >= excellent else 0.0
    return _clamp((pct - floor) / span * 100.0, 0.0, 100.0)


def _norm_earnings_momentum(pct: float | None, excellent: float = 10.0, floor: float = -10.0) -> float:
    """Linear map of EPS trend slope (%/yr) to 0-100. None → neutral 50."""
    if pct is None:
        return 50.0
    span = excellent - floor
    if span <= 0:
        return 100.0 if pct >= excellent else 0.0
    return _clamp((pct - floor) / span * 100.0, 0.0, 100.0)


def _norm_roic_trend(slope_pp: float | None, excellent: float = 3.0, floor: float = -3.0) -> float:
    """Linear map of ROIC trend slope (pp/yr) to 0-100. None → neutral 50."""
    if slope_pp is None:
        return 50.0
    span = excellent - floor
    if span <= 0:
        return 100.0 if slope_pp >= excellent else 0.0
    return _clamp((slope_pp - floor) / span * 100.0, 0.0, 100.0)


# ── Class ─────────────────────────────────────────────────────────────────────

class FactorMomentumAnalyzer:
    """
    Compute factor momentum score combining price and fundamental trends.

    Args:
        ticker:     Stock ticker (stored in output).
        price_hist: Monthly price history DataFrame (Date, Close columns).
                    May be None / empty — returns metrics default to None.
        financials: sec_facts dict as returned by sec_data.fetch_company_facts().
                    May be None / empty — fundamental metrics default to None.
    """

    _WEIGHTS = {
        "return_3m":         0.20,
        "return_6m":         0.25,
        "return_12m":        0.25,
        "earnings_momentum": 0.15,
        "roic_trend":        0.15,
    }

    def __init__(self, ticker: str, price_hist: "pd.DataFrame | None" = None,
                 financials: dict | None = None) -> None:
        self.ticker = ticker.upper().strip()
        self._f = financials or {}

        self._hist = None
        if price_hist is not None and not price_hist.empty:
            hist = price_hist.copy()
            hist["Date"] = pd.to_datetime(hist["Date"])
            hist = hist.sort_values("Date").reset_index(drop=True)
            self._hist = hist

    # ── Price momentum ────────────────────────────────────────────────────────

    def _return_n_months(self, n: int) -> float | None:
        """
        Return % from `n` monthly bars ago to the latest bar.
        Mirrors the index convention used in momentum.py (iloc[-n]).
        Requires at least n bars of history.
        """
        if self._hist is None or len(self._hist) < n:
            return None
        current = self._hist["Close"].iloc[-1]
        past    = self._hist["Close"].iloc[-n]
        if not past or past <= 0:
            return None
        return (current - past) / past * 100.0

    def calc_return_3m(self) -> float | None:
        return self._return_n_months(3)

    def calc_return_6m(self) -> float | None:
        return self._return_n_months(6)

    def calc_return_12m(self) -> float | None:
        return self._return_n_months(12)

    # ── Earnings momentum ─────────────────────────────────────────────────────

    def calc_earnings_momentum(self) -> float | None:
        """
        Trend slope (%/yr) of EPS over up to the last 5 fiscal years.
        Positive = accelerating earnings; negative = decelerating.
        Requires at least 3 years of EPS data.
        """
        eps_vals = _values(self._f.get("eps", []), n=5)  # newest-first
        if len(eps_vals) < 3:
            return None
        chronological = list(reversed(eps_vals))  # oldest → newest
        return _linear_slope(chronological)

    # ── ROIC trend ────────────────────────────────────────────────────────────

    def _roic_series(self, n: int = 5) -> list[float]:
        """
        ROIC (%) for up to n most-recent years, oldest → newest.
        ROIC = NOPAT / Invested Capital, same methodology as profitability.py.
        """
        op_inc_vals  = _values(self._f.get("op_income", []), n=n)
        net_inc_vals = _values(self._f.get("net_inc",   []), n=n)
        equity_vals  = _values(self._f.get("equity",    []), n=n)
        lt_debt_vals = _values(self._f.get("lt_debt",   []), n=n)
        cash_vals    = _values(self._f.get("cash",      []), n=n)

        pairs = min(len(op_inc_vals), len(equity_vals))
        series = []
        for i in range(pairs):
            oi = op_inc_vals[i]
            eq = equity_vals[i]
            ni = net_inc_vals[i] if i < len(net_inc_vals) else None
            ld = lt_debt_vals[i] if i < len(lt_debt_vals) else None
            ca = cash_vals[i]    if i < len(cash_vals)    else None

            if ni is not None and oi > 0:
                tax_rate = _clamp(1.0 - ni / oi, 0.0, 0.50)
            else:
                tax_rate = 0.21
            nopat = oi * (1.0 - tax_rate)

            net_debt = (ld or 0.0) - (ca or 0.0)
            ic = eq + max(net_debt, 0.0)
            if ic <= 0:
                continue
            series.append(nopat / ic * 100.0)

        return list(reversed(series))  # oldest → newest

    def calc_roic_trend_slope(self) -> float | None:
        """
        Slope (percentage points / yr) of ROIC over up to 5 years.
        Requires at least 3 years of usable ROIC data.
        """
        series = self._roic_series(n=5)
        if len(series) < 3:
            return None
        n = len(series)
        mean_v = sum(series) / n
        x_mean = (n - 1) / 2.0
        num = sum((i - x_mean) * (v - mean_v) for i, v in enumerate(series))
        den = sum((i - x_mean) ** 2 for i in range(n))
        if den < 1e-10:
            return None
        return num / den  # pp per year (un-normalised slope is already in pp)

    # ── Composite score ───────────────────────────────────────────────────────

    def get_factor_momentum_score(self) -> dict:
        """
        Compute and return a strict JSON-compatible dict.

        Missing metrics default to a neutral 50 normalised sub-score so the
        composite remains finite even with sparse data.
        """
        r3m  = self.calc_return_3m()
        r6m  = self.calc_return_6m()
        r12m = self.calc_return_12m()
        em   = self.calc_earnings_momentum()
        rts  = self.calc_roic_trend_slope()

        n_r3m  = _norm_return(r3m)
        n_r6m  = _norm_return(r6m)
        n_r12m = _norm_return(r12m)
        n_em   = _norm_earnings_momentum(em)
        n_rts  = _norm_roic_trend(rts)

        w = self._WEIGHTS
        raw = (
            n_r3m  * w["return_3m"]         +
            n_r6m  * w["return_6m"]         +
            n_r12m * w["return_12m"]        +
            n_em   * w["earnings_momentum"] +
            n_rts  * w["roic_trend"]
        )

        factor_momentum_score = round(_clamp(raw, 0.0, 100.0), 2)

        def _r(v: float | None, decimals: int = 4) -> float | None:
            return round(v, decimals) if v is not None else None

        return {
            "ticker":                self.ticker,
            "return_3m":             _r(r3m,  2),
            "return_6m":             _r(r6m,  2),
            "return_12m":            _r(r12m, 2),
            "earnings_momentum":     _r(em,   4),
            "roic_trend_slope":      _r(rts,  4),
            "factor_momentum_score": factor_momentum_score,
            "signal":                _signal(factor_momentum_score),
            # scorer.py compatibility
            "total_score": factor_momentum_score,
            "total_max":   100.0,
        }