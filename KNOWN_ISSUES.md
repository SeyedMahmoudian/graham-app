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

Status: []

Title: Maring of safety

Priority: Normal

Files: scorer.py

* 

Problem:
*  if margin of saftey is negative from both grahm and buffet we should not advertise buy , and if we still can buy it should come with a big warning
Required Fix:
* modify buy crataria to never promot negative margin of saftey stocks to be in buy list

Acceptance Criteria:
* any stock entering negative saftey margin is no longer a buy and should be down graded to weak
* if only one , either grahm or buffet have negative margin of safety there should be new label indicating that

---

## ISSUE-002

Status: []

Title: Fix FMP Historical Price API (Legacy Endpoint Removal + Correct EOD Usage)

Priority: High

Files: price_client.py

*

Problem:
* FMP historical price requests are using deprecated or invalid endpoints:
  - `/stable/historical-price-eod/{symbol}` ❌ (returns 404)
  - usage of `from=` / `to=` parameters on EOD endpoint ❌ (not supported)
* This causes 404 errors for valid symbols (e.g., NVDA, SPY)
* Incorrect behavior triggers unnecessary fallback to Alpha Vantage
* Current implementation incorrectly assumes server-side date filtering is supported by FMP

Required Fix:
* Replace ALL historical price calls with the correct endpoint:
  - `https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={SYMBOL}&apikey={API_KEY}`
* Remove all usage of:
  - path-based symbol endpoints
  - `from=` and `to=` query parameters for FMP EOD history
* Always fetch full dataset from FMP first
* Perform all date filtering client-side using pandas:
  - `df[df["date"] >= cutoff]`
* Update `_fmp_get_price_history` to:
  - correctly parse `data["historical"]`
  - safely handle missing/empty responses
  - ensure numeric conversion for OHLC fields where applicable
* Ensure 10-year history logic is implemented client-side using:
  - `pd.DateOffset(years=10)`
* Maintain existing monthly resampling logic (no behavioral change)

Acceptance Criteria:
* No historical request uses legacy or invalid FMP endpoints
* No 404 errors occur for valid tickers (e.g., NVDA, SPY)
* All date filtering is performed client-side only
* Alpha Vantage fallback only triggers on true API failure (not empty/invalid parsing)
* Monthly resampling output remains unchanged in structure and correctness
* Logs clearly indicate:
  - "fetching full history from FMP"
  - "filtering client-side to N years"
  - no false fallback due to endpoint misuse

---

## ISSUE-003

Status: []

Title: Incorprate earning revision into app.p

Priority: Normal

Files: earnings_revision.py app.py

* 

Problem:
*  we have the earnings_revision we just need it to show in analyze tab

Acceptance Criteria:
* display earning_revision result in analyze tab

---
---

## ISSUE-004

Status: []

Title: Veirfy new coloring system

Priority: Low

Files: app.py

* 

Problem:
*  the new coloring system in stats row must be logical, if the low number is good then the colors must be green and if higher is worse then red.

Acceptance Criteria:
* corrected and verified that color picking is valid

---

# CLOSED ISSUES


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
