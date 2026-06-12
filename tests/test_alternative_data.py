"""
Tests for P4 alternative_data.py framework stubs.

The module is intentionally provider-free for now: it must return a stable,
neutral schema without performing network access or affecting composite scores.
"""

import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "codes", "engine"))
sys.path.insert(0, os.path.join(ROOT, "codes", "models"))

import alternative_data
import scorer


def _base_scorer_args(**overrides):
    base = dict(
        graham_result={"total_score": 50, "total_max": 100},
        quality_result={"total_score": 50, "total_max": 100, "roe": 12},
        momentum_result={"total_score": 50, "total_max": 100},
        piotroski_result={"f_score": 5},
        risk_result={"risk_score": 50, "risk_score_max": 100},
        altman_result={"risk_score": 50, "zone": "safe"},
        buffett_result={"total_score": 50, "total_max": 100},
    )
    base.update(overrides)
    return base


def test_alternative_data_schema_is_neutral_stub():
    result = alternative_data.get_alternative_data_score(" aapl ")

    assert result["ticker"] == "AAPL"
    assert result["alternative_data_score"] == pytest.approx(50.0)
    assert result["total_score"] == pytest.approx(50.0)
    assert result["total_max"] == pytest.approx(100.0)
    assert result["signal"] == "NEUTRAL"
    assert result["status"] == "STUB"
    assert result["available"] is False
    assert result["low_coverage"] is True
    assert result["provider"] is None


def test_alternative_data_includes_three_stub_signals():
    result = alternative_data.get_alternative_data_score("MSFT")
    signals = result["signals"]

    assert [s["name"] for s in signals] == [
        "web_traffic",
        "hiring_velocity",
        "sentiment",
    ]
    assert all(s["score"] == pytest.approx(50.0) for s in signals)
    assert all(s["signal"] == "NEUTRAL" for s in signals)
    assert all(s["status"] == "STUB" for s in signals)
    assert all(s["available"] is False for s in signals)


def test_framework_only_does_not_change_enhanced_composite_weights():
    assert "alternative_data" not in scorer.ENHANCED_WEIGHTS

    baseline = scorer.enhanced_composite(**_base_scorer_args())
    with_alt_data_available = scorer.enhanced_composite(**_base_scorer_args())

    assert with_alt_data_available["composite_score"] == baseline["composite_score"]
