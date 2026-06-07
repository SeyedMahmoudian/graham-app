"""
Earnings Revision Model — forward momentum signal.

Priority: P1 per PROJECT_MAP.md

Empirical basis: stocks with rising EPS estimates outperform those with
falling estimates by ~7-10%/yr.
  Chan, Jegadeesh & Lakonishok (1996) "Momentum Strategies"
  Elton, Gruber & Gultekin (1984)    "Professional Expectations"

Output schema (per PROJECT_MAP.md):
  {
    'ticker':                str,
    'eps_revision_30d':      float | None,   # % change in EPS consensus (30d proxy)
    'eps_revision_90d':      float | None,   # % change in EPS consensus (90d proxy)
    'revenue_revision_30d':  float | None,   # % change in revenue consensus (30d proxy)
    'earnings_surprise_avg': float | None,   # avg actual vs estimate % (4Q)
    'revision_breadth':      float | None,   # analyst sentiment [-1, +1]
    'forward_momentum_score':float,          # 0-100 weighted composite
    'signal':                str,            # STRONG_UP|UP|NEUTRAL|DOWN|STRONG_DOWN
    'low_coverage':          bool,
    'total_score':           float,          # scorer.py compat (= forward_momentum_score)
    'total_max':             int,            # 100
    'criteria':              list[dict],
  }

Scoring weights (100 pts total):
  EPS revision 30d       35 pts
  EPS revision 90d       25 pts
  Revenue revision 30d   20 pts
  Earnings surprise avg  20 pts

Signal thresholds (forward_momentum_score 0-100):
  >= 75  STRONG_UP
  60-74  UP
  40-59  NEUTRAL
  25-39  DOWN
  <  25  STRONG_DOWN

Data source: Finnhub SDK (FINNHUB_API_KEY env var; already wired in api_fetcher).
  earnings_surprises()       -> quarterly actual vs estimate history
  eps_estimates()            -> forward quarterly EPS consensus
  revenue_estimates()        -> forward quarterly revenue consensus
  recommendation_trends()    -> monthly analyst buy/sell breadth

  ⚠️  FREE TIER NOTE: earnings_surprises, eps_estimates, revenue_estimates,
  and recommendation_trends are NOT available on the Finnhub free plan.
  These methods are absent from the free SDK client entirely (not rate-limited).
  All four fetchers will return empty results silently.  The model still runs
  but all four components default to neutral (50 pts each → score = 50.0,
  signal = NEUTRAL).  To unlock this model upgrade to a paid Finnhub plan,
  or replace the data source with Alpha Vantage EARNINGS endpoint (free, 25/day).

Note on revision proxies:
  True point-in-time revision (Q2-2025 consensus on March 1 vs April 1) requires
  FMP or Polygon paid tier.  We use two valid proxies:
    (a) Slope of consecutive EPS estimates across forward periods
    (b) Trend in earnings surprise % (improving beats => upward revision signal)
  Both are empirically correlated with future returns (SUE literature).
"""

import math
from typing import Optional

from ..data import api_fetcher as _av


# ── Constants ─────────────────────────────────────────────────────────────────

SIGNAL_THRESHOLDS: list[tuple[int, str]] = [
    (75, "STRONG_UP"),
    (60, "UP"),
    (40, "NEUTRAL"),
    (25, "DOWN"),
    (0,  "STRONG_DOWN"),
]

# Finnhub paid-tier methods absent from free SDK client.
# Checked once at module load via hasattr() in each fetcher.
_FH_PAID_METHODS = {
    "earnings_surprises",
    "eps_estimates",
    "revenue_estimates",
    "recommendation_trends",
}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _safe(val) -> Optional[float]:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _pct_change(old, new) -> Optional[float]:
    """Percentage change; returns None when old ≈ 0."""
    try:
        o, n = float(old), float(new)
        if abs(o) < 1e-10:
            return None
        return (n - o) / abs(o) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _sigmoid_score(value: float, center: float = 0.0, scale: float = 5.0) -> float:
    """Map a value to 0-100 via logistic sigmoid. center=0 => 0% revision => 50."""
    z = (value - center) / scale
    return 100.0 / (1.0 + math.exp(-z))


def _linear_slope(values: list) -> Optional[float]:
    """
    OLS slope of a sequence, normalised by |mean|.
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


def _filter_outliers(values: list, k: float = 3.0) -> list:
    """
    Remove values more than k * MAD from the median.
    MAD (median absolute deviation) is robust to single large outliers,
    unlike σ which gets inflated by the very value being tested.
    Scale factor 1.4826 makes MAD consistent with σ for normal distributions.
    """
    n = len(values)
    if n <= 2:
        return values
    sorted_vals = sorted(values)
    mid = n // 2
    median = sorted_vals[mid] if n % 2 else (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    deviations = sorted(abs(v - median) for v in sorted_vals)
    mad = deviations[n // 2] if n % 2 else (deviations[mid - 1] + deviations[mid]) / 2.0
    if mad < 1e-10:
        return values
    threshold = k * mad * 1.4826
    return [v for v in values if abs(v - median) <= threshold]


# ── Finnhub paid-tier availability check ──────────────────────────────────────

def _fh_method_available(method_name: str) -> bool:
    """
    Return True only if the Finnhub client exists AND the method is present
    on it.  On the free SDK these four methods are absent entirely —
    hasattr() is the correct guard; trying and catching AttributeError
    produces noisy log output on every call.
    """
    return (
        _av._fh_client is not None
        and hasattr(_av._fh_client, method_name)
    )


# ── Data fetchers (wrap Finnhub SDK, return [] on any error) ─────────────────

def _fetch_earnings_surprises(symbol: str) -> list:
    """Quarterly earnings surprises [{period, actual, estimate, surprise_pct}] newest-first."""
    if not _fh_method_available("earnings_surprises"):
        return []
    try:
        raw = _av._fh_client.earnings_surprises(symbol) or []
        results = []
        for item in raw:
            actual   = _safe(item.get("actual"))
            estimate = _safe(item.get("estimate"))
            if actual is None or estimate is None:
                continue
            results.append({
                "period":       item.get("period", ""),
                "actual":       actual,
                "estimate":     estimate,
                "surprise_pct": _pct_change(estimate, actual),
            })
        return sorted(results, key=lambda x: x["period"], reverse=True)
    except Exception as e:
        print(f"  [EarningsRevision] earnings_surprises error for {symbol}: {e}")
        return []


def _fetch_eps_estimates(symbol: str) -> list:
    """Forward quarterly EPS consensus [{period, eps_avg, n_analyst}] newest-first."""
    if not _fh_method_available("eps_estimates"):
        return []
    try:
        data  = _av._fh_client.eps_estimates(symbol, freq="quarterly") or {}
        items = data.get("data") or []
        results = []
        for item in items:
            avg = _safe(item.get("epsAvg"))
            if avg is None:
                continue
            results.append({
                "period":    item.get("period", ""),
                "eps_avg":   avg,
                "eps_high":  _safe(item.get("epsHigh")),
                "eps_low":   _safe(item.get("epsLow")),
                "n_analyst": _safe(item.get("numberAnalysts")),
            })
        return sorted(results, key=lambda x: x["period"], reverse=True)
    except Exception as e:
        print(f"  [EarningsRevision] eps_estimates error for {symbol}: {e}")
        return []


def _fetch_revenue_estimates(symbol: str) -> list:
    """Forward quarterly revenue consensus [{period, rev_avg, n_analyst}] newest-first."""
    if not _fh_method_available("revenue_estimates"):
        return []
    try:
        data  = _av._fh_client.revenue_estimates(symbol, freq="quarterly") or {}
        items = data.get("data") or []
        results = []
        for item in items:
            avg = _safe(item.get("revenueAvg"))
            if avg is None:
                continue
            results.append({
                "period":    item.get("period", ""),
                "rev_avg":   avg,
                "rev_high":  _safe(item.get("revenueHigh")),
                "rev_low":   _safe(item.get("revenueLow")),
                "n_analyst": _safe(item.get("numberAnalysts")),
            })
        return sorted(results, key=lambda x: x["period"], reverse=True)
    except Exception as e:
        print(f"  [EarningsRevision] revenue_estimates error for {symbol}: {e}")
        return []


def _fetch_recommendation_trends(symbol: str) -> list:
    """Monthly analyst recommendation snapshots [{period, strong_buy, buy, hold, sell, strong_sell}] newest-first."""
    if not _fh_method_available("recommendation_trends"):
        return []
    try:
        raw = _av._fh_client.recommendation_trends(symbol) or []
        results = []
        for item in raw:
            results.append({
                "period":      item.get("period", ""),
                "strong_buy":  int(_safe(item.get("strongBuy",  0)) or 0),
                "buy":         int(_safe(item.get("buy",        0)) or 0),
                "hold":        int(_safe(item.get("hold",       0)) or 0),
                "sell":        int(_safe(item.get("sell",       0)) or 0),
                "strong_sell": int(_safe(item.get("strongSell", 0)) or 0),
            })
        return sorted(results, key=lambda x: x["period"], reverse=True)
    except Exception as e:
        print(f"  [EarningsRevision] recommendation_trends error for {symbol}: {e}")
        return []


# ── Metric calculators (pure — no I/O, fully testable) ───────────────────────

def calc_eps_revision(eps_estimates: list, lookback_periods: int = 1) -> Optional[float]:
    """
    EPS revision proxy: % change from the estimate N periods ago to the most
    recent estimate across the available forward consensus series.

    lookback_periods=1 -> 30d proxy (adjacent quarter comparison)
    lookback_periods=3 -> 90d proxy (trend across 4 periods via slope)

    Positive = analysts have raised forward EPS expectations.
    """
    values = [d["eps_avg"] for d in eps_estimates if d.get("eps_avg") is not None]
    if len(values) < 2:
        return None
    if lookback_periods <= 1:
        return _pct_change(values[1], values[0])
    # Longer window: fit a slope over the available series (oldest → newest)
    chronological = list(reversed(values[:min(8, len(values))]))
    return _linear_slope(chronological)


def calc_revenue_revision(rev_estimates: list, lookback_periods: int = 1) -> Optional[float]:
    """Revenue revision proxy — same approach as EPS revision."""
    values = [d["rev_avg"] for d in rev_estimates if d.get("rev_avg") is not None]
    if len(values) < 2:
        return None
    if lookback_periods <= 1:
        return _pct_change(values[1], values[0])
    chronological = list(reversed(values[:min(8, len(values))]))
    return _linear_slope(chronological)


def calc_earnings_surprise_avg(surprises: list, n_quarters: int = 4) -> Optional[float]:
    """
    Average earnings surprise % over last N quarters, outliers filtered at 3σ.
    Positive = company consistently beats estimates (implicit upward revision signal).
    """
    raw = [s["surprise_pct"] for s in surprises[:n_quarters]
           if s.get("surprise_pct") is not None]
    if not raw:
        return None
    filtered = _filter_outliers(raw, k=3.0)
    return sum(filtered) / len(filtered) if filtered else None


def calc_revision_breadth(rec_trends: list) -> Optional[float]:
    """
    Net analyst sentiment ∈ [-1, +1].
    Uses change in bull/bear ratio between the two most recent monthly snapshots.
    Per PROJECT_MAP: skip when low_coverage (n_analyst == 1).
    """
    if not rec_trends:
        return None
    if len(rec_trends) == 1:
        r = rec_trends[0]
        total = r["strong_buy"] + r["buy"] + r["hold"] + r["sell"] + r["strong_sell"]
        if total == 0:
            return None
        return (r["strong_buy"] + r["buy"] - r["sell"] - r["strong_sell"]) / total
    curr, prev = rec_trends[0], rec_trends[1]
    total = max(curr["strong_buy"] + curr["buy"] + curr["hold"] +
                curr["sell"] + curr["strong_sell"], 1)
    delta_bull = (curr["strong_buy"] + curr["buy"]) - (prev["strong_buy"] + prev["buy"])
    delta_bear = (curr["sell"] + curr["strong_sell"]) - (prev["sell"] + prev["strong_sell"])
    return max(-1.0, min(1.0, (delta_bull - delta_bear) / total))


# ── Component scorers (sigmoid-based, return (score, note)) ──────────────────

def _score_eps_revision_30d(rev_pct: Optional[float]) -> tuple:
    max_pts = 35
    if rev_pct is None:
        return max_pts * 0.5, "Analyst estimate data unavailable (neutral)"
    s = _sigmoid_score(rev_pct, center=0.0, scale=4.0) / 100.0 * max_pts
    if rev_pct >= 5:
        note = f"EPS consensus trending up {rev_pct:+.1f}% — strong bullish momentum"
    elif rev_pct >= 2:
        note = f"EPS consensus trending up {rev_pct:+.1f}% — positive"
    elif rev_pct >= -2:
        note = f"EPS consensus {rev_pct:+.1f}% — roughly flat"
    elif rev_pct >= -5:
        note = f"EPS consensus trending down {rev_pct:+.1f}% — negative"
    else:
        note = f"EPS consensus trending down {rev_pct:+.1f}% — strong bearish revision"
    return round(s, 1), note


def _score_eps_revision_90d(rev_pct: Optional[float]) -> tuple:
    max_pts = 25
    if rev_pct is None:
        return max_pts * 0.5, "Analyst estimate data unavailable (neutral)"
    s = _sigmoid_score(rev_pct, center=0.0, scale=5.0) / 100.0 * max_pts
    if rev_pct >= 5:
        note = f"90d EPS trend {rev_pct:+.1f}% — sustained upward revision"
    elif rev_pct >= 1:
        note = f"90d EPS trend {rev_pct:+.1f}% — positive"
    elif rev_pct >= -1:
        note = f"90d EPS trend {rev_pct:+.1f}% — flat"
    else:
        note = f"90d EPS trend {rev_pct:+.1f}% — downward"
    return round(s, 1), note


def _score_revenue_revision(rev_pct: Optional[float]) -> tuple:
    max_pts = 20
    if rev_pct is None:
        return max_pts * 0.5, "Revenue estimate data unavailable (neutral)"
    s = _sigmoid_score(rev_pct, center=0.0, scale=3.0) / 100.0 * max_pts
    if rev_pct >= 3:
        note = f"Revenue consensus up {rev_pct:+.1f}% — analysts raising bar"
    elif rev_pct >= 1:
        note = f"Revenue consensus up {rev_pct:+.1f}%"
    elif rev_pct >= -1:
        note = f"Revenue consensus {rev_pct:+.1f}% — flat"
    else:
        note = f"Revenue consensus down {rev_pct:+.1f}%"
    return round(s, 1), note


def _score_earnings_surprise(surp_avg: Optional[float]) -> tuple:
    max_pts = 20
    if surp_avg is None:
        return max_pts * 0.5, "No earnings surprise history (neutral)"
    # Centre at +2%: companies that beat by 2% get 50 pts (meets bar)
    s = _sigmoid_score(surp_avg, center=2.0, scale=5.0) / 100.0 * max_pts
    if surp_avg >= 10:
        note = f"Avg earnings beat {surp_avg:+.1f}% — strong and consistent"
    elif surp_avg >= 3:
        note = f"Avg earnings beat {surp_avg:+.1f}% — consistent beater"
    elif surp_avg >= -3:
        note = f"Avg earnings surprise {surp_avg:+.1f}% — in line with estimates"
    else:
        note = f"Avg earnings miss {surp_avg:+.1f}% — consistently misses"
    return round(s, 1), note


# ── Main entry point ──────────────────────────────────────────────────────────

def get_revision_score(symbol: str) -> dict:
    """
    Compute earnings revision / forward momentum score for a single stock.

    Compatible with enhanced_composite() in scorer.py via total_score / total_max.

    When the Finnhub free tier is in use, all four data fetchers return empty
    results and every component defaults to neutral (50 pts).  The final score
    will be 50.0 / NEUTRAL for every symbol.  No errors are raised — the model
    runs cleanly and the caller can check n_available == 0 to detect this case.

    Args:
        symbol: Stock ticker (e.g. 'AAPL').

    Returns:
        Flat dict; total_score (0-100) and total_max (100) for scorer compat.
    """
    symbol = symbol.upper().strip()

    # ── Fetch ─────────────────────────────────────────────────────────────────
    surprises  = _fetch_earnings_surprises(symbol)
    eps_est    = _fetch_eps_estimates(symbol)
    rev_est    = _fetch_revenue_estimates(symbol)
    rec_trends = _fetch_recommendation_trends(symbol)

    # ── Metrics ───────────────────────────────────────────────────────────────
    eps_rev_30d = calc_eps_revision(eps_est, lookback_periods=1)
    eps_rev_90d = calc_eps_revision(eps_est, lookback_periods=3)
    rev_rev_30d = calc_revenue_revision(rev_est, lookback_periods=1)
    surp_avg    = calc_earnings_surprise_avg(surprises, n_quarters=4)

    # Low coverage: ≤1 analyst on the most recent period (per PROJECT_MAP)
    n_analysts   = eps_est[0].get("n_analyst") if eps_est else None
    low_coverage = bool(n_analysts is not None and n_analysts <= 1)
    breadth      = None if low_coverage else calc_revision_breadth(rec_trends)

    # ── Score ─────────────────────────────────────────────────────────────────
    s30d, n30d = _score_eps_revision_30d(eps_rev_30d)
    s90d, n90d = _score_eps_revision_90d(eps_rev_90d)
    srev, nrev = _score_revenue_revision(rev_rev_30d)
    ssrp, nsrp = _score_earnings_surprise(surp_avg)

    criteria = [
        {"label": "EPS Revision (30d proxy)",      "requirement": "Rising consensus",
         "actual": f"{eps_rev_30d:+.1f}%" if eps_rev_30d is not None else "N/A",
         "score": s30d, "max": 35, "note": n30d},
        {"label": "EPS Revision (90d proxy)",      "requirement": "Sustained uptrend",
         "actual": f"{eps_rev_90d:+.1f}%" if eps_rev_90d is not None else "N/A",
         "score": s90d, "max": 25, "note": n90d},
        {"label": "Revenue Revision (30d proxy)",  "requirement": "Rising estimates",
         "actual": f"{rev_rev_30d:+.1f}%" if rev_rev_30d is not None else "N/A",
         "score": srev, "max": 20, "note": nrev},
        {"label": "Earnings Surprise (avg 4Q)",    "requirement": "> 0%",
         "actual": f"{surp_avg:+.1f}%" if surp_avg is not None else "N/A",
         "score": ssrp, "max": 20, "note": nsrp},
    ]

    total_score = sum(c["score"] for c in criteria)
    total_max   = sum(c["max"]   for c in criteria)   # 100
    fwd_score   = round(total_score / total_max * 100, 1) if total_max else 50.0

    signal = "NEUTRAL"
    for threshold, sig in SIGNAL_THRESHOLDS:
        if fwd_score >= threshold:
            signal = sig
            break

    n_available = sum([
        eps_rev_30d is not None,
        eps_rev_90d is not None,
        rev_rev_30d is not None,
        surp_avg    is not None,
    ])

    return {
        "ticker":                  symbol,
        "eps_revision_30d":        round(eps_rev_30d,  2) if eps_rev_30d  is not None else None,
        "eps_revision_90d":        round(eps_rev_90d,  2) if eps_rev_90d  is not None else None,
        "revenue_revision_30d":    round(rev_rev_30d,  2) if rev_rev_30d  is not None else None,
        "earnings_surprise_avg":   round(surp_avg,     2) if surp_avg     is not None else None,
        "revision_breadth":        round(breadth,      3) if breadth      is not None else None,
        "forward_momentum_score":  fwd_score,
        "signal":                  signal,
        "low_coverage":            low_coverage,
        "n_analyst":               n_analysts,
        "n_available":             n_available,
        # scorer.py compatibility
        "total_score":             round(fwd_score, 1),
        "total_max":               100,
        "criteria":                criteria,
    }