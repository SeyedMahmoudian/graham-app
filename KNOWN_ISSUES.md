# CLAUDE CODE ISSUE RUNNER

This system allows deterministic execution of known issues with minimal token usage.

---

# USAGE FORMAT

To execute a task, always use:

```
run ISSUE-XXX
```

Example:

```
run ISSUE-007
```

---

# EXECUTION RULES (MANDATORY)

When `run ISSUE-XXX` is received:

## Step 1 — Load Context

Only read:

* KNOWN_ISSUES.md
* AI_CONTEXT.md
* PROJECT_MAP.md

Then extract:

* issue definition
* file scope
* acceptance criteria

Do NOT scan repository.

---

## Step 2 — Scope Lock

Restrict all work to files listed in the issue.

If files are missing or unclear:
→ STOP and ask a question

---

## Step 3 — Diagnosis Mode (NO CODE CHANGES)

Before editing:

* Identify root cause
* Identify exact function(s)
* Identify failure path
* Confirm fix strategy

Output:

* affected functions
* planned patch


STOP.

---

## Step 4 — Implementation Mode

Apply minimal patch only:

Rules:

* no refactors
* no renames
* no unrelated formatting changes
* preserve public APIs

---

## Step 5 — Tests

If tests exist:

* ensure compatibility
* add missing test cases if required

If no tests exist:

* create minimal unit tests only for issue

---

## Step 6 — Output Format

Return ONLY:

### Files Modified

* file paths

### Patch

```diff
...
```

### Tests Added/Updated

* list

### Status

* PASS / NEEDS INFO

---

# GLOBAL CONSTRAINTS

Never:

* scan full repo
* fix multiple issues in one run
* rewrite modules
* “improve architecture”
* optimize unrelated code

Always:

* minimal diff
* single issue focus
* deterministic output
* push to git after successful run
# FILE LOCK RULE:

If file is not explicitly listed in ISSUE → it is forbidden to open.
---

# ISSUE HANDLING MAP

## ISSUE TYPES

### Type A: Single-file bug

→ modify only one file

### Type B: Multi-file logic consistency

→ modify ONLY listed files

### Type C: Investigation required

→ stop after diagnosis

---

# EXAMPLE EXECUTION

User:

```
run ISSUE-003
```

Agent:

1. Reads ISSUE-003
2. Opens only <filefromissue>.py
3. Diagnoses covariance error
4. Applies fix
5. Adds test
6. push to git

---

# FAILURE RULE

If issue is ambiguous:

DO NOT GUESS.

Instead output:

```
NEED CLARIFICATION:
- missing definition of X
- unclear expected behavior of Y
```

Stop immediately.

---

# OPTIMIZATION GOAL

This system is designed to:

* reduce token usage by ~50–70%
* eliminate repo-wide scanning
* enforce deterministic patches
* improve test coverage reliability

# KNOWN_ISSUES.md

# Financial Model Issue System (Agent Optimized)

This file is the **single source of truth for all fixable defects**.

---

# Global Rules (MANDATORY)

When fixing any issue:

1. Load only:

   * KNOWN_ISSUES.md
   * AI_CONTEXT.md
   * PROJECT_MAP.md
   * explicitly selected files

2. Work on exactly ONE issue per run.

3. Do NOT scan the full repository.

4. Do NOT refactor unrelated code.

5. Output must be:

   * pushed to git
   * tests if required
   * no extra commentary unless asked

6. If a dependency outside allowed files is required → STOP and ask.

---

# ISSUE FORMAT STANDARD

Each issue must contain:

* Clear root cause
* Explicit file scope
* Deterministic fix
* Verifiable acceptance criteria

No vague instructions allowed.

---

# ACTIVE ISSUES

---

## ISSUE-001

Status: [ ]

Title: Division by zero in Greenblatt calculation

Priority: Critical

Files:

* greenblatt.py

Problem:
Division by zero occurs when denominator (likely EV or capital metric) equals 0 during Greenblatt ranking calculation.

Required Fix:
Add safe-guarded division logic:

* If denominator == 0 → return 0 score OR skip metric consistently

Acceptance Criteria:

* No runtime ZeroDivisionError
* Unit test added for denominator = 0 case
* Existing scoring behavior unchanged otherwise

---

## ISSUE-002

Status: [y]

Title: Enterprise value calculation correctness

Priority: High

Files:

* greenblatt.py
* scorer.py

Problem:
Enterprise value formula may be inconsistent or missing adjustments across modules.

Required Fix:
Standardize EV calculation across all usages.

Acceptance Criteria:

* Single EV definition used everywhere
* No duplicated logic
* Tests confirm consistency

---

## ISSUE-003

Status: [ ]

Title: Free Cash Flow yield miscalculation

Priority: High

Files:

* greenblatt.py
* scorer.py

Problem:
FCF yield may not be normalized correctly against enterprise value.

Required Fix:
Ensure:
FCF Yield = FCF / EV

Acceptance Criteria:

* Correct formula applied everywhere
* EV denominator validated non-zero
* Tests added for edge cases

---

## ISSUE-004

Status: [ ]

Title: Share dilution handling incorrect

Priority: High

Files:

* graham.py
* piotroski.py

Problem:
Share count growth is not consistently treated as dilution.

Required Fix:
Define consistent dilution rule:

* threshold-based share increase penalty

Acceptance Criteria:

* Same dilution logic across modules
* Unit tests for dilution thresholds

---

## ISSUE-005

Status: [ ]

Title: CAGR calculation inconsistencies

Priority: High

Files:

* portfolio.py
* utils/returns.py (if exists)

Problem:
CAGR may not correctly account for compounding or missing periods.

Required Fix:
Use standard CAGR formula with proper time delta normalization.

Acceptance Criteria:

* Accurate multi-year compounding
* Handles missing years safely

---

## ISSUE-006

Status: [ ]

Title: Earnings normalization inconsistency

Priority: High

Files:

* graham.py
* altman.py
* scorer.py

Problem:
Earnings values are not consistently normalized across models.

Required Fix:
Standardize earnings definition before scoring.

Acceptance Criteria:

* Same earnings input across models
* No inconsistent scaling

---

## ISSUE-007

Status: [ ]

Title: Monte Carlo return assumptions incorrect

Priority: High

Files:

* portfolio.py

Problem:
Monte Carlo uses arithmetic returns instead of geometric drift.

Required Fix:
Apply:
μ_geo = μ_arith − σ²/2

Acceptance Criteria:

* Correct drift applied
* Simulation validated via tests

---

## ISSUE-008

Status: [ ]

Title: Drawdown calculation incorrect

Priority: High

Files:

* risk_metrics.py

Problem:
Max drawdown logic may not track rolling peak correctly.

Required Fix:
Implement proper peak-to-trough equity curve tracking.

Acceptance Criteria:

* Correct max drawdown
* Handles all-negative series

---

## ISSUE-009

Status: [ ]

Title: Correlation estimation methodology incorrect

Priority: High

Files:

* portfolio.py

Problem:
Correlation assumptions may be oversimplified or inconsistent.

Required Fix:
Use proper covariance-derived correlation matrix.

Acceptance Criteria:

* Symmetric correlation matrix
* No independence assumptions

---

## ISSUE-010

Status: [ ]

Title: Survivorship bias in data handling

Priority: High

Files:

* sec_data.py

Problem:
Data pipeline may exclude delisted/failed companies.

Required Fix:
Document and mitigate survivorship bias where possible.

Acceptance Criteria:

* Bias acknowledged in code/docs
* Optional handling strategy implemented

---

## ISSUE-011

Status: [ ]

Title: Missing financial statement handling

Priority: High

Files:
sec_data.py
graham.py
piotroski.py
altman.py
portfolio.py
risk_metrics.py
scorer.py

Problem:
Missing data fields may cause silent failures or incorrect scoring.

Required Fix:
Standardize missing-data handling:

* explicit None checks
* fallback rules per model

Acceptance Criteria:

* No silent NaN propagation
* All models handle missing inputs safely

---

## ISSUE-012

Status: [ ]

Title: Project folder structure cleanup

Priority: Low

Files:

app.py
alpha_vantage_client.py
cache.py
greenblatt.py
momentum.py
quality.py
screener.py
universe.py
sec_data.py
graham.py
piotroski.py
altman.py
portfolio.py
risk_metrics.py
scorer.py
buffett.py

Problem:
Code organization is inconsistent.

Required Fix:
Group modules into logical folders:

* models/
* data/
* portfolio/
* risk/
* core/

Acceptance Criteria:

* Clean modular structure
* No broken imports
* Tests still pass

---

# CLOSED ISSUES

(None yet)

---

# TEST MAPPING (REFERENCE ONLY)

Each issue must have at least one test verifying fix.

Tests live in `/tests`.

Naming convention:
test_issue_XXX_<feature>.py

---

# AI EXECUTION PROTOCOL

When fixing an issue:

1. Identify issue in this file
2. Read ONLY listed files
3. Confirm root cause
4. Apply minimal patch
5. Add or update tests
6. push to git
7. STOP

---

# NON-NEGOTIABLE RULES

* No repo-wide scans
* No unrelated refactors
* No multi-issue fixes per run
* No guessing missing logic
* Ask if uncertain
