"""
Graham Score App — Full Quant Version
Pure Python / Dash with SEC EDGAR + Alpha Vantage
Enhanced score uses the orthogonal factor weights defined in codes.engine.scorer.
"""
import traceback
import sys
import os
from data import api_fetcher
# Allow both `python app.py` (direct) and `python -m codes.app` (module) execution.
# Inserts the project root so that `codes.*` package imports resolve in both cases.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dash
from dash import dcc, html, Input, Output, State, callback
import plotly.graph_objects as go
import pandas as pd
import json
import shutil
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
import hashlib
import functools
from codes.data   import cache, sec_data
from codes.models import graham, quality, momentum, piotroski, altman, risk_metrics, greenblatt, buffett, earnings_revision, profitability as profitability_model, fcf_quality as fcf_quality_model, capital_allocation as capital_allocation_model, growth_quality as growth_quality_model, regime as regime_model, insider_activity as insider_activity_model, factor_momentum as factor_momentum_model, alternative_data as alternative_data_model,options_signal_engine as options_signal_model
from codes.engine import scorer, screener, universe
import codes.portfolio as portfolio_engine
# ── App Init ──────────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    title="Graham Score — Quant",
    suppress_callback_exceptions=True,
    assets_folder='../assets',
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}]
)
server = app.server
@server.after_request
def _log_errors(response):
    return response
# Patch Dash's internal callback handler to log exceptions
_orig_dispatch = app.server.dispatch_request if hasattr(app.server, 'dispatch_request') else None
_orig_cb = dash.Dash.callback
def _logging_callback(self, *args, **kwargs):
    decorator = _orig_cb(self, *args, **kwargs)
    def wrap(func):
        @functools.wraps(func)
        def inner(*a, **kw):
            try:
                return func(*a, **kw)
            except Exception:
                print(f"\n[CALLBACK ERROR] in {func.__name__}", flush=True)
                traceback.print_exc()
                raise
        return decorator(inner)
    return wrap
dash.Dash.callback = _logging_callback
# ── Mobile touch fix: eliminate 300ms tap delay on all buttons ───────────────
app.index_string = app.index_string.replace(
    '</head>',
    '<style>'
    'button,a,[role="button"]{'
    'touch-action:manipulation;'
    '-webkit-tap-highlight-color:rgba(0,0,0,0.08);'
    'cursor:pointer;'
    '}'
    '</style></head>'
)
app.index_string = app.index_string.replace(
    '</head>',
    '<script>'
    '(function(){'
    '  let savedScroll = 0;'
    '  window.addEventListener("orientationchange", function(){'
    '    savedScroll = window.scrollY;'
    '  });'
    '  window.addEventListener("resize", function(){'
    '    if (savedScroll > 0) {'
    '      requestAnimationFrame(function(){'
    '        window.scrollTo(0, savedScroll);'
    '      });'
    '    }'
    '  });'
    '})();'
    '</script></head>'
)

app.index_string = app.index_string.replace(
    '</head>',
    '<script>'
    'const APP_VERSION = "v3.1";'  # bump this on each deploy
    'if (localStorage.getItem("app_version") !== APP_VERSION) {'
    '    localStorage.setItem("app_version", APP_VERSION);'
    '    location.reload(true);'
    '}'
    '</script></head>'
)
# ── Color Theme (CSS vars in style.css, keeping for reference) ────────────────
DARK, CARD, BORDER, GREEN, RED, AMBER, BLUE, TEXT, MUTED ,WHITE= (
    "#0f1117", "#1a1d27", "#2a2d3e", "#00c853", "#ff1744",
    "#ffc107", "#448aff", "#e0e0e0", "#9e9e9e", "#ffffff"
)
# ── Performance Optimization: Module-level caches ─────────────────────────────
_spy_history = None
_spy_history_lock = threading.Lock()
_analysis_cache = {}
_analysis_cache_lock = threading.Lock()
_last_screener_state = None
# ── Moat grade tooltips (shown on hover in Buffett badge) ────────────────────
_MOAT_TOOLTIPS = {
    "A": (
        "Wide Moat (A) — The company has a durable, hard-to-replicate competitive advantage "
        "that protects its profits for 10–20+ years. Examples: strong brand (Coca-Cola), "
        "network effects (Visa), switching costs (Oracle), low-cost producer (GEICO), "
        "or regulatory monopoly. ROE ≥15% consistently, high margins, strong FCF growth, "
        "and the stock is trading at or below Buffett's DCF intrinsic value."
    ),
    "B": (
        "Narrow Moat (B) — The company has a real but limited competitive advantage that "
        "may erode within 5–10 years without reinvestment. It earns above-average returns "
        "but faces meaningful competitive pressure. Solid ROE and margins, but not the "
        "consistent dominance Buffett prizes most. Worth owning at the right price."
    ),
    "C": (
        "No Clear Moat (C) — The company competes in a commoditised or highly competitive "
        "market with no evident structural advantage. Returns on capital are average or "
        "inconsistent. Buffett would typically avoid these unless the price is exceptionally "
        "cheap — and even then, he prefers 'a wonderful company at a fair price' over "
        "'a fair company at a wonderful price'."
    ),
    "D": (
        "Avoid (D) — Weak fundamentals: poor or declining ROE, thin or negative margins, "
        "heavy debt load, weak cash generation, or the stock is significantly overvalued "
        "vs its DCF intrinsic value. Buffett's rule #1: never lose money. Rule #2: "
        "don't forget rule #1. This stock fails on multiple quality or value criteria."
    ),
}
# ── State ──────────────────────────────────────────────────────────────────────
_last_screener_results = None
_last_progress_state = None
_last_progress_bar_state = None
# ── Helpers ───────────────────────────────────────────────────────────────────
def _get_spy_history_lazy():
    """Fetch SPY history once at startup, cache it module-level. Subsequent calls are instant."""
    global _spy_history
    if _spy_history is not None:
        return _spy_history
    
    with _spy_history_lock:
        if _spy_history is not None:  # Double-check after acquiring lock
            return _spy_history
        try:
            _spy_history = api_fetcher.get_price_history("SPY", years=10)
        except Exception as e:
            print(f"Failed to fetch SPY history: {e}")
            _spy_history = None  # Cache failure so we don't retry every time
        return _spy_history
def analyze_stock(symbol: str) -> dict:
    """Full pipeline: SEC → Graham + Quality + (Price→Momentum) → Composite.
    
    Optimizations:
    - 1A: In-memory cache for repeat lookups
    - 1G: Eliminate redundant graham.score(None, ...) call
    - 1B: Parallelize network fetches with ThreadPoolExecutor
    - 1C: Lazy-load SPY history once, reuse across all stocks
    """
    global _analysis_cache, _analysis_cache_lock
    
    symbol = symbol.upper().strip()
    # 1A: Check in-memory cache first (zero disk I/O for repeat lookups)
    with _analysis_cache_lock:
        if symbol in _analysis_cache:
            return _analysis_cache[symbol]
    # Then try disk cache
    cached = cache.read("analysis", symbol)
    if cached:
        with _analysis_cache_lock:
            _analysis_cache[symbol] = cached
        return cached
    # Fetch SEC fundamentals — lazy: returns cache instantly when not stale
    try:
        sec_facts = sec_data.get_financials(symbol)
    except ValueError as e:
        err_msg = str(e)
        # Provide a more actionable message for foreign-listed tickers that
        # don't file with the SEC (e.g. BMO, TD, RY, SHOP.TO, etc.)
        if "not found in SEC database" in err_msg:
            err_msg = (
                f"{err_msg}. This app uses SEC EDGAR filings, which only covers "
                "US-listed companies that file 10-K/10-Q reports. Foreign-listed "
                "or OTC-only tickers (e.g. Canadian banks like BMO, TD, RY) are "
                "not supported. Try the US-listed ADR or a US-domiciled equivalent."
            )
        return {"error": err_msg}
    except Exception as e:
        return {"error": f"SEC EDGAR error: {e}"}
    # Quality score (no price) — early calculation
    q = quality.score(sec_facts)
    # Now try to get price
    price = api_fetcher.get_price(symbol)
    # Earnings revision score
    earnings_revision_result = {"total_score": 0, "total_max": 100, "criteria": []}
    if price:
        try:
            earnings_revision_result = earnings_revision.get_revision_score(symbol)
        except Exception as e:
            print(f"Earnings revision calculation failed: {e}")
    hist = None
    spy_hist = None
    
    # 1B: Parallelize price history fetches with ThreadPoolExecutor
    if price:
        with ThreadPoolExecutor(max_workers=2) as executor:
            # Fetch stock history + use lazy-loaded SPY history
            hist_future = executor.submit(
                api_fetcher.get_price_history, symbol, 10
            )
            spy_hist_future = executor.submit(_get_spy_history_lazy)
            
            try:
                hist = hist_future.result(timeout=30)
            except Exception as e:
                print(f"Price history fetch failed for {symbol}: {e}")
            
            try:
                spy_hist = spy_hist_future.result(timeout=30)
            except Exception as e:
                print(f"SPY history fetch failed: {e}")
    # 1G: Calculate Graham score WITH price (if available), eliminating redundant call
    g = graham.score(price, sec_facts) if price else graham.score(None, sec_facts)
    # Momentum score (needs price history)
    m_result = {"total_score": 0, "total_max": 100, "criteria": []}
    if price and hist is not None:
        try:
            m_result = momentum.score(hist, spy_hist, symbol)
        except Exception as e:
            print(f"Momentum calculation failed: {e}")
    # Original composite (kept for backward-compat with screener)
    comp = scorer.composite(g, q, m_result)
    # ── New quant modules ─────────────────────────────────────────────────
    piotroski_result = piotroski.score(sec_facts)
    altman_result = altman.score(price, sec_facts)
    risk_result = {"risk_score": 50, "risk_score_max": 100, "risk_criteria": []}
    if hist is not None and not hist.empty:
        try:
            risk_result = risk_metrics.score(hist, spy_hist)
        except Exception as e:
            print(f"Risk metrics calculation failed: {e}")
    greenblatt_result = greenblatt.compute_single(price, sec_facts)
    buffett_result = buffett.score(price, sec_facts)
    # Profitability score (P1)
    profitability_result = None
    try:
        profitability_result = profitability_model.ProfitabilityAnalyzer(symbol, sec_facts).get_profitability_score()
    except Exception as e:
        print(f"Profitability calculation failed: {e}")
    # FCF Quality score (P1)
    fcf_quality_result = None
    try:
        fcf_quality_result = fcf_quality_model.FCFQualityAnalyzer(symbol, sec_facts).get_fcf_quality_score()
    except Exception as e:
        print(f"FCF quality calculation failed: {e}")

    # Capital Allocation score (P2)
    capital_allocation_result = None
    try:
        capital_allocation_result = capital_allocation_model.CapitalAllocationAnalyzer(
            symbol, sec_facts, price
        ).get_capital_allocation_score()
    except Exception as e:
        print(f"Capital allocation calculation failed: {e}")
    # Growth Quality score (P2)
    growth_quality_result = None
    try:
        growth_quality_result = growth_quality_model.GrowthQualityAnalyzer(
            symbol, sec_facts
        ).get_growth_quality_score()
    except Exception as e:
        print(f"Growth quality calculation failed: {e}")
    # Insider Activity (P4)
    insider_activity_result = None
    try:
        transactions = api_fetcher.get_insider_transactions(symbol)
        shares_out = None
        sh_recs = sec_facts.get("shares", [])
        if sh_recs:
            try:
                shares_out = float(sh_recs[0]["value"])
            except (KeyError, TypeError, ValueError):
                pass
        insider_activity_result = insider_activity_model.get_insider_score(
            symbol, transactions, shares_outstanding=shares_out
        )
    except Exception as e:
        print(f"Insider activity calculation failed: {e}")
    # Factor Momentum (P4)
    factor_momentum_result = None
    try:
        factor_momentum_result = (
            factor_momentum_model.FactorMomentumAnalyzer(
                symbol,
                hist,
                sec_facts
            ).get_factor_momentum_score()
        )
    except Exception as e:
        print(f"Factor momentum calculation failed: {e}")
    # Alternative Data (P4 framework only)
    alternative_data_result = None
    try:
        alternative_data_result = alternative_data_model.get_alternative_data_score(symbol)
    except Exception as e:
        print(f"Alternative data framework failed: {e}")
    # Enhanced orthogonal composite
    enhanced = scorer.enhanced_composite(
        g, q, m_result, piotroski_result, risk_result, altman_result, buffett_result,
        greenblatt_result=greenblatt_result, earnings_revision_result=earnings_revision_result,
        profitability_result=profitability_result, fcf_quality_result=fcf_quality_result,
        capital_allocation_result=capital_allocation_result,
        growth_quality_result=growth_quality_result,
        factor_momentum_result=factor_momentum_result,
    )

    # Regime overlay — uses SPY history already loaded above (portfolio risk layer)
    regime_result = None
    if spy_hist is not None and not spy_hist.empty:
        try:
            regime_result = regime_model.score(spy_hist)
        except Exception as e:
            print(f"Regime calculation failed: {e}")
    regime_overlay = scorer.apply_regime_overlay(
        enhanced.get("composite_score", 0), regime_result
    )
    # Options Signal (P4) — depends on regime + risk + price history
    options_signal_result = None
    try:
        options_signal_result = options_signal_model.get_options_signal(
            symbol, price_hist=hist, regime_result=regime_result,
            risk_result=risk_result, current_price=price,
        )
    except Exception as e:
        print(f"Options signal calculation failed: {e}")
    # Market cap for persistence/screener ordering.
    # Prefer graham.score()'s value (price × shares, $M); if unavailable
    # (no live price), fall back to live price (Tiingo/Finnhub via
    # api_fetcher.get_price) × shares outstanding from sec_facts.
    market_cap = g.get("market_cap")
    if market_cap is None and price:
        try:
            shares_recs = sec_facts.get("shares", [])
            shares_val = float(shares_recs[0]["value"]) if shares_recs else None
            if shares_val:
                market_cap = price * shares_val / 1e6
        except (KeyError, TypeError, ValueError, IndexError):
            pass
    result = {
        "symbol":    symbol,
        "name":      sec_facts["name"],
        "sector":    sec_facts["sector"],
        "price":     price,
        "market_cap": market_cap,
        "graham":    g,
        "quality":   q,
        "momentum":  m_result,
        "composite": comp,
        # ── New ──────────────────────────────────────────
        "piotroski":   piotroski_result,
        "altman":      altman_result,
        "risk":        risk_result,
        "greenblatt":  greenblatt_result,
        "buffett":     buffett_result,
        "earnings_revision": earnings_revision_result,
        "profitability": profitability_result,
        "fcf_quality": fcf_quality_result,
        "capital_allocation": capital_allocation_result,
        "growth_quality": growth_quality_result,
        "insider_activity":   insider_activity_result,
        "factor_momentum": factor_momentum_result,
        "alternative_data": alternative_data_result,
        "regime":             regime_result,
        "regime_overlay":     regime_overlay,
        "enhanced":    enhanced,
        "options_signal":     options_signal_result,
        # ─────────────────────────────────────────────────
        "price_history": hist.to_dict() if hist is not None else None,
        "spy_history": spy_hist.to_dict() if spy_hist is not None else None,
    }
    # 1F: Defer cache writes to daemon thread (already handled in screener.py)
    cache.write("analysis", symbol, result)
    
    # 1A: Update in-memory cache
    with _analysis_cache_lock:
        _analysis_cache[symbol] = result
    
    return result

def get_score_class(pct: float) -> str:
    """CSS class for score coloring."""
    if pct >= 65:
        return "high"
    elif pct >= 35:
        return "medium"
    else:
        return "low"

def get_verdict_class(label: str) -> str:
    """CSS class for verdict coloring."""
    return label.lower().replace(" ", "-") if label else "pending"

# ── Layout ────────────────────────────────────────────────────────────────────
app.layout = html.Div(className="app-container", children=[
    # Header
    html.Div(className="app-header", children=[
        html.Div("📊", className="app-header-icon"),
        html.Div(className="app-header-content", children=[
            html.H1("Graham Score — Quant Edition"),
            html.P("Orthogonal factor score: Value, Quality, Momentum, Profitability, FCF Quality, Earnings Revisions, Capital Allocation, Growth Quality, Risk, and Altman.")
        ])
    ]),
    # Tabs
    html.Div(className="tab-bar", children=[
        html.Button("📊 Screener",  id="tab-screener-btn",  className="tab-btn active"),
        html.Button("🔍 Analyze",   id="tab-analyze-btn",   className="tab-btn"),
        html.Button("💼 Portfolios", id="tab-portfolio-btn", className="tab-btn"),
    ]),
    # ── Tab: Screener ────────────────────────────────────────────────────────
    html.Div(id="tab-screener", className="screener-content block", children=[
        html.Div(className="screener-toolbar", children=[
            html.Div(className="screener-controls", children=[
                html.Button(
                    "Load Universe (Russell 3000 + Microcap)",
                    id="load-universe-btn",
                    className="load-btn",
                    n_clicks=0,
                    disabled=False
                ),
                html.Div(id="screener-progress-info", className="screener-info"),
            ]),
            html.Div(className="screener-controls flex gap-lg align-items-center", children=[
                html.Label("Filter by sector:", className="text-sm text-muted"),
                dcc.Dropdown(
                    id="sector-filter",
                    options=[{"label": "All Sectors", "value": ""}],
                    value="",
                    clearable=False,
                    style={
                        "background": CARD,
                        "border": f"1px solid {BORDER}",
                        "borderRadius": "10px",
                        "color": TEXT,
                        "width": "200px"
                    }
                ),
            ]),
        ]),
        html.Div(id="screener-progress", className="mb-2xl"),
        dcc.Loading(
            id="screener-loading",
            type="default",
            color=BLUE,
            children=[
                html.Div(id="screener-table-container", className="screener-table-wrap", children=[
                    html.Div("Loading screener data...", className="text-center p-4xl text-muted")
                ])
            ]
        ),
    ]),
    # ── Tab: Analyze ─────────────────────────────────────────────────────────
    html.Div(id="tab-analyze", className="main-content", children=[
        html.Div(className="search-section", children=[
            html.Div(className="search-container", children=[
                html.Div(className="search-input-wrapper", children=[
                    dcc.Input(
                        id="ticker-input",
                        type="text",
                        placeholder="Enter stock ticker (e.g. KO, JNJ, XOM)",
                        debounce=False,
                        className="ticker-input",
                        disabled=False
                    ),
                    html.Button("Analyze", id="analyze-btn", className="analyze-btn", disabled=False)
                ]),
                html.Div(id="status-msg", className="status-msg"),
            ]),
        ]),
        html.Div(id="history-section", className="history-section"),
        
        dcc.Loading(
            id="analysis-loading",
            type="default",
            color=BLUE,
            children=[
                html.Div(id="analysis-content", children=[])
            ]
        ),
        # ── Add to Portfolio panel (shown after analysis completes) ──────────
        html.Div(id="add-to-portfolio-panel",children=[
            html.Div(className="portfolio-add-panel", children=[
                html.Div(className="portfolio-add-header", children=[
                    html.Span("💼", className="text-2xl"),
                    html.Span("Add to Portfolio", className="font-semibold text-lg"),
                ]),
                html.Div(className="portfolio-add-controls", children=[
                    dcc.Dropdown(
                        id="portfolio-select-dropdown",
                        placeholder="Select or create portfolio…",
                        clearable=True,
                        className="min-w-220",
                    ),
                    dcc.Input(
                        id="portfolio-new-name",
                        type="text",
                        placeholder="Or type new portfolio name…",
                        className="max-w-220 ticker-input"
                    ),
                    dcc.Input(
                        id="portfolio-shares-input",
                        type="number",
                        placeholder="Shares (min 5)",
                        min=5,
                        step=1,
                        className="ticker-input max-w-130"
                    ),
                    html.Button("Add", id="portfolio-add-btn", className="analyze-btn", n_clicks=0),
                ]),
                html.Div(id="portfolio-add-msg", style={"fontSize": "13px", "marginTop": "6px"}),
            ])
        ]),
    ]),
    # ── Tab: Portfolios ──────────────────────────────────────────────────────
    html.Div(id="tab-portfolio", className="main-content", children=[
        # Top toolbar: portfolio switcher + create + compare
        html.Div(className="screener-toolbar", children=[
            html.Div(className="screener-controls", children=[
                dcc.Dropdown(
                    id="portfolio-active-dropdown",
                    placeholder="Select a portfolio…",
                    clearable=False,
                    className="min-w-240",
                ),
                html.Button("＋ New Portfolio", id="portfolio-new-btn",
                            className="load-btn", n_clicks=0),
                html.Button("🗑 Delete", id="portfolio-delete-btn",
                            className="load-btn",
                            style={"background": "#2a1a1a", "borderColor": "#ff1744"},
                            n_clicks=0),
            ]),
            html.Div(className="screener-controls", children=[
                html.Label("Compare:", style={"fontSize": "13px", "color": "#9e9e9e"}),
                dcc.Dropdown(
                    id="portfolio-compare-dropdown",
                    placeholder="Add portfolio to compare…",
                    clearable=True,
                    className="min-w-200",
                ),
            ]),
        ]),
        # New portfolio name modal (inline, hidden by default)
        html.Div(id="portfolio-create-panel", className="hidden", children=[
            html.Div(className="portfolio-add-panel", children=[
                html.Span("Name your portfolio:", className="text-primary"),
                dcc.Input(id="portfolio-create-name", type="text",
                          placeholder="e.g. Value Picks Q1",
                          className="ticker-input max-w-240"),
                html.Button("Create", id="portfolio-create-confirm-btn",
                            className="analyze-btn", n_clicks=0),
                html.Button("Cancel", id="portfolio-create-cancel-btn",
                            className="load-btn", n_clicks=0),
                html.Div(id="portfolio-create-msg",
                         style={"fontSize": "13px", "color": "#ff1744"}),
            ])
        ]),
        html.Div(id="portfolio-msg", style={"fontSize": "13px", "padding": "4px 0 8px"}),
        # Main portfolio content (holdings + run sim button)
        dcc.Loading(type="default", color="#448aff", children=[
            html.Div(id="portfolio-content", children=[
                html.Div("Select or create a portfolio to get started.",
                         style={"textAlign": "center", "padding": "60px", "color": "#9e9e9e"})
            ])
        ]),
        # Simulation results (charts)
        html.Div(id="portfolio-sim-results", children=[]),
    ]),
    # Stores
    dcc.Store(id="screener-cache"),
    dcc.Store(id="analysis-store"),
    dcc.Store(id="screener-sort-store", data={"col": "composite_score", "asc": False}),
    dcc.Store(id="screener-visible-count", data=50, storage_type="session"),  # infinite scroll — rows rendered so far, session-persisted
    dcc.Store(id="search-history-store"),
    dcc.Store(id="screener-click-ticker"),   # symbol clicked in screener table
    dcc.Store(id="portfolio-refresh-store", data=0),  # increment to trigger refresh
    dcc.Store(id="active-analysis-symbol"),           # symbol currently analyzed
    dcc.Store(id="screener-ready-store",  data=0),    # bumped once when loading completes
    dcc.Store(id="screener-viewed-store", data=[]),   # symbols the user has analyzed
    dcc.Store(id="screener-scroll-pos", data=0, storage_type="session"),  # remembered scroll position for screener tab
    # interval disabled=True once loading finishes to stop constant re-renders
    dcc.Interval(id="screener-progress-interval", interval=2000, disabled=True),
    # fires once 600ms after page load to render already-cached screener data
    # and re-enable the progress interval so a post-refresh render always works
    dcc.Interval(id="page-load-interval", interval=600, max_intervals=1, disabled=False),
    # polls the screener tab's scroll position so it can be restored on tab switch
    dcc.Interval(id="screener-scroll-poll-interval", interval=1000, disabled=False),
    dcc.Loading(id="loading", type="circle", color=BLUE, children=html.Div(id="loading-trigger"))
])

# ── Tab Navigation ───────────────────────────────────────────────────────────
@callback(
    Output("tab-screener",     "style"),
    Output("tab-analyze",      "style"),
    Output("tab-portfolio",    "style"),
    Output("tab-screener-btn", "className"),
    Output("tab-analyze-btn",  "className"),
    Output("tab-portfolio-btn","className"),
    Input("tab-screener-btn",     "n_clicks"),
    Input("tab-analyze-btn",      "n_clicks"),
    Input("tab-portfolio-btn",    "n_clicks"),
    Input("screener-click-ticker","data"),
    prevent_initial_call=False
)
def switch_tabs(n_screener, n_analyze, n_portfolio, clicked_ticker):
    triggered = dash.ctx.triggered_id
    SHOW, HIDE = {"display": "block"}, {"display": "none"}
    ACTIVE, IDLE = "tab-btn active", "tab-btn"
    if triggered == "screener-click-ticker" and clicked_ticker:
        return HIDE, SHOW, HIDE, IDLE, ACTIVE, IDLE
    if triggered == "tab-analyze-btn":
        return HIDE, SHOW, HIDE, IDLE, ACTIVE, IDLE
    if triggered == "tab-portfolio-btn":
        return HIDE, HIDE, SHOW, IDLE, IDLE, ACTIVE
    # Default: screener
    return SHOW, HIDE, HIDE, ACTIVE, IDLE, IDLE

# ── Screener ticker-click → store ─────────────────────────────────────────────
@callback(
    Output("screener-click-ticker", "data"),
    Input({"type": "screener-ticker-btn", "index": dash.ALL}, "n_clicks"),
    prevent_initial_call=True
)
def capture_screener_click(n_clicks_list):
    # Find which button was just clicked
    triggered = dash.ctx.triggered_id
    if not triggered or not any(n for n in n_clicks_list if n):
        return dash.no_update
    return triggered["index"]  # the symbol string

# ── Screener ──────────────────────────────────────────────────────────────────
# In-memory portfolio cache — guards against redundant reads within one render cycle
_portfolio_cache: dict = {"symbols": {}, "ts": 0.0}
def _invalidate_portfolio_cache() -> None:
    global _portfolio_cache
    _portfolio_cache = {"symbols": {}, "ts": 0.0}
def _get_portfolio_symbols() -> dict[str, list[str]]:
    """Return {symbol: [portfolio_name, ...]}, cached for 10 seconds.
    Always force-cleared by _invalidate_portfolio_cache() on any mutation."""
    import time as _t
    global _portfolio_cache
    if _t.time() - _portfolio_cache["ts"] < 10:
        return _portfolio_cache["symbols"]
    result: dict[str, list[str]] = {}
    try:
        for pname in (portfolio_engine.list_portfolios() or []):
            port = portfolio_engine.load_portfolio(pname)
            if not port:
                continue
            for sym in (port.get("holdings") or {}).keys():
                if sym:
                    result.setdefault(sym, [])
                    if pname not in result[sym]:
                        result[sym].append(pname)
    except Exception:
        pass
    _portfolio_cache = {"symbols": result, "ts": _t.time()}
    return result

@callback(
    Output("screener-progress-info", "children"),
    Output("screener-progress-interval", "disabled", allow_duplicate=True),
    Output("screener-ready-store", "data"),
    Input("screener-progress-interval", "n_intervals"),
    State("screener-ready-store", "data"),
    prevent_initial_call=True
)
def update_progress(n, ready_val):
    global _last_progress_state
    prog = screener.get_progress()
    prog_key = (prog["running"], prog["total"], prog["done"], prog["current"])
    if prog_key == _last_progress_state:
        return dash.no_update, dash.no_update, dash.no_update
    _last_progress_state = prog_key
    interval_disabled = not prog["running"] and prog["done"] > 0
    # Bump screener-ready-store whenever loading has finished so the table
    # rebuilds correctly both after initial load AND after a page refresh.
    # We encode the last signalled count as a negative number to distinguish
    # "never signalled" (0) from "signalled for N stocks" (-N).
    current_done = prog["done"]
    already_signalled = (ready_val or 0) < 0 and abs(ready_val or 0) == current_done
    if interval_disabled and current_done > 0 and not already_signalled:
        new_ready = -current_done
    else:
        new_ready = dash.no_update
    if not prog["running"] and prog["total"] == 0:
        return html.Div([
            html.Span("🟢 Ready to load universe", className="text-muted"),
        ], className="flex align-items-center gap-md"), True, new_ready
    if prog["running"]:
        pct = int(prog["done"] / prog["total"] * 100) if prog["total"] else 0
        phase_label = {
            "cached": "⚡ Scoring cached stocks",
        }.get(prog.get("phase", ""), "🔄 Processing")
        return html.Div([
            html.Span(f"{phase_label}: {prog['current']}", className="font-semibold text-info"),
            html.Span(f"({prog['done']}/{prog['total']} — {pct}%)", className="text-xs text-muted"),
        ], className="flex align-items-center gap-md"), False, dash.no_update
    else:
        if prog["done"] > 0:
            return html.Div([
                html.Span("✅ Analysis complete", className="font-semibold text-success"),
                html.Span(f"{prog['done']} stocks analyzed", className="text-xs text-muted"),
            ], className="flex align-items-center gap-md"), True, new_ready
        else:
            return "", True, new_ready

@callback(
    Output("screener-progress", "children"),
    Input("screener-progress-interval", "n_intervals"),
    prevent_initial_call=True
)
def update_progress_bar(n):
    global _last_progress_bar_state
    prog = screener.get_progress()
    prog_key = (prog["running"], prog["total"], prog["done"])
    if prog_key == _last_progress_bar_state:
        return dash.no_update
    _last_progress_bar_state = prog_key
    if prog["total"] == 0:
        return []
    pct = int(prog["done"] / prog["total"] * 100) if prog["total"] else 0
    if not prog["running"] and pct == 0:
        return []
    remaining_stocks = prog["total"] - prog["done"]
    eta_seconds = int(remaining_stocks * 0.35)
    minutes, seconds = divmod(eta_seconds, 60)
    eta_text = f"~{minutes}m {seconds:02d}s remaining" if prog["running"] and eta_seconds > 0 else (
        "Complete" if not prog["running"] else "Almost done..."
    )
    return html.Div(className="progress-container mb-3xl", children=[
        html.Div([
            html.Span("Processing Universe Data", className="font-semibold"),
            html.Span(f"({pct}%) {eta_text}", className="text-xs text-muted")
        ], className="flex justify-between mb-lg"),
        html.Div(className="progress-bar-wrapper", children=[
            html.Div(className="progress-bar-fill", style={"width": f"{pct}%"})
        ])
    ])

@callback(
    Output("screener-table-container", "children"),
    Output("sector-filter", "options"),
    Input("screener-ready-store",  "data"),
    Input("page-load-interval",    "n_intervals"),
    Input("sector-filter",         "value"),
    Input("screener-sort-store",   "data"),
    Input("screener-visible-count","data"),
    # screener-viewed-store is a State (not Input) so that analyzing a stock
    # does NOT trigger a re-render that could reset the page on mobile.
    # The viewed highlights update when the next natural re-render occurs
    # (e.g. infinite scroll, sort, filter, or screener-ready signal).
    State("screener-viewed-store", "data"),
    prevent_initial_call=False
)
def render_screener_table(ready, n_load, sector_filter, sort_state, visible_count, viewed_data):
    global _last_screener_state
    # Always allow a fresh render on page-load trigger so a browser refresh
    # never gets stuck behind a stale dedup-cache value.
    if dash.ctx.triggered_id == "page-load-interval":
        _last_screener_state = None
    results    = screener.get_screener_results()
    prog       = screener.get_progress()
    viewed_set = frozenset(viewed_data or [])
    sort_col   = (sort_state or {}).get("col", "composite_score")
    sort_asc   = (sort_state or {}).get("asc", False)
    visible_count = visible_count or 50
    # Reset visible row count to initial page size when filters/sorts change
    if dash.ctx.triggered_id in ["sector-filter", "screener-sort-store"]:
        visible_count = 50
    # 1E: Smart state key using MD5 hash of results for guaranteed deduplication
    state_tuple = (
        json.dumps([r["symbol"] for r in results], sort_keys=True),
        sector_filter or "",
        sort_col,
        sort_asc,
        sorted(viewed_set),
        visible_count
    )
    state_hash = hashlib.md5(json.dumps(state_tuple).encode()).hexdigest()
    
    if state_hash == _last_screener_state:
        return dash.no_update, dash.no_update
    _last_screener_state = state_hash
    sectors = sorted(set(r["sector"] for r in results if r.get("sector")))
    sector_options = [{"label": "All Sectors", "value": ""}] + [
        {"label": s, "value": s} for s in sectors
    ]
    if not results:
        if prog["running"]:
            return (
                html.Div([
                    html.Div("⚡ Loading in background…",
                             style={"color": BLUE, "fontWeight": "600", "marginBottom": "6px"}),
                    html.Div("Table will appear automatically when loading finishes.",
                             style={"color": MUTED, "fontSize": "13px"}),
                ], style={"textAlign": "center", "padding": "40px"}),
                sector_options,
            )
        return (
            html.Div("Click 'Load Universe' to start analysis",
                     className="text-center p-4xl text-muted"),
            sector_options,
        )
    portfolio_symbols = _get_portfolio_symbols()
    filtered = [r for r in results if not sector_filter or r.get("sector") == sector_filter]
    text_cols = {"symbol", "name", "sector", "updated_at"}
    if sort_col in text_cols:
        filtered = sorted(filtered, key=lambda r: (r.get(sort_col) or "").lower(), reverse=not sort_asc)
    else:
        filtered = sorted(filtered, key=lambda r: r.get(sort_col) or 0, reverse=not sort_asc)
    SORT_COLS = [
        ("#",           None,               None),
        ("Ticker",      "symbol",           "Stock ticker symbol. Click to run full analysis."),
        ("Company",     "name",             "Company name."),
        ("Sector",      "sector",           "Industry sector from SEC filings."),
        ("Market Cap ↕","market_cap",       "Market capitalization (price × shares outstanding, $M). Populated after running full analysis on a stock."),
        ("Composite ↕", "composite_score",  "Composite score (0–100): weighted blend of the orthogonal scoring pillars. Pre-analysis uses Graham+Quality only; run full analysis to include momentum, quality, forward revisions, growth, risk, and safety signals."),
        ("GN Price ↕",  "graham_number",    "Graham Number — intrinsic value estimate: √(22.5 × EPS × BVPS). Green = current price is below this number (margin of safety exists). Populated after running full analysis on a stock."),
        ("Buffett IV ↕","buffett_iv",       "Buffett Intrinsic Value — two-stage DCF on owner earnings (FCF/share or EPS) at 12% discount rate, 3% terminal growth. Green = current price is below IV. Populated after running full analysis on a stock."),
        ("Updated",     "updated_at",       "Date this stock was last fully analyzed."),
        ("Verdict",     None,               "Investment verdict based on composite score: STRONG BUY ≥75 · BUY ≥60 · WATCH ≥45 · HOLD ≥30 · AVOID <30. * = fundamentals only (momentum not yet loaded)."),
    ]
    header_cells = []
    for label, sort_key, tooltip in SORT_COLS:
        th_style = {"cursor": "help", "borderBottom": f"1px dashed {MUTED}"} if tooltip else {}
        if sort_key:
            header_cells.append(html.Th(
                html.Button(
                    label,
                    id={"type": "screener-sort-btn", "index": sort_key},
                    className="sort-header-btn", n_clicks=0,
                    title=tooltip or "",
                ),
                title=tooltip or "", style=th_style,
            ))
        else:
            header_cells.append(html.Th(label, title=tooltip or "", style=th_style))
    rows = []
    # Infinite scroll — render only the first `visible_count` rows; more are
    # appended as the user scrolls near the bottom of the table container.
    total_rows = len(filtered)
    visible_count = max(50, min(visible_count, total_rows)) if total_rows else 50
    page_filtered = filtered[:visible_count]

    for i, r in enumerate(page_filtered, 1):
        sym     = r["symbol"]
        viewed  = sym in viewed_set
        in_port = bool(portfolio_symbols.get(sym))
        verdict       = r["verdict"]
        verdict_label = r["verdict_label"]
        if verdict == "PENDING":
            score = r["composite_score"]
            if score >= 70:   verdict, verdict_label = "STRONG BUY*", "strong-buy"
            elif score >= 55: verdict, verdict_label = "BUY*",        "buy"
            elif score >= 40: verdict, verdict_label = "WATCH*",      "watch"
            elif score >= 25: verdict, verdict_label = "WEAK*",       "hold"
            else:             verdict, verdict_label = "AVOID*",      "avoid"
        badges = []
        port_list = portfolio_symbols.get(sym, [])
        for pname in port_list:
            badges.append(html.Span(f"💼 {pname}", style={
                "fontSize": "10px", "color": AMBER,
                "background": "#2a1e00", "border": f"1px solid {AMBER}55",
                "borderRadius": "4px", "padding": "1px 5px",
            }))
        # n_clicks on <td> not <button> — iOS Safari drops touch on <button> inside <table>
        ticker_cell = html.Td(
            html.Div([
                html.Span(sym, className="ticker-link-btn"),
                html.Div(badges, style={"display": "flex", "gap": "4px",
                                        "flexWrap": "wrap", "marginTop": "3px"})
                if badges else html.Div(),
            ]),
            id={"type": "screener-ticker-btn", "index": sym},
            n_clicks=0,
            className="ticker-cell",
            style={
                "cursor": "pointer",
                "touchAction": "manipulation",
                "WebkitTapHighlightColor": "rgba(0,0,0,0.08)",
                "userSelect": "none",
                "WebkitUserSelect": "none",
            }
        )
        # Graham Number cell — populated after full analysis
        gn    = r.get("graham_number")
        price = r.get("price")
        if gn:
            gn_color = GREEN if (price and price <= gn) else MUTED
            gn_cell = html.Td(
                html.Span(f"${gn:.0f}", style={"color": gn_color, "fontWeight": "600"}),
                title=f"Graham Number ${gn:.2f}" + (f" · Price ${price:.2f}" if price else "") +
                      (" · Price below GN ✓" if (price and price <= gn) else " · Price above GN"),
            )
        else:
            gn_cell = html.Td("—", className="text-xs text-muted",
                              title="Run full analysis to calculate Graham Number")
        # Buffett IV cell — populated after full analysis
        biv = r.get("buffett_iv")
        if biv:
            biv_color = GREEN if (price and price <= biv) else MUTED
            biv_cell = html.Td(
                html.Span(f"${biv:.0f}", style={"color": biv_color, "fontWeight": "600"}),
                title=f"Buffett IV ${biv:.2f}" + (f" · Price ${price:.2f}" if price else "") +
                      (" · Price below IV ✓" if (price and price <= biv) else " · Price above IV"),
            )
        else:
            biv_cell = html.Td("—", className="text-xs text-muted",
                               title="Run full analysis to calculate Buffett Intrinsic Value")
        row_style = {}
        if in_port:  row_style = {"borderLeft": f"3px solid {AMBER}"}
        elif viewed: row_style = {"borderLeft": f"3px solid {GREEN}44"}
        rows.append(html.Tr(style=row_style, children=[
            html.Td(str(i), className="rank-num"),
            ticker_cell,
            html.Td(r["name"][:30], className="company-name-cell", title=r["name"]),
            html.Td(r["sector"][:18], className="text-xs text-muted"),
            html.Td(_fmt_market_cap(r.get("market_cap")), className="text-xs"),
            html.Td(html.Span(f"{r['composite_score']:.0f}", className=f"score-pill {get_score_class(r['composite_score'])}")),
            gn_cell,
            biv_cell,
            html.Td(_fmt_updated(r.get("updated_at")), className="text-xs text-muted"),
            html.Td(html.Span(verdict, className=f"verdict-pill {get_verdict_class(verdict_label)}")),
        ]))
    n_analyzed  = sum(1 for r in filtered if r.get("analyzed"))
    n_portfolio = sum(1 for r in filtered if portfolio_symbols.get(r["symbol"]))
    note = html.Div([
        html.Span(f"{len(filtered):,} stocks", className="font-semibold"),
        html.Span(f" · {n_analyzed} analyzed · {n_portfolio} in portfolio"
                  " · * Verdict = fundamentals only — analyze individually to add Momentum",
                  className="text-muted"),
    ], style={"fontSize": "11px", "padding": "8px 4px", "fontStyle": "italic"})
    table = html.Table(className="screener-table", children=[
        html.Thead(html.Tr(children=header_cells)),
        html.Tbody(rows),
    ])
    # Infinite scroll — sentinel div observed by clientside callback; loading
    # text shown only while more rows remain to be appended.
    has_more = visible_count < total_rows
    scroll_sentinel = html.Div(
        f"Loading more… ({len(page_filtered):,} of {total_rows:,})" if has_more
        else f"Showing all {total_rows:,} rows",
        id="screener-scroll-sentinel",
        style={
            "textAlign": "center", "padding": "12px", "fontSize": "12px",
            "color": MUTED,
        }
    )
    return html.Div([table, note, scroll_sentinel]), sector_options

# ── Infinite scroll: bump visible row count when sentinel nears viewport ─────
app.clientside_callback(
    """
    function(n_intervals, scroll_pos, visible_count) {
        var wrap = document.getElementById('screener-table-container');
        if (!wrap) { return window.dash_clientside.no_update; }
        var rect = wrap.getBoundingClientRect();
        var nearBottom = rect.bottom - window.innerHeight < 400;
        if (!nearBottom) { return window.dash_clientside.no_update; }
        return (visible_count || 50) + 50;
    }
    """,
    Output("screener-visible-count", "data"),
    Input("page-load-interval", "n_intervals"),
    Input("screener-scroll-pos", "data"),
    State("screener-visible-count", "data"),
    prevent_initial_call=True,
)

# ── Screener column sort ──────────────────────────────────────────────────────
@callback(
    Output("screener-sort-store", "data"),
    Input({"type": "screener-sort-btn", "index": dash.ALL}, "n_clicks"),
    State("screener-sort-store", "data"),
    prevent_initial_call=True
)
def update_sort(n_clicks_list, sort_state):
    triggered = dash.ctx.triggered_id
    if not triggered or not any(n for n in n_clicks_list if n):
        return dash.no_update
    col = triggered["index"]
    # Toggle direction if same col clicked again, else default desc for scores,
    # asc for text columns
    if sort_state and sort_state.get("col") == col:
        return {"col": col, "asc": not sort_state["asc"]}
    text_cols = {"symbol", "name", "sector", "updated_at"}
    return {"col": col, "asc": col in text_cols}

@callback(
    Output("loading-trigger", "children"),
    Output("screener-progress-interval", "disabled"),
    Input("load-universe-btn", "n_clicks"),
    Input("page-load-interval", "n_intervals"),
    prevent_initial_call=True
)
def load_universe(n_clicks, n_load):
    triggered = dash.ctx.triggered_id
    if triggered == "page-load-interval":
        # On page load: enable the interval if a run is active or results exist
        prog = screener.get_progress()
        if prog["running"] or prog["done"] > 0:
            return dash.no_update, False
        return dash.no_update, True
    if n_clicks and n_clicks > 0:
        screener.load_universe_background()
        return "", False   # enable the interval so progress callbacks fire
    return "", True

# ── Analyze ───────────────────────────────────────────────────────────────────
# ── New quant UI helpers ──────────────────────────────────────────────────────
def _composite_banner(data: dict) -> html.Div:
    """
    Smart composite banner: shows enhanced orthogonal composite when available,
    falls back to original 3-pillar composite for older cached results.
    """
    enhanced = data.get("enhanced") or {}
    comp     = data.get("composite") or {}
    has_enh  = bool(enhanced.get("composite_score") is not None)
    src      = enhanced if has_enh else comp
    verdict       = src.get("verdict",      "N/A")
    verdict_label = src.get("verdict_label","pending")
    verdict_desc  = src.get("verdict_desc", "")
    score         = src.get("composite_score", 0) or 0
    # Pillar list
    if has_enh:
        pillars = [
            ("Graham",    enhanced.get("graham_pct",    0), "12%"),
            ("Quality",   enhanced.get("quality_pct",   0), "18%"),
            ("Momentum",  enhanced.get("momentum_pct",  0), "12%"),
            ("Risk",      enhanced.get("risk_pct",      0), " 6%"),
            ("Altman",    enhanced.get("altman_pct",    0), " 3%"),
            ("E.Rev",     enhanced.get("earnings_revision_pct", 0), "12%"),
            ("Profit.",   enhanced.get("profitability_pct", 0), "12%"),
            ("FCF Qual.", enhanced.get("fcf_quality_pct", 0), "10%"),
            ("Cap.Alloc", enhanced.get("capital_allocation_pct", 0), " 8%"),
            ("Growth Q.", enhanced.get("growth_quality_pct", 0), " 7%"),
        ]
        score_label = "Enhanced Score"
    else:
        pillars = [
            ("Graham",   comp.get("graham_pct",   0), "40%"),
            ("Quality",  comp.get("quality_pct",  0), "35%"),
            ("Momentum", comp.get("momentum_pct") or 0, "25%"),
        ]
        score_label = "Composite"
    pillar_els = [_pillar(l, round(v) if isinstance(v, float) else v, w)
                  for l, v, w in pillars]
    pillar_els.append(html.Div([
        html.Div(f"{score:.0f}", className="pillar-value text-4xl"),
        html.Div(score_label, className="pillar-label"),
    ]))
    # Flags row
    flags = []
    if enhanced.get("value_trap_warning") or comp.get("value_trap_warning"):
        flags.append(html.Span("⚠️ Value Trap Risk",
                               style={"background": "#3a2800", "color": AMBER,
                                      "borderRadius": "6px", "padding": "3px 10px",
                                      "fontSize": "12px", "fontWeight": "600"}))
    if enhanced.get("compounder_flag"):
        flags.append(html.Span("🚀 Compounder Signal",
                               style={"background": "#003a1a", "color": GREEN,
                                      "borderRadius": "6px", "padding": "3px 10px",
                                      "fontSize": "12px", "fontWeight": "600"}))
    if enhanced.get("altman_cap_applied"):
        flags.append(html.Span("🔴 Altman Distress Cap Active",
                               style={"background": "#3a0000", "color": RED,
                                      "borderRadius": "6px", "padding": "3px 10px",
                                      "fontSize": "12px", "fontWeight": "600"}))
    return html.Div(className="composite-banner", children=[
        html.Div([
            html.Div(verdict, className="composite-banner-verdict",
                     style={"color": _verdict_color(verdict_label)}),
            html.Div(verdict_desc, className="composite-banner-desc"),
            html.Div(flags, style={"display": "flex", "gap": "8px",
                                   "flexWrap": "wrap", "marginTop": "8px"})
            if flags else html.Div(),
        ]),
        html.Div(className="pillar-scores", children=pillar_els),
    ])

def _fcf_quality_card(data: dict) -> html.Div:
    """FCF Quality card: key metrics + scorecard criteria."""
    fcf = data.get("fcf_quality") or {}
    if not fcf:
        return html.Div()

    score  = fcf.get("fcf_quality_score")
    signal = fcf.get("signal", "")
    if score is None:
        return html.Div()

    sig_color = {
        "STRONG_CASH_GENERATOR": GREEN,
        "HIGH_CASH_QUALITY":     BLUE,
        "NEUTRAL":               AMBER,
        "WEAK_CASH_QUALITY":     MUTED,
        "EARNINGS_QUALITY_RISK": RED,
    }.get(signal, MUTED)

    def _fmt(v, fmt=",.2f", prefix="", suffix=""):
        if v is None:
            return "N/A"
        try:
            return f"{prefix}{v:{fmt}}{suffix}"
        except (ValueError, TypeError):
            return "N/A"

    metrics = [
        ("FCF",              _fmt(fcf.get("fcf"), ",.0f", "$")),
        ("Operating CF",     _fmt(fcf.get("operating_cash_flow"), ",.0f", "$")),
        ("CapEx",            _fmt(fcf.get("capex"), ",.0f", "$")),
        ("FCF Margin",       _fmt(fcf.get("fcf_margin"), ".1f", suffix="%")),
        ("FCF Conversion",   _fmt(fcf.get("fcf_conversion"), ".1f", suffix="%")),
        ("FCF Stability CV", _fmt(fcf.get("fcf_stability"), ".3f")),
        ("Growth Consist.",  _fmt(fcf.get("fcf_growth_consistency"), ".0%") if fcf.get("fcf_growth_consistency") is not None else "N/A"),
        ("Accrual Ratio",    _fmt(fcf.get("accrual_ratio"), ".4f")),
        ("FCF CAGR 5yr",     _fmt(fcf.get("fcf_cagr_5y"), ".1f", suffix="%")),
    ]

    metric_rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}",
            "fontSize": "12px",
        }, children=[
            html.Span(lbl, className="text-muted"),
            html.Span(val, style={"color": TEXT, "fontWeight": "600"}),
        ])
        for lbl, val in metrics
    ]

    return html.Div(className="scorecard", children=[
        html.Div(style={"display": "flex", "alignItems": "center",
                        "gap": "10px", "padding": "14px 18px 10px"}, children=[
            html.Span("FCF Quality",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{score:.0f}/100",
                      style={"fontSize": "22px", "fontWeight": "800", "color": sig_color}),
            html.Span(f"— {signal.replace('_', ' ').title()}",
                      style={"fontSize": "13px", "color": sig_color}),
        ]),
        html.Div(metric_rows, className="px-xl pb-2xl"),
    ])
def _options_signal_card(data: dict) -> html.Div:
    """Options Signal card: directional bias, IV regime, strike/expiry, risk/edge."""
    os_data = data.get("options_signal") or {}
    if not os_data:
        return html.Div()

    bias   = os_data.get("bias", "NEUTRAL")
    signal = os_data.get("signal", "NO_TRADE")
    edge   = os_data.get("edge_score")
    risk   = os_data.get("risk_score")
    if edge is None:
        return html.Div()

    bias_color = {"CALL": GREEN, "PUT": RED, "NEUTRAL": MUTED}.get(bias, MUTED)
    sig_color = {
        "BUY_CALL": GREEN, "BUY_PUT": GREEN,
        "WATCH": AMBER, "AVOID": RED, "NO_TRADE": MUTED,
    }.get(signal, MUTED)

    def _fmt(v, fmt=".2f", prefix="", suffix=""):
        if v is None:
            return "N/A"
        try:
            return f"{prefix}{v:{fmt}}{suffix}"
        except (ValueError, TypeError):
            return "N/A"

    metrics = [
        ("Bias",             html.Span(bias, style={"color": bias_color, "fontWeight": "700"})),
        ("Confidence",       _fmt(os_data.get("bias_confidence"), ".0f", suffix="/100")),
        ("IV Level",         os_data.get("iv_level", "N/A")),
        ("IV Trend",         os_data.get("iv_trend", "N/A")),
        ("Expected Move",    _fmt((os_data.get("expected_move_pct") or 0) * 100, ".1f", suffix="%")),
        ("Expected Move $",  _fmt(os_data.get("expected_move_dollar"), ",.2f", "$")),
        ("Suggested Strike", _fmt(os_data.get("recommended_strike"), ",.2f", "$")),
        ("Expiry (days)",    str(os_data.get("recommended_expiry_days", "N/A"))),
        ("Risk Score",       _fmt(risk, ".0f", suffix="/100")),
    ]

    metric_rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}",
            "fontSize": "12px",
        }, children=[
            html.Span(lbl, className="text-muted"),
            html.Span(val) if not isinstance(val, str) else
            html.Span(val, style={"color": TEXT, "fontWeight": "600"}),
        ])
        for lbl, val in metrics
    ]

    return html.Div(className="scorecard", children=[
        html.Div(style={"display": "flex", "alignItems": "center",
                        "gap": "10px", "padding": "14px 18px 10px"}, children=[
            html.Span("Options Signal",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{edge:.0f}/100",
                      style={"fontSize": "22px", "fontWeight": "800", "color": sig_color}),
            html.Span(f"— {signal.replace('_', ' ').title()}",
                      style={"fontSize": "13px", "color": sig_color}),
        ]),
        html.Div(
            "Models short-horizon option mark-to-market movement, not expiry payoff.",
            style={"fontSize": "11px", "color": MUTED, "padding": "0 18px 8px",
                   "fontStyle": "italic"},
        ),
        html.Div(metric_rows, className="px-xl pb-2xl"),
    ])

def _capital_allocation_card(data: dict) -> html.Div:
    """Capital Allocation card: key metrics display."""
    ca = data.get("capital_allocation") or {}
    if not ca:
        return html.Div()

    score  = ca.get("capital_allocation_score")
    signal = ca.get("signal", "")
    if score is None:
        return html.Div()

    sig_color = {
        "EXCELLENT_ALLOCATOR": GREEN,
        "GOOD_ALLOCATOR":      BLUE,
        "AVERAGE_ALLOCATOR":   AMBER,
        "POOR_ALLOCATOR":      MUTED,
        "CAPITAL_DESTROYER":   RED,
    }.get(signal, MUTED)

    def _fmt(v, fmt=".2f", prefix="", suffix=""):
        if v is None:
            return "N/A"
        try:
            return f"{prefix}{v:{fmt}}{suffix}"
        except (ValueError, TypeError):
            return "N/A"

    roic_spread_color = GREEN if (ca.get("roic_spread") or 0) > 0 else RED
    dilution_color    = GREEN if (ca.get("dilution_rate") or 1) <= 0 else (
        AMBER if (ca.get("dilution_rate") or 0) < 3 else RED
    )
    debt_color        = GREEN if (ca.get("debt_trend") or 1) <= 0 else AMBER

    metrics = [
        ("ROIC",              _fmt(ca.get("roic"), ".1f", suffix="%")),
        ("ROIC Spread (−10%)", html.Span(_fmt(ca.get("roic_spread"), "+.1f", suffix="%"),
                                          style={"color": roic_spread_color, "fontWeight": "600"})),
        ("Incremental ROIC",  _fmt(ca.get("incremental_roic"), ".1f", suffix="%")),
        ("Reinvestment Rate", _fmt(ca.get("reinvestment_rate"), ".1%") if ca.get("reinvestment_rate") is not None else "N/A"),
        ("Reinvest Method",   ca.get("reinvestment_method", "N/A")),
        ("Buyback Yield",     _fmt(ca.get("buyback_yield"), ".2f", suffix="%")),
        ("Dividend Yield",    _fmt(ca.get("dividend_yield_implied"), ".2f", suffix="%")),
        ("Shareholder Yield", _fmt(ca.get("shareholder_yield"), ".2f", suffix="%")),
        ("Dilution Rate",     html.Span(_fmt(ca.get("dilution_rate"), "+.2f", suffix="%"),
                                         style={"color": dilution_color, "fontWeight": "600"})),
        ("Debt Trend (Δ D/E)", html.Span(_fmt(ca.get("debt_trend"), "+.3f"),
                                           style={"color": debt_color, "fontWeight": "600"})),
    ]

    metric_rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}",
            "fontSize": "12px",
        }, children=[
            html.Span(lbl if isinstance(lbl, str) else lbl, className="text-muted"),
            html.Span(val) if isinstance(val, str) else val,
        ])
        for lbl, val in metrics
    ]

    return html.Div(className="scorecard", children=[
        html.Div(style={"display": "flex", "alignItems": "center",
                        "gap": "10px", "padding": "14px 18px 10px"}, children=[
            html.Span("Capital Allocation",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{score:.0f}/100",
                      style={"fontSize": "22px", "fontWeight": "800", "color": sig_color}),
            html.Span(f"— {signal.replace('_', ' ').title()}",
                      style={"fontSize": "13px", "color": sig_color}),
        ]),
        html.Div(metric_rows, className="px-xl pb-2xl"),
    ])


def _growth_quality_card(data: dict) -> html.Div:
    """Growth Quality card: 10-year growth quality and reinvestment durability."""
    gq = data.get("growth_quality") or {}
    if not gq:
        return html.Div()

    score = gq.get("growth_quality_score")
    signal = gq.get("signal", "Neutral")
    if score is None:
        return html.Div()

    sig_color = {
        "Bullish": GREEN,
        "Neutral": AMBER,
        "Bearish": RED,
    }.get(signal, MUTED)

    def _fmt(v, decimals=1, suffix="%"):
        return f"{v:.{decimals}f}{suffix}" if v is not None else "N/A"

    metrics = [
        ("Revenue CAGR 10Y", _fmt(gq.get("rev_cagr_10y"))),
        ("EPS CAGR 10Y", _fmt(gq.get("eps_cagr_10y"))),
        ("FCF CAGR 10Y", _fmt(gq.get("fcf_cagr_10y"))),
        ("Margin Stability", _fmt(gq.get("margin_stability"), 2, " pp")),
        ("Incremental ROIC", _fmt(gq.get("incremental_roic"))),
    ]

    metric_rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}",
            "fontSize": "12px",
        }, children=[
            html.Span(lbl, className="text-muted"),
            html.Span(val, style={"color": TEXT, "fontWeight": "600"}),
        ])
        for lbl, val in metrics
    ]

    return html.Div(className="scorecard", children=[
        html.Div(style={"display": "flex", "alignItems": "center",
                        "gap": "10px", "padding": "14px 18px 10px"}, children=[
            html.Span("Growth Quality",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{score:.0f}/100",
                      style={"fontSize": "22px", "fontWeight": "800", "color": sig_color}),
            html.Span(f"\u2014 {signal}",
                      style={"fontSize": "13px", "color": sig_color}),
        ]),
        html.Div(metric_rows, className="px-xl pb-2xl"),
    ])


def _insider_activity_card(data: dict) -> html.Div:
    """Insider buying/selling activity card."""
    ia = data.get("insider_activity") or {}
    if not ia or ia.get("low_coverage"):
        return html.Div()
    score  = ia.get("insider_confidence_score")
    signal = ia.get("signal", "NEUTRAL")
    if score is None:
        return html.Div()
    sig_color = {"BULLISH": GREEN, "NEUTRAL": AMBER, "BEARISH": RED}.get(signal, MUTED)

    def _fmt(v, fmt=".2f", suffix=""):
        return f"{v:{fmt}}{suffix}" if v is not None else "N/A"

    cluster_txt = "\u2705 Detected" if ia.get("cluster_detected") else "\u2014"
    metrics = [
        ("Net Insider Buying",  _fmt(ia.get("net_insider_buying"),  "+.2f", "%")),
        ("Cluster Buying",      cluster_txt),
        ("Type Quality Score",  _fmt(ia.get("insider_type_quality"), ".1f", "/100")),
        ("Buy Transactions",    str(ia.get("n_buy_transactions",  0))),
        ("Sell Transactions",   str(ia.get("n_sell_transactions", 0))),
        ("Distinct Buyers",     str(ia.get("n_distinct_buyers",   0))),
    ]
    rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}", "fontSize": "12px",
        }, children=[
            html.Span(lbl, className="text-muted"),
            html.Span(val, style={"color": TEXT, "fontWeight": "600"}),
        ])
        for lbl, val in metrics
    ]
    return html.Div(className="scorecard", children=[
        html.Div(style={
            "display": "flex", "alignItems": "center",
            "gap": "10px", "padding": "14px 18px 10px",
        }, children=[
            html.Span("Insider Activity",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{score:.0f}/100",
                      style={"fontSize": "22px", "fontWeight": "800", "color": sig_color}),
            html.Span(f"\u2014 {signal}",
                      style={"fontSize": "13px", "color": sig_color}),
        ]),
        html.Div(rows, className="px-xl pb-2xl"),
    ])


def _factor_momentum_card(data: dict) -> html.Div:
    """Factor Momentum card: price momentum plus fundamental trend signals."""
    fm = data.get("factor_momentum") or {}
    if not fm:
        return html.Div()

    score = fm.get("factor_momentum_score")
    signal = fm.get("signal", "Neutral")
    if score is None:
        return html.Div()

    sig_color = {
        "Bullish": GREEN,
        "Neutral": AMBER,
        "Bearish": RED,
    }.get(signal, MUTED)

    def _fmt(v, decimals=1, suffix="%"):
        return f"{v:.{decimals}f}{suffix}" if v is not None else "N/A"

    metrics = [
        ("3M Return", _fmt(fm.get("return_3m"))),
        ("6M Return", _fmt(fm.get("return_6m"))),
        ("12M Return", _fmt(fm.get("return_12m"))),
        ("Earnings Momentum", _fmt(fm.get("earnings_momentum"))),
        ("ROIC Trend Slope", _fmt(fm.get("roic_trend_slope"), 2, " pp/yr")),
    ]

    metric_rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}", "fontSize": "12px",
        }, children=[
            html.Span(lbl, className="text-muted"),
            html.Span(val, style={"color": TEXT, "fontWeight": "600"}),
        ])
        for lbl, val in metrics
    ]

    return html.Div(className="scorecard", children=[
        html.Div(style={
            "display": "flex", "alignItems": "center",
            "gap": "10px", "padding": "14px 18px 10px",
        }, children=[
            html.Span("Factor Momentum",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{score:.0f}/100",
                      style={"fontSize": "22px", "fontWeight": "800", "color": sig_color}),
            html.Span(f"\u2014 {signal}",
                      style={"fontSize": "13px", "color": sig_color}),
        ]),
        html.Div(metric_rows, className="px-xl pb-2xl"),
    ])


def _alternative_data_card(data: dict) -> html.Div:
    """Alternative Data card: provider-ready P4 framework placeholders."""
    ad = data.get("alternative_data") or {}
    if not ad:
        return html.Div()

    score = ad.get("alternative_data_score")
    signal = ad.get("signal", "NEUTRAL")
    status = ad.get("status", "STUB")
    if score is None:
        return html.Div()

    sig_color = {"BULLISH": GREEN, "NEUTRAL": AMBER, "BEARISH": RED}.get(signal, MUTED)
    signals = ad.get("signals") or []

    rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "gap": "12px", "padding": "5px 0", "borderBottom": f"1px solid {BORDER}",
            "fontSize": "12px",
        }, children=[
            html.Div([
                html.Div(s.get("label", s.get("name", "Signal")),
                         style={"color": TEXT, "fontWeight": "600"}),
                html.Div(s.get("description", ""),
                         style={"color": MUTED, "fontSize": "11px", "marginTop": "1px"}),
            ]),
            html.Span(s.get("status", status),
                      style={"color": MUTED, "fontWeight": "700", "whiteSpace": "nowrap"}),
        ])
        for s in signals
    ]

    return html.Div(className="scorecard", children=[
        html.Div(style={
            "display": "flex", "alignItems": "center",
            "gap": "10px", "padding": "14px 18px 10px",
        }, children=[
            html.Span("Alternative Data",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{score:.0f}/100",
                      style={"fontSize": "22px", "fontWeight": "800", "color": sig_color}),
            html.Span(f"\u2014 {status}",
                      style={"fontSize": "13px", "color": MUTED}),
        ]),
        html.Div(rows, className="px-xl pb-2xl"),
    ])


def _piotroski_card(data: dict) -> html.Div:
    """Piotroski F-Score card: 9 binary signals in 3 category blocks."""
    p = data.get("piotroski") or {}
    if not p:
        return html.Div()
    f_score  = p.get("f_score", 0)
    label    = p.get("label", "neutral")
    interp   = p.get("interpretation", "")
    signals  = p.get("signals", [])
    lc = {"strong": GREEN, "neutral": AMBER, "weak": RED}.get(label, MUTED)
    # Group signals by category
    cats: dict = {}
    for s in signals:
        cats.setdefault(s.get("category", "Other"), []).append(s)
    cat_blocks = []
    for cat_name, sigs in cats.items():
        rows = []
        for s in sigs:
            on = s["signal"] == 1
            rows.append(html.Div(style={
                "display": "flex", "gap": "10px", "alignItems": "flex-start",
                "padding": "6px 0", "borderBottom": f"1px solid {BORDER}"
            }, children=[
                html.Span("✅" if on else "❌",
                          style={"fontSize": "15px", "minWidth": "20px", "marginTop": "1px"}),
                html.Div([
                    html.Div(f"{s['id']}: {s['label']}",
                             style={"fontSize": "13px", "fontWeight": "600",
                                    "color": TEXT if on else MUTED}),
                    html.Div(s["note"],
                             style={"fontSize": "11px", "color": MUTED, "marginTop": "2px"}),
                ]),
            ]))
        cat_blocks.append(html.Div(className="flex-1 min-w-240", children=[
            html.Div(cat_name.upper(),
                     style={"fontSize": "10px", "fontWeight": "700", "color": MUTED,
                            "letterSpacing": "0.08em", "marginBottom": "6px",
                            "paddingBottom": "4px", "borderBottom": f"2px solid {BORDER}"}),
            *rows,
        ]))
    return html.Div(className="scorecard", children=[
        html.Div(style={"display": "flex", "alignItems": "center",
                        "gap": "10px", "padding": "14px 18px 10px"}, children=[
            html.Span("Piotroski F-Score",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{f_score}/9",
                      style={"fontSize": "22px", "fontWeight": "800", "color": lc}),
            html.Span(f"— {label.title()}",
                      style={"fontSize": "13px", "color": lc}),
        ]),
        html.Div(interp, style={"fontSize": "12px", "color": MUTED,
                                "padding": "0 18px 12px", "fontStyle": "italic"}),
        html.Div(cat_blocks,
                 style={"display": "flex", "gap": "20px", "flexWrap": "wrap",
                        "padding": "0 18px 16px"}),
    ])

def _altman_card(data: dict) -> html.Div:
    """Altman Z-Score card: zone badge + component breakdown."""
    a = data.get("altman") or {}
    if not a:
        return html.Div()
    z_score    = a.get("z_score")
    zone       = a.get("zone", "unknown")
    zone_label = a.get("zone_label", "Unknown")
    note       = a.get("note", "")
    model      = a.get("model", "")
    n_avail    = a.get("n_available", 0)
    comps      = a.get("components") or {}
    zc = {"safe": GREEN, "grey": AMBER, "distress": RED, "unknown": MUTED}.get(zone, MUTED)
    zbg = {"safe": "#001a0a", "grey": "#2a2000", "distress": "#2a0000",
           "unknown": CARD}.get(zone, CARD)
    comp_labels = [
        ("x1_working_capital",    "X1 — Working Capital / Assets"),
        ("x2_retained_earnings",  "X2 — Retained Earnings / Assets"),
        ("x3_ebit_ratio",         "X3 — EBIT / Assets"),
        ("x4_equity_liabilities", "X4 — Market Cap / Liabilities"),
        ("x5_asset_turnover",     "X5 — Revenue / Assets"),
    ]
    comp_rows = []
    for key, lbl in comp_labels:
        v = comps.get(key)
        comp_rows.append(html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}",
            "fontSize": "12px",
        }, children=[
            html.Span(lbl, className="text-muted"),
            html.Span(f"{v:.3f}" if v is not None else "N/A",
                      style={"color": TEXT if v is not None else MUTED,
                             "fontWeight": "600"}),
        ]))
    return html.Div(className="scorecard", children=[
        html.Div("Altman Z-Score (Bankruptcy Risk)",
                 style={"fontSize": "14px", "fontWeight": "700", "color": TEXT,
                        "padding": "14px 18px 10px"}),
        # Zone badge
        html.Div(style={"background": zbg, "borderRadius": "10px",
                        "margin": "0 16px 14px", "padding": "14px 18px",
                        "border": f"1px solid {zc}33"}, children=[
            html.Div(f"Z = {z_score:.2f}" if z_score is not None else "N/A",
                     style={"fontSize": "34px", "fontWeight": "800", "color": zc}),
            html.Div(zone_label,
                     style={"fontSize": "16px", "fontWeight": "700",
                            "color": zc, "marginTop": "2px"}),
            html.Div(note,
                     style={"fontSize": "11px", "color": MUTED, "marginTop": "6px"}),
            html.Div(f"Model: {model} · {n_avail}/5 components",
                     style={"fontSize": "10px", "color": MUTED, "marginTop": "3px"}),
        ]),
        # Components
        html.Div(comp_rows, className="px-xl pb-2xl"),
    ])

def _risk_card(data: dict) -> html.Div:
    """Risk & performance metrics dashboard."""
    r = data.get("risk") or {}
    if not r or r.get("error") and not r.get("sharpe"):
        return html.Div()
    n_yrs = r.get("n_years", 0)
    if not n_yrs:
        return html.Div()
    def _fv(val, decimals=2, suffix=""):
        return f"{val:.{decimals}f}{suffix}" if val is not None else "N/A"
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
    metrics = [
        ("Sharpe Ratio (≥1.0 good)",       _fv(r.get("sharpe")),
         _mc(r.get("sharpe"), good_above=1.0, bad_below=0)),
        ("Sortino Ratio (≥1.5 good)",      _fv(r.get("sortino")),
         _mc(r.get("sortino"), good_above=1.5, bad_below=0)),
        ("Beta vs SPY (<1.0 defensive)",   _fv(r.get("beta")),
         _mc(r.get("beta"), good_below=1.0, bad_above=1.5)),
        ("Alpha (>0 outperforms)",         _fv(r.get("alpha"), 1, "%"),
         _mc(r.get("alpha"), good_above=0, bad_below=-5)),
        ("Max Drawdown (>-30% ok)",        _fv(r.get("max_drawdown"), 1, "%"),
         _mc(r.get("max_drawdown"), bad_below=-30)),
        ("Ann. Volatility (<25% ok)",      _fv(r.get("volatility_annual"), 1, "%"),
         _mc(r.get("volatility_annual"), good_below=25, bad_above=40)),
        ("VaR 95% (monthly)",  _fv(r.get("var_95"), 1, "%"), MUTED),
        ("CVaR 95% (monthly)", _fv(r.get("cvar_95"), 1, "%"), MUTED),
        ("Ann. Return (≥10% good)",        _fv(r.get("annual_return"), 1, "%"),
         _mc(r.get("annual_return"), good_above=10, bad_below=0)),
        ("Calmar Ratio (≥1.0 good)",       _fv(r.get("calmar")),
         _mc(r.get("calmar"), good_above=1.0, bad_below=0)),
    ]
    metric_cells = [
        html.Div(style={}, children=[
            html.P(lbl, style={"color": MUTED, "fontSize": "12px", "margin": "0"}),
            html.P(val, style={"color": col, "fontWeight": "600", "margin": "0"}),
        ])
        for lbl, val, col in metrics
    ]
    risk_criteria = r.get("risk_criteria") or []
    return html.Div(className="risk-row",children=[
            
            html.Div( className="metric_cell scorecard",children=[
                    html.P(
                    f"Risk & Performance — {n_yrs:.0f}yr History", className="scorecard-header"
                ),
                *metric_cells
            ]),_render_scorecard("Risk Score Breakdown", risk_criteria, "risk")
        ])
        
    
def _regime_card(data: dict) -> html.Div:
    """Regime model card: market condition + portfolio risk overlay."""
    r = data.get("regime") or {}
    ov = data.get("regime_overlay") or {}
    if not r or r.get("error"):
        return html.Div()

    regime        = r.get("regime", "N/A")
    risk_level    = r.get("risk_level", "N/A")
    risk_alert    = r.get("risk_alert", False)
    multiplier    = ov.get("regime_multiplier", 1.0)
    exposure      = ov.get("max_equity_exposure", 1.0)
    adjusted      = ov.get("adjusted_score")
    trend_score   = r.get("market_trend_score")
    vol_pct       = r.get("volatility_percentile")
    drawdown      = r.get("drawdown_depth")

    regime_colors = {
        "BULL_LOW_VOL":  GREEN,
        "BULL_HIGH_VOL": AMBER,
        "SIDEWAYS":      MUTED,
        "BEAR_LOW_VOL":  AMBER,
        "BEAR_HIGH_VOL": RED,
        "CRISIS":        RED,
    }
    risk_colors = {"NORMAL": GREEN, "ELEVATED": AMBER, "HIGH": RED, "CRISIS": RED}
    rc = regime_colors.get(regime, MUTED)
    rlc = risk_colors.get(risk_level, MUTED)

    def _fmt(v, suffix="", decimals=1):
        return f"{v:.{decimals}f}{suffix}" if v is not None else "N/A"

    metrics = [
        ("Trend Score",       _fmt(trend_score, "/100", 0),   AMBER if trend_score and trend_score < 40 else GREEN if trend_score and trend_score >= 60 else MUTED),
        ("Vol Percentile",    _fmt(vol_pct, "%", 0),           RED if vol_pct and vol_pct >= 75 else AMBER if vol_pct and vol_pct >= 50 else GREEN),
        ("Drawdown (252D)",   _fmt(drawdown, "%"),              RED if drawdown and drawdown <= -20 else AMBER if drawdown and drawdown <= -10 else GREEN),
        ("SMA 50",            f"${r.get('sma_50'):.2f}" if r.get("sma_50") else "N/A",  TEXT),
        ("SMA 200",           f"${r.get('sma_200'):.2f}" if r.get("sma_200") else "N/A", TEXT),
        ("Vol 20D (ann.)",    _fmt(r.get("vol_20d"), "%"),     TEXT),
        ("Vol 60D (ann.)",    _fmt(r.get("vol_60d"), "%"),     TEXT),
        ("Regime Multiplier", f"×{multiplier:.2f}",             GREEN if multiplier >= 1.0 else AMBER if multiplier >= 0.8 else RED),
        ("Max Equity Exp.",   f"{exposure*100:.0f}%",           GREEN if exposure >= 1.0 else AMBER if exposure >= 0.7 else RED),
        ("Adjusted Score",    f"{adjusted:.1f}/100" if adjusted is not None else "N/A",
                              GREEN if adjusted and adjusted >= 60 else AMBER if adjusted and adjusted >= 40 else RED),
    ]

    metric_rows = [
        html.Div(style={
            "display": "flex", "justifyContent": "space-between",
            "padding": "4px 0", "borderBottom": f"1px solid {BORDER}", "fontSize": "12px",
        }, children=[
            html.Span(lbl, className="text-muted"),
            html.Span(val, style={"color": col, "fontWeight": "600"}),
        ])
        for lbl, val, col in metrics
    ]

    alert_banner = html.Div(
        "⚡ Fast Deterioration Alert — reduce position sizes",
        style={
            "background": "#3a0000", "color": RED, "borderRadius": "6px",
            "padding": "6px 12px", "margin": "0 0 10px 0", "fontSize": "12px",
            "fontWeight": "700", "border": f"1px solid {RED}",
        }
    ) if risk_alert else html.Div()

    return html.Div(className="scorecard", children=[
        html.Div(style={"display": "flex", "alignItems": "center",
                        "gap": "10px", "padding": "14px 18px 10px"}, children=[
            html.Span("Market Regime",
                      style={"fontSize": "14px", "fontWeight": "700", "color": TEXT}),
            html.Span(regime.replace("_", " "),
                      style={"fontSize": "18px", "fontWeight": "800", "color": rc}),
            html.Span(f"· {risk_level}",
                      style={"fontSize": "13px", "color": rlc, "fontWeight": "600"}),
        ]),
        html.Div(style={"padding": "0 18px 14px"}, children=[
            alert_banner,
            *metric_rows,
            html.Div(
                "Regime multiplier adjusts final score; max equity exposure governs position sizing. "
                "Based on SPY price history.",
                style={"fontSize": "11px", "color": MUTED, "marginTop": "8px",
                       "fontStyle": "italic", "lineHeight": "1.5"},
            ),
        ]),
    ])

def _build_analysis_content(data: dict) -> list:
    """Render analysis data into Dash components. Pure function, no side effects."""
    if not data or "error" in data:
        return []
    symbol = data["symbol"]
    name   = data["name"]
    sector = data["sector"]
    g      = data["graham"]
    q      = data["quality"]
    m      = data["momentum"]
    comp = (
        data.get("composite_score")
        or data.get("composite", {}).get("composite_score", 0)
    )
    price = data.get("price")
    er    = data.get("earnings_revision") or {}
    # ── Color Logic ──────────────────────────────────────────────────────────
    def _score_color(val, rule):
        """
        rule:
            {
                "direction": "high" | "low",
                "good_threshold": float,
                "bad_threshold": float | None
            }
        """
        if val is None:
            return MUTED
        direction = rule.get("direction", "high")
        good = rule.get("good_threshold")
        bad = rule.get("bad_threshold")
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
    
    # Earnings Revision Color + Display
    er_color = MUTED
    er_signal = er.get("signal", "NEUTRAL")
    if er and er.get("signal"):
        color_map = {
            "STRONG_UP": GREEN, "UP": GREEN,
            "NEUTRAL": AMBER,
            "DOWN": RED, "STRONG_DOWN": RED,
        }
        er_color = color_map.get(er_signal, MUTED)
    er_display = html.Span(
        f"{er.get('total_score', 0):.0f}/100 ({er_signal.replace('_', ' ')})",
        style={"color": er_color, "fontWeight": "700"}
    )
    # ── Extra stat row items ──────────────────────────────────────────────────
    p_data = data.get("piotroski") or {}
    a_data = data.get("altman") or {}
    r_data = data.get("risk") or {}
    b_data = data.get("buffett") or {}
    RULES = {
    "pe": {"direction": "low", "good_threshold": 15, "bad_threshold": 25},
    "pb": {"direction": "low", "good_threshold": 1.5, "bad_threshold": 3},
    "roe": {"direction": "high", "good_threshold": 15, "bad_threshold": 8},
    "op_margin": {"direction": "high", "good_threshold": 15, "bad_threshold": 5},
    "sharpe": {"direction": "high", "good_threshold": 1.0, "bad_threshold": 0.5},
    "beta": {"direction": "low", "good_threshold": 1.0, "bad_threshold": 1.5},
    "f_score": {"direction": "high", "good_threshold": 7, "bad_threshold": 4},
    }
    header = html.Div(
        className="company-header",
        children=[
            html.Div(
                className="company-header-left",
                children=[
                    html.H2(name),
                    html.Div(f"{symbol} · {sector}", className="company-meta"),
                    html.Div(
                        className="stats-row",
                        children=[
                            _stat(
                                "Price",
                                f"${price:.2f}" if price else "N/A",
                                "Current market price per share."
                            ),
                           _stat(
    "P/E",
    html.Span(
        f"{g.get('pe') or 0:.1f}×",
        style={"color": _score_color(g.get("pe"), RULES["pe"])}
    ),
    "Price-to-Earnings ratio. Graham's ceiling: 15×. Lower = cheaper."
),
_stat(
    "P/B",
    html.Span(
        f"{g.get('pb') or 0:.2f}×",
        style={"color": _score_color(g.get("pb"), RULES["pb"])}
    ),
    "Price-to-Book ratio. Graham's ceiling: 1.5×. Lower = better value."
),
_stat(
    "ROE",
    html.Span(
        f"{q.get('roe') or 0:.1f}%",
        style={"color": _score_color(q.get("roe"), RULES["roe"])}
    ),
    "Return on Equity. Target: ≥15%."
),
_stat(
    "Op Margin",
    html.Span(
        f"{q.get('op_margin') or 0:.1f}%",
        style={"color": _score_color(q.get("op_margin"), RULES["op_margin"])}
    ),
    "Operating Margin. Target: ≥15%."
),
_stat(
    "Sharpe",
    html.Span(
        f"{r_data.get('sharpe') or 0:.2f}",
        style={"color": _score_color(r_data.get('sharpe'), RULES["sharpe"])}
    ),
    "Sharpe Ratio. ≥1.0 = good, ≥1.5 = excellent."
),
_stat(
    "Beta",
    html.Span(
        f"{r_data.get('beta') or 0:.2f}",
        style={"color": _score_color(r_data.get('beta'), RULES["beta"])}
    ),
    "Beta vs SPY. <1.0 = defensive, >1.0 = more volatile."
),
_stat(
    "F-Score",
    html.Span(
        f"{p_data.get('f_score') or 0}/9",
        style={"color": _score_color(p_data.get('f_score'), RULES["f_score"])}
    ),
    "Piotroski F-Score. 8–9 = strong."
),
                            _stat(
                                "Buffett IV",
                                html.Span(
                                    f"${b_data.get('intrinsic_value'):.2f}"
                                    if b_data.get("intrinsic_value") else "N/A",
                                    style={
                                        "color": GREEN
                                        if (price and b_data.get("intrinsic_value") and price <= b_data["intrinsic_value"])
                                        else RED if b_data.get("intrinsic_value") else MUTED
                                    }
                                ),
                                "Buffett Intrinsic Value. Green = Price below IV (margin of safety)."
                            ),
                            _stat(
                                "Moat",
                                html.Span(
                                    f"{b_data.get('grade')} ({b_data.get('grade_label', '')})"
                                    if b_data.get("grade") else "N/A",
                                    style={
                                        "color": {
                                            "A": GREEN,
                                            "B": BLUE,
                                            "C": AMBER,
                                            "D": RED
                                        }.get(b_data.get("grade"), MUTED)
                                    }
                                ),
                                "Buffett moat grade: A=Wide Moat (best), D=Avoid."
                            ),
                            _stat(
                                "Comp",
                                html.Span(
                                    f"{comp:.0f}/100",
                                    style={
                                        "fontWeight": "700",
                                        "color": _verdict_color(
                                            data.get("enhanced", {}).get("verdict_label")
                                            or data.get("composite", {}).get("verdict_label", "pending")
                                        )
                                    }
                                ),
                                "Overall Composite Score (higher = better)."
                            ),
                            _stat(
                                "E. Rev",
                                er_display,
                                "Earnings Revision Score (0–100) — Measures analyst revisions."
                            ),
                        ]
                    ),
                ]
            ),
            html.Div(
                className="badges",
                children=[
                    html.Div(
                        className="grade-badge",
                        children=[
                            html.Div(
                                g["grade"],
                                className="grade-letter",
                                style={"color": _grade_color(g["grade"])}
                            ),
                            html.Div("Graham Grade", className="grade-label"),
                            html.Div(
                                f"{g['total_score']}/{g['total_max']}",
                                className="grade-score"
                            ),
                        ]
                    ),
                    html.Div(
                        className="grade-badge",
                        style={"borderLeft": f"1px solid {BORDER}"},
                        children=[
                            html.Div(
                                f"${b_data.get('intrinsic_value', 0):.0f}"
                                if b_data.get("intrinsic_value") else "—",
                                className="grade-letter",
                                style={
                                    "color": (
                                        GREEN
                                        if (price and b_data.get("intrinsic_value") and price <= b_data["intrinsic_value"])
                                        else RED if b_data.get("intrinsic_value") and price
                                        else MUTED
                                    ),
                                    "fontSize": "22px",
                                },
                            ),
                            html.Div("Buffett IV", className="grade-label"),
                            html.Span(
                                f"{b_data.get('grade')} — {b_data.get('grade_label')}"
                                if b_data.get("grade") else "N/A",
                                className="grade-score",
                                style={
                                    "color": {
                                        "A": GREEN,
                                        "B": BLUE,
                                        "C": AMBER,
                                        "D": RED
                                    }.get(b_data.get("grade", ""), MUTED),
                                    "cursor": "help",
                                    "borderBottom": f"1px dashed {MUTED}",
                                },
                                title=_MOAT_TOOLTIPS.get(b_data.get("grade", ""), ""),
                            ),
                        ]
                    ),
                ]
            ),
        ]
    )
    banner = _composite_banner(data)
    graham_card  = _render_scorecard("Graham Value Analysis", g["criteria"], "graham")
    quality_card = _render_scorecard("Quality Analysis", q["criteria"], "quality")
    
    buffett_card = (
        _render_scorecard("Buffett Quality & Value", b_data.get("criteria", []), "buffett")
        if b_data.get("criteria") else html.Div()
    )
    momentum_card = (
        _render_scorecard("Momentum Analysis", m.get("criteria", []), "momentum")
        if m.get("criteria") else html.Div()
    )
    
    piotroski_card = _piotroski_card(data)
    altman_card = _altman_card(data)
    risk_card = _risk_card(data)
    fcf_quality_card = _fcf_quality_card(data)
    regime_card = _regime_card(data)
    capital_allocation_card = _capital_allocation_card(data)
    growth_quality_card = _growth_quality_card(data)
    factor_momentum_card = _factor_momentum_card(data)
    alternative_data_card = _alternative_data_card(data)
    div_chart = _div_chart(g.get("div_history", []), symbol)
    graham_details = _graham_details_card(g, b_data)
    buffett_details = _buffett_details_card(data)
    row=(
        html.Div(className="card-row bg", children=[graham_details, buffett_details]),
        html.Div(className="moment_quality_row",children=[buffett_card, momentum_card]),
        risk_card,
        html.Div(className="card-row", children=[quality_card, graham_card]),
        html.Div(className="quant_row", children=[piotroski_card, altman_card])
        if p_data and a_data else html.Div(),
        html.Div(className="card-row", children=[fcf_quality_card, regime_card]),
        html.Div(className="card-row", children=[capital_allocation_card, growth_quality_card]),
        html.Div(className="card-row", children=[_insider_activity_card(data), alternative_data_card]),
        html.Div(className="card-row", children=[factor_momentum_card,_options_signal_card(data)]),
        html.Div(className="charts-grid",children=[_eps_chart(g.get("eps_history", []), symbol), _price_chart(data.get("price_history"), data.get("spy_history"), symbol),])
        )
    
  
   
    return [
        header,
        banner,

        *row,
        div_chart
    ]
@callback(
    Output("analysis-content",        "children"),
    Output("analysis-store",          "data"),
    Output("status-msg",              "children"),
    Output("analyze-btn",             "disabled"),
    Output("ticker-input",            "disabled"),
    Output("ticker-input",            "value"),
    Output("add-to-portfolio-panel",  "style"),
    Output("active-analysis-symbol",  "data"),
    Output("screener-viewed-store",   "data"),
    Input("analyze-btn",          "n_clicks"),
    Input("screener-click-ticker","data"),
    State("ticker-input",         "value"),
    State("screener-viewed-store","data"),
    prevent_initial_call=True
)
def run_analysis(n_clicks, clicked_ticker, ticker_input_value, viewed_list):
    """
    Single callback: fetch + score + render.
    Because analysis-content is a child of dcc.Loading(id='analysis-loading'),
    Dash shows the spinner for the entire duration of this callback.
    """
    triggered = dash.ctx.triggered_id
    if triggered == "screener-click-ticker" and clicked_ticker:
        ticker = clicked_ticker
    else:
        ticker = ticker_input_value
    if not ticker or not ticker.strip():
        return [], None, "❌ Please enter a ticker symbol.", False, False, dash.no_update, {"display": "none"}, None, dash.no_update
    symbol = ticker.strip().upper()
    result = analyze_stock(symbol)
    if "error" in result:
        return [], None, f"❌ {result['error']}", False, False, symbol, {"display": "none"}, None, dash.no_update
    viewed_updated = list(set((viewed_list or []) + [symbol]))
    content = _build_analysis_content(result)
    # Update screener row with full analysis data (Graham Number, live price, enhanced score)
    screener.update_stock_after_analysis(symbol, result)
    return (
        content,
        result,
        f"✅ {result['name']} ({symbol}) — Analysis complete",
        False, False, symbol,
        {"display": "block"},
        symbol,
        viewed_updated,
    )

# ── UI Components ─────────────────────────────────────────────────────────────
def _stat(label, value, tooltip=None):
    return html.Div([
        html.Div(label, className="stat-label",
                 title=tooltip or "",
                 style={"cursor": "help", "borderBottom": f"1px dashed {MUTED}"} if tooltip else {}),
        html.Div(value, className="stat-value")
    ], className="stat-item")

def _pillar(label, score, weight):
    return html.Div([
        html.Div(f"{score}%", className="pillar-value") if isinstance(score, (int, float)) else html.Div(score, className="pillar-value"),
        html.Div(label, className="pillar-label"),
        html.Div(f"({weight})", className="pillar-weight"),
    ])

def _grade_color(grade: str) -> str:
    return {"A": GREEN, "B": BLUE, "C": AMBER, "D": RED}.get(grade, MUTED)
def format_currency(val) -> str:
    if val is None:
        return "N/A"
    elif val >= 1e9:
        return f"${val/1e9:.2f}B"
    elif val >= 1e6:
        return f"${val/1e6:.2f}M"
    elif val >= 1e3:
        return f"${val/1e3:.2f}K"
    else:
        return f"${val:.2f}"
def _fmt_market_cap(v) -> str:
    if v is None:
        return "—"
    # v is stored in $M
    if v >= 1e6:
        return f"${v/1e6:.2f}T"
    if v >= 1e3:
        return f"${v/1e3:.2f}B"
    return f"${v:,.0f}M"

def _fmt_updated(v) -> str:
    if not v:
        return "—"
    try:
        return v[:10]  # ISO date portion
    except Exception:
        return "—"
def _verdict_color(label: str) -> str:
    return {
        "strong-buy": GREEN,
        "buy": BLUE,
        "watch": AMBER,
        "hold": MUTED,
        "avoid": RED,
        "pending": MUTED,
    }.get(label, MUTED)

def _render_scorecard(title: str, criteria: list, card_type: str) -> html.Div:
    rows = []
    for c in criteria:
        score = c["score"]
        max_s = c["max"]
        pct = score / max_s * 100 if max_s else 0
        color = GREEN if pct >= 66 else AMBER if pct >= 33 else RED
        rows.append(html.Div(className="criterion-row", children=[
            html.Div([
                html.Div(c["label"], className="criterion-label"),
                html.Div(c["note"], className="criterion-note"),
                html.Div(className="score-bar", children=[
                    html.Div(className="score-bar-fill", style={
                        "width": f"{pct}%", "background": color
                    })
                ])
            ]),
            html.Div(f"{score}/{max_s}", className="criterion-pts", style={"color": color}),
        ]))
    return html.Div(className="scorecard", children=[
        html.Div(title, className="scorecard-header"),
        html.Div(rows)
    ])

def _eps_chart(eps_history: list, symbol: str) -> html.Div:
    if not eps_history:
        return html.Div(className="empty-card", children=[
            html.Div("EPS History", className="empty-card-title"),
            html.Div("No EPS data", className="empty-title"),
            html.Div("Insufficient data available", className="empty-msg"),
        ])
    df = pd.DataFrame(eps_history).sort_values("year")
    colors = [GREEN if v >= 0 else RED for v in df["value"]]
    fig = go.Figure(go.Bar(
        x=df["year"].astype(str), y=df["value"],
        marker_color=colors,
        text=[format_currency(v) for v in df["value"]],
        textposition="outside",
        textfont=dict(size=12, color=WHITE) 
    ))
    fig.update_layout(**_chart_layout(f"{symbol} EPS History (10yr)"))
    return dcc.Graph(figure=fig, config={"displayModeBar": False})

def _price_chart(price_history_dict, spy_history_dict, symbol: str) -> html.Div:
    # Convert stored dict data back to DataFrames
    hist = pd.DataFrame(price_history_dict) if price_history_dict else pd.DataFrame()
    spy_hist = pd.DataFrame(spy_history_dict) if spy_history_dict else pd.DataFrame()
    if hist.empty:
        return html.Div(className="empty-card", children=[
            html.Div("Price History", className="empty-card-title"),
            html.Div("No price data", className="empty-title"),
            html.Div("Insufficient history available", className="empty-msg"),
        ])
    fig = go.Figure()
    def _normalise(df):
        df = df.copy()
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.dropna()
        if df.empty or df["Close"].iloc[0] <= 0:
            return df
        df["norm"] = df["Close"] / df["Close"].iloc[0] * 100
        return df
    hist = _normalise(hist)
    if not hist.empty:
        fig.add_trace(go.Scatter(
            x=hist["Date"], y=hist["norm"], name=symbol,
            line=dict(color=BLUE, width=2)
        ))
    if not spy_hist.empty:
        spy_hist = _normalise(spy_hist)
        if not spy_hist.empty:
            fig.add_trace(go.Scatter(
                x=spy_hist["Date"], y=spy_hist["norm"], name="SPY",
                line=dict(color=MUTED, width=1.5, dash="dot")
            ))
    fig.update_layout(**_chart_layout(f"{symbol} vs SPY (10yr normalised)"))
    fig.update_yaxes(title_text="Index (100 = start)")
    return dcc.Graph(figure=fig, config={"displayModeBar": False})

def _div_chart(div_history: list, symbol: str) -> html.Div:
    if not div_history:
        return html.Div(className="empty-card", children=[
            html.Div("Dividend History", className="empty-card-title"),
            html.Div("No dividends", className="empty-title"),
            html.Div("This company has not paid dividends", className="empty-msg"),
        ])
    df = pd.DataFrame(div_history).sort_values("year")
    df = df[df["value"] > 0]
    if df.empty:
        return html.Div(className="empty-card", children=[
            html.Div("Dividend History", className="empty-card-title"),
            html.Div("No dividends", className="empty-title"),
            html.Div("No dividend payments on record", className="empty-msg"),
        ])
    fig = go.Figure(go.Bar(
        x=df["year"].astype(str),
        y=df["value"] / 1e6,
        marker_color=BLUE,
        text=[format_currency(v) for v in df["value"]],
        textposition="outside",
        textfont=dict(size=20, color=WHITE) 
    ))
    fig.update_layout(**_chart_layout(f"{symbol} Dividend Payments (USD Millions)"))
    return dcc.Graph(figure=fig, config={"displayModeBar": False})

def _graham_details_card(g_data: dict, b_data: dict | None = None) -> html.Div:
    gn    = g_data.get("graham_number")
    price = g_data.get("price")
    mos   = g_data.get("margin_of_safety")
    # Buffett IV fields
    b_data   = b_data or {}
    biv      = b_data.get("intrinsic_value")
    b_mos    = b_data.get("margin_of_safety")
    b_grade  = b_data.get("grade")
    b_glabel = b_data.get("grade_label", "")
    rows = [
        ("Graham Number",     f"${gn:.2f}"  if gn    else "N/A"),
        ("Graham MoS",        f"{mos:.1f}%" if mos   else "N/A"),
        ("Current Price",     f"${price:.2f}" if price else "N/A"),
        ("EPS",               f"${g_data.get('eps', 0):.2f}" if g_data.get('eps') else "N/A"),
        ("Book Value/Share",  f"${g_data.get('bvps', 0):.2f}" if g_data.get('bvps') else "N/A"),
        ("Div Years",         str(g_data.get("div_years", 0))),
        ("EPS Years",         str(g_data.get("eps_years", 0))),
    ]
    gn_color  = GREEN if mos and mos > 0 else RED
    biv_color = GREEN if b_mos and b_mos > 0 else RED
    grade_color = {"A": GREEN, "B": BLUE, "C": AMBER, "D": RED}.get(b_grade or "", MUTED)
    def _row_color(label):
        if label == "Graham MoS":       return gn_color
        if label == "Buffett MoS":      return biv_color
        if label == "Buffett Grade":    return grade_color
        return TEXT
    detail_rows = [
        html.Div(className="detail-row", children=[
            html.Span(label, className="detail-label"),
            html.Span(value, className="detail-value",
                      style={"color": _row_color(label)}),
        ])
        for label, value in rows
    ]
    return html.Div(className="scorecard", children=[
        html.Div("Graham Number & Buffett IV", className="card-header"),
        *detail_rows
    ])

def _buffett_details_card(data: dict) -> html.Div:
    b = data.get("buffett") or {}
    iv  = b.get("intrinsic_value")
    mos = b.get("margin_of_safety")
    price = b.get("price")
    rows = [
        ("Grade",             f"{b.get('grade', 'N/A')} — {b.get('grade_label', '')}"),
        ("Intrinsic Value",   f"${iv:.2f} ({b.get('iv_base', '')})" if iv else "N/A"),
        ("Margin of Safety",  f"{mos:.1f}%" if mos is not None else "N/A"),
        ("ROE (latest)",      f"{b.get('roe_latest', 0):.1f}%" if b.get("roe_latest") else "N/A"),
        ("ROE ≥15% years",    f"{b.get('n_roe_above15', 0)}/{b.get('n_roe_years', 0)}"),
        ("Net Margin",        f"{b.get('net_margin', 0):.1f}%" if b.get("net_margin") else "N/A"),
        ("EPS CAGR",          f"{b.get('eps_cagr', 0):.1f}%/yr" if b.get("eps_cagr") is not None else "N/A"),
        ("FCF",               f"${b.get('fcf_latest', 0):.1f}B" if b.get("fcf_latest") is not None else "N/A"),
        ("ROIC",              f"{b.get('roic', 0):.1f}%" if b.get("roic") else "N/A"),
        ("Debt Payback",      f"{b.get('de_years', 0):.1f}yr" if b.get("de_years") is not None else "N/A"),
    ]
    iv_color = GREEN if mos and mos > 0 else RED
    detail_rows = [
        html.Div(className="detail-row", children=[
            html.Span(label, className="detail-label"),
            html.Span(value, className="detail-value",
                      style={"color": iv_color if label == "Margin of Safety" else TEXT}),
        ])
        for label, value in rows
    ]
    return html.Div(className="scorecard", children=[
        html.Div("Buffett DCF Details", className="card-header"),
        html.Div(detail_rows)
    ])

def _chart_layout(title: str, many_traces: bool = False) -> dict:
    """
    many_traces=True: vertical legend anchored top-right outside the plot.
    Used for portfolio charts which have 4-6 traces and would otherwise
    collide with the title.
    """
    if many_traces:
        legend = dict(
            bgcolor="rgba(26,29,39,0.88)",
            bordercolor=BORDER,
            borderwidth=1,
            font=dict(size=11),
            orientation="v",
            x=1.01,
            y=1.0,
            xanchor="left",
            yanchor="top",
        )
        margin = dict(l=16, r=160, t=44, b=16)   # right margin makes room
    else:
        legend = dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=11),
            orientation="h",
            x=0,
            y=1.08,
            xanchor="left",
            yanchor="bottom",
        )
        margin = dict(l=16, r=16, t=44, b=16)
    return dict(
        title=dict(text=title, font=dict(size=13, color=MUTED), x=0),
        paper_bgcolor=CARD,
        plot_bgcolor=CARD,
        font=dict(color=TEXT, family="Inter, system-ui, sans-serif"),
        margin=margin,
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(gridcolor=BORDER, zeroline=False),
        legend=legend,
    )

# ══════════════════════════════════════════════════════════════════════════════
# Portfolio callbacks
# ══════════════════════════════════════════════════════════════════════════════
# ── Populate portfolio dropdowns ──────────────────────────────────────────────
@callback(
    Output("portfolio-select-dropdown", "options"),
    Output("portfolio-active-dropdown", "options"),
    Output("portfolio-compare-dropdown","options"),
    Input("portfolio-refresh-store", "data"),
    prevent_initial_call=False
)
def refresh_portfolio_dropdowns(refresh):
    names = portfolio_engine.list_portfolios()
    opts  = [{"label": n, "value": n} for n in names]
    return opts, opts, opts

# ── Show/hide new-portfolio creation panel ────────────────────────────────────
@callback(
    Output("portfolio-create-panel", "style"),
    Input("portfolio-new-btn",            "n_clicks"),
    Input("portfolio-create-confirm-btn", "n_clicks"),
    Input("portfolio-create-cancel-btn",  "n_clicks"),
    prevent_initial_call=True
)
def toggle_create_panel(new, confirm, cancel):
    triggered = dash.ctx.triggered_id
    if triggered == "portfolio-new-btn":
        return {"display": "block"}
    return {"display": "none"}

# ── Create portfolio ──────────────────────────────────────────────────────────
@callback(
    Output("portfolio-refresh-store",    "data", allow_duplicate=True),
    Output("portfolio-active-dropdown",  "value"),
    Output("portfolio-create-msg",       "children"),
    Output("portfolio-create-name",      "value"),
    Input("portfolio-create-confirm-btn","n_clicks"),
    State("portfolio-create-name",       "value"),
    State("portfolio-refresh-store",     "data"),
    prevent_initial_call=True
)
def create_portfolio(n, name, refresh):
    if not n:
        return dash.no_update, dash.no_update, "", ""
    name = (name or "").strip()
    if not name:
        return dash.no_update, dash.no_update, "❌ Please enter a name.", dash.no_update
    existing = portfolio_engine.list_portfolios()
    if name in existing:
        return dash.no_update, dash.no_update, f"❌ '{name}' already exists.", dash.no_update
    portfolio_engine.create_portfolio(name)
    return (refresh or 0) + 1, name, "", ""

# ── Delete portfolio ──────────────────────────────────────────────────────────
@callback(
    Output("portfolio-refresh-store",   "data", allow_duplicate=True),
    Output("portfolio-active-dropdown", "value", allow_duplicate=True),
    Output("portfolio-msg",             "children", allow_duplicate=True),
    Input("portfolio-delete-btn",       "n_clicks"),
    State("portfolio-active-dropdown",  "value"),
    State("portfolio-refresh-store",    "data"),
    prevent_initial_call=True
)
def delete_portfolio(n, active, refresh):
    if not n or not active:
        return dash.no_update, dash.no_update, dash.no_update
    portfolio_engine.delete_portfolio(active)
    _invalidate_portfolio_cache()
    return (refresh or 0) + 1, None, f"🗑 Portfolio '{active}' deleted."

# ── Add holding from Analyze tab ──────────────────────────────────────────────
@callback(
    Output("portfolio-add-msg",       "children"),
    Output("portfolio-add-msg",       "style"),
    Output("portfolio-refresh-store", "data", allow_duplicate=True),
    Output("portfolio-shares-input",  "value"),
    Input("portfolio-add-btn",        "n_clicks"),
    State("portfolio-select-dropdown","value"),
    State("portfolio-new-name",       "value"),
    State("portfolio-shares-input",   "value"),
    State("active-analysis-symbol",   "data"),
    State("analysis-store",           "data"),
    State("portfolio-refresh-store",  "data"),
    prevent_initial_call=True
)
def add_to_portfolio(n, selected, new_name, shares, symbol, analysis, refresh):
    if not n:
        return "", {}, dash.no_update, dash.no_update
    # Resolve portfolio name
    port_name = (new_name or "").strip() or selected
    if not port_name:
        return "❌ Select or name a portfolio first.", {"color": RED}, dash.no_update, dash.no_update
    # Shares validation
    try:
        shares = int(shares or 0)
    except (ValueError, TypeError):
        shares = 0
    if shares < 5:
        return "❌ Minimum 5 shares.", {"color": RED}, dash.no_update, dash.no_update
    if not symbol:
        return "❌ Analyze a stock first.", {"color": RED}, dash.no_update, dash.no_update
    # Create portfolio if it doesn't exist
    if port_name not in portfolio_engine.list_portfolios():
        portfolio_engine.create_portfolio(port_name)
    price       = (analysis or {}).get("price") or 0
    company     = (analysis or {}).get("name", symbol)
    _, err = portfolio_engine.add_holding(port_name, symbol, shares, price, company)
    if err:
        return f"❌ {err}", {"color": RED}, dash.no_update, dash.no_update
    portfolio_engine.invalidate_simulation_cache(port_name)
    _invalidate_portfolio_cache()
    p = portfolio_engine.load_portfolio(port_name)
    count = len(p["holdings"])
    msg = f"✅ Added {shares}× {symbol} to '{port_name}' ({count}/{portfolio_engine.MAX_HOLDINGS} stocks)"
    return msg, {"color": GREEN}, (refresh or 0) + 1, None

# ── Render active portfolio holdings ─────────────────────────────────────────
@callback(
    Output("portfolio-content", "children"),
    Input("portfolio-active-dropdown", "value"),
    Input("portfolio-refresh-store",   "data"),
    prevent_initial_call=False
)
def render_portfolio_holdings(active, refresh):
    if not active:
        return html.Div("Select or create a portfolio to get started.",
                        className="text-center p-5xl text-muted")
    p = portfolio_engine.load_portfolio(active)
    if p is None:
        return html.Div("Portfolio not found.", className="text-danger")
    holdings = p.get("holdings", {})
    count    = len(holdings)
    cap      = portfolio_engine.MAX_HOLDINGS
    header = html.Div(className="portfolio-header", children=[
        html.Div(className="portfolio-meta", children=[
            html.Span(active, style={"fontSize": "20px", "fontWeight": "700", "color": TEXT}),
            html.Span(f"{count}/{cap} stocks",
                      style={"fontSize": "13px", "color": MUTED, "marginLeft": "12px"}),
        ]),
    ])
    if not holdings:
        body = html.Div("No holdings yet. Analyze a stock and click 'Add to Portfolio'.",
                        className="p-4xl text-muted text-center")
    else:
        total_invested = sum(h["shares"] * h["price_at_add"] for h in holdings.values())
        rows = []
        for sym, h in holdings.items():
            invested = h["shares"] * h["price_at_add"]
            weight   = invested / total_invested * 100 if total_invested > 0 else 0
            # Pull Sharpe from cached analysis if available
            sharpe_val = None
            cached_analysis = cache.read("analysis", sym)
            if cached_analysis:
                sharpe_val = (cached_analysis.get("risk") or {}).get("sharpe")
            sharpe_str = f"{sharpe_val:.2f}" if sharpe_val is not None else "—"
            sharpe_color = GREEN if (sharpe_val is not None and sharpe_val >= 1.0) else (
                AMBER if (sharpe_val is not None and sharpe_val >= 0) else RED
                if sharpe_val is not None else MUTED
            )
            rows.append(html.Tr([
                html.Td(sym, className="font-semibold text-info"),
                html.Td(h["name"][:28], className="text-xs text-muted"),
                # Editable shares cell
                html.Td(
                    html.Div(className="flex align-items-center gap-sm", children=[
                        dcc.Input(
                            id={"type": "shares-edit-input", "index": f"{active}|{sym}"},
                            type="number",
                            value=h["shares"],
                            min=5,
                            step=1,
                            debounce=False,
                            style={
                                "width": "70px", "padding": "3px 6px",
                                "background": DARK, "border": f"1px solid {BORDER}",
                                "borderRadius": "6px", "color": TEXT, "fontSize": "13px",
                            }
                        ),
                        html.Button(
                            "✓",
                            id={"type": "shares-save-btn", "index": f"{active}|{sym}"},
                            n_clicks=0,
                            style={
                                "background": "none", "border": f"1px solid {BORDER}",
                                "borderRadius": "5px", "color": GREEN,
                                "touchAction": "manipulation","cursor": "pointer" , "fontSize": "13px",
                                "padding": "2px 7px", "lineHeight": "1",
                            }
                        ),
                    ])
                ),
                html.Td(f"${h['price_at_add']:.2f}" if h["price_at_add"] else "N/A"),
                html.Td(f"${invested:,.2f}", id={"type": "invested-cell", "index": f"{active}|{sym}"}),
                html.Td(f"{weight:.1f}%"),
                html.Td(sharpe_str, title="Sharpe Ratio from last full analysis. ≥1.0 = good risk-adjusted return.", style={"color": sharpe_color, "fontWeight": "600"}),
                html.Td(
                    html.Button("✕", n_clicks=0,
                                id={"type": "remove-holding-btn", "index": f"{active}|{sym}"},
                                style={"background": "none", "border": "none",
                                       "color": RED, "touchAction": "manipulation","cursor": "pointer" , "fontSize": "14px"})
                ),
            ]))
        table = html.Table(className="screener-table", children=[
            html.Thead(html.Tr([
                html.Th("Ticker"), html.Th("Company"), html.Th("Shares"),
                html.Th("Price Added"), html.Th("Invested"), html.Th("Weight"),
                html.Th("Sharpe", title="Sharpe Ratio (risk-adjusted return) from last full analysis. ≥1.0 = good, ≥1.5 = excellent. '—' = stock not yet analyzed.", style={"cursor": "help", "borderBottom": f"1px dashed {MUTED}"}),
                html.Th(""),
            ])),
            html.Tbody(rows),
        ])
        total_row = html.Div(
            f"Total invested: ${total_invested:,.2f}",
            style={"textAlign": "right", "fontSize": "14px",
                   "fontWeight": "600", "color": TEXT, "padding": "8px 4px"}
        )
        ready = count >= 10
        sim_btn = html.Button(
            f"🚀 Run Simulation ({count}/10 stocks)" if not ready else "🚀 Run Simulation",
            id="run-simulation-btn",
            className="analyze-btn",
            n_clicks=0,
            disabled=(count == 0),
            style={"marginTop": "16px",
                   "background": GREEN if ready else AMBER,
                   "opacity": "1" if count > 0 else "0.5"},
        )
        body = html.Div([table, total_row, sim_btn])
    return html.Div([header, body])

# ── Remove holding ────────────────────────────────────────────────────────────
@callback(
    Output("portfolio-refresh-store", "data", allow_duplicate=True),
    Input({"type": "remove-holding-btn", "index": dash.ALL}, "n_clicks"),
    State("portfolio-refresh-store", "data"),
    prevent_initial_call=True
)
def remove_holding(n_clicks_list, refresh):
    triggered = dash.ctx.triggered_id
    if not triggered or not any(n for n in n_clicks_list if n):
        return dash.no_update
    port_name, symbol = triggered["index"].split("|", 1)
    portfolio_engine.remove_holding(port_name, symbol)
    portfolio_engine.invalidate_simulation_cache(port_name)
    _invalidate_portfolio_cache()
    return (refresh or 0) + 1

# ── Update shares ─────────────────────────────────────────────────────────────
@callback(
    Output("portfolio-refresh-store", "data", allow_duplicate=True),
    Output("portfolio-msg",           "children", allow_duplicate=True),
    Input({"type": "shares-save-btn", "index": dash.ALL}, "n_clicks"),
    State({"type": "shares-edit-input", "index": dash.ALL}, "value"),
    State({"type": "shares-edit-input", "index": dash.ALL}, "id"),
    State("portfolio-refresh-store", "data"),
    prevent_initial_call=True
)
def update_shares(n_clicks_list, values, ids, refresh):
    triggered = dash.ctx.triggered_id
    if not triggered or not any(n for n in n_clicks_list if n):
        return dash.no_update, dash.no_update
    # Find the matching input value by aligning triggered index with ids list
    triggered_index = triggered["index"]
    new_shares = None
    for id_dict, val in zip(ids, values):
        if id_dict["index"] == triggered_index:
            new_shares = val
            break
    if new_shares is None:
        return dash.no_update, "❌ Could not read share count."
    try:
        new_shares = int(new_shares)
    except (ValueError, TypeError):
        return dash.no_update, "❌ Shares must be a whole number."
    if new_shares < portfolio_engine.MIN_SHARES:
        return dash.no_update, f"❌ Minimum {portfolio_engine.MIN_SHARES} shares."
    port_name, symbol = triggered_index.split("|", 1)
    p = portfolio_engine.load_portfolio(port_name)
    if p is None:
        return dash.no_update, f"❌ Portfolio '{port_name}' not found."
    if symbol not in p["holdings"]:
        return dash.no_update, f"❌ {symbol} not in portfolio."
    old_shares = p["holdings"][symbol]["shares"]
    if new_shares == old_shares:
        return dash.no_update, f"ℹ️ {symbol} shares unchanged ({old_shares})."
    p["holdings"][symbol]["shares"] = new_shares
    portfolio_engine.save_portfolio(p)
    portfolio_engine.invalidate_simulation_cache(port_name)
    return (refresh or 0) + 1, f"✅ {symbol} updated to {new_shares} shares."

# ── Run simulation ────────────────────────────────────────────────────────────
@callback(
    Output("portfolio-sim-results", "children"),
    Input("run-simulation-btn",        "n_clicks"),
    State("portfolio-active-dropdown", "value"),
    State("portfolio-compare-dropdown","value"),
    prevent_initial_call=True
)
def run_simulation(n, active, compare):
    if not n or not active:
        return []
    def _build_sim_charts(port_name: str, color: str) -> list:
        sim = portfolio_engine.run_simulation(port_name)
        if sim.get("error"):
            return [html.Div(f"❌ {sim['error']}", className="text-danger")]
        bt = sim["backtest"]
        mc = sim["montecarlo"]
        components = []
        # ── Summary stats row ──────────────────────────────────────────────
        def _delta(val, ref):
            d = val - ref
            c = GREEN if d >= 0 else RED
            sign = "+" if d >= 0 else ""
            return html.Span(f" ({sign}${d:,.0f})", style={"color": c, "fontSize": "12px"})
        if not bt.get("error"):
            components.append(html.Div(className="portfolio-stats-row", children=[
                html.Div(className="stat-item", children=[
                    html.Div("Invested", className="stat-label"),
                    html.Div(f"${bt['total_invested']:,.2f}", className="stat-value"),
                ]),
                html.Div(className="stat-item", children=[
                    html.Div("Portfolio Value", className="stat-label"),
                    html.Div([
                        html.Span(f"${bt['final_value']:,.2f}", className="stat-value"),
                        _delta(bt["final_value"], bt["total_invested"]),
                    ]),
                ]),
                html.Div(className="stat-item", children=[
                    html.Div("SPY (same $)", className="stat-label"),
                    html.Div([
                        html.Span(f"${bt['final_spy']:,.2f}", className="stat-value"),
                        _delta(bt["final_spy"], bt["spy_invested"]),
                    ]),
                ]),
                html.Div(className="stat-item", children=[
                    html.Div("Portfolio CAGR", className="stat-label"),
                    html.Div(f"{bt['cagr']:+.1f}%", className="stat-value",
                             style={"color": GREEN if bt["cagr"] > 0 else RED}),
                ]),
                html.Div(className="stat-item", children=[
                    html.Div("SPY CAGR", className="stat-label"),
                    html.Div(f"{bt['spy_cagr']:+.1f}%", className="stat-value",
                             style={"color": GREEN if bt["spy_cagr"] > 0 else RED}),
                ]),
                html.Div(className="stat-item", children=[
                    html.Div("vs SPY", className="stat-label"),
                    html.Div(f"{bt['cagr'] - bt['spy_cagr']:+.1f}% / yr", className="stat-value",
                             style={"color": GREEN if bt["cagr"] > bt["spy_cagr"] else RED}),
                ]),
            ]))
        # ── Backtest chart ─────────────────────────────────────────────────
        if not bt.get("error"):
            fig_bt = go.Figure()
            fig_bt.add_trace(go.Scatter(
                x=bt["dates"], y=bt["portfolio_value"],
                name=port_name, line=dict(color=color, width=2.5)
            ))
            fig_bt.add_trace(go.Scatter(
                x=bt["dates"], y=bt["spy_value"],
                name="SPY", line=dict(color=MUTED, width=1.5, dash="dot")
            ))
            fig_bt.update_layout(**_chart_layout(f"{port_name} — 10yr Backtest vs SPY (actual $)", many_traces=True))
            fig_bt.update_yaxes(title_text="Portfolio Value ($)", tickprefix="$")
            components.append(dcc.Graph(figure=fig_bt, config={"displayModeBar": False}))
        # ── Monte Carlo chart ──────────────────────────────────────────────
        if not mc.get("error"):
            fig_mc = go.Figure()
            # SPY band (grey)
            fig_mc.add_trace(go.Scatter(
                x=mc["dates"] + mc["dates"][::-1],
                y=mc["spy_p90"] + mc["spy_p10"][::-1],
                fill="toself", fillcolor="rgba(158,158,158,0.12)",
                line=dict(color="rgba(0,0,0,0)"), name="SPY range", showlegend=True,
            ))
            fig_mc.add_trace(go.Scatter(
                x=mc["dates"], y=mc["spy_p50"],
                name="SPY median", line=dict(color=MUTED, width=1.5, dash="dot")
            ))
            # Portfolio band (colour)
            r, g_c, b = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
            fill_rgba = f"rgba({r},{g_c},{b},0.15)"
            fig_mc.add_trace(go.Scatter(
                x=mc["dates"] + mc["dates"][::-1],
                y=mc["p90"] + mc["p10"][::-1],
                fill="toself", fillcolor=fill_rgba,
                line=dict(color="rgba(0,0,0,0)"), name=f"{port_name} range",
            ))
            fig_mc.add_trace(go.Scatter(
                x=mc["dates"], y=mc["p50"],
                name=f"{port_name} median", line=dict(color=color, width=2.5)
            ))
            fig_mc.add_trace(go.Scatter(
                x=mc["dates"], y=mc["p10"],
                name="Worst case (p10)", line=dict(color=color, width=1, dash="dash")
            ))
            fig_mc.add_trace(go.Scatter(
                x=mc["dates"], y=mc["p90"],
                name="Best case (p90)", line=dict(color=color, width=1, dash="dash")
            ))
            fig_mc.update_layout(**_chart_layout(
                f"{port_name} — 2yr Monte Carlo Projection (1,000 paths)", many_traces=True
            ))
            fig_mc.update_yaxes(title_text="Projected Value ($)", tickprefix="$")
            components.append(dcc.Graph(figure=fig_mc, config={"displayModeBar": False}))
        # ── Holdings detail table ──────────────────────────────────────────
        if not bt.get("error") and bt.get("holdings_detail"):
            detail_rows = []
            for sym, d in bt["holdings_detail"].items():
                gain_color = GREEN if d["gain_pct"] >= 0 else RED
                # Build shares cell — show split badge when a forward split occurred
                factor = d.get("split_factor", 1.0)
                orig   = d.get("original_shares", d["shares"])
                if factor and factor != 1.0 and orig:
                    split_label = f"÷{1/factor:.0f}" if factor < 1 else f"×{factor:.4g}"
                    shares_cell = html.Td([
                        str(d["shares"]),
                        html.Span(
                            f" (split {split_label})",
                            style={"fontSize": "11px", "color": AMBER, "marginLeft": "4px"}
                        ),
                    ])
                else:
                    shares_cell = html.Td(str(d["shares"]))
                detail_rows.append(html.Tr([
                    html.Td(sym, className="font-semibold text-info"),
                    shares_cell,
                    html.Td(f"${d['entry_price']:.2f}"),
                    html.Td(f"${d['current_price']:.2f}"),
                    html.Td(f"${d['current_value']:,.2f}"),
                    html.Td(f"{d['gain_pct']:+.1f}%", style={"color": gain_color}),
                ]))
            components.append(html.Div(className="scorecard", children=[
                html.Div("Holdings Performance (10yr backtest period)", className="scorecard-header"),
                html.Table(className="screener-table", children=[
                    html.Thead(html.Tr([
                        html.Th("Ticker"), html.Th("Shares"),
                        html.Th("Entry Price"), html.Th("Exit Price"),
                        html.Th("Value"), html.Th("Total Return"),
                    ])),
                    html.Tbody(detail_rows),
                ]),
            ]))
        # ── Weak-link analysis ─────────────────────────────────────────────
        if not bt.get("error"):
            p_obj = portfolio_engine.load_portfolio(port_name)
            if p_obj:
                wl = portfolio_engine.analyze_weak_links(p_obj, bt)
                if wl.get("error"):
                    components.append(html.Div(
                        f"⚠️  Weak-link analysis unavailable: {wl['error']}",
                        style={"color": MUTED, "fontSize": "13px", "padding": "8px 4px"}
                    ))
                else:
                    gap      = wl["gap_cagr"]
                    gap_col  = GREEN if gap >= 0 else RED
                    gap_text = (
                        f"Portfolio CAGR {wl['port_cagr']:+.1f}%  vs  "
                        f"SPY {wl['spy_cagr']:+.1f}%  —  {gap:+.2f}% / yr gap "
                        f"over {wl['n_years']:.1f} yr"
                    )
                    # Banner: weakest link callout OR all-clear
                    if wl.get("weakest"):
                        ws  = wl["weakest"]
                        wd  = wl["holdings"][ws]
                        banner = html.Div(
                            f"⚠️  Weakest link: {ws} — "
                            f"replacing it with SPY would have improved total returns "
                            f"by +{wd['swap_delta_pct']:.2f}%",
                            style={
                                "background": "rgba(239,83,80,0.10)",
                                "border": f"1px solid {RED}",
                                "borderRadius": "6px",
                                "padding": "8px 14px",
                                "marginBottom": "12px",
                                "color": RED,
                                "fontSize": "13px",
                                "fontWeight": "600",
                            }
                        )
                    else:
                        banner = html.Div(
                            "✅  No weak links — every holding beat SPY over the backtest period.",
                            style={
                                "background": "rgba(102,187,106,0.10)",
                                "border": f"1px solid {GREEN}",
                                "borderRadius": "6px",
                                "padding": "8px 14px",
                                "marginBottom": "12px",
                                "color": GREEN,
                                "fontSize": "13px",
                                "fontWeight": "600",
                            }
                        )
                    # Per-holding rows — worst to best (ranking is worst-first)
                    wl_rows = []
                    for sym in wl["ranking"]:
                        d       = wl["holdings"][sym]
                        verdict = d["verdict"]
                        v_col   = (RED   if verdict == "weak link"   else
                                   GREEN if verdict == "contributor" else MUTED)
                        v_icon  = ("⚠️"  if verdict == "weak link"   else
                                   "✅" if verdict == "contributor" else "—")
                        wl_rows.append(html.Tr([
                            html.Td(sym,
                                    className="font-semibold text-info"),
                            html.Td(f"{d['weight']:.1f}%"),
                            html.Td(f"{d['stock_cagr']:+.1f}%",
                                    style={"color": GREEN if d["stock_cagr"] >= 0 else RED}),
                            html.Td(f"{d['cagr_vs_spy']:+.1f}%",
                                    style={"color": GREEN if d["cagr_vs_spy"] >= 0 else RED}),
                            html.Td(f"{d['drag_bps']:+.1f}",
                                    style={"color": GREEN if d["drag_bps"] >= 0 else RED}),
                            html.Td(f"{d['swap_delta_pct']:+.2f}%",
                                    style={"color": GREEN if d["swap_delta_pct"] <= 0 else RED}),
                            html.Td(
                                f"{v_icon} {verdict}",
                                style={"color": v_col, "fontWeight": "600"}
                            ),
                        ]))
                    components.append(html.Div(className="scorecard", children=[
                        html.Div("🔍 Weak Link Analysis", className="scorecard-header"),
                        html.Div(gap_text, style={
                            "color": gap_col, "fontSize": "13px",
                            "marginBottom": "14px", "padding": "0 4px",
                        }),
                        banner,
                        html.Table(className="screener-table", children=[
                            html.Thead(html.Tr([
                                html.Th("Ticker"),
                                html.Th("Weight"),
                                html.Th("Stock CAGR"),
                                html.Th("vs SPY"),
                                html.Th("Drag (bps)"),
                                html.Th("Swap Δ"),
                                html.Th("Verdict"),
                            ])),
                            html.Tbody(wl_rows),
                        ]),
                        html.Div(
                            "Table sorted worst-to-best.  "
                            "Drag (bps): weighted annualised underperformance vs SPY (negative = drag).  "
                            "Swap Δ: total-return change if this stock were replaced with SPY "
                            "(positive = stock was a drag; negative = stock beat SPY).",
                            style={
                                "fontSize": "11px", "color": MUTED,
                                "marginTop": "10px", "padding": "0 4px",
                                "lineHeight": "1.6",
                            }
                        ),
                    ]))
        return components
    PALETTE = [BLUE, GREEN, AMBER, "#e040fb", "#00bcd4"]
    sections = [
        html.Div(f"📊 {active}", className="scorecard-header",
                 style={"marginTop": "24px", "fontSize": "16px"}),
        *_build_sim_charts(active, PALETTE[0]),
    ]
    if compare and compare != active:
        sections += [
            html.Div(f"📊 {compare} (comparison)",
                     className="scorecard-header",
                     style={"marginTop": "32px", "fontSize": "16px", "color": PALETTE[1]}),
            *_build_sim_charts(compare, PALETTE[1]),
        ]
    return sections

# Touch bridge removed — ticker n_clicks now on <td> which handles touch natively on all browsers (ISSUE-003)

# ── Screener tab: remember scroll position; other tabs reset to top ──────────
# Polls window.scrollY while the screener tab is visible and stores it so it
# can be restored when the user navigates back.
app.clientside_callback(
    """
    function(n_intervals) {
        var tab = document.getElementById('tab-screener');
        if (tab && tab.style.display !== 'none') {
            return window.scrollY;
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("screener-scroll-pos", "data"),
    Input("screener-scroll-poll-interval", "n_intervals"),
)

# Restore the saved scroll position when the screener tab becomes visible.
# Analyze / Portfolio tabs always reset to top on switch.
app.clientside_callback(
    """
    function(screener_style, analyze_style, portfolio_style, saved_pos) {
        if (screener_style && screener_style.display !== 'none') {
            requestAnimationFrame(function() {
                window.scrollTo(0, saved_pos || 0);
            });
        } else if ((analyze_style && analyze_style.display !== 'none') ||
                   (portfolio_style && portfolio_style.display !== 'none')) {
            window.scrollTo(0, 0);
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("screener-table-container", "id"),
    Input("tab-screener", "style"),
    Input("tab-analyze", "style"),
    Input("tab-portfolio", "style"),
    State("screener-scroll-pos", "data"),
)

# ── Startup ───────────────────────────────────────────────────────────────────
def startup():
    print("\n🚀 Graham Score — Quant Edition")
   
    sec_data.get_ticker_map()
    universe.get_universe()
    results = screener.load_cached_only()
    print(f"✅ {len(results)} cached stocks ready\n")

startup()
if __name__ == "__main__":
    app.run(host="0.0.0.0",debug=True, port=8050)
