# KNOWN_ISSUES.md

# Financial Model Audit Backlog

Purpose:
Track known mathematical, financial-model, and implementation issues discovered during code review.

Status values:

* [ ] Open
* [~] In Progress
* [x] Completed
* [?] Requires Investigation

---
# ISSUE-001

Status: [x]

Title:
Sortino Ratio Uses Downside Count Denominator

Priority:
Critical

File:
risk_metrics.py

Problem:

Downside deviation may be divided by downside observations only.

Standard Sortino methodology uses total observations.

Required Fix:

downside_variance =
Σ(min(0, r-target)^2) / N

where:

N = total observations

Acceptance Criteria:

* Total observations used.
* Sortino calculation updated.
* Unit tests added.



# ISSUE-002

Status: [x]

Title:
Graham Counts Total Dividend Years Instead of Consecutive Years

Priority:
Critical

File:
graham.py

Problem:

Current implementation appears to count total years containing dividends.

Benjamin Graham methodology requires consecutive dividend-paying years.

Example:

Dividend years:

2025
2024
2023
2022
2020

Correct result:

4

Incorrect result:

5

Required Fix:

Count uninterrupted dividend years ending at the most recent year.

Acceptance Criteria:

* Consecutive years only.
* Stops at first missing year.
* Unit tests added.

---

# ISSUE-003

Status: [x]

Title:
Monte Carlo Portfolio Volatility Ignores Correlation

Priority:
Critical

File:
portfolio.py

Problem:

Portfolio volatility may be calculated from individual asset volatility without covariance adjustment.

This can materially misstate risk.

Required Fix:

Use covariance matrix.

Formula:

σp = sqrt(wᵀ Σ w)

Acceptance Criteria:

* Covariance matrix used.
* Correlations influence risk estimate.
* Unit tests validate behavior.

---

# ISSUE-004

Status: [x]

Title:
Monte Carlo Drift Uses Arithmetic Mean

Priority:
Critical

File:
portfolio.py

Problem:

Monte Carlo simulation appears to use arithmetic expected return directly.

This can overstate long-term growth.

Required Fix:

Use geometric drift.

Formula:

μ_geo = μ_arith − σ²/2

Acceptance Criteria:

* Drift adjustment applied before simulation.
* Tests validate expected behavior.

---

# ISSUE-005

Status: [x]

Title:
Piotroski YoY Comparisons May Use Wrong Fiscal Period

Priority:
High

File:
piotroski.py

Problem:

Comparisons may be performed using adjacent statements rather than periods separated by roughly one year.

This can invalidate F-score calculations.

Required Fix:

Validate fiscal dates before comparison.

Acceptance Criteria:

* Comparisons use proper annual separation.
* Invalid comparisons rejected.
* Unit tests added.

---

# ISSUE-006

Status: [x]

Title:
Partial Altman Scores Not Properly Normalized

Priority:
High

File:
altman.py

Problem:

Missing components may reduce score unfairly.

Required Fix:

Scale score by available-weight fraction.

Example:

adjusted_score = raw_score / available_fraction

Acceptance Criteria:

* Missing metrics do not artificially depress score.
* Missing factors logged.
* Unit tests added.

---

# ISSUE-007

Status: [x]

Title:
Greenblatt Net Working Capital Includes Cash

Priority:
High

File:
greenblatt.py

Problem:

Cash and equivalents may be included in Net Working Capital.

Magic Formula methodology excludes excess cash.

Required Fix:

NWC = Current Assets - Cash - Current Liabilities

Acceptance Criteria:

* Cash excluded.
* Existing calculations remain stable.
* Tests added.

---

# ISSUE-008

Status: [x]

Title:
Greenblatt Earnings Yield Composite Score Treatment

Priority:
Medium

Files:

app.py
scorer.py

Problem:

Unclear whether Greenblatt Earnings Yield participates in composite scoring.

Investigation Required:

1. Locate composite scoring logic.
2. Determine current behavior.
3. Document rationale.
4. Decide inclusion or exclusion.

Acceptance Criteria:

* Behavior documented.
* Scoring methodology clarified.
* Implementation updated if required.

---



---

# ISSUE-009

Status: [x]

Title:
Dividend History Lookback Too Short

Priority:
Low

File:
sec_data.py

Problem:

Dividend history retrieval does not use a sufficiently long lookback period.

This may cause:

* Incomplete dividend histories
* Incorrect dividend consistency calculations
* Graham analysis errors

Required Fix:

Increase dividend history retrieval window to approximately 10 years.

Acceptance Criteria:

* Long historical dividend records are available when data provider supports them.
* Graham dividend calculations receive complete history.
* Tests validate extended lookback behavior.

---

# Future Audit Candidates

Status: [?]

Potential Review Areas:

* Enterprise value calculations
* FCF yield calculations
* Share dilution handling
* CAGR calculations
* Earnings normalization
* Monte Carlo return assumptions
* Drawdown calculations
* Correlation estimation methodology
* Data survivorship bias
* Missing financial statement handling

---

# Unit Test Coverage Backlog

Purpose:
Track missing unit test coverage per module.
All test files live in tests/ at the repository root.
Use pytest. No test should hit the network or disk — mock all I/O.

Existing partial coverage:
* tests/test_graham_consecutive_dividends.py  — graham.py (ISSUE-002 only)
* tests/test_risk_metrics_sortino.py          — risk_metrics.py (ISSUE-001 only)
* tests/test_issue008_greenblatt_composite.py — greenblatt.py (ISSUE-008 only)
* test_issue001_div_lookback.py               — sec_data.py (ISSUE-009 only)

---

## TEST-001

Status: [ ]

Title:
Full Unit Tests for altman.py

File:
tests/test_altman.py

Required Tests:

* safe_zone: all 5 components available, Z > 2.99 → zone="safe", risk_penalty=0
* grey_zone: Z between 1.81 and 2.99 → zone="grey", risk_penalty=15
* distress_zone: Z < 1.81 → zone="distress", risk_penalty=35
* zpp_model_selected: ppe_net missing or zero → model="Z''"
* original_model_selected: ppe_net present and non-zero → model="Original"
* insufficient_data: fewer than 3 components available → z_score=None, zone="unknown", risk_score=50
* partial_components: exactly 3 components available → z_score is not None
* null_price: price=None → x4=None, score still computed from remaining components
* risk_score_bounds: risk_score always in [0, 100]
* note_content: note string contains Z value and model name when z_score is not None
* zpp_safe_threshold: Z'' > 2.60 → zone="safe"
* zpp_distress_threshold: Z'' < 1.10 → zone="distress"

Acceptance Criteria:

* No network or file I/O.
* All zone thresholds for both models covered.
* risk_penalty and risk_score validated.

---

## TEST-002

Status: [ ]

Title:
Full Unit Tests for greenblatt.py

File:
tests/test_greenblatt.py

Note:
tests/test_issue008_greenblatt_composite.py covers ISSUE-008 only.
This test file must cover the full module.

Required Tests:

* compute_single_full: all fields available → earnings_yield and roic are not None
* compute_single_no_ppe: ppe_net missing → ic_method contains "fallback" or "NWC only"
* compute_single_no_price: price=None → ev=None, earnings_yield=None
* compute_single_negative_ebit: EBIT < 0 → earnings_yield negative
* nwc_excludes_cash: invested_capital uses cur_ast - cash - cur_lib, not cur_ast - cur_lib
* rank_universe_basic: 3 stocks with valid EY and ROIC → magic_score in [0, 100] for all
* rank_universe_excludes_negative_ey: stock with negative earnings_yield → magic_score=None
* rank_universe_single_stock: 1 valid stock → magic_score=100.0
* rank_universe_empty: all stocks missing EY or ROIC → list returned unchanged, no crash
* rank_universe_sorted: returned list sorted by magic_score descending
* rank_universe_does_not_mutate_excluded: excluded stocks retain original magic_score=None

Acceptance Criteria:

* No network or file I/O.
* nwc_excludes_cash is a dedicated failing test that passes only after ISSUE-007 fix.

---

## TEST-003

Status: [ ]

Title:
Full Unit Tests for risk_metrics.py

File:
tests/test_risk_metrics.py

Note:
tests/test_risk_metrics_sortino.py covers ISSUE-001 only.
This test file must cover the full module.

Required Tests:

* empty_history: price_hist with fewer than 6 rows → returns _empty dict, risk_score=50
* short_history: exactly 5 rows → returns _empty (need >= 6)
* full_metrics: 60-row price history → all keys present, no None for core metrics
* sharpe_positive: monotonically rising prices → sharpe > 0
* sortino_uses_total_n: downside std denominator is len(returns), not len(downside_returns)
* max_drawdown_non_positive: drawdown always <= 0
* max_drawdown_flat: flat prices → max_drawdown=0.0
* beta_without_spy: spy_hist=None → beta=None, alpha=None, risk_score still numeric
* beta_with_spy: aligned spy_hist provided → beta is a finite float
* risk_score_bounds: risk_score always in [0, 100]
* calmar_near_zero_drawdown: max_drawdown near zero → calmar=None, no ZeroDivisionError
* cvar_leq_var: CVaR <= VaR for any input distribution

Acceptance Criteria:

* No network or file I/O. Construct DataFrames in-test.
* sortino_uses_total_n is a dedicated failing test that passes only after ISSUE-001 fix.

---

## TEST-004

Status: [ ]

Title:
Full Unit Tests for scorer.py

File:
tests/test_scorer.py

Required Tests:

* composite_strong_buy: all three pillars >= 70 pct → verdict="STRONG BUY"
* composite_buy: pillars average to 55-69 → verdict="BUY"
* composite_watch: pillars average to 40-54 → verdict="WATCH"
* composite_hold: pillars average to 25-39 → verdict="HOLD/WEAK"
* composite_avoid: all three pillars < 25 pct → verdict="AVOID"
* composite_weights_sum: WEIGHTS values sum to 1.0
* enhanced_weights_sum: ENHANCED_WEIGHTS values sum to 1.0
* altman_cap_distress: altman zone="distress" → composite_score <= 50.0, altman_cap_applied=True
* altman_cap_safe: altman zone="safe" and high raw score → cap not applied, altman_cap_applied=False
* value_trap_warning_true: graham_pct >= 60, momentum_pct < 30, f_score <= 3 → value_trap_warning=True
* value_trap_warning_false: conditions not met → value_trap_warning=False
* compounder_flag_true: f_score >= 7, quality_pct >= 65, buffett_pct >= 60 → compounder_flag=True
* compounder_flag_false: any condition not met → compounder_flag=False
* fundamental_only_momentum_none: momentum_pct=None in return dict
* enhanced_backward_compat: buffett_result=None → no crash, b_pct treated as 50

Acceptance Criteria:

* No network or file I/O.
* Every verdict threshold covered by at least one test.
* All flag combinations covered.

---

## TEST-005

Status: [ ]

Title:
Full Unit Tests for piotroski.py

File:
tests/test_piotroski.py

Required Tests:

* all_pass: sec dict where all 9 signals fire → f_score=9, label="strong"
* all_fail: sec dict where no signal fires → f_score=0, label="weak"
* neutral_range: exactly 5 signals fire → label="neutral"
* f1_roa_positive: net_inc > 0, total_assets > 0 → F1=1
* f1_roa_negative: net_inc < 0 → F1=0
* f2_ocf_positive: op_cf > 0 → F2=1
* f2_ocf_negative: op_cf < 0 → F2=0
* f3_roa_improving: roa this year > roa prior year → F3=1
* f3_roa_declining: roa this year < roa prior year → F3=0
* f4_accruals_pass: ocf/assets > roa → F4=1
* f4_accruals_fail: ocf/assets <= roa → F4=0
* f5_leverage_primary: lt_debt present, ratio falling → F5=1
* f5_leverage_fallback: lt_debt missing, tot_lib present → uses TotalLiab/Assets, still scores
* f5_leverage_missing: both lt_debt and tot_lib absent → F5=0, no crash
* f6_current_ratio_improving: cr this year > cr prior year → F6=1
* f7_no_dilution_within_tolerance: shares up 0.5% → F7=1
* f7_dilution_detected: shares up 2% → F7=0
* f8_gross_margin_improving: gm this year > gm prior year → F8=1
* f9_asset_turnover_improving: at this year > at prior year → F9=1
* missing_prior_year: only one year of data → F3, F5, F6, F8, F9 all score 0, no crash
* missing_all_data: empty sec dict → f_score=0, all signals score 0, no crash

Acceptance Criteria:

* No network or file I/O.
* Every signal (F1–F9) has a pass case and a fail case.
* Fallback and missing-data paths explicitly tested.

---

## TEST-006

Status: [ ]

Title:
Full Unit Tests for graham.py

File:
tests/test_graham.py

Note:
tests/test_graham_consecutive_dividends.py covers ISSUE-002 only.
This test file must cover the full module.

Required Tests:

* grade_A: total_score >= 70 → grade="A", grade_label="Defensive"
* grade_B: total_score 50-69 → grade="B", grade_label="Enterprising"
* grade_C: total_score 30-49 → grade="C", grade_label="Speculative"
* grade_D: total_score < 30 → grade="D", grade_label="Avoid"
* pe_at_ceiling: P/E = 15.0 → pe_score=15
* pe_above_ceiling: P/E > 20 → pe_score=0
* pe_negative_earnings: eps < 0 → pe_score=0
* pe_no_price: price=None → pe=None, pe_score=0
* pb_deep_value: P/B <= 1.5 → pb_score=10
* pb_expensive: P/B > 2.5 → pb_score=0
* gn_full_margin: price <= 67% of Graham Number → gn_score=20
* gn_partial_margin: 67% < price <= GN → gn_score=10
* gn_no_margin: price > Graham Number → gn_score=0
* gn_no_price: price=None → gn_score=0
* consecutive_dividends_gap: gap in div_hist → div_years stops at gap
* consecutive_dividends_continuous: uninterrupted 20+ years → dv_score=10
* consecutive_dividends_empty: no div_hist entries → div_years=0, dv_score=0
* eps_loss_year: any eps value < 0 in history → eps_score=0 regardless of growth
* eps_insufficient_history: fewer than 5 years → eps_score=0
* nnwc_net_net: nnwc > mkt_cap → nn_score=5
* nnwc_not_net_net: nnwc < mkt_cap → nn_score=0
* total_score_bounded: total_score always in [0, 100]

Acceptance Criteria:

* No network or file I/O.
* consecutive_dividends_gap is a dedicated failing test that passes only after ISSUE-002 fix.
* Every scoring criterion has at least one pass and one fail case.

---

## TEST-007

Status: [ ]

Title:
Full Unit Tests for portfolio.py (pure functions only)

File:
tests/test_portfolio.py

Required Tests:

* split_factor_no_splits: empty splits list → factor=1.0
* split_factor_one_split_before: one 2:1 split on date <= as_of → factor=2.0
* split_factor_one_split_after: split date > as_of → factor=1.0 (not counted)
* split_factor_cumulative: two splits both before as_of → factors multiplied
* add_holding_success: valid symbol, shares, price → holding added, error=""
* add_holding_max_cap: portfolio already at MAX_HOLDINGS → error string returned
* add_holding_min_shares: shares < MIN_SHARES → error string returned
* add_holding_duplicate: same symbol twice → error string returned
* remove_holding_present: symbol in portfolio → removed, error=""
* remove_holding_missing: symbol not in portfolio → error string returned
* montecarlo_geometric_drift: drift used in simulation is μ - σ²/2, not μ (ISSUE-004)
* port_variance_uses_covariance: σp = sqrt(wᵀΣw), not sum of weighted stds (ISSUE-003)
* run_montecarlo_returns_bands: p10 <= p50 <= p90 at every time step
* run_montecarlo_start_value: paths[:,0] == start_value for all simulated paths

Acceptance Criteria:

* No network or file I/O. Mock alpha_vantage_client and cache entirely.
* montecarlo_geometric_drift and port_variance_uses_covariance are dedicated failing tests
  that pass only after ISSUE-004 and ISSUE-003 fixes respectively.
* Storage helpers (save/load/delete/list) tested with a mocked cache module.

---

## Test Infrastructure Notes

* Test runner: pytest
* Run all tests with: pytest tests/ -q
* Mock all external calls with unittest.mock.patch or pytest-mock
* Shared sec_facts fixture builders belong in tests/conftest.py
* Do not import app.py in any unit test (pulls in Dash and all dependencies)
* Each test file is self-contained — no shared mutable state across files
* Tests for known-open issues should be marked @pytest.mark.xfail(strict=True)
  so they fail visibly until the issue is resolved, then automatically pass

---

# AI Agent Instructions

When working on an issue:

1. Read AI_CONTEXT.md.
2. Read PROJECT_MAP.md.
3. Read this file.
4. Work on only one issue at a time.
5. Verify issue exists before implementing.
6. Produce minimal diffs.
7. Add tests.
8. Update issue status after completion.

Do not refactor unrelated code.
Do not scan the entire repository unless explicitly requested.
