"""
ISSUE-002: Verify new coloring system in stats row is logical.
- Low number is good → green; high number is bad → red
- High number is good → green; low number is bad → red
"""

# ── Replicate the color helpers from app.py ───────────────────────────────────

GREEN = "#00c853"
RED   = "#ff1744"
AMBER = "#ffc107"
MUTED = "#9e9e9e"

def _score_color(val, rule):
    if val is None:
        return MUTED
    direction = rule.get("direction", "high")
    good = rule.get("good_threshold")
    bad  = rule.get("bad_threshold")
    if direction == "high":
        if good is not None and val >= good:
            return GREEN
        if bad is not None and val <= bad:
            return RED
        return AMBER
    else:  # low is better
        if good is not None and val <= good:
            return GREEN
        if bad is not None and val >= bad:
            return RED
        return AMBER

def _mc(val, good_above=None, bad_below=None, good_below=None, bad_above=None):
    if val is None:
        return MUTED
    if good_above is not None and val >= good_above:
        return GREEN
    if good_below is not None and val <= good_below:
        return GREEN
    if bad_above is not None and val >= bad_above:
        return RED
    if bad_below is not None and val <= bad_below:
        return RED
    return AMBER

RULES = {
    "pe":        {"direction": "low",  "good_threshold": 15,  "bad_threshold": 25},
    "pb":        {"direction": "low",  "good_threshold": 1.5, "bad_threshold": 3},
    "roe":       {"direction": "high", "good_threshold": 15,  "bad_threshold": 8},
    "op_margin": {"direction": "high", "good_threshold": 15,  "bad_threshold": 5},
    "sharpe":    {"direction": "high", "good_threshold": 1.0, "bad_threshold": 0.5},
    "beta":      {"direction": "low",  "good_threshold": 1.0, "bad_threshold": 1.5},
    "f_score":   {"direction": "high", "good_threshold": 7,   "bad_threshold": 4},
}

# ── Stats-row tests (_score_color) ────────────────────────────────────────────

def test_pe_low_is_good():
    assert _score_color(12, RULES["pe"]) == GREEN   # below good threshold
    assert _score_color(20, RULES["pe"]) == AMBER   # between
    assert _score_color(30, RULES["pe"]) == RED     # above bad threshold

def test_pb_low_is_good():
    assert _score_color(1.0, RULES["pb"]) == GREEN
    assert _score_color(2.0, RULES["pb"]) == AMBER
    assert _score_color(4.0, RULES["pb"]) == RED

def test_roe_high_is_good():
    assert _score_color(20, RULES["roe"]) == GREEN
    assert _score_color(10, RULES["roe"]) == AMBER
    assert _score_color(5,  RULES["roe"]) == RED

def test_op_margin_high_is_good():
    assert _score_color(20, RULES["op_margin"]) == GREEN
    assert _score_color(10, RULES["op_margin"]) == AMBER
    assert _score_color(3,  RULES["op_margin"]) == RED

def test_sharpe_high_is_good():
    assert _score_color(1.5, RULES["sharpe"]) == GREEN
    assert _score_color(0.7, RULES["sharpe"]) == AMBER
    assert _score_color(0.3, RULES["sharpe"]) == RED

def test_beta_low_is_good():
    # β < 1.0 → defensive → GREEN
    assert _score_color(0.8, RULES["beta"]) == GREEN
    # β between 1.0 and 1.5 → AMBER
    assert _score_color(1.2, RULES["beta"]) == AMBER
    # β > 1.5 → highly volatile → RED
    assert _score_color(1.8, RULES["beta"]) == RED

def test_f_score_high_is_good():
    assert _score_color(8, RULES["f_score"]) == GREEN
    assert _score_color(5, RULES["f_score"]) == AMBER
    assert _score_color(3, RULES["f_score"]) == RED

# ── Risk-card tests (_mc with bad_above support) ──────────────────────────────

def test_mc_beta_low_is_good():
    # β ≤ 1.0 → green; β ≥ 1.5 → red
    assert _mc(0.7, good_below=1.0, bad_above=1.5) == GREEN
    assert _mc(1.2, good_below=1.0, bad_above=1.5) == AMBER
    assert _mc(1.6, good_below=1.0, bad_above=1.5) == RED

def test_mc_volatility_low_is_good():
    # Ann. Volatility < 25% → green; > 40% → red
    assert _mc(20, good_below=25, bad_above=40) == GREEN
    assert _mc(30, good_below=25, bad_above=40) == AMBER
    assert _mc(45, good_below=25, bad_above=40) == RED

def test_mc_sharpe_high_is_good():
    assert _mc(1.2, good_above=1.0, bad_below=0) == GREEN
    assert _mc(0.5, good_above=1.0, bad_below=0) == AMBER
    assert _mc(-0.1, good_above=1.0, bad_below=0) == RED

def test_mc_max_drawdown_high_is_better():
    # drawdown is negative; -10% is better than -40%
    assert _mc(-10, bad_below=-30) == AMBER   # between
    assert _mc(-5,  bad_below=-30) == AMBER
    assert _mc(-35, bad_below=-30) == RED     # below -30 → bad

def test_none_returns_muted():
    assert _score_color(None, RULES["pe"]) == MUTED
    assert _mc(None, good_above=1.0) == MUTED
