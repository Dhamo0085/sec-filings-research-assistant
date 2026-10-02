#!/usr/bin/env python3
"""Verify fiscal labels against the filer's own DEI facts (P2-03).

    python scripts/verify_dei_labels.py                     # newest 5 per ticker
    python scripts/verify_dei_labels.py --filings 3 --dry-run
    python scripts/verify_dei_labels.py --offline            # cache only

D6 says the fiscal label is **the company's own**, not one this project infers.
P1-09 had no choice but to seed every catalog row from ``period_end.year``,
because nothing read the cover page yet. This closes that: for each filing it
reads ``dei:DocumentFiscalYearFocus``, compares it with the inferred label,
reports every mismatch, and writes the declared value back to the catalog with
``fiscal_label_source='dei'``.

A mismatch is not an error. A filer whose year ends in June or September can
legitimately label it differently from its end date's calendar year — Microsoft
FY2026 ends 2026-06-30 and Apple FY2024 ends 2024-09-28 — and the whole point
of D6 is that their label is the answer to "fiscal 2024", not ours.

While each submission is open it also records ``exhibit_docs``, which spec 6.1
defines and P1-09 left empty. That is what makes Wells Fargo resolvable at all.

Network: one FilingSummary.xml plus the iXBRL documents per filing, at most 5
requests/second with a contact User-Agent, every response cached. Re-running
makes no requests.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loguru import logger  # noqa: E402

from catalog.store import CatalogStore  # noqa: E402
from config import settings  # noqa: E402
from facts.documents import DocumentFetcher  # noqa: E402
from facts.errors import FactsError  # noqa: E402
from facts.extract import parse_submission  # noqa: E402


def check_filing(filing, fetcher: DocumentFetcher) -> Dict:
    """Read one filing's DEI block. Returns a row, never raises."""
    row: Dict = {
        "accession": filing.accession,
        "ticker": filing.ticker,
        "cik": filing.cik,
        "form_type": filing.form_type,
        "period_end": filing.period_end,
        "filing_date": filing.filing_date,
        "label_inferred": filing.fiscal_label,
        "label_dei": None,
        "status": "unknown",
        "documents": [],
        "dei": {},
        "error": None,
    }
    try:
        paths = fetcher.ensure(filing)
    except FactsError as exc:
        row["status"] = "fetch_failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    if not paths:
        row["status"] = "no_documents"
        return row

    row["documents"] = [p.name for p in paths]
    try:
        submission = parse_submission(paths, accession=filing.accession)
    except FactsError as exc:
        # An unknown numeric transform is fatal by design (P2-02). It is
        # recorded per filing rather than aborting the sweep, so one odd filer
        # does not cost the other 64 filings' worth of downloads.
        row["status"] = "extract_failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    dei = submission.dei()
    row["dei"] = dei
    focus = dei.get("DocumentFiscalYearFocus")
    if focus is None:
        row["status"] = "no_dei_label"
        return row
    try:
        row["label_dei"] = int(str(focus).strip())
    except ValueError:
        row["status"] = "unparsable_dei_label"
        row["error"] = f"DocumentFiscalYearFocus={focus!r}"
        return row

    row["status"] = ("match" if row["label_dei"] == row["label_inferred"]
                     else "mismatch")
    return row


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--filings", type=int, default=None,
                    help="newest N filings per ticker "
                         "(default: FACTS_FILINGS_PER_COMPANY)")
    ap.add_argument("--ticker", action="append", dest="tickers")
    ap.add_argument("--offline", action="store_true",
                    help="use only cached documents; make no requests")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; do not write to the catalog")
    ap.add_argument("--include-amendments", action="store_true",
                    help="also check 10-K/A rows (off by default: an amendment "
                         "shares its original's label)")
    ap.add_argument("--out", default="reports/phase2/dei_labels.json")
    args = ap.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level="WARNING",
               format="<level>{level}</level> | {message}")

    per_ticker = args.filings or settings.facts_filings_per_company
    store = CatalogStore(Path(settings.catalog_path))
    fetcher = DocumentFetcher(offline=args.offline)

    tickers = [t.upper() for t in (args.tickers or store.tickers())]
    print(f"catalog   : {store.path}")
    print(f"tickers   : {len(tickers)} ({', '.join(tickers)})")
    print(f"per ticker: newest {per_ticker} filing(s)"
          + ("  [offline]" if args.offline else "")
          + ("  [dry run]" if args.dry_run else "") + "\n")

    rows: List[Dict] = []
    print(f"{'TICKER':7s} {'FORM':8s} {'PERIOD END':11s} {'INFERRED':>8s} "
          f"{'DEI':>5s}  STATUS")
    for ticker in tickers:
        filings = store.for_ticker(ticker)
        if not args.include_amendments:
            filings = [f for f in filings if not f.is_amendment]
        for filing in filings[:per_ticker]:
            row = check_filing(filing, fetcher)
            rows.append(row)
            dei = row["label_dei"]
            print(f"{row['ticker']:7s} {row['form_type']:8s} "
                  f"{row['period_end']:11s} {row['label_inferred']:8d} "
                  f"{(str(dei) if dei else '-'):>5s}  {row['status']}"
                  + (f"  {row['error'][:60]}" if row["error"] else ""))

            if args.dry_run:
                continue
            if row["status"] in ("match", "mismatch") and dei is not None:
                store.set_fiscal_label(filing.accession, dei, "dei")
            if len(row["documents"]) > 1:
                store.set_exhibit_docs(filing.accession, row["documents"][1:])

    by_status: Dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    mismatches = [r for r in rows if r["status"] == "mismatch"]
    multi_doc = [r for r in rows if len(r["documents"]) > 1]

    print("\nsummary")
    for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"  {status:22s} {count}")
    print(f"  {'multi-document filings':22s} {len(multi_doc)}")
    print(f"  {'EDGAR requests':22s} {fetcher.requests_made}")
    print(f"  {'bytes fetched':22s} {fetcher.bytes_fetched / 1e6:.1f} MB")

    if mismatches:
        print(f"\n{len(mismatches)} label mismatch(es) — the filer's own label wins (D6):")
        for row in mismatches:
            print(f"  {row['ticker']:6s} period_end {row['period_end']} -> "
                  f"inferred FY{row['label_inferred']}, declared "
                  f"FY{row['label_dei']}")

    payload = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "filings_per_ticker": per_ticker,
        "offline": args.offline,
        "dry_run": args.dry_run,
        "catalog_written": not args.dry_run,
        "counts": by_status,
        "mismatches": [
            {k: r[k] for k in ("ticker", "accession", "period_end",
                               "label_inferred", "label_dei")}
            for r in mismatches
        ],
        "multi_document_filings": [
            {"ticker": r["ticker"], "accession": r["accession"],
             "documents": r["documents"]}
            for r in multi_doc
        ],
        "rows": rows,
        "edgar": fetcher.stats(),
    }
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")

    failed = sum(by_status.get(k, 0) for k in
                 ("fetch_failed", "extract_failed", "no_documents",
                  "no_dei_label", "unparsable_dei_label"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
