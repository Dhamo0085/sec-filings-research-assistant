"""Build the facts store from the catalog (P2-04). Drives ``make facts``.

    python -m facts.build                      # newest N filings per ticker
    python -m facts.build --ticker AAPL --filings 3
    python -m facts.build --offline            # cached documents only
    python -m facts.build --rebuild            # ignore the source hashes

Idempotent per accession: a filing whose document bytes have not changed is
skipped, so a second run does nothing and ``content_hash()`` is unchanged
(T2-08). That is also what makes the build resumable — the same property the
indexer needed for the same reason, a long job on a machine that may be
interrupted.

One filing's failure never stops the build. An unknown ``@format`` is fatal
*for that submission* by design (P2-02), and the five filings that hit it
during P2-03 are exactly why: the run has to report them individually rather
than abort after 60 downloads.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from loguru import logger

from catalog.store import CatalogStore, Filing
from config import settings
from facts.documents import DocumentFetcher
from facts.errors import FactsError
from facts.extract import annual_facts, parse_submission
from facts.store import FactsStore, source_hash


def build_submission(
    filing: Filing,
    *,
    store: FactsStore,
    fetcher: DocumentFetcher,
    rebuild: bool = False,
) -> Dict:
    """Extract and store one filing's facts. Returns a result row, never raises."""
    row: Dict = {
        "accession": filing.accession,
        "ticker": filing.ticker,
        "form_type": filing.form_type,
        "period_end": filing.period_end,
        "fiscal_label": filing.fiscal_label,
        "documents": [],
        "facts_stored": 0,
        "facts_total": 0,
        "status": "unknown",
        "error": None,
    }
    try:
        paths = fetcher.ensure(filing)
    except FactsError as exc:
        row.update(status="fetch_failed", error=f"{type(exc).__name__}: {exc}")
        return row
    if not paths:
        row["status"] = "no_documents"
        return row

    row["documents"] = [p.name for p in paths]
    current_hash = source_hash(paths)
    if not rebuild and not store.needs_build(filing.accession, current_hash):
        row["status"] = "unchanged"
        built = {s["accession"]: s for s in store.built_submissions(filing.ticker)}
        row["facts_stored"] = built.get(filing.accession, {}).get("fact_count", 0)
        return row

    try:
        submission = parse_submission(paths, accession=filing.accession)
    except FactsError as exc:
        row.update(status="extract_failed", error=f"{type(exc).__name__}: {exc}")
        return row

    period_end = datetime.fromisoformat(filing.period_end).date()
    facts = annual_facts(submission, period_end)
    row["facts_total"] = len(submission.facts)
    row["facts_stored"] = store.replace_submission(
        accession=filing.accession,
        ticker=filing.ticker,
        facts=facts,
        source_hash_value=current_hash,
        documents=row["documents"],
        cik=filing.cik,
        form_type=filing.form_type,
        period_end=filing.period_end,
        fiscal_label=filing.fiscal_label,
    )
    row["status"] = "built"
    return row


def build(
    *,
    tickers: Optional[Sequence[str]] = None,
    filings_per_ticker: Optional[int] = None,
    offline: bool = False,
    rebuild: bool = False,
    include_amendments: bool = True,
    catalog_path: Optional[Path] = None,
    facts_path: Optional[Path] = None,
) -> Dict:
    catalog = CatalogStore(Path(catalog_path or settings.catalog_path))
    store = FactsStore(Path(facts_path or settings.facts_db_path))
    fetcher = DocumentFetcher(offline=offline)
    per_ticker = filings_per_ticker or settings.facts_filings_per_company

    wanted = [t.upper() for t in (tickers or catalog.tickers())]
    rows: List[Dict] = []
    for ticker in wanted:
        filings = catalog.for_ticker(ticker)
        originals = [f for f in filings if not f.is_amendment][:per_ticker]
        chosen = list(originals)
        if include_amendments:
            # Amendments for the periods we are building, so D14 has both
            # sides. An amendment that restates nothing (the GS FY2023 10-K/A
            # has zero financial facts) still needs a row, or the resolver
            # cannot tell "no restatement" from "never looked".
            periods = {f.period_end for f in originals}
            chosen += [f for f in filings
                       if f.is_amendment and f.period_end in periods]
        for filing in chosen:
            row = build_submission(filing, store=store, fetcher=fetcher,
                                   rebuild=rebuild)
            rows.append(row)
            if row["status"] == "built":
                catalog.mark_facts_built(filing.accession)
            logger.info(
                f"facts: {row['ticker']:6s} {row['form_type']:8s} "
                f"{row['period_end']} -> {row['status']} "
                f"({row['facts_stored']} fact(s) of {row['facts_total']})"
                + (f"  {row['error']}" if row["error"] else "")
            )

    by_status: Dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "filings_per_ticker": per_ticker,
        "offline": offline,
        "rebuild": rebuild,
        "counts": by_status,
        "facts_db": str(store.path),
        "store_stats": store.stats(),
        "content_hash": store.content_hash(),
        "edgar": fetcher.stats(),
        "rows": rows,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ticker", action="append", dest="tickers")
    ap.add_argument("--filings", type=int, default=None,
                    help="newest N originals per ticker "
                         "(default: FACTS_FILINGS_PER_COMPANY)")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--rebuild", action="store_true",
                    help="rebuild even when the source hash is unchanged")
    ap.add_argument("--no-amendments", action="store_true")
    ap.add_argument("--report", default="reports/phase2/facts_build.json")
    args = ap.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")

    result = build(
        tickers=args.tickers,
        filings_per_ticker=args.filings,
        offline=args.offline,
        rebuild=args.rebuild,
        include_amendments=not args.no_amendments,
    )

    print("\nsummary")
    for status, count in sorted(result["counts"].items(), key=lambda kv: -kv[1]):
        print(f"  {status:18s} {count}")
    for key, value in result["store_stats"].items():
        print(f"  {key:18s} {value}")
    print(f"  {'content hash':18s} {result['content_hash'][:16]}…")
    print(f"  {'EDGAR requests':18s} {result['edgar']['requests_made']}")

    failures = [r for r in result["rows"]
                if r["status"] in ("fetch_failed", "extract_failed", "no_documents")]
    if failures:
        print(f"\n{len(failures)} filing(s) did not build:")
        for row in failures:
            print(f"  {row['ticker']:6s} {row['period_end']} {row['status']}"
                  f"  {row['error'] or ''}")

    if args.report:
        out = Path(__file__).resolve().parents[1] / args.report
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
