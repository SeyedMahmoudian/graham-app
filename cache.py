import json
import math
import os
import time
from pathlib import Path

CACHE_DIR = Path(".cache")

# Fallback TTL used only for the ticker map (not for company facts).
# Company facts are invalidated by filing date, not wall-clock time.
TICKER_MAP_TTL = 7 * 24 * 60 * 60   # 7 days in seconds

CACHE_DIR.mkdir(exist_ok=True)


def _path(kind: str, key: str) -> Path:
    return CACHE_DIR / f"{kind}-{key.lower()}.json"


# ── JSON encoder that handles numpy / pandas scalar types ─────────────────────

class _SafeEncoder(json.JSONEncoder):
    """
    Converts types that the stdlib json module can't handle:
      - numpy bool_   → Python bool
      - numpy int*    → Python int
      - numpy float*  → Python float  (nan/inf → None)
      - numpy ndarray → list
      - pandas NA / NaT → None
      - plain Python float nan/inf → None
    """

    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                v = float(obj)
                return None if not math.isfinite(v) else v
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass

        try:
            import pandas as pd
            if obj is pd.NA or obj is pd.NaT:
                return None
        except ImportError:
            pass

        return super().default(obj)

    def iterencode(self, o, _one_shot=False):
        return super().iterencode(self._sanitise(o), _one_shot)

    def _sanitise(self, obj):
        if isinstance(obj, float):
            return None if not math.isfinite(obj) else obj
        if isinstance(obj, dict):
            return {k: self._sanitise(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._sanitise(v) for v in obj]
        return obj


def _dumps(data, *, latest_filing: str | None = None) -> str:
    """
    Serialise a cache entry.

    ``latest_filing`` is an ISO date string (e.g. ``"2024-11-05"``).
    When present it is stored alongside the payload so that
    ``is_stale_for_company()`` can compare it against whatever SEC
    reports as the most-recent filing without re-downloading the full
    facts blob.
    """
    payload: dict = {"ts": time.time(), "data": data}
    if latest_filing is not None:
        payload["latest_filing"] = latest_filing
    return json.dumps(payload, indent=2, cls=_SafeEncoder)


# ── Public API ────────────────────────────────────────────────────────────────

def read(kind: str, key: str):
    """
    Return cached data if it exists (no TTL check — callers decide staleness).
    Returns ``None`` when the entry is absent or unreadable.
    """
    p = _path(kind, key)
    try:
        if p.exists():
            entry = json.loads(p.read_text())
            return entry["data"]
    except Exception:
        pass
    return None


def read_entry(kind: str, key: str) -> dict | None:
    """Return the full cache entry dict (including ``ts`` and ``latest_filing``)."""
    p = _path(kind, key)
    try:
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return None


def is_ticker_map_stale(kind: str = "sec", key: str = "tickermap") -> bool:
    """True when the ticker map is older than TICKER_MAP_TTL."""
    entry = read_entry(kind, key)
    if entry is None:
        return True
    age = time.time() - entry["ts"]
    stale = age >= TICKER_MAP_TTL
    if stale:
        print(f"[CACHE STALE] {kind}:{key} — {int(age / 86400)} days old")
    return stale


def is_stale_for_company(symbol: str, sec_latest_filing: str) -> bool:
    """
    Return True when the cached company-facts entry is older than the most
    recent 10-K / 10-Q filing date reported by SEC.

    ``sec_latest_filing`` should be an ISO date string such as ``"2024-11-05"``
    obtained cheaply from the ``/submissions/`` endpoint (a few KB) before
    deciding whether to pull the full ``/companyfacts/`` blob (often >1 MB).
    """
    entry = read_entry("sec_facts", symbol.lower())
    if entry is None:
        print(f"[CACHE MISS]  sec_facts:{symbol} — no cache entry")
        return True

    cached_filing = entry.get("latest_filing")
    if cached_filing is None:
        # Legacy entry written before filing-aware caching; treat as stale.
        print(f"[CACHE STALE] sec_facts:{symbol} — no filing date recorded (legacy entry)")
        return True

    stale = sec_latest_filing > cached_filing
    if stale:
        print(f"[CACHE STALE] sec_facts:{symbol} — "
              f"new filing {sec_latest_filing} > cached {cached_filing}")
    else:
        print(f"[CACHE HIT]   sec_facts:{symbol} — "
              f"up to date (latest filing {cached_filing})")
    return stale


def write(kind: str, key: str, data, *, latest_filing: str | None = None) -> None:
    try:
        _path(kind, key).write_text(_dumps(data, latest_filing=latest_filing))
        suffix = f" (filing {latest_filing})" if latest_filing else ""
        print(f"[CACHE SAVED] {kind}:{key}{suffix}")
    except Exception as e:
        print(f"[CACHE ERROR] {e}")


def list_cached_stocks() -> list[str]:
    return sorted(
        p.stem.replace("quote-", "").upper()
        for p in CACHE_DIR.glob("quote-*.json")
    )


def list_cached_kind(kind: str) -> list[str]:
    prefix = f"{kind}-"
    return sorted(
        p.stem[len(prefix):].upper()
        for p in CACHE_DIR.glob(f"{prefix}*.json")
    )


def clear(kind: str, key: str) -> None:
    p = _path(kind, key)
    if p.exists():
        p.unlink()
        print(f"[CACHE CLEARED] {kind}:{key}")