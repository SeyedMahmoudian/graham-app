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

Status: [ ]

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

Status: [ ]

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

Status: [ ]

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

Status: [?]

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
