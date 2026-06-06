"""
SEC EDGAR data fetching.
Uses the free XBRL API — no API key required.
Docs: https://www.sec.gov/edgar/sec-api-documentation

Cache invalidation strategy
────────────────────────────
Rather than evicting on a fixed wall-clock TTL, we:

  1. Fetch only the lightweight /submissions/ endpoint (~few KB) for a ticker.
  2. Scan its recent filings for the latest 10-K or 10-Q date.
  3. Compare that date against what was recorded in the local cache.
  4. Re-fetch the heavy /companyfacts/ blob (~1 MB+) only when SEC has
     a newer filing than the one we last cached.

Sector-aware extraction
────────────────────────
Different industries use different XBRL concepts for the same economic line
items.  We detect the sector via the SIC code in /submissions/ and apply
concept fallback lists tuned per sector:

  bank       SIC 6000–6299  (commercial banks, S&Ls, credit unions, brokers)
  insurance  SIC 6300–6499  (P&C, life, reinsurance)
  realestate SIC 6500–6599  (REITs, property management)
  utility    SIC 4900–4999  (electric, gas, water)
  oil_gas    SIC 1300–1399  (crude petroleum, pipelines)
  mining     SIC 1000–1299  (metals, coal, non-metallic minerals)
  biotech    SIC 2830–2836  (pharma/biotech — often pre-revenue, no GP line)
  general    everything else

SURVIVORSHIP BIAS NOTE (MITIGATION)
────────────────────────────────────
The SEC company_tickers.json and /companyfacts/ endpoints primarily reflect
currently active (surviving) companies. Delisted, bankrupt, or acquired firms
are gradually removed from the live ticker map and may have incomplete
historical XBRL filings.

This introduces classic survivorship bias when performing long-term backtests
or cross-sectional analysis: averages/metrics will be optimistically biased
because failed companies (often poor performers) are excluded.

Mitigation in this module:
  - Explicit documentation and warnings printed/logged.
  - Users should supplement with historical ticker databases (e.g., CRSP,
    Compustat, or paid delisted data sources) for rigorous backtesting.
  - Cache strategy already minimizes re-fetching; no additional bias introduced.
"""

import requests
import pandas as pd
import cache

SEC_HEADERS = {"User-Agent": "GrahamScoreApp/1.0 contact@example.com"}
FACTS_URL   = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBS_URL    = "https://data.sec.gov/submissions/CIK{cik}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Module-level in-memory cache for the ticker map (one read per process).
_tickermap: dict | None = None


# ── Ticker map ────────────────────────────────────────────────────────────────

def get_ticker_map() -> dict:
    global _tickermap
    if _tickermap is not None:
        return _tickermap

    if not cache.is_ticker_map_stale():
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


# ── Submissions / latest-filing check ────────────────────────────────────────

def _fetch_submissions(cik: str) -> dict:
    """Fetch the submissions JSON for a company (lightweight, ~few KB)."""
    r = requests.get(SUBS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def _latest_filing_date(subs: dict) -> str | None:
    """
    Return the most-recent filing date (ISO string) for 10-K or 10-Q forms.
    """
    try:
        recent = subs["filings"]["recent"]
        forms  = recent.get("form", [])
        dates  = recent.get("filingDate", [])
        target = {"10-K", "10-Q", "10-K/A", "10-Q/A"}
        matches = [date for form, date in zip(forms, dates) if form in target]
        return max(matches) if matches else None
    except (KeyError, TypeError):
        return None


# ── Sector classification via SIC code ───────────────────────────────────────

def _sector_class(sic: int) -> str:
    """
    Map a 4-digit SIC code to a broad sector bucket used for XBRL fallback
    concept selection.  Returns one of:
      bank | insurance | realestate | utility | oil_gas | mining | biotech | general
    """
    if 6000 <= sic <= 6199:
        return "bank"
    if 6200 <= sic <= 6299:
        return "bank"          # brokers/dealers share bank-like income statements
    if 6300 <= sic <= 6499:
        return "insurance"
    if 6500 <= sic <= 6599:
        return "realestate"
    if 4900 <= sic <= 4999:
        return "utility"
    if 1300 <= sic <= 1399:
        return "oil_gas"
    if 1000 <= sic <= 1299:
        return "mining"
    if 2830 <= sic <= 2836:
        return "biotech"
    return "general"


# ── Low-level DataFrame helpers ───────────────────────────────────────────────

def _annual_df(facts: dict, concept: str, unit: str = "USD",
               years: int = 11, ns: str = "us-gaap") -> pd.DataFrame:
    """Pull annual 10-K entries for a single concept from a given namespace."""
    try:
        entries = facts[ns][concept]["units"][unit]
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


def _try_concepts(facts: dict, concepts: list, unit: str = "USD",
                  years: int = 11, ns: str = "us-gaap") -> pd.DataFrame:
    """
    Try a list of XBRL concept names in order, returning the first non-empty
    result.  All concepts are tried in the same namespace.
    """
    for concept in concepts:
        df = _annual_df(facts, concept, unit, years, ns=ns)
        if not df.empty:
            return df
    return pd.DataFrame()


def _try_concepts_multins(facts: dict,
                          concept_ns_pairs: list[tuple[str, str]],
                          unit: str = "USD",
                          years: int = 11) -> pd.DataFrame:
    """
    Try (concept, namespace) pairs in order.  Enables fallback across
    namespaces, e.g. (concept, 'us-gaap') then (concept, 'dei').
    """
    for concept, ns in concept_ns_pairs:
        df = _annual_df(facts, concept, unit, years, ns=ns)
        if not df.empty:
            return df
    return pd.DataFrame()


# ── Sector-aware concept lists ────────────────────────────────────────────────

def _revenue_concepts(sector: str) -> list[str]:
    """
    Ordered list of us-gaap revenue concepts to try, most-specific first.
    The general fallbacks (Revenues, etc.) are always appended so any
    company that uses non-standard concepts still gets a result.
    """
    sector_specific = {
        "bank": [
            # Net interest income is the closest analogue to gross revenue for banks.
            # We prefer the full top-line (interest + non-interest) where available.
            "InterestAndDividendIncomeOperating",
            "RevenuesNetOfInterestExpense",
            "InterestAndNoninterestIncome",
            "InterestIncomeExpenseAfterProvisionForLoanLosses",
            "NetInterestIncome",
            "BankingRevenue",
        ],
        "insurance": [
            "PremiumsEarnedNet",
            "PremiumsEarned",
            "NetPremiumsEarned",
            "PolicyChargesAndFeeIncome",
            "NetInvestmentIncome",            # life insurers
            "RevenuesNetOfInterestExpense",   # some use this
        ],
        "realestate": [
            "RealEstateRevenueNet",
            "OperatingLeasesIncomeStatementLeaseRevenue",
            "LeaseIncome",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ],
        "utility": [
            "RegulatedAndUnregulatedOperatingRevenue",
            "ElectricUtilityRevenue",
            "UtilitiesOperatingRevenue",
            "GasAndOilRevenue",
        ],
        "oil_gas": [
            "OilAndGasRevenue",
            "RevenuesFromOilGasProducingActivities",
            "ExplorationAndProductionRevenue",
            "SalesRevenueNet",
        ],
        "mining": [
            "MineralRevenue",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
        "biotech": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "ProductRevenue",
            "LicenseRevenue",
            "Revenues",
        ],
    }
    base = sector_specific.get(sector, [])
    general = [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenuesNetOfInterestExpense",
    ]
    # Return deduplicated list preserving order
    seen: set[str] = set()
    result = []
    for c in base + general:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _lt_debt_concepts(sector: str) -> list[str]:
    """Long-term debt concepts ordered by sector relevance."""
    sector_specific = {
        "bank": [
            "LongTermDebt",
            "SubordinatedDebt",
            "FederalHomeLoanBankAdvancesLongTerm",
            "JuniorSubordinatedDebentureOwedToUnconsolidatedSubsidiaryTrusts",
        ],
        "realestate": [
            "SecuredDebt",
            "MortgageNotesPayable",
            "NotesPayable",
            "LongTermDebt",
            "LongTermLineOfCredit",
        ],
        "utility": [
            "LongTermDebt",
            "LongTermDebtNoncurrent",
            "LongTermPollutionControlBond",
            "LongTermDebtAndCapitalLeaseObligations",
        ],
        "oil_gas": [
            "LongTermDebt",
            "LongTermLineOfCredit",
            "LongTermDebtAndCapitalLeaseObligations",
            "NotesPayable",
        ],
        "mining": [
            "LongTermDebt",
            "LongTermDebtAndCapitalLeaseObligations",
            "NotesPayable",
        ],
    }
    base = sector_specific.get(sector, [])
    general = [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "NotesPayable",
    ]
    seen: set[str] = set()
    result = []
    for c in base + general:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _op_income_concepts(sector: str) -> list[str]:
    """Operating income concepts ordered by sector relevance."""
    sector_specific = {
        "bank": [
            # Banks don't report traditional operating income; pre-tax income
            # is the closest proxy Greenblatt/Altman can use.
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            "IncomeLossBeforeIncomeTaxExpenseBenefit",
            "OperatingIncomeLoss",
        ],
        "insurance": [
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossBeforeIncomeTaxExpenseBenefit",
            "OperatingIncomeLoss",
            "UnderwritingIncomeLoss",
        ],
        "realestate": [
            "OperatingIncomeLoss",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "NetIncomeLossFromRealEstateInvestmentPartnership",
        ],
        "utility": [
            "OperatingIncomeLoss",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "UtilitiesOperatingIncomeLoss",
        ],
        "oil_gas": [
            "OperatingIncomeLoss",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "ResultsOfOperationsIncomeBeforeIncomeTaxes",
        ],
    }
    base = sector_specific.get(sector, [])
    general = [
        "OperatingIncomeLoss",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ]
    seen: set[str] = set()
    result = []
    for c in base + general:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _capex_concepts(sector: str) -> list[str]:
    """Capital expenditure concepts ordered by sector relevance."""
    sector_specific = {
        "realestate": [
            "PaymentsToAcquireRealEstateHeldForInvestment",
            "PaymentsToAcquireRealEstate",
            "PaymentsForCapitalImprovements",
            "PaymentsToAcquirePropertyPlantAndEquipment",
        ],
        "utility": [
            "PaymentsToAcquireProductiveAssets",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForCapitalImprovements",
        ],
        "oil_gas": [
            "PaymentsToExploreAndDevelopOilAndGasProperties",
            "PaymentsToAcquireOilAndGasPropertyAndEquipment",
            "PaymentsToAcquirePropertyPlantAndEquipment",
        ],
        "mining": [
            "PaymentsToAcquireMiningAssets",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForCapitalImprovements",
        ],
    }
    base = sector_specific.get(sector, [])
    general = [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
    ]
    seen: set[str] = set()
    result = []
    for c in base + general:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _cash_concepts(sector: str) -> list[str]:
    """Cash & equivalents concepts ordered by sector relevance."""
    sector_specific = {
        "bank": [
            # Banks hold "cash and due from banks" as their primary liquidity measure.
            "CashAndDueFromBanks",
            "CashAndCashEquivalentsAtCarryingValue",
            "CashAndCashEquivalentsPeriodIncreaseDecrease",
        ],
        "insurance": [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsAndShortTermInvestments",
        ],
    }
    base = sector_specific.get(sector, [])
    general = [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
        "Cash",
    ]
    seen: set[str] = set()
    result = []
    for c in base + general:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _cur_ast_concepts(sector: str) -> list[str]:
    """
    Current asset proxy concepts for sectors that don't use AssetsCurrent.
    Returns [] for sectors where current assets are genuinely inapplicable
    (banks) so scoring modules can handle N/A gracefully.
    """
    if sector == "bank":
        # Banks have no current/non-current split — leave empty deliberately.
        return []
    if sector == "insurance":
        return [
            "AssetsCurrent",
            "PremiumsReceivableAtCarryingValue",
            "ReceivablesNetCurrent",
            "InvestmentsAndCash",
        ]
    if sector == "realestate":
        return [
            "AssetsCurrent",
            "ReceivablesNetCurrent",
        ]
    # utility, oil_gas, mining, biotech, general — standard concept usually works
    return ["AssetsCurrent"]


def _cur_lib_concepts(sector: str) -> list[str]:
    """
    Current liability proxy concepts for sectors that don't use LiabilitiesCurrent.
    """
    if sector == "bank":
        return []   # no current/non-current split for banks
    if sector == "insurance":
        return [
            "LiabilitiesCurrent",
            "UnearnedPremiums",
            "LossAndLossAdjustmentExpenseReserves",
            "InsuranceLossReserves",
        ]
    if sector == "realestate":
        return [
            "LiabilitiesCurrent",
            "AccountsPayableAndAccruedLiabilitiesCurrentAndNoncurrent",
        ]
    return ["LiabilitiesCurrent"]


# ── Derived-field helpers ─────────────────────────────────────────────────────

def _shares_df(facts: dict, years: int = 11) -> pd.DataFrame:
    """
    Resolve shares outstanding across multiple namespaces.
    Insurance companies and others file EntityCommonStockSharesOutstanding
    under the DEI namespace rather than us-gaap.

    Sanity check: REITs and operating partnerships sometimes co-file under
    the same CIK, and the LP/OP unit count (often just a few thousand) can
    appear first in us-gaap:CommonStockSharesOutstanding even though the
    actual common share count is in the hundreds of millions.  We require
    the most-recent value to be ≥ MIN_PLAUSIBLE_SHARES (100,000); if a
    concept returns fewer shares we skip it and keep searching.
    """
    # Any company with fewer than 100k shares is almost certainly an LP/OP
    # unit class, not the common equity we want for per-share calculations.
    MIN_PLAUSIBLE_SHARES = 100_000

    concept_ns_pairs = [
        ("CommonStockSharesOutstanding",          "us-gaap"),
        ("SharesOutstanding",                     "us-gaap"),
        ("EntityCommonStockSharesOutstanding",    "us-gaap"),
        ("CommonStockSharesIssuedNet",            "us-gaap"),
        ("EntityCommonStockSharesOutstanding",    "dei"),    # ← insurers, financials
        ("CommonStockSharesIssued",               "us-gaap"),
        ("CommonStockSharesAuthorized",           "us-gaap"),  # last resort
    ]

    for concept, ns in concept_ns_pairs:
        try:
            entries = facts[ns][concept]["units"]["shares"]
        except KeyError:
            continue

        df = pd.DataFrame(entries)
        if df.empty:
            continue

        df_10k = df[df["form"] == "10-K"].copy()
        if df_10k.empty:
            df_10k = df.copy()

        df_10k["year"] = pd.to_datetime(df_10k["end"]).dt.year
        df_10k = (
            df_10k.sort_values("filed")
            .drop_duplicates("year", keep="last")
            .sort_values("year", ascending=False)
            .head(years)[["year", "val"]]
            .rename(columns={"val": "value"})
            .reset_index(drop=True)
        )

        if df_10k.empty:
            continue

        # Sanity check: skip LP/OP unit classes with implausibly few shares
        latest_shares = float(df_10k["value"].iloc[0])
        if latest_shares < MIN_PLAUSIBLE_SHARES:
            print(f"  [SEC] shares via '{ns}:{concept}' rejected "
                  f"(latest={latest_shares:,.0f} < {MIN_PLAUSIBLE_SHARES:,} — "
                  "likely LP/OP units, not common equity)")
            continue

        print(f"  [SEC] shares via '{ns}:{concept}' "
              f"({len(df_10k)} years, latest {df_10k['year'].iloc[0]}, "
              f"{latest_shares/1e6:.1f}M shares)")
        return df_10k

    print("  [SEC] ⚠️  No shares-outstanding concept found")
    return pd.DataFrame()


def _equity_df(facts: dict, years: int = 11) -> pd.DataFrame:
    concepts = [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapital",
        "MembersEquity",                        # LLCs/partnerships
        "EquityAttributableToParent",
    ]
    for concept in concepts:
        df = _annual_df(facts, concept, "USD", years)
        if not df.empty:
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


def _tot_lib_df(facts: dict, equity_df: pd.DataFrame,
                sector: str = "general", years: int = 11) -> pd.DataFrame:
    """
    Total liabilities: try direct concepts first, then derive from
    LiabilitiesAndStockholdersEquity − Equity as fallback.
    Banks use a broader balance sheet so we add their specific concept.
    """
    direct_concepts = ["Liabilities", "LiabilitiesNoncurrentAndCurrent"]
    if sector == "bank":
        direct_concepts = [
            "Liabilities",
            "LiabilitiesAndStockholdersEquity",   # some banks only file L+SE
        ] + direct_concepts

    df = _try_concepts(facts, direct_concepts)
    if not df.empty:
        print(f"  [SEC] total liabilities: direct ({len(df)} years)")
        return df

    # Derivation: L+SE − Equity
    lse_df = annual(facts, "LiabilitiesAndStockholdersEquity", years=years)
    if not lse_df.empty and not equity_df.empty:
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
    if equity_df.empty or shares_df.empty:
        return pd.DataFrame()

    # Exact-year join first
    merged = equity_df.merge(
        shares_df[["year", "value"]].rename(columns={"value": "shares"}),
        on="year", how="inner"
    )
    merged = merged[merged["shares"] > 0].copy()

    if not merged.empty:
        merged["value"] = merged["value"] / merged["shares"]
        print(f"  [SEC] BVPS: exact-year join ({len(merged)} rows)")
        return merged[["year", "value"]].reset_index(drop=True)

    print(f"  [SEC] BVPS exact join failed "
          f"(equity years: {list(equity_df['year'])[:4]}, "
          f"share years: {list(shares_df['year'])[:4]}) — trying nearest-year")

    rows = []
    for _, eq_row in equity_df.iterrows():
        yr = eq_row["year"]
        near = shares_df[abs(shares_df["year"] - yr) <= 1]
        if near.empty:
            continue
        near = near.iloc[(near["year"] - yr).abs().argsort()[:1]]
        sh = float(near["value"].iloc[0])
        if sh > 0:
            rows.append({"year": yr, "value": float(eq_row["value"]) / sh})

    if rows:
        result = pd.DataFrame(rows).sort_values("year", ascending=False).reset_index(drop=True)
        print(f"  [SEC] BVPS: nearest-year join ({len(result)} rows)")
        return result

    # Last-resort: latest single row
    eq_val = float(equity_df["value"].iloc[0])
    sh_val = float(shares_df["value"].iloc[0])
    yr_val = int(equity_df["year"].iloc[0])
    if sh_val > 0:
        print(f"  [SEC] BVPS: last-resort single-row fallback (yr={yr_val})")
        return pd.DataFrame([{"year": yr_val, "value": eq_val / sh_val}])

    return pd.DataFrame()


def _gross_profit_df(facts: dict, sector: str, years: int = 11) -> pd.DataFrame:
    """
    Gross profit is only meaningful for manufacturers, retailers, and
    some tech companies.  Banks, insurers, REITs, and utilities don't
    report it — we return empty for those sectors to avoid forcing a
    zero score in quality.py when the concept simply isn't applicable.
    """
    if sector in ("bank", "insurance", "realestate", "utility"):
        return pd.DataFrame()   # genuinely N/A — don't pretend it's 0
    return _try_concepts(facts, [
        "GrossProfit",
        "GrossProfitLoss",
        "RevenueFromContractWithCustomerExcludingAssessedTaxAfterCostOfGoods",
    ], years=years)


# ── Main fetch entry-point ────────────────────────────────────────────────────

def fetch_company_facts(symbol: str, include_delisted_warning: bool = True) -> dict:
    """
    Return company facts for ``symbol``, using the local cache when SEC has
    not published a newer 10-K or 10-Q since the last fetch.

    Flow
    ────
    1. Resolve ticker → CIK.
    2. Fetch /submissions/ to learn the latest filing date (cheap).
    3. Return cached facts if they're already up-to-date.
    4. Otherwise fetch the full /companyfacts/ blob and rebuild the cache.

    Sector detection
    ────────────────
    The SIC code from /submissions/ is used to select the right XBRL concept
    fallback lists.  All downstream scoring modules receive the same key
    names regardless of sector — they just may have fewer populated fields
    for sectors where certain line items are structurally absent (e.g. no
    current assets for banks).
    """
    cik, name = get_cik(symbol)
    print(f"\n📡 Checking SEC EDGAR for {symbol} (CIK {cik})...")

    if include_delisted_warning:
        print("  [SEC] ⚠️  Survivorship bias warning: Data reflects surviving "
              "companies only. Delisted / bankrupt entities are typically excluded.")

    # ── Step 1: cheap submissions fetch ───────────────────────────────────────
    subs      = _fetch_submissions(cik)
    sector    = subs.get("sicDescription", "Unknown")
    full_name = subs.get("name", name)

    try:
        sic = int(subs.get("sic", 0) or 0)
    except (TypeError, ValueError):
        sic = 0
    sector_cls = _sector_class(sic)
    print(f"  [SEC] Sector: {sector} (SIC {sic} → '{sector_cls}')")

    latest_filing = _latest_filing_date(subs)
    print(f"  [SEC] Latest 10-K/10-Q filing date: {latest_filing}")

    # ── Step 2: cache check ───────────────────────────────────────────────────
    if latest_filing and not cache.is_stale_for_company(symbol, latest_filing):
        cached = cache.read("sec_facts", symbol.lower())
        if cached:
            print(f"  [SEC] ✅ Returning cached facts for {symbol}")
            return cached

    # ── Step 3: fetch full companyfacts blob ──────────────────────────────────
    print(f"  [SEC] Fetching full companyfacts for {symbol}...")
    facts_r = requests.get(FACTS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=30)
    facts_r.raise_for_status()
    facts = facts_r.json()["facts"]

    # ── Core income / book data ───────────────────────────────────────────────
    eps_df    = annual_per_share(facts, "EarningsPerShareBasic")
    if eps_df.empty:
        eps_df = annual_per_share(facts, "EarningsPerShareDiluted")

    equity_df = _equity_df(facts)
    shares_df = _shares_df(facts)
    net_inc_df = annual(facts, "NetIncomeLoss")

    # ── Balance sheet ─────────────────────────────────────────────────────────
    cur_ast_df = _try_concepts(facts, _cur_ast_concepts(sector_cls))
    cur_lib_df = _try_concepts(facts, _cur_lib_concepts(sector_cls))
    lt_debt_df = _try_concepts(facts, _lt_debt_concepts(sector_cls))
    tot_lib_df = _tot_lib_df(facts, equity_df, sector=sector_cls)

    total_assets_df = _try_concepts(facts, ["Assets"])

    retained_earnings_df = _try_concepts(facts, [
        "RetainedEarningsAccumulatedDeficit",
        "RetainedEarningsUnappropriated",
        "RetainedEarningsDeficit",
    ])

    ppe_net_df = _try_concepts(facts, [
        "PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        # REITs / real-estate specific
        "RealEstateInvestmentPropertyNet",
        "RealEstateInvestmentPropertyAtCost",
    ])

    cash_df = _try_concepts(facts, _cash_concepts(sector_cls))

    # ── Income statement ──────────────────────────────────────────────────────
    rev_df          = _try_concepts(facts, _revenue_concepts(sector_cls))
    gross_profit_df = _gross_profit_df(facts, sector_cls)
    operating_inc_df = _try_concepts(facts, _op_income_concepts(sector_cls))

    # ── Cash flow ─────────────────────────────────────────────────────────────
    operating_cf_df = _try_concepts(facts, [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ])
    capex_df = _try_concepts(facts, _capex_concepts(sector_cls))

    # ── Dividends ─────────────────────────────────────────────────────────────
    div_df = _try_concepts(facts, [
        "PaymentsOfDividendsCommonStock",
        "DividendsCommonStockCash",
        "PaymentsOfDividends",
        "PaymentsOfOrdinaryDividends",
    ], years=30)
    if div_df.empty:
        div_df = annual_per_share(facts, "CommonStockDividendsPerShareDeclared", years=30)

    # ── BVPS ─────────────────────────────────────────────────────────────────
    bvps_df = _bvps_df(equity_df, shares_df)

    # ── Diagnostic summary ────────────────────────────────────────────────────
    always_required = {
        "eps": eps_df, "equity": equity_df, "shares": shares_df,
        "net_inc": net_inc_df, "total_assets": total_assets_df, "cash": cash_df,
    }
    sector_optional = {
        # Genuinely absent for some sectors — absence is not an error
        "bvps": bvps_df, "tot_lib": tot_lib_df, "lt_debt": lt_debt_df,
        "revenue": rev_df, "op_income": operating_inc_df, "op_cf": operating_cf_df,
    }
    missing_required = [k for k, v in always_required.items() if v.empty]
    missing_optional = [k for k, v in sector_optional.items() if v.empty]

    if missing_required:
        print(f"  [SEC] ⚠️  Missing required fields for {symbol}: {', '.join(missing_required)}")
    if missing_optional:
        print(f"  [SEC] ℹ️  Optional fields absent for {symbol} ({sector_cls}): {', '.join(missing_optional)}")
    if not missing_required:
        print(f"  [SEC] ✅ All required fields resolved for {symbol}")

    result = {
        # Identification
        "cik":               cik,
        "name":              full_name,
        "sector":            sector,
        "sector_cls":        sector_cls,
        "sic":               sic,

        # Per-share
        "eps":               eps_df.to_dict("records"),
        "bvps":              bvps_df.to_dict("records"),

        # Balance sheet
        "cur_ast":           cur_ast_df.to_dict("records"),
        "cur_lib":           cur_lib_df.to_dict("records"),
        "lt_debt":           lt_debt_df.to_dict("records"),
        "tot_lib":           tot_lib_df.to_dict("records"),
        "equity":            equity_df.to_dict("records"),
        "shares":            shares_df.to_dict("records"),
        "total_assets":      total_assets_df.to_dict("records"),
        "retained_earnings": retained_earnings_df.to_dict("records"),
        "ppe_net":           ppe_net_df.to_dict("records"),
        "cash":              cash_df.to_dict("records"),

        # Income statement
        "net_inc":           net_inc_df.to_dict("records"),
        "revenue":           rev_df.to_dict("records"),
        "gross_profit":      gross_profit_df.to_dict("records"),
        "op_income":         operating_inc_df.to_dict("records"),

        # Cash flow
        "op_cf":             operating_cf_df.to_dict("records"),
        "capex":             capex_df.to_dict("records"),
        "dividends":         div_df.to_dict("records"),
    }

    cache.write("sec_facts", symbol.lower(), result, latest_filing=latest_filing)
    return result
