"""
Regime Model — P3 market condition classifier.

Classifies market regimes from SPY price history and applies portfolio-level
risk controls (position sizing overlays).

Assumptions:
  - Input is monthly price history (Date, Close columns) as used elsewhere.
  - "Daily" periods (20D, 50D, 200D, etc.) map to monthly bars:
      20D  ≈  1 month
      50D  ≈  3 months (rounded down)
      60D  ≈  3 months
      200D ≈ 10 months
      252D ≈ 12 months (1 year)
  - Volatility is annualised std dev of monthly log-returns.
  - Volatility percentile is computed against a rolling 36-month history
    (percentile within the lookback window itself when < 36 months available).
  - "10 trading days" maps to 2 monthly bars.
  - "5D return" maps to 1-month return (adjacent bar).
  - All price series expected newest-first OR oldest-first — we sort internally.

Outputs:
  {
    "market_trend_score":     float,    # 0-100
    "volatility_percentile":  float,    # 0-100
    "drawdown_depth":         float,    # % (negative = below peak)
    "regime":                 str,      # one of 6 regimes
    "risk_level":             str,      # NORMAL | ELEVATED | HIGH | CRISIS
    "risk_alert":             bool,
    "max_equity_exposure":    float,    # 0-1
    "regime_multiplier":      float,    # 0.5-1.1
    "sma_50":                 float | None,
    "sma_200":                float | None,
    "current_price":          float | None,
    "vol_20d":                float | None,   # annualised %
    "vol_60d":                float | None,   # annualised %
    "error":                  str | None,
  }
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd
from typing import Any


# ── Constants ─────────────────────────────────────────────────────────────────

# Monthly-bar equivalents
_BARS_20D  = 1
_BARS_50D  = 3
_BARS_60D  = 3
_BARS_200D = 10
_BARS_252D = 12   # rolling peak lookback (1 year)
_BARS_VOL_HISTORY = 36  # months for vol percentile baseline

# Regime labels
BULL_LOW_VOL  = "BULL_LOW_VOL"
BULL_HIGH_VOL = "BULL_HIGH_VOL"
BEAR_LOW_VOL  = "BEAR_LOW_VOL"
BEAR_HIGH_VOL = "BEAR_HIGH_VOL"
SIDEWAYS      = "SIDEWAYS"
CRISIS        = "CRISIS"

# Risk level thresholds (drawdown %)
_RISK_THRESHOLDS = [
    (-20.0, "CRISIS"),
    (-10.0, "HIGH"),
    (-5.0,  "ELEVATED"),
    (0.0,   "NORMAL"),
]

# Max equity exposure per risk level
_MAX_EXPOSURE = {
    "NORMAL":   1.00,
    "ELEVATED": 0.90,
    "HIGH":     0.70,
    "CRISIS":   0.40,
}

# Regime multipliers
_REGIME_MULTIPLIER = {
    BULL_LOW_VOL:  1.10,
    BULL_HIGH_VOL: 1.00,
    SIDEWAYS:      0.90,
    BEAR_LOW_VOL:  0.80,
    BEAR_HIGH_VOL: 0.65,
    CRISIS:        0.50,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(val: Any) -> float | None:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _sma(prices: np.ndarray, window: int) -> float | None:
    """Simple moving average over last `window` bars."""
    if len(prices) < window:
        return None
    return float(np.mean(prices[-window:]))


def _realized_vol(log_returns: np.ndarray, window: int) -> float | None:
    """Annualised realised vol from last `window` log-return bars (monthly → ×√12)."""
    if len(log_returns) < window:
        return None
    subset = log_returns[-window:]
    return float(np.std(subset, ddof=1)) * math.sqrt(12) * 100  # annualised %


def _vol_percentile(log_returns: np.ndarray, current_vol: float,
                    history_bars: int = _BARS_VOL_HISTORY) -> float:
    """
    Rank current_vol within a rolling window of historical vols.
    Each historical vol is computed over _BARS_20D (1 bar) rolling windows.
    """
    n = len(log_returns)
    if n < 2:
        return 50.0

    # Build a time series of monthly absolute returns as vol proxy
    abs_rets = np.abs(log_returns[-min(n, history_bars):])
    if len(abs_rets) == 0:
        return 50.0

    pct = float(np.sum(abs_rets < (current_vol / 100 / math.sqrt(12))) / len(abs_rets) * 100)
    return round(max(0.0, min(100.0, pct)), 2)


def _drawdown_from_peak(prices: np.ndarray, lookback: int = _BARS_252D) -> float:
    """Current drawdown (%) from rolling `lookback`-bar high."""
    if len(prices) < 1:
        return 0.0
    window = prices[-min(len(prices), lookback):]
    peak = float(np.max(window))
    current = float(prices[-1])
    if peak <= 0:
        return 0.0
    return round((current / peak - 1.0) * 100, 4)


def _risk_level(drawdown_pct: float) -> str:
    for threshold, level in _RISK_THRESHOLDS:
        if drawdown_pct <= threshold:
            return level
    return "NORMAL"


def _classify_regime(trend_score: float, vol_pct: float) -> str:
    if trend_score >= 60:
        return BULL_LOW_VOL if vol_pct < 50 else BULL_HIGH_VOL
    if trend_score <= 40:
        return BEAR_LOW_VOL if vol_pct < 50 else BEAR_HIGH_VOL
    return SIDEWAYS


# ── Main ──────────────────────────────────────────────────────────────────────

def score(price_hist: pd.DataFrame) -> dict:
    """
    Classify market regime from monthly price history.

    Args:
        price_hist: DataFrame with 'Date' and 'Close' columns (monthly bars).
                    Typically SPY history from api_fetcher.get_price_history().

    Returns:
        Dict with all regime outputs. See module docstring for schema.
    """
    def _empty(reason: str) -> dict:
        return {
            "market_trend_score":    None,
            "volatility_percentile": None,
            "drawdown_depth":        None,
            "regime":                None,
            "risk_level":            None,
            "risk_alert":            False,
            "max_equity_exposure":   1.0,
            "regime_multiplier":     1.0,
            "sma_50":                None,
            "sma_200":               None,
            "current_price":         None,
            "vol_20d":               None,
            "vol_60d":               None,
            "error":                 reason,
        }

    if price_hist is None or price_hist.empty:
        return _empty("No price history provided")

    hist = price_hist.copy()
    hist["Date"]  = pd.to_datetime(hist["Date"])
    hist["Close"] = pd.to_numeric(hist["Close"], errors="coerce")
    hist = hist.dropna(subset=["Close"])
    hist = hist[hist["Close"] > 0].sort_values("Date").reset_index(drop=True)

    if len(hist) < max(_BARS_50D, 6):
        return _empty(f"Insufficient price history ({len(hist)} bars, need ≥{_BARS_50D})")

    prices = hist["Close"].values.astype(float)
    log_rets = np.log(prices[1:] / prices[:-1])

    current_price = float(prices[-1])

    # ── Moving averages ───────────────────────────────────────────────────────
    sma_50  = _sma(prices, _BARS_50D)
    sma_200 = _sma(prices, _BARS_200D)

    # ── Trend score (3 binary signals → 0/33.33/66.67/100) ───────────────────
    trend_signals = 0
    if sma_200 is not None and current_price > sma_200:
        trend_signals += 1
    if sma_50 is not None and sma_200 is not None and sma_50 > sma_200:
        trend_signals += 1
    if sma_50 is not None and current_price > sma_50:
        trend_signals += 1

    max_signals = sum([
        sma_200 is not None,
        sma_50 is not None and sma_200 is not None,
        sma_50 is not None,
    ])
    trend_score = round((trend_signals / max_signals * 100) if max_signals > 0 else 50.0, 2)

    # ── Volatility ────────────────────────────────────────────────────────────
    vol_20d = _realized_vol(log_rets, _BARS_20D)
    vol_60d = _realized_vol(log_rets, _BARS_60D)

    # Use 20D vol as primary; fall back to 60D
    primary_vol = vol_20d if vol_20d is not None else vol_60d
    vol_pct = _vol_percentile(log_rets, primary_vol) if primary_vol is not None else 50.0

    # ── Drawdown ──────────────────────────────────────────────────────────────
    drawdown = _drawdown_from_peak(prices, _BARS_252D)

    # ── Crisis override (highest priority) ───────────────────────────────────
    crisis_override = (drawdown <= -25.0 and vol_pct >= 90.0)

    if crisis_override:
        regime    = CRISIS
        risk_lv   = "CRISIS"
    else:
        regime    = _classify_regime(trend_score, vol_pct)
        risk_lv   = _risk_level(drawdown)

    max_exposure      = _MAX_EXPOSURE[risk_lv]
    regime_multiplier = _REGIME_MULTIPLIER[regime]

    # ── Fast deterioration alert ──────────────────────────────────────────────
    risk_alert = False
    # 1. 5D return <= -7% (1 bar proxy)
    if len(prices) >= 2:
        ret_1m = (prices[-1] / prices[-2] - 1) * 100
        if ret_1m <= -7.0:
            risk_alert = True

    # 2. Vol percentile increases ≥30pts over 10 trading days (2 bars proxy)
    if not risk_alert and len(log_rets) >= 2 + _BARS_60D:
        old_vol_raw = _realized_vol(log_rets[:-2], _BARS_60D)
        if old_vol_raw is not None:
            old_vol_pct = _vol_percentile(log_rets[:-2], old_vol_raw)
            if (vol_pct - old_vol_pct) >= 30.0:
                risk_alert = True

    # 3. Drawdown worsens ≥5% over 10 trading days (2 bars proxy)
    if not risk_alert and len(prices) >= 3:
        old_dd = _drawdown_from_peak(prices[:-2], _BARS_252D)
        if (drawdown - old_dd) <= -5.0:
            risk_alert = True

    return {
        "market_trend_score":    trend_score,
        "volatility_percentile": vol_pct,
        "drawdown_depth":        drawdown,
        "regime":                regime,
        "risk_level":            risk_lv,
        "risk_alert":            risk_alert,
        "max_equity_exposure":   max_exposure,
        "regime_multiplier":     regime_multiplier,
        "sma_50":                round(sma_50, 4) if sma_50 is not None else None,
        "sma_200":               round(sma_200, 4) if sma_200 is not None else None,
        "current_price":         round(current_price, 4),
        "vol_20d":               round(vol_20d, 4) if vol_20d is not None else None,
        "vol_60d":               round(vol_60d, 4) if vol_60d is not None else None,
        "error":                 None,
    }
