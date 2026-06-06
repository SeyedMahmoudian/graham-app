"""
Risk & performance metrics from monthly price history.

Why this matters:
  A 20% return sounds great until you learn it came with 50% drawdowns and
  a Sharpe of 0.3. Risk-adjusted metrics reveal whether you are being
  compensated for the risk you are taking on.

Metrics computed:
  Volatility          Annualised std dev of monthly log-returns
  Beta                Covariance(stock, SPY) / Variance(SPY)
  Alpha               Jensen's alpha: annualised excess return vs CAPM
  Sharpe Ratio        (Annual return − risk-free) / Annual volatility
  Sortino Ratio       (Annual return − risk-free) / Downside deviation
  Max Drawdown        Worst peak-to-trough percentage decline (full history)
  Calmar Ratio        Annual return / |Max Drawdown| (higher = better)
  VaR 95%             5th-percentile monthly return (worst expected normal month)
  CVaR 95%            Mean of returns below VaR (expected loss in bad months)

Risk-free rate: 4.5% annual (approximate US 10yr yield as of 2025).
Change RISK_FREE_RATE if you want to wire in a live feed.

All DataFrames: columns 'Date' (str or datetime) and 'Close' (float).
"""

import math
import numpy as np
import pandas as pd

RISK_FREE_RATE  = 0.045          # annual; adjust to live 10yr yield if desired
MONTHS_PER_YEAR = 12


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def score(
    price_hist: pd.DataFrame,
    spy_hist: pd.DataFrame | None = None,
) -> dict:
    """
    Compute full risk/performance metrics.

    Args:
        price_hist: Monthly price history for the stock.
        spy_hist:   Monthly price history for SPY (optional; enables Beta/Alpha).

    Returns:
        Flat dict of metrics plus a 0-100 risk_score for use in enhanced_composite.
    """
    if price_hist is None or price_hist.empty or len(price_hist) < 6:
        return _empty("Insufficient price history (need ≥ 6 months)")

    # ── Prepare ───────────────────────────────────────────────────────────────
    hist = price_hist.copy()
    hist["Date"] = pd.to_datetime(hist["Date"])
    hist = hist.sort_values("Date").reset_index(drop=True)

    # Remove non-positive prices: log(0) = -inf, log(negative) = NaN
    hist = hist[hist["Close"] > 0].reset_index(drop=True)
    if len(hist) < 6:
        return _empty("Insufficient valid price data after removing non-positive prices")

    # Use log returns for better statistical properties
    hist["log_ret"] = np.log(hist["Close"] / hist["Close"].shift(1))
    returns = hist["log_ret"].dropna().values

    if len(returns) < 5:
        return _empty("Too few return observations")

    # ── Volatility ────────────────────────────────────────────────────────────
    vol_monthly = float(np.std(returns, ddof=1))
    vol_annual  = vol_monthly * math.sqrt(MONTHS_PER_YEAR)

    # ── Total & annualised return ─────────────────────────────────────────────
    n_months     = len(returns)
    n_years      = n_months / MONTHS_PER_YEAR
    total_return = float(hist["Close"].iloc[-1] / hist["Close"].iloc[0] - 1)
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0.0

    # ── Sharpe Ratio ──────────────────────────────────────────────────────────
    rf_monthly = RISK_FREE_RATE / MONTHS_PER_YEAR
    excess     = returns - rf_monthly
    std_excess = float(np.std(excess, ddof=1))
    sharpe = (float(np.mean(excess)) / std_excess * math.sqrt(MONTHS_PER_YEAR)
              if std_excess > 0 else None)

    # ── Sortino Ratio ─────────────────────────────────────────────────────────
    # Downside deviation uses total N as denominator (not downside-count only).
    # Formula: sqrt( Σ min(0, r − rf)² / N ) — Sortino & Price (1994).
    n_total = len(returns)
    downside_sq = np.minimum(returns - rf_monthly, 0.0) ** 2
    down_var = float(np.sum(downside_sq) / n_total)
    if down_var > 0:
        down_std = math.sqrt(down_var) * math.sqrt(MONTHS_PER_YEAR)
        sortino  = (annual_return - RISK_FREE_RATE) / down_std
    else:
        sortino = None

    # ── Max Drawdown ──────────────────────────────────────────────────────────
    # Robust version: works on price series (positive or negative)
    prices = hist["Close"].values.astype(float)
    if len(prices) == 0:
        max_drawdown = 0.0
    else:
        # Use running peak on equity curve; normalize handling for negatives
        peak = np.maximum.accumulate(prices)
        # Avoid div-by-zero / sign issues
        with np.errstate(divide='ignore', invalid='ignore'):
            drawdown = np.where(peak != 0, (prices - peak) / peak, 0.0)
            # For fully negative series, fall back to relative change from max
            if np.all(prices <= 0):
                drawdown = (prices - np.maximum.accumulate(prices)) / np.abs(np.maximum.accumulate(prices) + 1e-8)
        max_drawdown = float(np.min(drawdown))
    # ── Calmar Ratio ─────────────────────────────────────────────────────────
    calmar = (annual_return / abs(max_drawdown)
              if max_drawdown < -0.001 else None)

    # ── VaR & CVaR (95%) ─────────────────────────────────────────────────────
    var_95  = float(np.percentile(returns, 5))
    mask    = returns <= var_95
    cvar_95 = float(np.mean(returns[mask])) if np.any(mask) else var_95

    # ── Beta & Alpha (Jensen's, vs SPY) ──────────────────────────────────────
    beta = alpha = None
    spy_annual_ret = None

    if spy_hist is not None and not spy_hist.empty:
        spy = spy_hist.copy()
        spy["Date"]    = pd.to_datetime(spy["Date"])
        spy            = spy.sort_values("Date").reset_index(drop=True)
        spy["log_ret"] = np.log(spy["Close"] / spy["Close"].shift(1))

        merged = (
            hist[["Date", "log_ret"]].rename(columns={"log_ret": "stock"})
            .merge(spy[["Date", "log_ret"]].rename(columns={"log_ret": "spy"}),
                   on="Date", how="inner")
            .dropna()
        )

        if len(merged) >= 6:
            s = merged["stock"].values
            m = merged["spy"].values
            cov_sm = float(np.cov(s, m)[0, 1])
            var_m  = float(np.var(m, ddof=1))
            if var_m > 0:
                beta = cov_sm / var_m
                n_merged = len(m)
                spy_total = float(
                    spy["Close"].iloc[-1] / spy["Close"].iloc[0] - 1
                ) if len(spy) > 1 else 0.0
                spy_annual_ret = (1 + spy_total) ** (MONTHS_PER_YEAR / n_merged) - 1
                # Jensen's alpha: α = actual − [Rf + β(Rm − Rf)]
                alpha = annual_return - (
                    RISK_FREE_RATE + beta * (spy_annual_ret - RISK_FREE_RATE)
                )

    # ── Risk score (0-100) ────────────────────────────────────────────────────
    risk_result = _risk_score(
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        vol_annual=vol_annual,
        beta=beta,
    )

    return {
        # Returns
        "total_return":        round(total_return * 100, 2),
        "annual_return":       round(annual_return * 100, 2),
        "n_months":            n_months,
        "n_years":             round(n_years, 1),

        # Risk
        "volatility_monthly":  round(vol_monthly * 100, 2),
        "volatility_annual":   round(vol_annual  * 100, 2),
        "max_drawdown":        round(max_drawdown * 100, 2),
        "var_95":              round(var_95  * 100, 2),
        "cvar_95":             round(cvar_95 * 100, 2),

        # Risk-adjusted
        "sharpe":   round(sharpe,  3) if sharpe  is not None else None,
        "sortino":  round(sortino, 3) if sortino is not None else None,
        "calmar":   round(calmar,  3) if calmar  is not None else None,

        # Market sensitivity
        "beta":  round(beta,  3) if beta  is not None else None,
        "alpha": round(alpha * 100, 2) if alpha is not None else None,  # %/yr

        # Score for composite
        "risk_score":      risk_result["total_score"],
        "risk_score_max":  risk_result["total_max"],
        "risk_criteria":   risk_result["criteria"],

        "risk_free_rate": RISK_FREE_RATE,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Risk scoring (0-100) for composite integration
# ══════════════════════════════════════════════════════════════════════════════

def _risk_score(sharpe, sortino, max_drawdown, vol_annual, beta) -> dict:
    """Translate risk metrics into a 0-100 score (higher = better risk profile)."""
    criteria = []

    # Sharpe Ratio — 35 pts
    if sharpe is None:
        sh_s, sh_n = 15, "Insufficient data (neutral score)"
    elif sharpe >= 1.5:
        sh_s, sh_n = 35, f"Sharpe {sharpe:.2f} — exceptional risk-adjusted return"
    elif sharpe >= 1.0:
        sh_s, sh_n = 26, f"Sharpe {sharpe:.2f} — good"
    elif sharpe >= 0.5:
        sh_s, sh_n = 16, f"Sharpe {sharpe:.2f} — acceptable"
    elif sharpe >= 0:
        sh_s, sh_n = 6,  f"Sharpe {sharpe:.2f} — barely positive"
    else:
        sh_s, sh_n = 0,  f"Sharpe {sharpe:.2f} — negative risk-adjusted return"
    criteria.append({"label": "Sharpe Ratio", "score": sh_s, "max": 35, "note": sh_n})

    # Max Drawdown — 30 pts
    dd = max_drawdown * 100 if max_drawdown is not None else None
    if dd is None:
        dd_s, dd_n = 0, "N/A"
    elif dd >= -10:
        dd_s, dd_n = 30, f"Max drawdown {dd:.1f}% — highly resilient"
    elif dd >= -20:
        dd_s, dd_n = 20, f"Max drawdown {dd:.1f}% — moderate"
    elif dd >= -35:
        dd_s, dd_n = 10, f"Max drawdown {dd:.1f}% — significant"
    else:
        dd_s, dd_n = 0,  f"Max drawdown {dd:.1f}% — severe historical loss"
    criteria.append({"label": "Max Drawdown", "score": dd_s, "max": 30, "note": dd_n})

    # Annualised Volatility — 20 pts
    if vol_annual is None:
        v_s, v_n = 5, "N/A"
    elif vol_annual <= 0.15:
        v_s, v_n = 20, f"Vol {vol_annual*100:.1f}%/yr — low"
    elif vol_annual <= 0.25:
        v_s, v_n = 13, f"Vol {vol_annual*100:.1f}%/yr — moderate"
    elif vol_annual <= 0.40:
        v_s, v_n = 5,  f"Vol {vol_annual*100:.1f}%/yr — high"
    else:
        v_s, v_n = 0,  f"Vol {vol_annual*100:.1f}%/yr — very high"
    criteria.append({"label": "Annualised Volatility", "score": v_s, "max": 20, "note": v_n})

    # Beta vs Market — 15 pts (favour low-beta / defensive stocks)
    if beta is None:
        b_s, b_n = 7, "Beta unavailable — SPY history not loaded (neutral score)"
    elif beta <= 0.7:
        b_s, b_n = 15, f"Beta {beta:.2f} — low-sensitivity defensive stock"
    elif beta <= 1.0:
        b_s, b_n = 10, f"Beta {beta:.2f} — in line with market"
    elif beta <= 1.3:
        b_s, b_n = 5,  f"Beta {beta:.2f} — above-market sensitivity"
    else:
        b_s, b_n = 0,  f"Beta {beta:.2f} — highly market-sensitive / amplified swings"
    criteria.append({"label": "Market Beta", "score": b_s, "max": 15, "note": b_n})

    total     = sum(c["score"] for c in criteria)
    total_max = sum(c["max"]   for c in criteria)
    return {"total_score": total, "total_max": total_max, "criteria": criteria}


# ══════════════════════════════════════════════════════════════════════════════
# Empty result
# ══════════════════════════════════════════════════════════════════════════════

def _empty(reason: str) -> dict:
    return {
        "total_return": None, "annual_return": None,
        "n_months": 0, "n_years": 0,
        "volatility_monthly": None, "volatility_annual": None,
        "max_drawdown": None, "var_95": None, "cvar_95": None,
        "sharpe": None, "sortino": None, "calmar": None,
        "beta": None, "alpha": None,
        "risk_score": 50, "risk_score_max": 100, "risk_criteria": [],
        "risk_free_rate": RISK_FREE_RATE,
        "error": reason,
    }
