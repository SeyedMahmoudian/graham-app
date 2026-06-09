
PROJECT: Fundamental Stock Analysis + Portfolio Intelligence System

GOAL:
Multi-factor equity scoring engine combining value, quality, momentum, risk, and forward-looking alpha signals with efficient SEC data ingestion and persistent metric storage.

========================================================
CORE ARCHITECTURE RULES (HARD CONSTRAINTS)
========================================================
- Preserve all existing public APIs
- No repo-wide refactors
- Minimal diffs only
- Add tests for every new module
- sec_data.py is single source of financial truth
- All new models are plug-in modules
- scorer.py remains stable unless explicitly extended
- Deterministic outputs required
- Avoid cross-module entanglement unless defined


========================================================
DATA LAYER
========================================================
sec_data.py:
- SEC + fundamentals ingestion
- Dividend + financial history provider

P5 UPGRADE:
- Convert to lazy fetch + caching
- Add:
  get_financials(ticker, force_refresh=False)
  is_cache_stale(ticker)
  refresh_if_needed(ticker)
- TTL:
  annual=7d, quarterly=1–3d
- Cache-first behavior; fetch only when needed

========================================================
CORE VALUE MODELS
========================================================
graham.py:
- dividend stability, earnings consistency, value score

greenblatt.py:
- earnings yield + ROC + NWC

piotroski.py:
- F-score (profitability, leverage, efficiency)

altman.py:
- bankruptcy risk (Z-score)

earnings_revision.py:
- EPS/revenue revision momentum + surprises

fcf_quality.py:
- 10Y cash flow quality (margin, conversion, stability, accruals)

profitability.py (weight 12%):
- ROIC, ROE, margins, capital efficiency, incremental ROIC

========================================================
NEW P2–P3–P4 MODELS
========================================================

P2 CAPITAL ALLOCATION (capital_allocation.py, weight 8%):
- reinvestment rate
- ROIC spread
- buyback yield
- dilution rate
- dividend stability
- debt allocation trend
- M&A efficiency
=> capital_allocation_score + signal

P2 GROWTH QUALITY (growth_quality.py, weight 8%):
- revenue CAGR (5Y/10Y)
- EPS CAGR
- FCF CAGR
- margin stability
- incremental ROIC
=> growth_quality_score + signal

P3 REGIME MODEL (regime.py):
- market trend score
- volatility percentile (20D/60D)
- drawdown depth
=> regimes:
BULL_LOW_VOL | BULL_HIGH_VOL | BEAR_LOW_VOL | BEAR_HIGH_VOL | SIDEWAYS

P4 INSIDER ACTIVITY:
- net insider buying
- cluster buying detection
- insider confidence score

P4 FACTOR MOMENTUM:
- 3M/6M/12M returns
- earnings momentum
- ROIC trend slope

P4 ALTERNATIVE DATA (framework only):
- web traffic (stub)
- hiring velocity (stub)
- sentiment (stub)

========================================================
OPTIONS LAYER (P4 EXPANSION)
========================================================
options_signal_engine.py:
- CALL vs PUT directional bias
- short-horizon option price movement prediction
- IV regime + volatility expansion detection
- strike/expiry recommendation
- risk score (theta + liquidity + IV)
- edge score

NOTE:
Models option mark-to-market movement, NOT expiry payoff

Dependencies:
sec_data.py, regime.py, risk_metrics.py, portfolio.py

========================================================
P5 INFRASTRUCTURE UPGRADES
========================================================

1. SEC LAZY FETCH (critical):
- eliminate bulk ingestion
- fetch on demand + cache reuse
- auto-refresh if stale in portfolio simulation

2. PERSISTENCE STORE:
value_metrics SQLite table:
- ticker (PK)
- graham_score
- buffett_score
- updated_at
=> survives restarts, used for fast retrieval

3. MOBILE UX:
- stat labels expandable on tap
- single active expanded stat
- requires (label, value, description)

========================================================
SCORER SYSTEM (UNCHANGED CORE LOGIC)
========================================================
Current weights (legacy):
- Graham 15%
- Buffett 25%
- Quality 18%
- Momentum 14%
- Piotroski 14%
- Risk 8%
- Altman 6%

PROPOSED ORTHOGONAL SYSTEM:

Value: 12%
Quality: 18%
Momentum: 12%
Profitability: 12%
FCF Quality: 10%
Earnings Revisions: 12%
Capital Allocation: 8%
Growth Quality: 7%
Risk: 6%
Altman: 3%

TOTAL = 100%

KEY DESIGN SHIFT:
- Reduce overlap (Buffett/Piotroski/Altman redundancy removed)
- Increase forward-looking signals (earnings revisions, growth)
- Improve factor independence (orthogonal decomposition)

========================================================
EXECUTION PRIORITY ORDER
========================================================
1. profitability.py
2. fcf_quality.py
3. earnings_revision.py
4. capital_allocation.py
5. growth_quality.py
6. regime.py
7. insider_activity.py
8. factor_momentum.py
9. alternative_data.py
10. options_signal_engine.py
11. sec_data lazy upgrade
12. persistence layer
13. mobile UX

========================================================
GLOBAL AI AGENT RULES
========================================================
- Read only relevant files
- Avoid repo-wide scans
- No unrelated refactors
- Preserve APIs strictly
- Add tests for every change
- Minimal diff output
- Stop after scoped task completion

========================================================
EXPECTED SYSTEM OUTCOME
========================================================
- Higher Sharpe ratio via orthogonal factors
- Reduced value traps via revisions + growth filters
- Better regime-aware risk control
- Improved capital efficiency detection
- Faster SEC access via lazy ingestion
- Persistent computed value signals
- Mobile-friendly interpretability layer

END