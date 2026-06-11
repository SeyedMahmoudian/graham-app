"""
Screener: shows the full US equity universe as a table.

Loading strategy (on-demand, no bulk SEC fetches):
  Page load  → build stub rows from ticker map (symbol + name only, instant).
  "Load Universe" button → enrich already-cached stocks with Graham/Quality
                           scores (no new SEC calls).
  Ticker click → app.analyze_stock() fetches SEC data for that one stock
                 and calls update_stock_after_analysis() to enrich its row.

This means the table is always fast to display and SEC EDGAR is only hit
when the user explicitly asks to analyze a specific ticker.

Threading:
  Phase 1 (cached) — ThreadPoolExecutor(max_workers=16), pure CPU.
  No Phase 2 fetch loop — SEC fetches are now on-demand via analyze_stock.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..data import cache
from ..data import sec_data
from ..models import graham, quality
from . import scorer, universe


# ── Shared state ──────────────────────────────────────────────────────────────

_progress: dict = {
    "running":  False,
    "phase":    "",
    "total":    0,
    "done":     0,
    "failed":   0,
    "current":  "",
    "results":  [],
}
_lock = threading.Lock()


# ── Progress accessors ────────────────────────────────────────────────────────

def get_progress() -> dict:
    with _lock:
        return dict(_progress)


def get_screener_results() -> list[dict]:
    """Return current results sorted by composite_score descending."""
    with _lock:
        return sorted(_progress["results"],
                      key=lambda x: x["composite_score"], reverse=True)


# ── Stub row (ticker-map only — no SEC, no scoring) ───────────────────────────

def _stub_row(symbol: str, name: str = "", sector: str = "Unknown") -> dict:
    """
    Minimal row built without any SEC data.
    Shown in the table immediately on page load.
    Enriched later if the user clicks the ticker or the cached scorer runs.
    """
    return {
        "symbol":          symbol,
        "name":            name or symbol,
        "sector":          sector,
        "graham_score":    0,
        "graham_max":      100,
        "graham_pct":      0,
        "quality_score":   0,
        "quality_max":     100,
        "quality_pct":     0,
        "composite_score": 0,
        "verdict":         "PENDING",
        "verdict_label":   "pending",
        "roe":             None,
        "op_margin":       None,
        "eps_years":       0,
        "div_years":       0,
        "graham_number":   None,
        "buffett_iv":      None,
        "price":           None,
        "analyzed":        False,
    }


# ── Score one already-cached stock (no network) ───────────────────────────────

def _score_cached(symbol: str) -> dict | None:
    """
    Score a stock that is already in the local SEC cache.
    Never makes a network call — if cache is missing, returns None.
    """
    try:
        sec = cache.read("sec_facts", symbol)
        if not sec:
            return None

        g    = graham.score(None, sec)
        q    = quality.score(sec)
        comp = scorer.fundamental_only(g, q)

        return {
            "symbol":          symbol,
            "name":            sec.get("name", symbol),
            "sector":          sec.get("sector", "Unknown"),
            "graham_score":    g["total_score"],
            "graham_max":      g["total_max"],
            "graham_pct":      comp["graham_pct"],
            "quality_score":   q["total_score"],
            "quality_max":     q["total_max"],
            "quality_pct":     comp["quality_pct"],
            "composite_score": comp["composite_score"],
            "verdict":         comp["verdict"],
            "verdict_label":   comp["verdict_label"],
            "roe":             q.get("roe"),
            "op_margin":       q.get("op_margin"),
            "eps_years":       g.get("eps_years", 0),
            "div_years":       g.get("div_years", 0),
            "graham_number":   None,
            "buffett_iv":      None,
            "price":           None,
            "analyzed":        False,
        }
    except Exception:
        return None


# ── Update a row after full analysis (from app.analyze_stock) ─────────────────

def update_stock_after_analysis(symbol: str, analysis_result: dict) -> None:
    """
    Overwrite the screener row for `symbol` with accurate data from a full
    analysis (with live price).  Called from app.py after analyze_stock().
    """
    g        = analysis_result.get("graham",   {})
    q        = analysis_result.get("quality",  {})
    enhanced = analysis_result.get("enhanced", {})
    comp     = analysis_result.get("composite",{})

    g_pct      = enhanced.get("graham_pct")    or comp.get("graham_pct",    0)
    q_pct      = enhanced.get("quality_pct")   or comp.get("quality_pct",   0)
    composite  = enhanced.get("composite_score") or comp.get("composite_score", 0)
    verdict    = enhanced.get("verdict")       or comp.get("verdict",       "PENDING")
    vl         = enhanced.get("verdict_label") or comp.get("verdict_label", "pending")

    new_row_data = {
        "graham_pct":      round(g_pct, 1),
        "quality_pct":     round(q_pct, 1),
        "composite_score": round(composite, 1),
        "verdict":         verdict,
        "verdict_label":   vl,
        "graham_number":   g.get("graham_number"),
        "buffett_iv":      analysis_result.get("buffett", {}).get("intrinsic_value"),
        "price":           analysis_result.get("price"),
        "analyzed":        True,
    }

    with _lock:
        for i, row in enumerate(_progress["results"]):
            if row["symbol"] == symbol:
                _progress["results"][i] = {**row, **new_row_data}
                return
        # Symbol not yet in table — add a full row
        _progress["results"].append({
            "symbol":          symbol,
            "name":            analysis_result.get("name",   symbol),
            "sector":          analysis_result.get("sector", "Unknown"),
            "graham_score":    g.get("total_score",    0),
            "graham_max":      g.get("total_max",     100),
            "quality_score":   q.get("total_score",    0),
            "quality_max":     q.get("total_max",     100),
            "roe":             None,
            "op_margin":       None,
            "eps_years":       0,
            "div_years":       0,
            **new_row_data,
        })


# ── Enrich screener rows from persisted analysis cache ────────────────────────

def _enrich_from_analysis_cache() -> int:
    """
    Apply enriched fields from any previously-saved full analysis results
    so reboots don't lose data the user already fetched.
    """
    analysis_symbols = cache.list_cached_kind("analysis")
    if not analysis_symbols:
        return 0

    with _lock:
        idx_map = {row["symbol"]: i for i, row in enumerate(_progress["results"])}

    enriched = 0
    for sym in analysis_symbols:
        data = cache.read("analysis", sym)
        if not data or "error" in data:
            continue

        g        = data.get("graham",   {}) or {}
        q        = data.get("quality",  {}) or {}
        enhanced = data.get("enhanced", {}) or {}
        comp     = data.get("composite",{}) or {}
        b        = data.get("buffett",  {}) or {}

        g_pct     = enhanced.get("graham_pct")    or comp.get("graham_pct",    0)
        q_pct     = enhanced.get("quality_pct")   or comp.get("quality_pct",   0)
        composite = enhanced.get("composite_score") or comp.get("composite_score", 0)
        verdict   = enhanced.get("verdict")       or comp.get("verdict",       "PENDING")
        vl        = enhanced.get("verdict_label") or comp.get("verdict_label", "pending")

        patch = {
            "graham_pct":      round(g_pct, 1),
            "quality_pct":     round(q_pct, 1),
            "composite_score": round(composite, 1),
            "verdict":         verdict,
            "verdict_label":   vl,
            "graham_number":   g.get("graham_number"),
            "buffett_iv":      b.get("intrinsic_value"),
            "price":           data.get("price"),
            "analyzed":        True,
        }

        with _lock:
            i = idx_map.get(sym)
            if i is not None:
                _progress["results"][i] = {**_progress["results"][i], **patch}
            else:
                _progress["results"].append({
                    "symbol":          sym,
                    "name":            data.get("name",   sym),
                    "sector":          data.get("sector", "Unknown"),
                    "graham_score":    g.get("total_score",  0),
                    "graham_max":      g.get("total_max",  100),
                    "graham_pct":      round(g_pct, 1),
                    "quality_score":   q.get("total_score",  0),
                    "quality_max":     q.get("total_max",  100),
                    "quality_pct":     round(q_pct, 1),
                    "composite_score": round(composite, 1),
                    "verdict":         verdict,
                    "verdict_label":   vl,
                    "roe":             q.get("roe"),
                    "op_margin":       q.get("op_margin"),
                    "eps_years":       g.get("eps_years", 0),
                    "div_years":       g.get("div_years", 0),
                    "graham_number":   g.get("graham_number"),
                    "buffett_iv":      b.get("intrinsic_value"),
                    "price":           data.get("price"),
                    "analyzed":        True,
                })
                idx_map[sym] = len(_progress["results"]) - 1
        enriched += 1

    if enriched:
        print(f"  ✅ Enriched {enriched} screener rows from analysis cache")
    return enriched


# ── "Load Universe" button handler ────────────────────────────────────────────

def load_universe_background(tickers: list[str] | None = None):
    """
    Score only already-cached stocks (no new SEC fetches).
    Uncached stocks remain as stub rows — they get scored when the user
    clicks their ticker and analyze_stock() runs.

    Safe to call multiple times — no-ops if already running.
    """
    with _lock:
        if _progress["running"]:
            return

    def _worker():
        symbols = tickers or universe.get_universe()
        cached_syms = [s for s in symbols if cache.read("sec_facts", s) is not None]

        with _lock:
            _progress.update({
                "running": True,
                "phase":   "cached",
                "total":   len(cached_syms),
                "done":    0,
                "failed":  0,
                "current": "",
            })

        print(f"\n⚡ Screener: scoring {len(cached_syms)} cached stocks "
              f"({len(symbols) - len(cached_syms)} uncached — fetch on click)")

        def _record(symbol, row):
            with _lock:
                _progress["current"] = symbol
                _progress["done"]   += 1
                if row:
                    _progress["results"] = [
                        r for r in _progress["results"] if r["symbol"] != symbol
                    ]
                    _progress["results"].append(row)
                else:
                    _progress["failed"] += 1

        if cached_syms:
            with ThreadPoolExecutor(max_workers=16) as exe:
                futures = {exe.submit(_score_cached, s): s for s in cached_syms}
                for future in as_completed(futures):
                    _record(futures[future], future.result())
            print(f"  ✅ Scored {len(cached_syms)} cached stocks")
            _enrich_from_analysis_cache()

        with _lock:
            _progress["running"] = False
            _progress["phase"]   = ""
            _progress["current"] = ""

        total  = _progress["done"]
        failed = _progress["failed"]
        print(f"✅ Screener ready: {total} scored, {failed} failed\n")

    threading.Thread(target=_worker, daemon=True).start()


# ── Startup: instant table from ticker map + cached analysis ──────────────────

def load_cached_only() -> list[dict]:
    """
    Called at startup.  Builds the initial screener table instantly:
      1. Stub rows for every ticker in the universe (name from ticker map).
      2. Overwrite stubs with scored rows for any already-cached SEC data.
      3. Enrich with full analysis data for tickers the user has clicked before.

    No SEC EDGAR requests are made here.
    """
    all_symbols = universe.get_universe()
    if not all_symbols:
        return []

    # Build name map from SEC ticker map (already cached locally)
    try:
        ticker_map = sec_data.get_ticker_map()
    except Exception:
        ticker_map = {}

    # Step 1: stub rows for every symbol
    results: list[dict] = []
    for sym in all_symbols:
        entry = ticker_map.get(sym, {})
        results.append(_stub_row(sym, name=entry.get("name", sym)))

    with _lock:
        _progress["results"] = results

    # Step 2: score already-cached stocks (parallel, no network)
    cached_syms = [s for s in all_symbols if cache.read("sec_facts", s) is not None]
    if cached_syms:
        print(f"  ⚡ Scoring {len(cached_syms)} cached stocks at startup…")
        scored: list[dict] = []
        with ThreadPoolExecutor(max_workers=16) as exe:
            futures = {exe.submit(_score_cached, s): s for s in cached_syms}
            for future in as_completed(futures):
                row = future.result()
                if row:
                    scored.append(row)

        # Merge scored rows into results list
        scored_map = {r["symbol"]: r for r in scored}
        with _lock:
            _progress["results"] = [
                scored_map.get(r["symbol"], r)
                for r in _progress["results"]
            ]

    # Step 3: restore full-analysis enrichment
    _enrich_from_analysis_cache()

    with _lock:
        _progress["results"].sort(
            key=lambda x: x.get("composite_score") or 0, reverse=True
        )

    total_scored = len(cached_syms)
    total_stubs  = len(all_symbols) - total_scored
    print(f"  ✅ {total_scored} scored + {total_stubs} stub rows ready (no SEC fetches)")
    return _progress["results"]
