"""
Options Signal Engine (PROJECT_MAP.md — P4 Expansion)

Models short-horizon OPTION MARK-TO-MARKET MOVEMENT, NOT expiry payoff.

Combines:
  - CALL vs PUT directional bias (regime trend + price momentum)
  - IV regime classification (level + expansion/contraction trend)
  - Expected move sizing over a given horizon
  - Strike / expiry recommendation
  - Risk score (theta decay + IV level + liquidity)
  - Overall edge score

Dependencies (read-only — this module fetches nothing itself):
  - price_hist: monthly price history DataFrame (Date, Close), e.g. from
    codes.portfolio._load_history / api_fetcher.get_price_history
  - regime_result: output of codes.models.regime.score()
  - risk_result: output of codes.models.risk_metrics.score()

All inputs are optional. Missing data degrades to neutral defaults rather
than raising — consistent with the rest of the P1-P4 model suite.
"""

import math
import numpy as np
import pandas as pd

MONTHS_PER_YEAR = 12

SIGNAL_THRESHOLDS = [
    (60, "STRONG_EDGE"),
    (40, "WATCH"),
    (0,  "AVOID"),
]


# ══════════════════════════════════════════════════════════════════════════════
# Pure helpers
# ══════════════════════════════════════════════════════════════════════════════

def _clip(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def _norm_momentum(ret_pct: float | None) -> float | None:
    """Map a return (e.g. +0.10 = +10%) to a 0-100 score. ±20% -> 100/0."""
    if ret_pct is None:
        return None
    return _clip((ret_pct + 0.20) / 0.40 * 100.0)


def calc_momentum(price_hist: pd.DataFrame | None, lookback_months: int = 3) -> float | None:
    """Total return over the last `lookback_months` rows of monthly closes."""
    if price_hist is None or price_hist.empty or len(price_hist) < lookback_months + 1:
        return None
    closes = price_hist["Close"].astype(float).values
    start, end = closes[-(lookback_months + 1)], closes[-1]
    if start <= 0:
        return None
    return (end / start) - 1.0


def calc_monthly_volatility(price_hist: pd.DataFrame | None) -> float | None:
    """Std dev of monthly log returns."""
    if price_hist is None or price_hist.empty or len(price_hist) < 3:
        return None
    closes = price_hist["Close"].astype(float)
    closes = closes[closes > 0]
    if len(closes) < 3:
        return None
    log_rets = np.log(closes / closes.shift(1)).dropna().values
    if len(log_rets) < 2:
        return None
    return float(np.std(log_rets, ddof=1))


def _signal(score: float) -> str:
    for threshold, sig in SIGNAL_THRESHOLDS:
        if score >= threshold:
            return sig
    return "AVOID"


# ══════════════════════════════════════════════════════════════════════════════
# OptionsSignalEngine
# ══════════════════════════════════════════════════════════════════════════════

class OptionsSignalEngine:
    """
    Args:
        ticker:        stock symbol
        price_hist:    monthly Date/Close DataFrame (optional)
        regime_result: codes.models.regime.score() output (optional)
        risk_result:   codes.models.risk_metrics.score() output (optional)
        current_price: latest price (optional; falls back to last Close)
    """

    def __init__(self, ticker: str, price_hist: pd.DataFrame | None = None,
                 regime_result: dict | None = None, risk_result: dict | None = None,
                 current_price: float | None = None):
        self.ticker = ticker.upper().strip()
        self.price_hist = price_hist
        self.regime_result = regime_result or {}
        self.risk_result = risk_result or {}

        if current_price is not None:
            self.current_price = current_price
        elif price_hist is not None and not price_hist.empty:
            self.current_price = float(price_hist["Close"].iloc[-1])
        else:
            self.current_price = None

    # ── Directional bias ────────────────────────────────────────────────────

    def calc_directional_bias(self) -> tuple[str, float]:
        """
        Returns (bias, confidence) where bias is CALL / PUT / NEUTRAL and
        confidence is 0-100. Combines regime trend score (60%) and recent
        price momentum (40%); reweights if one input is missing.
        """
        trend_score = self.regime_result.get("market_trend_score")
        mom_score = _norm_momentum(calc_momentum(self.price_hist))

        parts = []
        if trend_score is not None:
            parts.append((trend_score, 0.6))
        if mom_score is not None:
            parts.append((mom_score, 0.4))

        if not parts:
            return "NEUTRAL", 0.0

        total_w = sum(w for _, w in parts)
        combined = sum(v * w for v, w in parts) / total_w

        if combined >= 60:
            bias = "CALL"
        elif combined <= 40:
            bias = "PUT"
        else:
            bias = "NEUTRAL"

        confidence = round(_clip(abs(combined - 50) * 2), 1)
        return bias, confidence

    # ── IV regime ────────────────────────────────────────────────────────────

    def calc_iv_regime(self) -> tuple[str, str]:
        """
        Returns (iv_level, iv_trend).
          iv_level: HIGH / NORMAL / LOW / UNKNOWN  (from volatility percentile)
          iv_trend: EXPANDING / CONTRACTING / STABLE / UNKNOWN (vol_20d vs vol_60d)
        """
        vol_pct = self.regime_result.get("volatility_percentile")
        if vol_pct is None:
            iv_level = "UNKNOWN"
        elif vol_pct >= 75:
            iv_level = "HIGH"
        elif vol_pct <= 25:
            iv_level = "LOW"
        else:
            iv_level = "NORMAL"

        vol_20d = self.regime_result.get("vol_20d")
        vol_60d = self.regime_result.get("vol_60d")
        if vol_20d is not None and vol_60d is not None and vol_60d > 0:
            ratio = vol_20d / vol_60d
            if ratio >= 1.15:
                iv_trend = "EXPANDING"
            elif ratio <= 0.85:
                iv_trend = "CONTRACTING"
            else:
                iv_trend = "STABLE"
        else:
            iv_trend = "UNKNOWN"

        return iv_level, iv_trend

    # ── Expected move ────────────────────────────────────────────────────────

    def calc_expected_move(self, horizon_days: int = 30) -> tuple[float | None, float | None]:
        """
        Returns (expected_move_pct, expected_move_dollar) for `horizon_days`,
        scaled from monthly volatility via sqrt-time. None if vol unavailable.
        """
        monthly_vol = calc_monthly_volatility(self.price_hist)
        if monthly_vol is None:
            return None, None
        months = horizon_days / 30.0
        move_pct = monthly_vol * math.sqrt(months)
        move_dollar = (self.current_price * move_pct
                        if self.current_price is not None else None)
        return round(move_pct, 4), (round(move_dollar, 2) if move_dollar is not None else None)

    # ── Strike / expiry recommendation ──────────────────────────────────────

    def recommend_strike_expiry(self, bias: str, move_pct: float | None,
                                 horizon_days: int = 30) -> dict:
        """
        Slightly-OTM strike in the direction of the bias, sized at half the
        expected move. ATM if NEUTRAL or inputs unavailable.
        """
        if self.current_price is None or move_pct is None or bias == "NEUTRAL":
            strike = self.current_price
        elif bias == "CALL":
            strike = self.current_price * (1 + 0.5 * move_pct)
        else:  # PUT
            strike = self.current_price * (1 - 0.5 * move_pct)

        return {
            "strike": round(strike, 2) if strike is not None else None,
            "expiry_days": horizon_days,
        }

    # ── Risk score (theta + IV + liquidity) ─────────────────────────────────

    def calc_risk_score(self, iv_level: str, horizon_days: int = 30) -> float:
        """0-100, higher = riskier for an option buyer."""
        if horizon_days <= 14:
            theta_risk = 80
        elif horizon_days <= 45:
            theta_risk = 50
        else:
            theta_risk = 25

        iv_risk = {"HIGH": 80, "NORMAL": 50, "LOW": 20}.get(iv_level, 50)

        # No live options-chain data source — neutral placeholder.
        liquidity_risk = 50

        return round((theta_risk + iv_risk + liquidity_risk) / 3, 1)

    # ── Edge score ───────────────────────────────────────────────────────────

    def calc_edge_score(self, bias_confidence: float, iv_level: str, iv_trend: str) -> float:
        """
        0-100. Directional confidence scaled by IV favorability — buying
        options is more attractive when IV is LOW / CONTRACTING (cheap
        premium) and less attractive when IV is HIGH / EXPANDING.
        """
        if iv_level == "LOW" or iv_trend == "CONTRACTING":
            factor = 1.2
        elif iv_level == "HIGH" or iv_trend == "EXPANDING":
            factor = 0.7
        else:
            factor = 1.0

        return round(_clip(bias_confidence * factor), 1)

    # ── Main entry point ─────────────────────────────────────────────────────

    def get_options_signal(self, horizon_days: int = 30) -> dict:
        bias, confidence = self.calc_directional_bias()
        iv_level, iv_trend = self.calc_iv_regime()
        move_pct, move_dollar = self.calc_expected_move(horizon_days)
        strike_info = self.recommend_strike_expiry(bias, move_pct, horizon_days)
        risk_score = self.calc_risk_score(iv_level, horizon_days)
        edge_score = self.calc_edge_score(confidence, iv_level, iv_trend)

        if bias == "NEUTRAL":
            signal = "NO_TRADE"
        else:
            sig = _signal(edge_score)
            signal = f"BUY_{bias}" if sig == "STRONG_EDGE" else sig

        return {
            "ticker": self.ticker,
            "bias": bias,
            "bias_confidence": confidence,
            "iv_level": iv_level,
            "iv_trend": iv_trend,
            "expected_move_pct": move_pct,
            "expected_move_dollar": move_dollar,
            "recommended_strike": strike_info["strike"],
            "recommended_expiry_days": strike_info["expiry_days"],
            "risk_score": risk_score,
            "edge_score": edge_score,
            "signal": signal,
            "total_score": edge_score,
            "total_max": 100.0,
        }


def get_options_signal(ticker: str, price_hist: pd.DataFrame | None = None,
                        regime_result: dict | None = None, risk_result: dict | None = None,
                        current_price: float | None = None, horizon_days: int = 30) -> dict:
    """Module-level convenience wrapper (mirrors other P4 model entry points)."""
    return OptionsSignalEngine(
        ticker, price_hist, regime_result, risk_result, current_price
    ).get_options_signal(horizon_days)
