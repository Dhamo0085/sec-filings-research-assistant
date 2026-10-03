"""The gold set's plan: every item named explicitly, with its reason (P4-01).

Why a committed plan rather than a sample
-----------------------------------------
Spec section 11 fixes the shape of the gold set (numeric 25, computed 12,
compare/trend 10, narrative 15, ``as_of`` 8, abstain 10) and the coverage it has
to reach: all four sectors, at least one thousands-reporting filer, one
split-document filer, one multi-concept filer. A random sample can satisfy those
counts and still miss the cases the project was built for, and it cannot be
reviewed — the owner's P4-02 gate is a line-by-line check of ≥ 25 items, which
needs each item to say why it is in the set.

So the plan is a table. ``build_gold.py`` resolves each row against the facts
store, verifies it against SEC companyfacts, and fails loudly if a row cannot be
produced, rather than quietly emitting a smaller set.

The four sectors are Technology, Banking and Asset Management from
``config.COMPANIES``, plus Media (Netflix), which is the bundled on-demand
filer and the corpus's only thousands-reporting one. P2-10 used the same
four-way split for the owner spot-check sheet.

The three named filer properties, and where they are covered:

* **thousands-reporting** — NFLX tags with ``scale="3"`` (822 facts; every other
  filer in the corpus uses 6 or 9). This is the shape that broke v1: its
  "assume millions" prompt turned Netflix's figures into a 1000x error.
* **split-document** — WFC incorporates Items 1A, 7 and 8 by reference into its
  EX-13 annual report, so its 10-K submission is several documents.
* **multi-concept** — BLK tags both ``us-gaap:Revenues`` and
  ``...RevenueFromContractWithCustomerExcludingAssessedTax`` with different
  values, and needs its evidenced override to resolve (D0.3).

Every row here was checked against the facts store before being committed, and
``build_gold.py`` fails rather than shrinking the set if one stops resolving.
That already caught a planned row: Netflix was first given a gross-profit item,
and Netflix does not tag a gross-profit line — only AAPL, MSFT, NVDA and TSLA in
this corpus do. The row moved to Microsoft and Netflix took an operating-income
item instead, which keeps all twelve registry metrics covered and keeps the
thousands-reporting filer at three items.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Tuple

#: ticker -> the sector label this gold set uses.
SECTORS: Dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology",
    "GOOGL": "Technology", "AMZN": "Technology",
    "JPM": "Banking", "WFC": "Banking", "BAC": "Banking", "GS": "Banking",
    "BLK": "Asset Management", "STT": "Asset Management",
    "TROW": "Asset Management", "IVZ": "Asset Management",
    "NFLX": "Media",
}

#: How a company is named in a question. Using the filer's common name rather
#: than its ticker is the point: entity resolution is part of what is measured.
DISPLAY_NAMES: Dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Alphabet",
    "AMZN": "Amazon", "JPM": "JPMorgan Chase", "WFC": "Wells Fargo",
    "BAC": "Bank of America", "GS": "Goldman Sachs", "BLK": "BlackRock",
    "STT": "State Street", "TROW": "T. Rowe Price", "IVZ": "Invesco",
    "NFLX": "Netflix",
}

#: How a metric is asked for, in the filer's own vocabulary where it differs.
#: A bank does not call it "revenue" and the question should not either —
#: D5 is about the headline total each filer actually reports.
METRIC_PHRASES: Dict[str, str] = {
    "revenue": "revenue",
    "net_income": "net income",
    "operating_income": "operating income",
    "gross_profit": "gross profit",
    "rd_expense": "research and development expense",
    "total_assets": "total assets",
    "total_liabilities": "total liabilities",
    "stockholders_equity": "total stockholders' equity",
    "cash_and_equivalents": "cash and cash equivalents",
    "operating_cash_flow": "cash flow from operating activities",
    "capex": "capital expenditures",
    "eps_diluted": "diluted earnings per share",
}

#: Bank-specific phrasing for the headline revenue line (D5).
BANK_REVENUE_PHRASE: Dict[str, str] = {
    "JPM": "total net revenue",
    "BAC": "total revenue, net of interest expense",
    "GS": "total net revenues",
    "WFC": "total revenue",
}


class NumericRow(NamedTuple):
    ticker: str
    metric: str
    fiscal_label: int
    why: str


#: 25 numeric items. Each sector appears, each of the twelve registry metrics
#: appears at least once, and the three named filer properties are covered.
NUMERIC_PLAN: Tuple[NumericRow, ...] = (
    # Technology — the straightforward path, and the fiscal-label edge cases.
    NumericRow("AAPL",  "revenue",              2024, "the worked example in spec 6.5; a 52/53-week fiscal year"),
    NumericRow("AAPL",  "eps_diluted",          2024, "a per-share unit, not a currency total"),
    NumericRow("MSFT",  "net_income",           2024, "June fiscal year end — the label is not the calendar year"),
    NumericRow("MSFT",  "revenue",              2026, "the newest filing in the corpus; fiscal label 2026 in calendar 2026"),
    NumericRow("MSFT",  "gross_profit",         2024, "only AAPL, MSFT, NVDA and TSLA tag a gross-profit line at all"),
    NumericRow("GOOGL", "rd_expense",           2024, "R&D is a tech-only line; banks do not report it"),
    NumericRow("AMZN",  "operating_cash_flow",  2024, "cash-flow statement rather than income statement"),
    NumericRow("AMZN",  "capex",                2024, "a negative-signed investing line in the filing"),
    NumericRow("AMZN",  "total_assets",         2023, "an instant fact, not a duration — balance-sheet date, no start"),
    # Banking — D5's headline-revenue problem, and the split-document filer.
    NumericRow("JPM",   "revenue",              2024, "D5: a bank's headline is total net revenue, not a product sale"),
    NumericRow("JPM",   "net_income",           2024, "cross-checks the same filing on a second line"),
    NumericRow("BAC",   "revenue",              2024, "revenue net of interest expense — a different headline again"),
    NumericRow("GS",    "revenue",              2024, "total net revenues; GS tags with scale 9, not 6"),
    NumericRow("GS",    "total_assets",         2024, "a trillion-dollar instant value — magnitude stress"),
    NumericRow("WFC",   "revenue",              2024, "SPLIT-DOCUMENT FILER: Items 1A/7/8 live in the EX-13 exhibit"),
    NumericRow("WFC",   "stockholders_equity",  2024, "split-document filer on a balance-sheet line"),
    NumericRow("BAC",   "total_liabilities",    2024, "a line AMZN does not tag at all — coverage is filer-specific"),
    # Asset management — the multi-concept filer and the two-CIK filer.
    NumericRow("BLK",   "revenue",              2024, "MULTI-CONCEPT FILER: two revenue concepts, 37% apart (D0.3)"),
    NumericRow("BLK",   "revenue",              2023, "BLK FY2023 is filed under the OLD CIK 1364742 (D24)"),
    NumericRow("STT",   "net_income",           2024, "a custody bank; its balance sheet is a Statement of Condition"),
    NumericRow("TROW",  "revenue",              2024, "a filer whose Item 7 the v1 parser lost entirely (P4-00)"),
    NumericRow("IVZ",   "cash_and_equivalents", 2024, "tier choice: carrying value, not the restricted-cash superset"),
    # Media — the thousands-reporting filer this project exists to get right.
    NumericRow("NFLX",  "revenue",              2024, "THOUSANDS-REPORTING FILER: scale=3, the v1 1000x failure (K2)"),
    NumericRow("NFLX",  "operating_income",     2024, "thousands-reporting filer on the income statement's subtotal"),
    NumericRow("NFLX",  "net_income",           2023, "thousands-reporting filer, second year and second line"),
)


class ComputedRow(NamedTuple):
    kind: str            # margin_pct | growth_pct | cagr_pct | ratio | difference
    ticker: str
    args: Tuple         # metric names and fiscal labels, per kind
    why: str


#: 12 computed items. Every operation in facts/calc.py that a user can ask for
#: is exercised, and the awkward cases are deliberate: a bank has no gross
#: margin, a CAGR needs a year count, a ratio is unitless.
COMPUTED_PLAN: Tuple[ComputedRow, ...] = (
    ComputedRow("margin_pct",  "AAPL",  ("operating_income", "revenue", 2024),  "operating margin — the spec 6.5 computed example"),
    ComputedRow("margin_pct",  "AAPL",  ("net_income", "revenue", 2024),        "net margin on the same filing; the two must not be confused"),
    ComputedRow("margin_pct",  "MSFT",  ("gross_profit", "revenue", 2024),      "gross margin, which needs a gross-profit line"),
    ComputedRow("margin_pct",  "NFLX",  ("operating_income", "revenue", 2024),  "a margin over thousands-scaled operands — scale must cancel"),
    ComputedRow("growth_pct",  "AAPL",  ("revenue", 2023, 2024),                "year-over-year growth, the commonest computed question"),
    ComputedRow("growth_pct",  "JPM",   ("net_income", 2023, 2024),             "growth on a bank's bottom line"),
    ComputedRow("growth_pct",  "NFLX",  ("revenue", 2023, 2024),                "growth where both operands are thousands-scaled"),
    ComputedRow("cagr_pct",    "MSFT",  ("revenue", 2022, 2025, 3),             "a three-year CAGR — the year count is part of the answer"),
    ComputedRow("cagr_pct",    "GOOGL", ("revenue", 2022, 2025, 3),             "a second CAGR, on a December filer"),
    ComputedRow("ratio",       "AAPL",  ("total_liabilities", "stockholders_equity", 2024), "a unitless ratio, not a percentage"),
    ComputedRow("difference",  "AMZN",  ("operating_cash_flow", "capex", 2024), "free cash flow as a difference of two cash-flow lines"),
    ComputedRow("margin_pct",  "GS",    ("net_income", "revenue", 2024),        "a bank's net margin over its own headline revenue (D5)"),
)


class CompareRow(NamedTuple):
    kind: str            # compare | trend
    tickers: Tuple[str, ...]
    metric: str
    labels: Tuple[int, ...]
    why: str


#: 10 compare/trend items, scored as `multi`: every value must be attributed to
#: the right company and year, which is the specific failure a single-number
#: scorer cannot see.
COMPARE_PLAN: Tuple[CompareRow, ...] = (
    CompareRow("compare", ("AAPL", "MSFT"),  "revenue",          (2024,), "two tech filers with different fiscal year ends"),
    CompareRow("compare", ("JPM", "BAC"),    "net_income",       (2024,), "two banks, same calendar year end"),
    CompareRow("compare", ("NFLX", "GOOGL"), "revenue",          (2024,), "a thousands-scaled filer against a millions-scaled one"),
    CompareRow("compare", ("BLK", "TROW"),   "revenue",          (2024,), "two asset managers; BLK needs its override to resolve"),
    CompareRow("compare", ("GS", "MSFT"),    "total_assets",     (2024,), "a bank's balance sheet against a tech filer's"),
    CompareRow("trend",   ("AAPL",),         "revenue",          (2023, 2024, 2025), "three consecutive years of one filer"),
    CompareRow("trend",   ("NFLX",),         "revenue",          (2023, 2024, 2025), "a trend entirely in thousands-scaled facts"),
    CompareRow("trend",   ("JPM",),          "net_income",       (2022, 2023, 2024), "a bank's trend across three filings"),
    CompareRow("trend",   ("MSFT",),         "operating_income", (2024, 2025, 2026), "a June-fiscal-year trend ending at the newest filing"),
    CompareRow("trend",   ("WFC",),          "revenue",          (2023, 2024, 2025), "the split-document filer across three years"),
)


class AsOfRow(NamedTuple):
    ticker: str
    metric: str
    fiscal_label: int
    stance: str          # "answerable" | "look_ahead"
    why: str


#: 8 point-in-time items. Half must be answered, half must be refused with
#: ``period_not_filed_as_of``. ``build_gold.py`` derives each ``as_of`` date
#: from the catalog's own filing dates — a look-ahead item whose date is not
#: genuinely before the filing proves nothing, and the schema validator
#: rejects it (T4-02).
AS_OF_PLAN: Tuple[AsOfRow, ...] = (
    AsOfRow("AAPL", "revenue",    2024, "look_ahead", "the commonest look-ahead: ask the day before Apple filed"),
    AsOfRow("MSFT", "net_income", 2025, "look_ahead", "a June filer, so the fiscal label alone looks available"),
    AsOfRow("JPM",  "revenue",    2024, "look_ahead", "a bank filing in February about the previous December"),
    AsOfRow("NFLX", "revenue",    2025, "look_ahead", "the newest NFLX filing, asked before it existed"),
    AsOfRow("AAPL", "revenue",    2023, "answerable", "the same company and metric, on a date when it WAS public"),
    AsOfRow("MSFT", "net_income", 2024, "answerable", "the year before the look-ahead item above"),
    AsOfRow("GS",   "revenue",    2024, "answerable", "a bank answerable well after its filing date"),
    AsOfRow("BLK",  "revenue",    2023, "answerable", "BLK FY2023 under the old CIK, answerable as of 2025 (D24)"),
)


def sector_coverage(tickers: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for ticker in tickers:
        sector = SECTORS.get(ticker, "Unknown")
        counts[sector] = counts.get(sector, 0) + 1
    return counts


def metric_phrase(ticker: str, metric: str) -> str:
    """How to ask for ``metric`` from ``ticker``, in that filer's vocabulary."""
    if metric == "revenue" and ticker in BANK_REVENUE_PHRASE:
        return BANK_REVENUE_PHRASE[ticker]
    return METRIC_PHRASES[metric]
