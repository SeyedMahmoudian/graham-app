"""
codes — Graham Score Quant Platform
=====================================
Package layout
--------------
  codes/data/     data acquisition (cache, SEC EDGAR, price client)
  codes/models/   scoring models (Graham, Buffett, Piotroski, Altman, ...)
  codes/engine/   composite scoring, screener, universe
  codes/portfolio.py  portfolio analytics
  codes/app.py        Dash application entry-point
"""
import sys

# Import sub-packages in dependency order
from .data    import api_fetcher, cache, sec_data   # noqa: F401
from .models  import (                                         # noqa: F401
    piotroski, graham, buffett, altman, greenblatt,
    quality, momentum, risk_metrics, earnings_revision,
    profitability, fcf_quality, capital_allocation,
)
from .engine  import scorer, screener, universe               # noqa: F401
from .        import portfolio                                 # noqa: F401

# ── Backward-compatibility aliases (ISSUE-012) ────────────────────────────────
# Registers legacy paths like `codes.sec_data`, `codes.scorer` in sys.modules
# so existing test imports and external code keep working without changes.
_compat = {
    'cache':                cache,
    'sec_data':             sec_data,
    'api_fetcher': api_fetcher,
    'graham':               graham,
    'buffett':              buffett,
    'piotroski':            piotroski,
    'altman':               altman,
    'greenblatt':           greenblatt,
    'quality':              quality,
    'momentum':             momentum,
    'risk_metrics':         risk_metrics,
    'earnings_revision':    earnings_revision,
    'scorer':               scorer,
    'screener':             screener,
    'universe':             universe,
    'portfolio':            portfolio,
    'fcf_quality':          fcf_quality,
    'capital_allocation':   capital_allocation,
}
for _n, _m in _compat.items():
    sys.modules.setdefault(__name__ + '.' + _n, _m)
