#!/usr/bin/env python3
"""Owner verification sheet for the gold set (P4-02, spec section 11).

    python scripts/make_gold_verification_sheet.py

Writes ``reports/phase4/gold_verification.csv``: one row per generated numeric
and computed gold item, with everything needed to check it against the filing
itself — the SEC URL, which statement to look at, the figure **as the filing
prints it**, and SEC companyfacts' own value.

Why this gate exists
--------------------
The gold set's expectations were produced by this project's own resolver and
confirmed against companyfacts. Both read the same iXBRL facts, so both can be
wrong in the same way: if a concept is the wrong line for a metric, the
extractor and the oracle will agree on a number that answers a different
question. Only a person reading the statement can catch that, which is why spec
section 11 publishes no headline metric until this sheet is signed.

Reading a row
-------------
``filing_value_as_printed`` is the figure in the filer's own scale and
punctuation — ``391,035`` for a filer reporting in millions, not
``391035000000``. The check is: open ``filing_url``, find
``statement_to_check``, and look for that figure on the line
``metric_label`` names.

``oracle_value`` is SEC companyfacts' value for the same accession, concept,
period and unit. It is already known to match; it is printed so a row where the
two agree and the statement disagrees is visibly a concept problem rather than
an arithmetic one.

A computed row additionally carries its operands and the operation, so the
arithmetic can be redone from two figures on the page.

``priority`` marks a stratified core of 25 rows — spec section 11's minimum —
covering every sector, both categories and the three named filer properties.
The owner may sign the core and leave the rest, and the report says which.

Fill in ``verdict`` (OK / WRONG) and ``owner_note``. Nothing in this script
writes a verdict.
"""

from __future__ import annotations

import argparse
import csv
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from catalog.store import CatalogStore  # noqa: E402
from config import settings  # noqa: E402
from eval.gold.plan import DISPLAY_NAMES, SECTORS  # noqa: E402
from eval.gold.schema import read_jsonl  # noqa: E402
from facts.resolve import FactsResolver, PeriodSelector  # noqa: E402
from facts.store import FactsStore  # noqa: E402
from scripts.crosscheck_companyfacts import CompanyFacts, index_oracle  # noqa: E402
from scripts.make_spotcheck_sheet import printed_numeral  # noqa: E402

GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"
DEFAULT_OUT = REPO_ROOT / "reports" / "phase4" / "gold_verification.csv"

#: Spec section 11's minimum for this gate.
CORE_ROWS = 25

STATEMENT_HINT = {
    "income": "Consolidated Statements of Operations / of Income",
    "balance": "Consolidated Balance Sheets / Statements of Financial Condition",
    "cash_flow": "Consolidated Statements of Cash Flows",
}

#: The filers spec section 11 names. Every one is in the signed core.
REQUIRED_TICKERS = ("NFLX", "WFC", "BLK")


def _filing_url(catalog: CatalogStore, accession: str, source_doc: str) -> str:
    filing = catalog.get(accession)
    if not filing:
        return ""
    doc = source_doc or filing.primary_doc or ""
    return (f"https://www.sec.gov/Archives/edgar/data/{filing.cik}/"
            f"{accession.replace('-', '')}/{doc}")


def _scale_and_doc(store: FactsStore, accession: str, concept: str, value: str):
    """The filer's declared scale and source document for this exact fact."""
    for fact in store.query(accession=accession, concept=concept):
        if str(fact.value) == value or format(fact.value, "f") == value:
            return fact.scale_raw, fact.source_doc
    return None, ""


def choose_core(rows: List[Dict]) -> None:
    """Mark a stratified core of CORE_ROWS rows, in place.

    Stratified means: one row from every (sector, category) pair that exists,
    then every filer spec section 11 names, then a round-robin over sectors
    until the quota is full. Round-robin rather than "the first 25" so the
    core cannot end up as four rows of one sector and none of another, and
    deterministic so the sheet does not reshuffle between runs.
    """
    for row in rows:
        row["priority"] = "extra"

    chosen: List[int] = []
    seen_cells = set()
    for index, row in enumerate(rows):
        cell = (row["sector"], row["category"])
        if cell not in seen_cells:
            seen_cells.add(cell)
            chosen.append(index)

    for ticker in REQUIRED_TICKERS:
        if any(rows[i]["ticker"] == ticker for i in chosen):
            continue
        for index, row in enumerate(rows):
            if row["ticker"] == ticker and index not in chosen:
                chosen.append(index)
                break

    sectors = sorted({row["sector"] for row in rows})
    while len(chosen) < min(CORE_ROWS, len(rows)):
        added = False
        for sector in sectors:
            if len(chosen) >= CORE_ROWS:
                break
            for index, row in enumerate(rows):
                if row["sector"] == sector and index not in chosen:
                    chosen.append(index)
                    added = True
                    break
        if not added:          # every row is already chosen
            break

    for index in chosen:
        rows[index]["priority"] = "core"


def build_rows(gold: Sequence[Dict], *, allow_network: bool = False) -> List[Dict]:
    catalog = CatalogStore(Path(settings.data_dir) / "derived" / "catalog.sqlite")
    store = FactsStore(Path(settings.data_dir) / "derived" / "facts.sqlite")
    resolver = FactsResolver()
    company_facts = CompanyFacts(offline=not allow_network)
    oracle_index: Dict[int, Optional[Dict]] = {}
    ciks = {f.accession: f.cik for f in catalog.all_filings()}

    def oracle_value(accession: str, concept: str, start: str, end: str, unit: str) -> str:
        cik = ciks.get(accession)
        if cik is None:
            return ""
        if cik not in oracle_index:
            payload = company_facts.load(cik)
            oracle_index[cik] = index_oracle(payload) if payload else None
        index = oracle_index[cik]
        if not index:
            return ""
        for unit_key in (unit, unit.replace("/", "-per-")):
            found = index.get((accession, concept, start or "", end or "", unit_key))
            if found is not None:
                return format(found, "f")
        return ""

    rows: List[Dict] = []
    for item in gold:
        if item["category"] not in ("numeric", "computed"):
            continue
        expected = item["expected"]
        ticker = expected["ticker"]
        base = {
            "gold_id": item["id"],
            "category": item["category"],
            "sector": SECTORS.get(ticker, ""),
            "ticker": ticker,
            "company": DISPLAY_NAMES.get(ticker, ticker),
            "question": item["question"],
            "expected_value": expected["value"],
            "unit": expected.get("unit", ""),
            "why_this_item": item.get("notes", ""),
            "verified_by": item["verified_by"],
        }

        if item["category"] == "numeric":
            accession = item["source"]["accession"]
            concept = item["source"]["concept"]
            scale_raw, source_doc = _scale_and_doc(
                store, accession, concept, expected["value"]
            )
            spec = resolver.registry.metric(expected["metric"])
            outcome = resolver.resolve(
                ticker=ticker, metric=expected["metric"],
                period=PeriodSelector.fiscal_label(expected["fiscal_label"]),
            )
            rows.append({
                **base,
                "fiscal_label": expected["fiscal_label"],
                "period_end": expected["period_end"],
                "metric_label": outcome.metric_label if outcome.resolved else "",
                "concept": concept,
                "operation": "",
                "operands": "",
                "filing_value_as_printed": printed_numeral(
                    Decimal(expected["value"]), scale_raw
                ),
                "scale_attr": scale_raw or "",
                "statement_to_check": STATEMENT_HINT.get(spec.statement, spec.statement),
                "accession": accession,
                "filing_url": _filing_url(catalog, accession, source_doc),
                "oracle_value": oracle_value(
                    accession, concept,
                    outcome.start_date if outcome.resolved else "",
                    expected["period_end"], expected.get("unit", ""),
                ),
                "verdict": "",
                "owner_note": "",
            })
            continue

        # computed: carry the operands so the arithmetic can be redone
        operands = item["source"]["operands"]
        operand_text = " ; ".join(
            f"{o['metric']} FY{o['fiscal_label']} = {o['value']}" for o in operands
        )
        accession = operands[0]["accession"]
        rows.append({
            **base,
            "fiscal_label": operands[-1]["fiscal_label"],
            "period_end": "",
            "metric_label": item["source"]["operation"],
            "concept": " ; ".join(sorted({o["concept"] for o in operands})),
            "operation": item["source"]["operation"],
            "operands": operand_text,
            "filing_value_as_printed": expected["value"],
            "scale_attr": "",
            "statement_to_check": "re-compute from the operands; each operand "
                                  "has its own numeric row in this sheet or in "
                                  "reports/phase2/owner_spotcheck.csv",
            "accession": accession,
            "filing_url": _filing_url(catalog, accession, ""),
            "oracle_value": "",
            "verdict": "",
            "owner_note": "",
        })

    rows.sort(key=lambda r: (r["sector"], r["category"], r["ticker"], r["gold_id"]))
    choose_core(rows)
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gold", type=Path, default=GOLD_PATH)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--allow-network", action="store_true")
    args = ap.parse_args(argv)

    if not args.gold.is_file():
        print(f"error: {args.gold} does not exist; run eval.gold.build_gold first",
              file=sys.stderr)
        return 2

    rows = build_rows(read_jsonl(args.gold), allow_network=args.allow_network)
    if not rows:
        print("error: no numeric or computed items in the gold set", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    core = [r for r in rows if r["priority"] == "core"]
    sectors: Dict[str, int] = {}
    categories: Dict[str, int] = {}
    for row in core:
        sectors[row["sector"]] = sectors.get(row["sector"], 0) + 1
        categories[row["category"]] = categories.get(row["category"], 0) + 1

    print(f"wrote {args.out.relative_to(REPO_ROOT)}")
    print(f"  {len(rows)} rows, {len(core)} marked core (spec minimum {CORE_ROWS})")
    print("  core by sector:   " + ", ".join(f"{k}={v}" for k, v in sorted(sectors.items())))
    print("  core by category: " + ", ".join(f"{k}={v}" for k, v in sorted(categories.items())))
    missing = [t for t in REQUIRED_TICKERS if t not in {r["ticker"] for r in core}]
    if missing:
        print(f"  WARNING: core is missing required filers {missing}", file=sys.stderr)
        return 1
    blank_oracle = [r["gold_id"] for r in rows
                    if r["category"] == "numeric" and not r["oracle_value"]]
    if blank_oracle:
        print(f"  NOTE {len(blank_oracle)} numeric rows have no oracle value printed: "
              f"{', '.join(blank_oracle)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
