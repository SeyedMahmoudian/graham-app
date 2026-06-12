"""
Alternative Data Model — P4 framework-only module.

This module defines the stable schema for alternative data signals without
calling paid or external providers yet. Each signal is a neutral stub so the
app can render the framework while composite scoring remains deterministic.

Signals:
  - web_traffic:     site/app traffic trend placeholder
  - hiring_velocity: job posting / headcount trend placeholder
  - sentiment:       news/social/customer sentiment placeholder
"""

from __future__ import annotations

from typing import Any


STUB_STATUS = "STUB"
NEUTRAL_SCORE = 50.0


def _stub_signal(name: str, label: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "score": NEUTRAL_SCORE,
        "signal": "NEUTRAL",
        "status": STUB_STATUS,
        "available": False,
        "value": None,
        "description": description,
        "source": None,
    }


def get_web_traffic_signal(ticker: str) -> dict[str, Any]:
    """Return the web traffic placeholder signal."""
    return _stub_signal(
        "web_traffic",
        "Web Traffic",
        "Placeholder for website/app traffic trend data.",
    )


def get_hiring_velocity_signal(ticker: str) -> dict[str, Any]:
    """Return the hiring velocity placeholder signal."""
    return _stub_signal(
        "hiring_velocity",
        "Hiring Velocity",
        "Placeholder for job posting or headcount growth trend data.",
    )


def get_sentiment_signal(ticker: str) -> dict[str, Any]:
    """Return the sentiment placeholder signal."""
    return _stub_signal(
        "sentiment",
        "Sentiment",
        "Placeholder for news, social, review, or customer sentiment data.",
    )


def get_alternative_data_score(ticker: str) -> dict[str, Any]:
    """
    Return a neutral, JSON-compatible alternative-data framework result.

    The output includes total_score / total_max for future scorer compatibility,
    but app/scorer wiring deliberately treats it as display-only until real
    provider-backed data exists.
    """
    symbol = ticker.upper().strip()
    signals = [
        get_web_traffic_signal(symbol),
        get_hiring_velocity_signal(symbol),
        get_sentiment_signal(symbol),
    ]

    return {
        "ticker": symbol,
        "alternative_data_score": NEUTRAL_SCORE,
        "signal": "NEUTRAL",
        "status": STUB_STATUS,
        "available": False,
        "low_coverage": True,
        "provider": None,
        "signals": signals,
        "total_score": NEUTRAL_SCORE,
        "total_max": 100.0,
    }
