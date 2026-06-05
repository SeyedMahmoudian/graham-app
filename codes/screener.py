"""
Screener: batch-scores the full US equity universe.

Threading strategy:
  Phase 1 — Cached stocks (no network):
    ThreadPoolExecutor(max_workers=16) — pure CPU, fully parallel.
    ~3,000 stocks cached → completes in a few seconds.

  Phase 2 — Uncached stocks (need SEC EDGAR fetch):
    ThreadPoolExecutor(max_workers=3) with a token-bucket rate limiter.
    Keeps SEC requests at ≤ 3/sec as a courtesy to the free API.

SEC allows ~10 req/sec. We use 3/sec to stay polite.
"""

import time
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import cache
import sec_data
import graham
import quality
import scorer
import universe


# ── Shared state ──────────────────────────────────────────────────────────────

_progress: dict = {
    "running":  False,
    "phase":    "",       # "cached" | "fetching" | ""
    "total":    0,
    "done":     0,
    "failed":   0,
    "current":  "",
    "results":  [],
}
_lock = threading.Lock()

# ── Rate limiter for SEC fetches (token-bucket, ≤ 3 calls/sec) ───────────────

_sec_rate_lock = threading.Lock()
_sec_last_call = 0.0
_SEC_MIN_GAP   = 0.34   # seconds between SEC requests


def _sec_rate_wait():
    """Block until it's safe to make the next SEC request."""
    global _sec_last_call
    with _sec_rate_lock:
        gap = _SEC_MIN_GAP - (time.time() - _sec_last_call)
        if gap > 0:
            time.sleep(gap)
        _sec_last_call = time.time()


# ── Progress accessors ────────────────────────────────────────────────────────

def get_progress() -> dict:
    with _lock:
        return dict(_progress)


def get_screener_results() -> list[dict]:
    """Return current results sorted by composite_score descending."""
    with _lock:
        return sorted(_progress["results"],
                      key=lambda x: x["composite_score"], reverse=True)


# ── Score one stock ───────────────────────────────────────────────────────────

def _score_one(symbol: str) -> dict | None:
    """Score a single stock from cache or fresh fetch. Returns row dict or None."""
    try:
        cached_sec = cache.read("sec_facts", symbol)
        if not cached_sec:
            _sec_rate_wait()
            cached_sec = sec_data.fetch_company_facts(symbol)
            # Defer cache write to background thread (non-blocking)
            threading.Thread(
                target=cache.write, 
                args=("sec_facts", symbol, cached_sec),
                daemon=True
            ).start()

        g    = graham.score(None, cached_sec)
        q    = quality.score(cached_sec)
        comp = scorer.fundamental_only(g, q)

        return {
            "symbol":          symbol,
            "name":            cached_sec.get("name", symbol),
            "sector":          cached_sec.get("sector", "Unknown"),
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
            # ── Enriched after full analysis ──────────────────────────────────
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

    Updates: graham_pct, quality_pct, composite_score, verdict, graham_number,
             price, and sets analyzed=True.
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


# ── Background loader ─────────────────────────────────────────────────────────

def load_universe_background(tickers: list[str] | None = None):
    """
    Kick off a background thread to score the full universe.
    Safe to call multiple times — no-ops if already running.
    """
    with _lock:
        if _progress["running"]:
            return

    def _worker():
        symbols = tickers or universe.get_universe()

        # Split: cached (instant) vs needs-fetch (rate-limited)
        cached_syms   = [s for s in symbols if cache.read("sec_facts", s) is not None]
        uncached_syms = [s for s in symbols if cache.read("sec_facts", s) is None]

        with _lock:
            _progress.update({
                "running": True,
                "phase":   "cached",
                "total":   len(symbols),
                "done":    0,
                "failed":  0,
                "current": "",
            })

        print(f"\n⚡ Screener: {len(cached_syms)} cached | {len(uncached_syms)} need fetch")

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

        # ── Phase 1: fully parallel for cached stocks ─────────────────────────
        if cached_syms:
            with _lock:
                _progress["phase"] = "cached"
            with ThreadPoolExecutor(max_workers=16) as exe:
                futures = {exe.submit(_score_one, s): s for s in cached_syms}
                for future in as_completed(futures):
                    _record(futures[future], future.result())
            print(f"  ✅ Phase 1 done: {len(cached_syms)} cached stocks scored")
            # Restore prior full-analysis data immediately after Phase 1
            # so users see enriched rows without waiting for Phase 2
            _enrich_from_analysis_cache()

        # ── Phase 2: rate-limited fetching for uncached stocks ────────────────
        if uncached_syms:
            with _lock:
                _progress["phase"] = "fetching"
            print(f"  🌐 Phase 2: fetching {len(uncached_syms)} stocks from SEC EDGAR…")
            with ThreadPoolExecutor(max_workers=3) as exe:
                futures = {exe.submit(_score_one, s): s for s in uncached_syms}
                for future in as_completed(futures):
                    _record(futures[future], future.result())
            print(f"  ✅ Phase 2 done: {len(uncached_syms)} stocks fetched")

        with _lock:
            _progress["running"] = False
            _progress["phase"]   = ""
            _progress["current"] = ""

        total   = _progress["done"]
        failed  = _progress["failed"]
        print(f"\n✅ Screener complete: {total} scored, {failed} failed\n")

    threading.Thread(target=_worker, daemon=True).start()


# ── Enrich screener rows from persisted analysis cache ───────────────────────

def _enrich_from_analysis_cache() -> int:
    """
    After building base screener rows from SEC facts, scan the .cache directory
    for any previously-saved full analysis results (cache kind 'analysis') and
    apply the enriched fields (price, graham_number, buffett_iv, composite_score,
    verdict, etc.) to the matching rows in _progress["results"].

    This restores the post-analysis state of the screener table after a reboot
    so users don't lose the context of stocks they've already analysed.

    Returns the number of rows enriched.
    """
    analysis_symbols = cache.list_cached_kind("analysis")
    if not analysis_symbols:
        return 0

    # Build a fast lookup: symbol → row index
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

        # Mirror the same logic as update_stock_after_analysis()
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
                # Stock wasn't in universe cache — add a minimal row
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


# ── Load cached only (instant startup) ───────────────────────────────────────

def load_cached_only() -> list[dict]:
    """
    Score only already-cached stocks (instant — no network).
    Returns sorted results list and populates shared state.
    After building base rows, restores any prior full-analysis enrichment
    from the persistent analysis cache so reboots don't lose that data.
    """
    symbols = universe.get_cached_universe()
    if not symbols:
        return []

    print(f"  ⚡ load_cached_only: {len(symbols)} symbols in parallel…")
    results = []

    with ThreadPoolExecutor(max_workers=16) as exe:
        futures = {exe.submit(_score_one, s): s for s in symbols}
        for future in as_completed(futures):
            row = future.result()
            if row:
                results.append(row)

    results.sort(key=lambda x: x["composite_score"], reverse=True)

    with _lock:
        _progress["results"] = results

    # Restore enriched analysis data from persistent cache
    _enrich_from_analysis_cache()

    # Re-sort after enrichment so composite scores reflect full analysis
    with _lock:
        _progress["results"].sort(
            key=lambda x: x.get("composite_score") or 0, reverse=True
        )

    print(f"  ✅ {len(_progress['results'])} stocks ready")
    return _progress["results"]