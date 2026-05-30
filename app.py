"""
Graham Score App — Full Quant Version
Pure Python / Dash with SEC EDGAR + Alpha Vantage
Graham (40%) + Quality (35%) + Momentum (25%)
"""

import dash
from dash import dcc, html, Input, Output, State, callback
import plotly.graph_objects as go
import pandas as pd
import json
import shutil
from pathlib import Path

import cache
import sec_data
import graham
import quality
import momentum
import scorer
import screener
import universe
import alpha_vantage_client
import portfolio as portfolio_engine

# ── App Init ──────────────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    title="Graham Score — Quant",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}]
)
server = app.server

# ── Color Theme (CSS vars in style.css, keeping for reference) ────────────────

DARK, CARD, BORDER, GREEN, RED, AMBER, BLUE, TEXT, MUTED = (
    "#0f1117", "#1a1d27", "#2a2d3e", "#00c853", "#ff1744",
    "#ffc107", "#448aff", "#e0e0e0", "#9e9e9e"
)

# ── State ──────────────────────────────────────────────────────────────────────

_last_screener_results = None
_last_progress_state = None
_last_progress_bar_state = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def analyze_stock(symbol: str) -> dict:
    """Full pipeline: SEC → Graham + Quality + (Price→Momentum) → Composite."""
    symbol = symbol.upper().strip()

    # Try cache
    cached = cache.read("analysis", symbol)
    if cached:
        return cached

    # Fetch SEC fundamentals
    try:
        sec_facts = sec_data.fetch_company_facts(symbol)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"SEC EDGAR error: {e}"}

    # Graham score (no price)
    g = graham.score(None, sec_facts)

    # Quality score (no price)
    q = quality.score(sec_facts)

    # Now try to get price
    price = alpha_vantage_client.get_price(symbol)
    hist = None
    spy_hist = None
    
    if price:
        # Recalculate Graham WITH price
        g = graham.score(price, sec_facts)
        
        # Fetch price history for charts (do this early to cache it)
        try:
            hist = alpha_vantage_client.get_price_history(symbol, years=10)
            spy_hist = alpha_vantage_client.get_price_history("SPY", years=10)
        except Exception as e:
            print(f"Price history fetch failed: {e}")

    # Momentum score (needs price history)
    m_result = {"total_score": 0, "total_max": 100, "criteria": []}
    if price and hist is not None:
        try:
            m_result = momentum.score(hist, spy_hist, symbol)
        except Exception as e:
            print(f"Momentum calculation failed: {e}")

    # Composite
    comp = scorer.composite(g, q, m_result)

    result = {
        "symbol":    symbol,
        "name":      sec_facts["name"],
        "sector":    sec_facts["sector"],
        "price":     price,
        "graham":    g,
        "quality":   q,
        "momentum":  m_result,
        "composite": comp,
        "price_history": hist.to_dict() if hist is not None else None,
        "spy_history": spy_hist.to_dict() if spy_hist is not None else None,
    }

    cache.write("analysis", symbol, result)
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
            html.P("Graham (40%) + Quality (35%) + Momentum (25%)")
        ])
    ]),

    # Tabs
    html.Div(className="tab-bar", children=[
        html.Button("📊 Screener",  id="tab-screener-btn",  className="tab-btn active"),
        html.Button("🔍 Analyze",   id="tab-analyze-btn",   className="tab-btn"),
        html.Button("💼 Portfolios", id="tab-portfolio-btn", className="tab-btn"),
    ]),

    # ── Tab: Screener ────────────────────────────────────────────────────────
    html.Div(id="tab-screener", className="screener-content", children=[
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
            html.Div(className="screener-controls", style={"display": "flex", "gap": "10px", "alignItems": "center"}, children=[
                html.Label("Filter by sector:", style={"fontSize": "13px", "color": MUTED}),
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

        html.Div(id="screener-progress", style={"marginBottom": "16px"}),

        dcc.Loading(
            id="screener-loading",
            type="default",
            color=BLUE,
            children=[
                html.Div(id="screener-table-container", className="screener-table-wrap", children=[
                    html.Div("Loading screener data...", style={"textAlign": "center", "padding": "40px", "color": MUTED})
                ])
            ]
        ),
    ], style={"display": "block"}),

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
        html.Div(id="add-to-portfolio-panel", style={"display": "none"}, children=[
            html.Div(className="portfolio-add-panel", children=[
                html.Div(className="portfolio-add-header", children=[
                    html.Span("💼", style={"fontSize": "20px"}),
                    html.Span("Add to Portfolio", style={"fontWeight": "600", "fontSize": "16px"}),
                ]),
                html.Div(className="portfolio-add-controls", children=[
                    dcc.Dropdown(
                        id="portfolio-select-dropdown",
                        placeholder="Select or create portfolio…",
                        clearable=True,
                        style={"minWidth": "220px", "color": "#000"},
                    ),
                    dcc.Input(
                        id="portfolio-new-name",
                        type="text",
                        placeholder="Or type new portfolio name…",
                        className="ticker-input",
                        style={"maxWidth": "220px"},
                    ),
                    dcc.Input(
                        id="portfolio-shares-input",
                        type="number",
                        placeholder="Shares (min 5)",
                        min=5,
                        step=1,
                        className="ticker-input",
                        style={"maxWidth": "130px"},
                    ),
                    html.Button("Add", id="portfolio-add-btn", className="analyze-btn", n_clicks=0),
                ]),
                html.Div(id="portfolio-add-msg", style={"fontSize": "13px", "marginTop": "6px"}),
            ])
        ]),
    ], style={"display": "none"}),

    # ── Tab: Portfolios ──────────────────────────────────────────────────────
    html.Div(id="tab-portfolio", className="main-content", children=[

        # Top toolbar: portfolio switcher + create + compare
        html.Div(className="screener-toolbar", children=[
            html.Div(className="screener-controls", children=[
                dcc.Dropdown(
                    id="portfolio-active-dropdown",
                    placeholder="Select a portfolio…",
                    clearable=False,
                    style={"minWidth": "240px", "color": "#000"},
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
                    style={"minWidth": "200px", "color": "#000"},
                ),
            ]),
        ]),

        # New portfolio name modal (inline, hidden by default)
        html.Div(id="portfolio-create-panel", style={"display": "none"}, children=[
            html.Div(className="portfolio-add-panel", children=[
                html.Span("Name your portfolio:", style={"color": "#e0e0e0"}),
                dcc.Input(id="portfolio-create-name", type="text",
                          placeholder="e.g. Value Picks Q1",
                          className="ticker-input", style={"maxWidth": "240px"}),
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

    ], style={"display": "none"}),

    # Stores
    dcc.Store(id="screener-cache"),
    dcc.Store(id="analysis-store"),
    dcc.Store(id="screener-sort-store", data={"col": "composite_score", "asc": False}),
    dcc.Store(id="search-history-store"),
    dcc.Store(id="screener-click-ticker"),   # symbol clicked in screener table
    dcc.Store(id="portfolio-refresh-store", data=0),  # increment to trigger refresh
    dcc.Store(id="active-analysis-symbol"),           # symbol currently analyzed
    # interval disabled=True once loading finishes to stop constant re-renders
    dcc.Interval(id="screener-progress-interval", interval=2000, disabled=True),
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

@callback(
    Output("screener-progress-info", "children"),
    Output("screener-progress-interval", "disabled", allow_duplicate=True),
    Input("screener-progress-interval", "n_intervals"),
    prevent_initial_call=True
)
def update_progress(n):
    global _last_progress_state

    prog = screener.get_progress()
    prog_key = (prog["running"], prog["total"], prog["done"], prog["current"])

    if prog_key == _last_progress_state:
        return dash.no_update, dash.no_update

    _last_progress_state = prog_key

    # Disable interval once loading is fully complete
    interval_disabled = not prog["running"] and prog["done"] > 0

    if not prog["running"] and prog["total"] == 0:
        return html.Div([
            html.Span("🟢 Ready to load universe", style={"color": MUTED}),
        ], style={"display": "flex", "alignItems": "center", "gap": "8px"}), True

    if prog["running"]:
        pct = int(prog["done"] / prog["total"] * 100) if prog["total"] else 0
        return html.Div([
            html.Span(f"🔄 Processing: {prog['current']}", style={"color": BLUE, "fontWeight": "600"}),
            html.Span(f"({prog['done']}/{prog['total']} — {pct}%)", style={"color": MUTED, "fontSize": "12px"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "8px"}), False
    else:
        if prog["done"] > 0:
            return html.Div([
                html.Span("✅ Analysis complete", style={"color": GREEN, "fontWeight": "600"}),
                html.Span(f"{prog['done']} stocks analyzed", style={"color": MUTED, "fontSize": "12px"}),
            ], style={"display": "flex", "alignItems": "center", "gap": "8px"}), True
        else:
            return "", True


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
    eta_text = f"~{eta_seconds}s remaining" if prog["running"] and eta_seconds > 0 else (
        "Complete" if not prog["running"] else "Almost done..."
    )

    return html.Div(className="progress-container", children=[
        html.Div([
            html.Span("Processing Universe Data", style={"fontWeight": "600", "color": TEXT}),
            html.Span(f"({pct}%) {eta_text}", style={"color": MUTED, "fontSize": "12px"})
        ], style={"display": "flex", "justifyContent": "spaceBetween", "marginBottom": "8px"}),
        html.Div(className="progress-bar-wrapper", children=[
            html.Div(className="progress-bar-fill", style={"width": f"{pct}%"})
        ])
    ], style={"marginBottom": "20px"})


@callback(
    Output("screener-table-container", "children"),
    Output("sector-filter", "options"),
    Input("screener-progress-interval", "n_intervals"),
    Input("sector-filter", "value"),
    Input("screener-sort-store", "data"),
    prevent_initial_call=True
)
def render_screener_table(n, sector_filter, sort_state):
    global _last_screener_results

    prog = screener.get_progress()
    results = screener.get_screener_results()

    # Only re-render when results set or running state actually changes
    sort_col = (sort_state or {}).get("col", "composite_score")
    sort_asc = (sort_state or {}).get("asc", False)
    state_key = (prog["running"], len(results), sector_filter or "", sort_col, sort_asc)
    if state_key == _last_screener_results:
        return dash.no_update, dash.no_update

    _last_screener_results = state_key

    # Rebuild sector dropdown options
    sectors = sorted(set(r["sector"] for r in results if r.get("sector")))
    sector_options = [{"label": "All Sectors", "value": ""}] + [
        {"label": s, "value": s} for s in sectors
    ]

    if prog["running"]:
        return (
            html.Div("Loading universe data...",
                     style={"textAlign": "center", "padding": "40px", "color": BLUE, "fontWeight": "600"}),
            sector_options
        )

    if not results:
        return (
            html.Div("Click 'Load Universe' to start analysis",
                     style={"textAlign": "center", "padding": "40px", "color": MUTED}),
            sector_options
        )

    # Apply sector filter
    filtered = [r for r in results if not sector_filter or r.get("sector") == sector_filter]

    # Apply sort
    sort_col = (sort_state or {}).get("col", "composite_score")
    sort_asc = (sort_state or {}).get("asc", False)
    text_cols = {"symbol", "name", "sector"}
    if sort_col in text_cols:
        filtered = sorted(filtered, key=lambda r: (r.get(sort_col) or "").lower(), reverse=not sort_asc)
    else:
        filtered = sorted(filtered, key=lambda r: r.get(sort_col) or 0, reverse=not sort_asc)

    # ── Sortable column headers ───────────────────────────────────────────────
    # Each sortable column renders as a button; the sort-store callback handles
    # re-ordering. Non-sortable cols (#, Verdict) are plain <th>.
    SORT_COLS = [
        ("#",         None),
        ("Ticker",    "symbol"),
        ("Company",   "name"),
        ("Sector",    "sector"),
        ("Graham ↕",  "graham_pct"),
        ("Quality ↕", "quality_pct"),
        ("Composite ↕", "composite_score"),
        ("Verdict",   None),
    ]

    header_cells = []
    for label, sort_key in SORT_COLS:
        if sort_key:
            header_cells.append(html.Th(
                html.Button(
                    label,
                    id={"type": "screener-sort-btn", "index": sort_key},
                    className="sort-header-btn",
                    n_clicks=0,
                )
            ))
        else:
            header_cells.append(html.Th(label))

    rows = []
    for i, r in enumerate(filtered, 1):
        # fundamental_only() returns "PENDING" — derive a label from the score
        verdict = r["verdict"]
        verdict_label = r["verdict_label"]
        if verdict == "PENDING":
            score = r["composite_score"]
            if score >= 70:
                verdict, verdict_label = "STRONG BUY*", "strong-buy"
            elif score >= 55:
                verdict, verdict_label = "BUY*", "buy"
            elif score >= 40:
                verdict, verdict_label = "WATCH*", "watch"
            elif score >= 25:
                verdict, verdict_label = "WEAK*", "hold"
            else:
                verdict, verdict_label = "AVOID*", "avoid"

        rows.append(html.Tr(children=[
            html.Td(str(i), className="rank-num"),
            html.Td(
                html.Button(
                    r["symbol"],
                    id={"type": "screener-ticker-btn", "index": r["symbol"]},
                    className="ticker-link-btn",
                    n_clicks=0,
                ),
                className="ticker-cell"
            ),
            html.Td(r["name"][:30], className="company-name-cell", title=r["name"]),
            html.Td(r["sector"][:18], style={"fontSize": "12px", "color": MUTED}),
            html.Td(html.Span(f"{r['graham_pct']:.0f}", className=f"score-pill {get_score_class(r['graham_pct'])}")),
            html.Td(html.Span(f"{r['quality_pct']:.0f}", className=f"score-pill {get_score_class(r['quality_pct'])}")),
            html.Td(html.Span(f"{r['composite_score']:.0f}", className=f"score-pill {get_score_class(r['composite_score'])}")),
            html.Td(html.Span(verdict, className=f"verdict-pill {get_verdict_class(verdict_label)}")),
        ]))

    note = html.Div(
        f"Showing all {len(filtered):,} stocks. * Verdict based on fundamentals only (Graham + Quality). Analyze individually to include Momentum.",
        style={"fontSize": "11px", "color": MUTED, "padding": "8px 4px", "fontStyle": "italic"}
    )

    table = html.Table(className="screener-table", children=[
        html.Thead(html.Tr(children=header_cells)),
        html.Tbody(rows)
    ])

    return html.Div([table, note]), sector_options



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
    text_cols = {"symbol", "name", "sector"}
    return {"col": col, "asc": col in text_cols}


@callback(
    Output("loading-trigger", "children"),
    Output("screener-progress-interval", "disabled"),
    Input("load-universe-btn", "n_clicks"),
    prevent_initial_call=True
)
def load_universe(n_clicks):
    if n_clicks and n_clicks > 0:
        screener.load_universe_background()
        return "", False   # enable the interval so progress callbacks fire
    return "", True


# ── Analyze ───────────────────────────────────────────────────────────────────

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
    comp   = data["composite"]
    price  = data.get("price")

    header = html.Div(className="company-header", children=[
        html.Div(className="company-header-left", children=[
            html.H2(name),
            html.Div(f"{symbol} · {sector}", className="company-meta"),
            html.Div(className="stats-row", children=[
                _stat("Price",     f"${price:.2f}"            if price              else "N/A"),
                _stat("P/E",       f"{g.get('pe', 0):.1f}×"  if g.get('pe')        else "N/A"),
                _stat("P/B",       f"{g.get('pb', 0):.2f}×"  if g.get('pb')        else "N/A"),
                _stat("ROE",       f"{q.get('roe', 0):.1f}%" if q.get('roe')       else "N/A"),
                _stat("Op Margin", f"{q.get('op_margin', 0):.1f}%" if q.get('op_margin') else "N/A"),
            ])
        ]),
        html.Div(className="grade-badge", children=[
            html.Div(g["grade"], className="grade-letter",
                     style={"color": _grade_color(g["grade"])}),
            html.Div("Graham Grade", className="grade-label"),
            html.Div(f"{g['total_score']}/{g['total_max']}", className="grade-score"),
        ])
    ])

    composite_banner = html.Div(className="composite-banner", children=[
        html.Div([
            html.Div(comp["verdict"], className="composite-banner-verdict",
                     style={"color": _verdict_color(comp["verdict_label"])}),
            html.Div(comp["verdict_desc"], className="composite-banner-desc"),
        ]),
        html.Div(className="pillar-scores", children=[
            _pillar("Graham",   comp["graham_pct"],              "40%"),
            _pillar("Quality",  comp["quality_pct"],             "35%"),
            _pillar("Momentum", comp.get("momentum_pct") or "--","25%"),
            html.Div([
                html.Div(f"{comp['composite_score']:.0f}", className="pillar-value",
                         style={"fontSize": "28px"}),
                html.Div("Composite", className="pillar-label"),
            ])
        ])
    ])

    warnings = []
    if comp["value_trap_warning"]:
        warnings.append(html.Div(
            "⚠️ Value Trap Risk: Cheap but declining margins and weak momentum.",
            className="warning-banner"
        ))

    graham_card   = _render_scorecard("Graham Value Analysis", g["criteria"], "graham")
    quality_card  = _render_scorecard("Quality Analysis",      q["criteria"], "quality")
    momentum_card = (_render_scorecard("Momentum Analysis", m["criteria"], "momentum")
                     if m.get("criteria") else html.Div())

    charts_row = html.Div(className="charts-grid", children=[
        _eps_chart(g.get("eps_history", []), symbol),
        _price_chart(data.get("price_history"), data.get("spy_history"), symbol),
    ])

    div_chart      = _div_chart(g.get("div_history", []), symbol)
    graham_details = _graham_details_card(g)

    return [header, composite_banner] + warnings + [
        graham_card, quality_card, momentum_card,
        charts_row, div_chart, graham_details
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
    Input("analyze-btn",          "n_clicks"),
    Input("screener-click-ticker","data"),
    State("ticker-input",         "value"),
    prevent_initial_call=True
)
def run_analysis(n_clicks, clicked_ticker, ticker_input_value):
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
        return [], None, "❌ Please enter a ticker symbol.", False, False, dash.no_update, {"display": "none"}, None

    symbol = ticker.strip().upper()
    result = analyze_stock(symbol)

    if "error" in result:
        return [], None, f"❌ {result['error']}", False, False, symbol, {"display": "none"}, None

    content = _build_analysis_content(result)
    return (
        content,
        result,
        f"✅ {result['name']} ({symbol}) — Analysis complete",
        False, False, symbol,
        {"display": "block"},   # show add-to-portfolio panel
        symbol,
    )


# ── UI Components ─────────────────────────────────────────────────────────────

def _stat(label, value):
    return html.Div([
        html.Div(label, className="stat-label"),
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
        text=[f"${v:.2f}" for v in df["value"]],
        textposition="outside"
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
        text=[f"${v/1e6:,.0f}M" for v in df["value"]],
        textposition="outside"
    ))
    fig.update_layout(**_chart_layout(f"{symbol} Dividend Payments (USD Millions)"))
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


def _graham_details_card(data: dict) -> html.Div:
    gn = data.get("graham_number")
    price = data.get("price")
    mos = data.get("margin_of_safety")

    rows = [
        ("Graham Number", f"${gn:.2f}" if gn else "N/A"),
        ("Current Price", f"${price:.2f}" if price else "N/A"),
        ("Margin of Safety", f"{mos:.1f}%" if mos else "N/A"),
        ("EPS", f"${data.get('eps', 0):.2f}" if data.get('eps') else "N/A"),
        ("Book Value/Share", f"${data.get('bvps', 0):.2f}" if data.get('bvps') else "N/A"),
        ("Div Years", str(data.get("div_years", 0))),
        ("EPS Years", str(data.get("eps_years", 0))),
    ]

    color = GREEN if mos and mos > 0 else RED

    detail_rows = [
        html.Div(className="detail-row", children=[
            html.Span(label, className="detail-label"),
            html.Span(value, className="detail-value", style={"color": color if label == "Margin of Safety" else TEXT}),
        ])
        for label, value in rows
    ]

    return html.Div(className="detail-card", children=[
        html.Div("Graham Number Details", className="card-header"),
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
                        style={"textAlign": "center", "padding": "60px", "color": MUTED})

    p = portfolio_engine.load_portfolio(active)
    if p is None:
        return html.Div("Portfolio not found.", style={"color": RED})

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
                        style={"padding": "30px", "color": MUTED, "textAlign": "center"})
    else:
        total_invested = sum(h["shares"] * h["price_at_add"] for h in holdings.values())

        rows = []
        for sym, h in holdings.items():
            invested = h["shares"] * h["price_at_add"]
            weight   = invested / total_invested * 100 if total_invested > 0 else 0
            rows.append(html.Tr([
                html.Td(sym, style={"fontWeight": "600", "color": BLUE}),
                html.Td(h["name"][:28], style={"fontSize": "12px", "color": MUTED}),
                # Editable shares cell
                html.Td(
                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "6px"}, children=[
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
                                "cursor": "pointer", "fontSize": "13px",
                                "padding": "2px 7px", "lineHeight": "1",
                            }
                        ),
                    ])
                ),
                html.Td(f"${h['price_at_add']:.2f}" if h["price_at_add"] else "N/A"),
                html.Td(f"${invested:,.2f}", id={"type": "invested-cell", "index": f"{active}|{sym}"}),
                html.Td(f"{weight:.1f}%"),
                html.Td(
                    html.Button("✕", n_clicks=0,
                                id={"type": "remove-holding-btn", "index": f"{active}|{sym}"},
                                style={"background": "none", "border": "none",
                                       "color": RED, "cursor": "pointer", "fontSize": "14px"})
                ),
            ]))

        table = html.Table(className="screener-table", children=[
            html.Thead(html.Tr([
                html.Th("Ticker"), html.Th("Company"), html.Th("Shares"),
                html.Th("Price Added"), html.Th("Invested"), html.Th("Weight"), html.Th(""),
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
            return [html.Div(f"❌ {sim['error']}", style={"color": RED})]

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
                    html.Td(sym, style={"fontWeight": "600", "color": BLUE}),
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


# ── Startup ───────────────────────────────────────────────────────────────────

def startup():
    print("\n🚀 Graham Score — Quant Edition")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("Graham (40%) + Quality (35%) + Momentum (25%)")
    print("SEC EDGAR (free) + Alpha Vantage (free)\n")

    sec_data.get_ticker_map()
    universe.get_universe()

    results = screener.load_cached_only()
    print(f"✅ {len(results)} cached stocks ready\n")


startup()

if __name__ == "__main__":
    app.run(debug=True, port=8050)