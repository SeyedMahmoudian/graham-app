"""
Tests for ISSUE-008: Greenblatt Earnings Yield composite score treatment.

Verifies:
1. enhanced_composite() accepts greenblatt_result without raising.
2. Greenblatt data does NOT alter the composite_score (excluded from weighted sum).
3. Greenblatt raw values are passed through in the return dict for display.
4. magic_score is None for a single stock (requires cross-sectional ranking).
"""

import sys
import os
import math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codes import scorer


def _make_result(score=50, max_=100, **extra):
    base = {"total_score": score, "total_max": max_}
    base.update(extra)
    return base


def _base_args():
    return dict(
        graham_result    = _make_result(50),
        quality_result   = _make_result(50, roe=12),
        momentum_result  = _make_result(50),
        piotroski_result = {"f_score": 5, "f_score_max": 9},
        risk_result      = {"risk_score": 50, "risk_score_max": 100, "risk_criteria": []},
        altman_result    = {"risk_score": 50, "zone": "grey"},
        buffett_result   = _make_result(50),
    )


def test_greenblatt_not_in_composite_score():
    """Passing greenblatt_result must not change composite_score."""
    args = _base_args()
    without_gb = scorer.enhanced_composite(**args)

    gb = {"earnings_yield": 15.0, "roic": 25.0, "magic_score": None}
    with_gb = scorer.enhanced_composite(**args, greenblatt_result=gb)

    assert math.isclose(without_gb["composite_score"], with_gb["composite_score"]), (
        f"composite_score changed: {without_gb['composite_score']} → {with_gb['composite_score']}"
    )


def test_greenblatt_raw_values_passed_through():
    """earnings_yield and roic should appear in the return dict."""
    args = _base_args()
    gb = {"earnings_yield": 12.5, "roic": 30.0, "magic_score": None}
    result = scorer.enhanced_composite(**args, greenblatt_result=gb)

    assert result.get("greenblatt_earnings_yield") == 12.5
    assert result.get("greenblatt_roic") == 30.0
    assert result.get("greenblatt_magic_score") is None


def test_greenblatt_none_is_default():
    """Omitting greenblatt_result (or passing None) should return None values."""
    args = _base_args()
    result = scorer.enhanced_composite(**args)

    assert result.get("greenblatt_earnings_yield") is None
    assert result.get("greenblatt_roic") is None
    assert result.get("greenblatt_magic_score") is None


def test_greenblatt_accepted_without_magic_score_key():
    """compute_single() result with magic_score absent should not raise."""
    args = _base_args()
    gb = {"earnings_yield": 8.0, "roic": 18.0}   # no magic_score key
    result = scorer.enhanced_composite(**args, greenblatt_result=gb)
    assert result.get("greenblatt_earnings_yield") == 8.0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
