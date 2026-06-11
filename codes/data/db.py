"""
Persistent value metrics store — SQLite.

Survives process restarts; complements the JSON file cache with a
queryable store that supports ordering by market cap.

Table: value_metrics
  ticker          TEXT PRIMARY KEY
  market_cap      REAL   -- price × shares in $M (for size ordering)
  graham_number   REAL   -- Graham Number (√(22.5 × EPS × BVPS))
  buffett_iv      REAL   -- Buffett two-stage DCF intrinsic value
  composite_score REAL   -- latest enhanced composite score (0-100)
  verdict         TEXT   -- STRONG BUY / BUY / WATCH / HOLD/WEAK / AVOID
  updated_at      TEXT   -- ISO-8601 UTC timestamp of last write
"""

import sqlite3
import datetime
from pathlib import Path

DB_PATH = Path(".cache") / "value_metrics.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS value_metrics (
    ticker          TEXT PRIMARY KEY,
    market_cap      REAL,
    graham_number   REAL,
    buffett_iv      REAL,
    composite_score REAL,
    verdict         TEXT,
    updated_at      TEXT NOT NULL
)
"""

_UPSERT = """
INSERT INTO value_metrics
    (ticker, market_cap, graham_number, buffett_iv, composite_score, verdict, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ticker) DO UPDATE SET
    market_cap      = excluded.market_cap,
    graham_number   = excluded.graham_number,
    buffett_iv      = excluded.buffett_iv,
    composite_score = excluded.composite_score,
    verdict         = excluded.verdict,
    updated_at      = excluded.updated_at
"""

_initialized = False


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    return sqlite3.connect(str(DB_PATH), check_same_thread=False)


def init_db() -> None:
    with _conn() as con:
        con.execute(_CREATE_TABLE)


def _ensure_init() -> None:
    global _initialized
    if not _initialized:
        init_db()
        _initialized = True


def upsert(
    ticker: str,
    *,
    market_cap: float | None = None,
    graham_number: float | None = None,
    buffett_iv: float | None = None,
    composite_score: float | None = None,
    verdict: str | None = None,
) -> None:
    """Insert or update a ticker's value metrics row."""
    _ensure_init()
    now = datetime.datetime.utcnow().isoformat()
    try:
        with _conn() as con:
            con.execute(_UPSERT, (
                ticker.upper(),
                market_cap,
                graham_number,
                buffett_iv,
                composite_score,
                verdict,
                now,
            ))
    except Exception as e:
        print(f"  [DB] upsert failed for {ticker}: {e}")


def get(ticker: str) -> dict | None:
    """Return a single ticker's row, or None if absent."""
    _ensure_init()
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM value_metrics WHERE ticker = ?",
            (ticker.upper(),),
        ).fetchone()
    return dict(row) if row else None


def get_all(order_by: str = "market_cap") -> list[dict]:
    """
    Return all rows ordered by `order_by` descending, NULLs last.

    order_by must be one of the allowed column names to prevent injection.
    """
    _safe_cols = {
        "market_cap", "composite_score", "graham_number",
        "buffett_iv", "updated_at", "ticker",
    }
    col = order_by if order_by in _safe_cols else "market_cap"
    _ensure_init()
    with _conn() as con:
        con.row_factory = sqlite3.Row
        # `col IS NULL` evaluates to 0/1 in SQLite — puts NULLs last
        rows = con.execute(
            f"SELECT * FROM value_metrics ORDER BY {col} IS NULL, {col} DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete(ticker: str) -> None:
    """Remove a ticker's row (e.g. when a portfolio holding is deleted)."""
    _ensure_init()
    with _conn() as con:
        con.execute("DELETE FROM value_metrics WHERE ticker = ?", (ticker.upper(),))


def count() -> int:
    """Return total number of rows."""
    _ensure_init()
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM value_metrics").fetchone()[0]