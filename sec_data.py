"""
SEC EDGAR data fetching.
Uses the free XBRL API — no API key required.
Docs: https://www.sec.gov/edgar/sec-api-documentation
"""

import requests
import pandas as pd
import cache

SEC_HEADERS = {"User-Agent": "GrahamScoreApp/1.0 contact@example.com"}
FACTS_URL   = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBS_URL    = "https://data.sec.gov/submissions/CIK{cik}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Module-level in-memory cache for the ticker map.
# The file cache is read exactly once per process; all subsequent calls
# (including from 16 parallel screener threads) return this dict directly.
_tickermap: dict | None = None


def get_ticker_map() -> dict:
    global _tickermap
    if _tickermap is not None:
        return _tickermap

    cached = cache.read("sec", "tickermap")
    if cached:
        _tickermap = cached
        return _tickermap

    print("📋 Loading SEC ticker map...")
    r = requests.get(TICKERS_URL, headers=SEC_HEADERS, timeout=15)
    r.raise_for_status()
    raw = r.json()

    _tickermap = {
        v["ticker"].upper(): {
            "cik": str(v["cik_str"]).zfill(10),
            "name": v["title"]
        }
        for v in raw.values()
    }
    cache.write("sec", "tickermap", _tickermap)
    print(f"✅ Loaded {len(_tickermap):,} tickers")
    return _tickermap


def get_cik(symbol: str) -> tuple[str, str]:
    m = get_ticker_map()
    entry = m.get(symbol.upper())
    if not entry:
        raise ValueError(f'Ticker "{symbol}" not found in SEC database')
    return entry["cik"], entry["name"]


def _annual_df(facts: dict, concept: str, unit: str = "USD", years: int = 11) -> pd.DataFrame:
    try:
        entries = facts["us-gaap"][concept]["units"][unit]
    except KeyError:
        return pd.DataFrame()

    df = pd.DataFrame(entries)
    if df.empty:
        return df

    df = (
        df[df["form"] == "10-K"]
        .sort_values("filed")
        .drop_duplicates("fy", keep="last")
        .sort_values("fy", ascending=False)
        .head(years)[["fy", "val", "end"]]
        .rename(columns={"fy": "year", "val": "value"})
        .reset_index(drop=True)
    )
    return df


def annual(facts, concept, unit="USD", years=11):
    return _annual_df(facts, concept, unit, years)

def annual_per_share(facts, concept, years=11):
    return _annual_df(facts, concept, "USD/shares", years)

def annual_shares(facts, concept, years=11):
    return _annual_df(facts, concept, "shares", years)

def _try_concepts(facts, concepts: list, unit="USD", years=11) -> pd.DataFrame:
    """Try multiple GAAP concept names, return first non-empty result."""
    for concept in concepts:
        df = _annual_df(facts, concept, unit, years)
        if not df.empty:
            return df
    return pd.DataFrame()


def _shares_df(facts: dict, years: int = 11) -> pd.DataFrame:
    """
    Shares outstanding is an SEC 'instant' concept — its fy field is often 0
    or missing in XBRL data, which breaks a year-based merge with equity.
    This function derives the year from the period end date instead of fy,
    so the result aligns properly with other annual DataFrames.
    """
    concepts = [
        "CommonStockSharesOutstanding",
        "SharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "CommonStockSharesIssuedNet",
    ]

    for concept in concepts:
        try:
            entries = facts["us-gaap"][concept]["units"]["shares"]
        except KeyError:
            continue

        df = pd.DataFrame(entries)
        if df.empty:
            continue

        # Prefer 10-K filings; fall back to any filing if 10-K yields nothing
        df_10k = df[df["form"] == "10-K"].copy()
        if df_10k.empty:
            df_10k = df.copy()

        # Derive year from end date (YYYY-MM-DD → YYYY), not fy, which is
        # unreliable for instant/balance-sheet concepts.
        df_10k["year"] = pd.to_datetime(df_10k["end"]).dt.year

        df_10k = (
            df_10k.sort_values("filed")
            .drop_duplicates("year", keep="last")
            .sort_values("year", ascending=False)
            .head(years)[["year", "val"]]
            .rename(columns={"val": "value"})
            .reset_index(drop=True)
        )

        if not df_10k.empty:
            print(f"  [SEC] shares via '{concept}' "
                  f"({len(df_10k)} years, latest {df_10k['year'].iloc[0]})")
            return df_10k

    print("  [SEC] ⚠️  No shares-outstanding concept found")
    return pd.DataFrame()


def _equity_df(facts: dict, years: int = 11) -> pd.DataFrame:
    """
    Try multiple stockholders-equity GAAP concepts.
    Also derive year from end date, matching _shares_df behaviour.
    """
    concepts = [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapital",
    ]
    for concept in concepts:
        df = _annual_df(facts, concept, "USD", years)
        if not df.empty:
            # Re-derive year from end date so it matches _shares_df years
            try:
                raw = facts["us-gaap"][concept]["units"]["USD"]
                raw_df = pd.DataFrame(raw)
                raw_df = raw_df[raw_df["form"] == "10-K"].copy()
                raw_df["year"] = pd.to_datetime(raw_df["end"]).dt.year
                raw_df = (
                    raw_df.sort_values("filed")
                    .drop_duplicates("year", keep="last")
                    .sort_values("year", ascending=False)
                    .head(years)[["year", "val"]]
                    .rename(columns={"val": "value"})
                    .reset_index(drop=True)
                )
                if not raw_df.empty:
                    print(f"  [SEC] equity via '{concept}' "
                          f"({len(raw_df)} years, latest {raw_df['year'].iloc[0]})")
                    return raw_df
            except Exception:
                pass
            return df
    print("  [SEC] ⚠️  No equity concept found")
    return pd.DataFrame()


def _tot_lib_df(facts: dict, equity_df: pd.DataFrame, years: int = 11) -> pd.DataFrame:
    """
    Total liabilities with two fallbacks:
      1. 'Liabilities' or 'LiabilitiesNoncurrentAndCurrent'
      2. Derive: LiabilitiesAndStockholdersEquity − StockholdersEquity
    """
    df = _try_concepts(facts, [
        "Liabilities",
        "LiabilitiesNoncurrentAndCurrent",
    ], unit="USD", years=years)

    if not df.empty:
        print(f"  [SEC] total liabilities: direct ({len(df)} years)")
        return df

    # Fallback: total assets (= L+E) minus equity
    lse_df = annual(facts, "LiabilitiesAndStockholdersEquity", years=years)
    if not lse_df.empty and not equity_df.empty:
        # Re-derive years from end date so they align
        try:
            raw_lse = facts["us-gaap"]["LiabilitiesAndStockholdersEquity"]["units"]["USD"]
            lse = pd.DataFrame(raw_lse)
            lse = lse[lse["form"] == "10-K"].copy()
            lse["year"] = pd.to_datetime(lse["end"]).dt.year
            lse = (
                lse.sort_values("filed")
                .drop_duplicates("year", keep="last")
                .sort_values("year", ascending=False)
                .head(years)[["year", "val"]]
                .rename(columns={"val": "lse"})
                .reset_index(drop=True)
            )
            merged = lse.merge(
                equity_df[["year", "value"]].rename(columns={"value": "eq"}),
                on="year", how="inner"
            )
            if not merged.empty:
                merged["value"] = merged["lse"] - merged["eq"]
                result = merged[["year", "value"]].reset_index(drop=True)
                print(f"  [SEC] total liabilities: derived L+E−E ({len(result)} years)")
                return result
        except Exception as e:
            print(f"  [SEC] ⚠️  total liabilities derivation failed: {e}")

    print("  [SEC] ⚠️  No total liabilities data found")
    return pd.DataFrame()


def _bvps_df(equity_df: pd.DataFrame, shares_df: pd.DataFrame) -> pd.DataFrame:
    """
    BVPS = Equity / Shares Outstanding.

    Primary:  inner join on 'year' (both end-date derived, so they should align).
    Fallback: nearest-year match (±1yr tolerance).
    Last:     most-recent value of each regardless of year.
    """
    if equity_df.empty or shares_df.empty:
        return pd.DataFrame()

    # ── Primary: exact year join ──────────────────────────────────────────────
    merged = equity_df.merge(
        shares_df[["year", "value"]].rename(columns={"value": "shares"}),
        on="year", how="inner"
    )
    merged = merged[merged["shares"] > 0].copy()

    if not merged.empty:
        merged["value"] = merged["value"] / merged["shares"]
        print(f"  [SEC] BVPS: exact-year join ({len(merged)} rows)")
        return merged[["year", "value"]].reset_index(drop=True)

    # ── Fallback: nearest-year join (±1yr) ────────────────────────────────────
    print(f"  [SEC] BVPS exact join failed "
          f"(equity years: {list(equity_df['year'])[:4]}, "
          f"share years: {list(shares_df['year'])[:4]}) — trying nearest-year")

    rows = []
    for _, eq_row in equity_df.iterrows():
        yr = eq_row["year"]
        # Find share row within ±1 year
        near = shares_df[abs(shares_df["year"] - yr) <= 1]
        if near.empty:
            continue
        # Pick the closest year
        near = near.iloc[(near["year"] - yr).abs().argsort()[:1]]
        sh = float(near["value"].iloc[0])
        if sh > 0:
            rows.append({"year": yr, "value": float(eq_row["value"]) / sh})

    if rows:
        result = pd.DataFrame(rows).sort_values("year", ascending=False).reset_index(drop=True)
        print(f"  [SEC] BVPS: nearest-year join ({len(result)} rows)")
        return result

    # ── Last resort: most recent of each ─────────────────────────────────────
    eq_val = float(equity_df["value"].iloc[0])
    sh_val = float(shares_df["value"].iloc[0])
    yr_val = int(equity_df["year"].iloc[0])
    if sh_val > 0:
        print(f"  [SEC] BVPS: last-resort single-row fallback (yr={yr_val})")
        return pd.DataFrame([{"year": yr_val, "value": eq_val / sh_val}])

    return pd.DataFrame()


def fetch_company_facts(symbol: str) -> dict:
    cik, name = get_cik(symbol)
    print(f"\n📡 Fetching SEC EDGAR for {symbol} (CIK {cik})...")

    facts_r = requests.get(FACTS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=30)
    facts_r.raise_for_status()
    facts = facts_r.json()["facts"]

    subs_r = requests.get(SUBS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=15)
    subs_r.raise_for_status()
    subs = subs_r.json()

    sector    = subs.get("sicDescription", "Unknown")
    full_name = subs.get("name", name)

    # ── Graham data ───────────────────────────────────────────────────────────
    eps_df     = annual_per_share(facts, "EarningsPerShareBasic")
    equity_df  = _equity_df(facts)
    shares_df  = _shares_df(facts)
    cur_ast_df = annual(facts, "AssetsCurrent")
    cur_lib_df = annual(facts, "LiabilitiesCurrent")
    lt_debt_df = _try_concepts(facts, [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
    ])
    tot_lib_df = _tot_lib_df(facts, equity_df)
    net_inc_df = annual(facts, "NetIncomeLoss")

    rev_df = _try_concepts(facts, [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
    ])

    div_df = _try_concepts(facts, [
        "PaymentsOfDividendsCommonStock",
        "DividendsCommonStockCash",
    ])
    if div_df.empty:
        div_df = annual_per_share(facts, "CommonStockDividendsPerShareDeclared")

    # ── Quality data ──────────────────────────────────────────────────────────
    gross_profit_df  = annual(facts, "GrossProfit")
    operating_inc_df = _try_concepts(facts, [
        "OperatingIncomeLoss",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ])
    operating_cf_df  = _try_concepts(facts, [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ])
    capex_df = _try_concepts(facts, [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
    ])

    # ── BVPS (requires end-date-aligned equity + shares) ─────────────────────
    bvps_df = _bvps_df(equity_df, shares_df)

    # ── Diagnostic summary ────────────────────────────────────────────────────
    missing = [k for k, v in {
        "eps": eps_df, "equity": equity_df, "shares": shares_df,
        "bvps": bvps_df, "tot_lib": tot_lib_df, "lt_debt": lt_debt_df,
    }.items() if v.empty]
    if missing:
        print(f"  [SEC] ⚠️  Missing fields for {symbol}: {', '.join(missing)}")
    else:
        print(f"  [SEC] ✅ All key fields resolved for {symbol}")

    return {
        "cik":          cik,
        "name":         full_name,
        "sector":       sector,
        "eps":          eps_df.to_dict("records"),
        "bvps":         bvps_df.to_dict("records"),
        "cur_ast":      cur_ast_df.to_dict("records"),
        "cur_lib":      cur_lib_df.to_dict("records"),
        "lt_debt":      lt_debt_df.to_dict("records"),
        "tot_lib":      tot_lib_df.to_dict("records"),
        "equity":       equity_df.to_dict("records"),
        "shares":       shares_df.to_dict("records"),
        "net_inc":      net_inc_df.to_dict("records"),
        "revenue":      rev_df.to_dict("records"),
        "dividends":    div_df.to_dict("records"),
        "gross_profit": gross_profit_df.to_dict("records"),
        "op_income":    operating_inc_df.to_dict("records"),
        "op_cf":        operating_cf_df.to_dict("records"),
        "capex":        capex_df.to_dict("records"),
    }