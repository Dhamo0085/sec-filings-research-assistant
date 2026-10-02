#!/usr/bin/env python3
"""Owner spot-check sheet (P2-10): verify extracted values against the filings.

    python scripts/make_spotcheck_sheet.py

Writes ``reports/phase2/owner_spotcheck.csv`` with one row per
(ticker, metric, fiscal year) to check by hand. Each row carries the EDGAR URL
of the exact document, which statement to look at, the concept the resolver
chose, the value as extracted, and the value as the filing prints it (the
pre-scale numeral), plus an empty ``verdict`` column for OK / WRONG.

The point of the sheet is that it is checkable without trusting anything in
this repository. The `filing_value_as_printed` column is what the owner will
actually see on the page — 391,035 rather than 391035000000 — because asking
someone to confirm a twelve-digit number against a table printed in millions is
asking them to do arithmetic instead of checking.

Selection covers what the spec asks for: every sector, and specifically BLK
(dual revenue concepts, resolved by override), BAC, GS, WFC (facts in an EX-13
exhibit), and NFLX (reports in thousands). It also includes the cases most
likely to be wrong rather than the ones most likely to be right: the override,
the tier-2 fallbacks, and a negative value.
"""

from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from catalog.store import CatalogStore  # noqa: E402
from config import settings  # noqa: E402
from facts.resolve import FactsResolver, PeriodSelector  # noqa: E402
from facts.store import FactsStore  # noqa: E402

STATEMENT_HINT = {
    "income": "Consolidated Statements of Operations / of Income",
    "balance": "Consolidated Balance Sheets / Statements of Financial Condition",
    "cash_flow": "Consolidated Statements of Cash Flows",
}

# (ticker, metric, fiscal label, why this row is on the sheet)
ROWS: List[Tuple[str, str, int, str]] = [
    # Technology
    ("AAPL",  "revenue",             2024, "baseline; 52/53-week fiscal year (363 days)"),
    ("AAPL",  "eps_diluted",         2024, "a per-share unit, not a currency total"),
    ("MSFT",  "revenue",             2026, "fiscal label 2026 for a year ending 2026-06-30"),
    ("GOOGL", "net_income",          2024, "tier-1 NetIncomeLoss, not ProfitLoss (D2-02)"),
    ("AMZN",  "operating_cash_flow", 2024, "cash-flow statement rather than income"),
    # Media / thousands reporter
    ("NFLX",  "revenue",             2024, "REPORTS IN THOUSANDS (scale=3) - the K2 1000x case"),
    ("NFLX",  "total_assets",        2024, "instant fact at the period end, in thousands"),
    # Banking
    ("JPM",   "revenue",             2024, "two revenue concepts tagged with the SAME value"),
    ("BAC",   "revenue",             2024, "bank: revenue is not a product sale"),
    ("BAC",   "net_income",          2024, ""),
    ("GS",    "revenue",             2024, ""),
    ("GS",    "operating_cash_flow", 2024, "NEGATIVE value - check the sign"),
    ("WFC",   "revenue",             2024, "facts live in the EX-13 exhibit, not the 10-K wrapper"),
    ("WFC",   "stockholders_equity", 2024, "tier-1 parent equity, not including-NCI (D2-02)"),
    # Asset management
    ("BLK",   "revenue",             2024, "OVERRIDE: 20,407 (total revenue), not the 12,794 component"),
    ("BLK",   "net_income",          2024, "tier-1 NetIncomeLoss 5,901 vs ProfitLoss 6,205"),
    ("STT",   "revenue",             2024, ""),
    ("TROW",  "net_income",          2024, ""),
    ("IVZ",   "cash_and_equivalents", 2024, "tier-1 cash, NOT the restricted-cash superset"),
    # Cross-check-only filer, never tuned for
    ("COST",  "revenue",             2025, "a filer with no override, sector entry or fixture"),
]


def printed_numeral(value: Decimal, scale_raw: Optional[str]) -> str:
    """The value as the filing prints it: pre-scale, with thousands separators."""
    try:
        scale = int(scale_raw) if scale_raw else 0
    except ValueError:
        scale = 0
    unscaled = value.scaleb(-scale)
    plain = format(unscaled.normalize(), "f")
    negative = plain.startswith("-")
    plain = plain.lstrip("-")
    integer, _, fraction = plain.partition(".")
    try:
        grouped = f"{int(integer):,}"
    except ValueError:
        return plain
    body = f"{grouped}.{fraction}" if fraction else grouped
    return f"({body})" if negative else body


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="reports/phase2/owner_spotcheck.csv")
    args = ap.parse_args(argv)

    catalog = CatalogStore(Path(settings.catalog_path))
    store = FactsStore(Path(settings.facts_db_path))
    resolver = FactsResolver(catalog=catalog, store=store)

    out_rows: List[Dict] = []
    unresolved: List[str] = []
    for ticker, metric, label, note in ROWS:
        outcome = resolver.resolve(ticker, metric, PeriodSelector.fiscal_label(label))
        if not outcome.resolved:
            unresolved.append(f"{ticker} {metric} FY{label}: {outcome.reason}")
            continue
        filing = catalog.get(outcome.accession)
        facts = [f for f in store.query(accession=outcome.accession,
                                        concept=outcome.concept)
                 if f.value == outcome.value]
        scale_raw = facts[0].scale_raw if facts else None
        spec = resolver.registry.metric(metric)
        acc_nodash = outcome.accession.replace("-", "")
        doc = facts[0].source_doc if facts else (filing.primary_doc if filing else "")
        url = (f"https://www.sec.gov/Archives/edgar/data/{filing.cik}/"
               f"{acc_nodash}/{doc}") if filing else ""
        out_rows.append({
            "ticker": ticker,
            "company": filing.entity_name if filing else "",
            "fiscal_label": label,
            "period_end": outcome.end_date,
            "metric": metric,
            "metric_label": outcome.metric_label,
            "concept": outcome.concept,
            "value_extracted": str(outcome.value),
            "unit": outcome.unit or "",
            "filing_value_as_printed": printed_numeral(outcome.value, scale_raw),
            "scale_attr": scale_raw or "",
            "statement_to_check": STATEMENT_HINT.get(spec.statement, spec.statement),
            "form_type": outcome.form_type,
            "filing_date": outcome.filing_date,
            "accession": outcome.accession,
            "filing_url": url,
            "selection": outcome.selection,
            "validation_status": outcome.validation_status,
            "why_this_row": note,
            "verdict": "",
            "owner_note": "",
        })

    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)

    sectors = {resolver.registry.sector_for(r["ticker"]) for r in out_rows}
    required = {"BLK", "BAC", "GS", "WFC", "NFLX"}
    missing = required - {r["ticker"] for r in out_rows}

    print(f"{len(out_rows)} row(s) written to {out.relative_to(REPO_ROOT)}")
    print(f"sectors covered: {', '.join(sorted(sectors))}")
    print(f"spec-required filers present: "
          f"{'all' if not missing else 'MISSING ' + ', '.join(sorted(missing))}")
    if unresolved:
        print(f"\n{len(unresolved)} planned row(s) did not resolve:")
        for item in unresolved:
            print(f"  {item}")

    print(f"\n{'TICKER':7s} {'FY':>5s} {'METRIC':20s} {'AS PRINTED':>16s} "
          f"{'SCALE':>5s}  CONCEPT")
    for row in out_rows:
        print(f"{row['ticker']:7s} {row['fiscal_label']:5d} {row['metric']:20s} "
              f"{row['filing_value_as_printed']:>16s} {row['scale_attr']:>5s}  "
              f"{row['concept'].split(':')[-1][:44]}")
    return 1 if (unresolved or missing or len(out_rows) < 15) else 0


if __name__ == "__main__":
    sys.exit(main())
