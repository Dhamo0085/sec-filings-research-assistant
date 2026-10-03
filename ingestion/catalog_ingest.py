"""Catalog-driven ingestion: every CIK a ticker has filed under (D24, P3-06).

    python -m ingestion.catalog_ingest --link              # join index to catalog
    python -m ingestion.catalog_ingest --ticker BLK --years 2023

Two problems this module exists for, both measured.

**A ticker is not a filer (D24).** ``sec_edgar_downloader``'s ``get("10-K",
"BLK")`` resolves the ticker through SEC's ticker file, which maps it to
*one* CIK. BlackRock reorganised in 2024: its FY2024 and FY2025 10-Ks are
filed by BlackRock, Inc. (CIK 2012383) and its FY2023 10-K by BlackRock
Finance, Inc. (CIK 1364742). Asking for "the last three BLK 10-Ks" by ticker
therefore returns two, silently. The catalog already merges both CIKs
(``catalog/cik_overrides.yaml``), so the filing list comes from there and the
download is by accession.

**Nothing linked the index to the catalog.** ``catalog.filings.collection_name``
existed from P1-09 and was written by nothing: all 483 catalog rows had it
null, so ``answering/text_answer.eligible_collections`` — which is how
``as_of`` scopes the text path (D2) — would have found no collections for any
filer on the real data. ``link_collections`` performs that join, and
``--link`` is now part of ingesting anything.

The join is by ``(ticker, fiscal_label)`` because that is what v1's collection
names encode (``AAPL_2024``). Any collection that matches no catalog filing,
and any filing whose collection does not exist, is **reported** rather than
silently skipped: a mismatch there means a question about that year will
quietly find nothing, which is the kind of gap that only shows up as a bad
evaluation number months later.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence

from loguru import logger

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: v1 names a collection ``{TICKER}_{fiscal_year}`` (retrieval/vector_store.py).
COLLECTION_RE = re.compile(r"^(?P<ticker>[A-Z][A-Z0-9.\-]*)_(?P<year>\d{4})$")


class LinkReport(NamedTuple):
    """What the catalog/index join found."""

    linked: List[str]                 # collection names now recorded on a filing
    already: List[str]                # already correct, left alone
    orphan_collections: List[str]     # indexed, but no catalog filing matches
    unindexed_filings: List[str]      # catalog filing with no collection
    cleared: List[str]                # link dropped: the store no longer holds it

    @property
    def ok(self) -> bool:
        return not self.orphan_collections


def filings_to_ingest(
    catalog,
    ticker: str,
    *,
    limit: Optional[int] = None,
    as_of: Optional[str] = None,
    include_amendments: bool = True,
) -> List:
    """The filings to ingest for ``ticker``, newest first, across every CIK.

    This is the D24 fix in one function: the catalog is keyed by ticker and
    already holds rows for every CIK the ticker has filed under, so asking it
    cannot lose a year the way asking SEC's ticker file can.

    ``limit`` counts *original* 10-Ks. An amendment is carried along with the
    original it amends rather than consuming a slot, because dropping the
    amendment would silently discard the restated figures D14 depends on.
    """
    filings = catalog.for_ticker(ticker, as_of=as_of)
    originals = [f for f in filings if not f.is_amendment]
    chosen = originals[:limit] if limit else originals

    if include_amendments:
        wanted_labels = {f.fiscal_label for f in chosen}
        chosen = chosen + [
            f for f in filings
            if f.is_amendment and f.fiscal_label in wanted_labels
        ]
    chosen.sort(key=lambda f: (f.period_end, f.filing_date), reverse=True)
    return chosen


def ciks_covered(filings: Iterable) -> List[int]:
    """Every distinct CIK in a filing list, for reporting the D24 case."""
    return sorted({int(f.cik) for f in filings})


def link_collections(
    catalog,
    collection_names: Sequence[str],
    *,
    dry_run: bool = False,
) -> LinkReport:
    """Record on each catalog filing which Qdrant collection holds its text.

    Only *original* filings are linked. An amendment has no collection of its
    own — v1 indexes one collection per (ticker, fiscal year) built from the
    original document — and pointing both at the same collection would make a
    citation claim the amendment's text was searched when it was not.

    A link the store can no longer honour is **cleared**, not left in place.
    P4-12 re-indexed 39 filings into a new store and deliberately left TSLA
    out, so the catalog's TSLA FY2025 row survived the swap pointing at a
    collection the live store does not hold. ``collection_name`` is what
    ``query._years_with`` reads to tell the user which years have text, so a
    stale one makes a refusal name a year that nothing can search. Retrieval
    itself is unharmed — it intersects its target list with the live
    collections first — which is exactly why this was quiet enough to need a
    test.

    ``collection_names`` is therefore read as the whole truth about the store,
    not as a patch to apply to it. Pass every live collection, never a subset.
    """
    by_key: Dict[tuple, str] = {}
    orphans: List[str] = []
    for name in collection_names:
        match = COLLECTION_RE.match(name)
        if not match:
            orphans.append(name)
            continue
        by_key[(match.group("ticker"), int(match.group("year")))] = name

    live = set(by_key.values())

    linked: List[str] = []
    already: List[str] = []
    unindexed: List[str] = []
    cleared: List[str] = []
    matched_keys: set = set()

    for ticker in catalog.tickers():
        for filing in catalog.for_ticker(ticker):
            key = (filing.ticker, filing.fiscal_label)
            name = by_key.get(key)
            if name is None or filing.is_amendment:
                if not filing.is_amendment:
                    unindexed.append(f"{filing.ticker} FY{filing.fiscal_label}")
                stale = filing.collection_name
                if stale and stale not in live:
                    if not dry_run:
                        catalog.set_collection_name(filing.accession, None)
                    cleared.append(stale)
                continue
            matched_keys.add(key)
            if filing.collection_name == name:
                already.append(name)
                continue
            if not dry_run:
                catalog.set_collection_name(filing.accession, name)
            linked.append(f"{name} -> {filing.accession}")

    orphans.extend(f"{t}_{y}" for (t, y) in sorted(set(by_key) - matched_keys))
    return LinkReport(linked=sorted(linked), already=sorted(set(already)),
                      orphan_collections=sorted(orphans),
                      unindexed_filings=sorted(unindexed),
                      cleared=sorted(set(cleared)))


def facts_for_ticker(ticker: str, *, filings_per_ticker: int = 3,
                     offline: bool = False) -> dict:
    """Build facts for a newly ingested filer; report failure, never hide it.

    P3-06: "on facts failure -> text path only, flagged". The flag is the
    return value. The caller records it and the facts path then simply finds
    no fact for that filer, which the resolver already reports as
    ``metric_not_found_in_filing`` — an honest refusal. What must not happen
    is a partial build that looks complete, so a failure here is logged and
    returned rather than swallowed.
    """
    from facts.build import build

    try:
        result = build(tickers=[ticker], filings_per_ticker=filings_per_ticker,
                       offline=offline)
        return {"ok": True, "counts": result.get("counts", {}),
                "facts_path": result.get("facts_db")}
    except Exception as exc:                 # reported, not swallowed
        logger.error(f"facts build failed for {ticker}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "ticker": ticker}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--link", action="store_true",
                        help="join the Qdrant collections to the catalog")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ticker", default="",
                        help="report the filings that would be ingested")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)

    from catalog.store import CatalogStore
    from config import settings

    catalog = CatalogStore(settings.catalog_path)

    if args.ticker:
        filings = filings_to_ingest(catalog, args.ticker, limit=args.limit)
        print(f"{args.ticker}: {len(filings)} filing(s) across "
              f"CIK(s) {ciks_covered(filings)}")
        for f in filings:
            print(f"  FY{f.fiscal_label} {f.form_type:7s} {f.accession} "
                  f"cik={f.cik} filed {f.filing_date} "
                  f"collection={f.collection_name or '-'}")

    if args.link:
        from retrieval.vector_store import list_collections

        report = link_collections(catalog, list(list_collections()),
                                  dry_run=args.dry_run)
        print(f"\nlinked {len(report.linked)}, already correct "
              f"{len(report.already)}, cleared {len(report.cleared)}, "
              f"orphan collections "
              f"{len(report.orphan_collections)}, unindexed filings "
              f"{len(report.unindexed_filings)}")
        for line in report.linked:
            print(f"  + {line}")
        for name in report.cleared:
            print(f"  - cleared a link to {name}: no such collection in the store")
        for name in report.orphan_collections:
            print(f"  ! orphan collection with no catalog filing: {name}")
        return 0 if report.ok else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
