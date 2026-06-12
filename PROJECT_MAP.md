
PROJECT: Fundamental Stock Analysis + Portfolio Intelligence System

GOAL:
Multi-factor equity scoring engine combining value, quality, momentum, risk, and forward-looking alpha signals with efficient SEC data ingestion and persistent metric storage.


CORE ARCHITECTURE RULES (HARD CONSTRAINTS)
===
- Preserve all existing public APIs
- No repo-wide refactors
- Minimal diffs only
- Add tests for every new module
- sec_data.py is single source of financial truth
- All new models are plug-in modules
- scorer.py remains stable unless explicitly extended
- Deterministic outputs required
- Avoid cross-module entanglement unless defined


DATA LAYER
===
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


CORE VALUE MODELS
===
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


NEW P2–P3–P4 MODELS
===


P4 ALTERNATIVE DATA (framework only):
- web traffic (stub)
- hiring velocity (stub)
- sentiment (stub)


OPTIONS LAYER (P4 EXPANSION)
===
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

P5 INFRASTRUCTURE UPGRADES
===


3. MOBILE UX:
- stat labels expandable on tap
- single active expanded stat
- requires (label, value, description)

4. Portfolio
# Portfolio Page Refactor – Multi-Portfolio Support

## Objective
Refactor the Portfolio page so users can create, manage, compare, and delete multiple portfolios instead of being limited to a single active portfolio.

---

## Functional Requirements

### 1. Portfolio List View
Replace the current single-portfolio page with a portfolio management page.

Display all saved portfolios in a list/table/card layout.

Each portfolio row/card should include:

- Checkbox for selection
- Portfolio Name
- Number of Holdings
- Creation Date
- Portfolio CAGR (if available)
- Current Value (if available)

Example:

☐ Growth Portfolio
☐ Dividend Portfolio
☐ AI Stocks Portfolio

---

### 2. Selection Rules

#### No Portfolio Selected
- Disable Compare button
- Disable Delete button
- Show empty state message

#### One Portfolio Selected
- Show the existing portfolio detail view exactly as it works today.
- Display:
  - Holdings table
  - Backtest results
  - Monte Carlo results
  - Weak link analysis
  - Charts

#### Two Portfolios Selected
- Enable Compare button
- Show comparison view
- Display portfolios side-by-side

#### More Than Two Selected
- Disable Compare button
- Show validation message:

"Select exactly 2 portfolios to compare."

Delete should still remain available.

---

### 3. Delete Portfolios

Allow multi-select deletion.

Workflow:

1. User selects one or more portfolios
2. Click Delete
3. Confirmation dialog:

Delete 3 portfolios?
This action cannot be undone.

4. Delete selected portfolios
5. Refresh portfolio list
6. Clear selections

Backend should call:

delete_portfolio(portfolio_name)

for each selected portfolio.

---

### 4. Portfolio Comparison View

When exactly 2 portfolios are selected:

Show:

Portfolio A | Portfolio B

Side-by-side comparison.

## Comparison Metrics

### Portfolio Summary

| Metric | Portfolio A | Portfolio B |
|----------|----------|----------|
| Current Value | | |
| Final Backtest Value | | |
| CAGR | | |
| SPY CAGR | | |
| Alpha vs SPY | | |
| Number of Holdings | | |
| Created Date | | |

### Holdings Comparison

Show holdings for both portfolios.

### Performance Charts

Render both portfolios on the same chart:

- Portfolio A line
- Portfolio B line
- SPY benchmark line

### Monte Carlo Comparison

Display:

Portfolio A:
- P10
- P50
- P90

Portfolio B:
- P10
- P50
- P90

### Weak Link Comparison

For each portfolio show:

- Weakest holding
- Worst drag_bps
- Largest swap_delta_pct

---

## Determine Which Portfolio Is Better

### Scoring

Primary metric priority:

1. Higher CAGR
2. Higher final portfolio value
3. Higher alpha vs SPY
4. Better Monte Carlo P50 outcome
5. Fewer weak-link holdings

Example:

score =
  (cagr * 0.40)
+ (alpha_vs_spy * 0.25)
+ (normalized_final_value * 0.15)
+ (normalized_p50 * 0.15)
+ (weak_link_score * 0.05)

### Comparison Result Banner

Display:

🏆 Portfolio A is stronger

Reasons:

- Higher CAGR
- Better alpha vs SPY
- Higher projected median value
- Fewer weak-link holdings

If scores are nearly identical:

"Both portfolios perform similarly."

---

## Backend Requirements

Create a new comparison service:

```python
compare_portfolios(portfolio_a_name, portfolio_b_name)
```

Returns:

```python
{
    "winner": "Growth Portfolio",
    "score_a": 82.4,
    "score_b": 71.8,
    "reasons": [
        "Higher CAGR",
        "Better alpha vs SPY",
        "Higher projected median return"
    ],
    "portfolio_a": {...},
    "portfolio_b": {...}
}
```

The comparison service should:

1. Load both portfolios
2. Run simulations if not cached
3. Calculate comparison metrics
4. Determine winner
5. Return comparison payload

---

## UI State Management

```javascript
selectedPortfolios = []
```

Rules:

- length == 0 → empty state
- length == 1 → portfolio detail view
- length == 2 → comparison view
- length > 2 → compare disabled

Selections should persist while navigating within the portfolio page.

---

## Existing Functionality

Do NOT modify:

- run_backtest()
- run_montecarlo()
- analyze_weak_links()
- run_simulation()

Reuse existing simulation results whenever possible.

Implement the new functionality as a layer on top of the current portfolio engine.

---

## Success Criteria

✓ Create multiple portfolios

✓ View all portfolios on one page

✓ Select one portfolio and see the current detailed analysis

✓ Select multiple portfolios and delete them

✓ Select exactly two portfolios and compare them side-by-side

✓ See an automatically generated winner with clear reasoning

✓ Reuse existing backtest, Monte Carlo, and weak-link analytics without changing their behavior


SCORER SYSTEM (UNCHANGED CORE LOGIC)
===
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

EXECUTION PRIORITY ORDER
===
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


GLOBAL AI AGENT RULES
===
- Read only relevant files
- Avoid repo-wide scans
- No unrelated refactors
- Preserve APIs strictly
- Add tests for every change
- Minimal diff output
- Stop after scoped task completion

EXPECTED SYSTEM OUTCOME
===

- Higher Sharpe ratio via orthogonal factors
- Reduced value traps via revisions + growth filters
- Better regime-aware risk control
- Improved capital efficiency detection
- Faster SEC access via lazy ingestion
- Persistent computed value signals
- Mobile-friendly interpretability layer

END
