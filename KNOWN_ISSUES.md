# CLAUDE CODE ISSUE RUNNER

This system allows deterministic execution of known issues with minimal token usage.

---

# USAGE FORMAT

```
run ISSUE-XXX
```

---

# EXECUTION RULES (MANDATORY)



## Step 1 — Load Context

- Read **only**: `KNOWN_ISSUES.md`, `AI_CONTEXT.md`, `PROJECT_MAP.md`
- Extract: issue definition, file scope, acceptance criteria
- Never scan repository

---

## Step 2 — Scope Lock
- Work **only** on files explicitly listed in the issue
- If file missing/unclear → STOP and ask
---

## 3. Diagnosis (Very Brief)**
   - Think: root cause + exact location
   - Output **only**:
    - DIAGNOSIS: [one sentence root cause + function]
    - PLAN: [minimal fix description]
---

## Step 4 — Implementation Mode

- Apply **smallest possible patch**
- No refactor, rename, or unrelated changes
- Preserve APIs

---

## Step 5 — Tests

- Add/update minimal test only for this issue

---

## Step 6 — Output Format

- no need just push to git,if git not working show diff

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

**Status:** [ ]  
**Title:** Free Cash Flow yield miscalculation  
**Files:** `greenblatt.py`, `scorer.py`  
**Fix:** Enforce FCF Yield = FCF / EV with non-zero check  
**Criteria:** Correct formula, edge case tests

---

## ISSUE-004

**Status:** [ ]  
**Title:** Share dilution handling incorrect  
**Files:** `graham.py`, `piotroski.py`  
**Fix:** Consistent threshold-based dilution rule  
**Criteria:** Same logic across files + tests

---

## ISSUE-005

**Status:** [ ]  
**Title:** CAGR calculation inconsistencies  
**Files:** `portfolio.py`, `utils/returns.py` (if exists)  
**Fix:** Standard CAGR with proper time delta  
**Criteria:** Correct compounding, safe missing periods

---

## ISSUE-006

**Status:** [ ]  
**Title:** Earnings normalization inconsistency  
**Files:** `graham.py`, `altman.py`, `scorer.py`  
**Fix:** Standardize earnings definition  
**Criteria:** Consistent input across models

---

## ISSUE-007

**Status:** [ ]  
**Title:** Monte Carlo return assumptions incorrect  
**Files:** `portfolio.py`  
**Fix:** Use geometric drift μ_geo = μ_arith − σ²/2  
**Criteria:** Correct drift + validation tests

---

## ISSUE-008

**Status:** [ ]  
**Title:** Drawdown calculation incorrect  
**Files:** `risk_metrics.py`  
**Fix:** Proper peak-to-trough tracking  
**Criteria:** Correct max drawdown, handles negative series

---

## ISSUE-009

**Status:** [ ]  
**Title:** Correlation estimation methodology incorrect  
**Files:** `portfolio.py`  
**Fix:** Use covariance-derived correlation matrix  
**Criteria:** Symmetric matrix, no false independence

---

## ISSUE-010

**Status:** [ ]  
**Title:** Survivorship bias in data handling  
**Files:** `sec_data.py`  
**Fix:** Document + mitigate bias  
**Criteria:** Bias acknowledged, optional handling

---

## ISSUE-011

**Status:** [ ]  
**Title:** Missing financial statement handling  
**Files:** `sec_data.py`, `graham.py`, `piotroski.py`, `altman.py`, `portfolio.py`, `risk_metrics.py`, `scorer.py`  
**Fix:** Standardize None checks + fallbacks  
**Criteria:** No silent NaN, safe handling

---

## ISSUE-012

**Status:** [ ]  
**Title:** Project folder structure cleanup  
**Files:** (many - see original)  
**Fix:** Group into logical folders  
**Criteria:** Clean structure, working imports, tests pass

---

# CLOSED ISSUES

(None yet)

**TEST RULE**: Add minimal test `test_issue_XXX_*.py` when needed.

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
