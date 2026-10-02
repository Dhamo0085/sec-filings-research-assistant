"""A real catalog + facts store built from the committed iXBRL fixtures.

Shared by the Phase 2 resolver tests and the Phase 3 end-to-end test (T3-08),
so the two cannot drift apart about what the fixture corpus contains. Nothing
here touches the network or reads ``data/``: each call parses
``tests/fixtures/ixbrl/`` into fresh SQLite files under the caller's
``tmp_path``, so a test cannot pass because of something a previous run left
behind.

The ``filing_date`` values are the real ones from the catalog. That matters:
the ``as_of`` tests are only meaningful if the dates they filter on are the
dates the SEC actually recorded.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import NamedTuple

from catalog.store import CatalogStore, Filing
from facts.concepts import load_registry
from facts.extract import annual_facts, parse_submission
from facts.resolve import FactsResolver
from facts.store import FactsStore, source_hash

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ixbrl"
MANIFEST = {e["name"]: e for e in json.loads(
    (FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))}

#: A fixed "today", so "is fiscal 2030 in the future" cannot change its answer
#: with the calendar — the same injectable-clock rule as P3-02.
TODAY = date(2026, 10, 2)

FIXTURE_FILINGS = {
    "aapl_fy2024": {"ticker": "AAPL", "cik": 320193, "entity_name": "Apple Inc.",
                    "accession": "0000320193-24-000123", "form_type": "10-K",
                    "period_end": "2024-09-28", "fiscal_label": 2024,
                    "filing_date": "2024-11-01"},
    "nflx_fy2024_thousands": {"ticker": "NFLX", "cik": 1065280,
                              "entity_name": "Netflix, Inc.",
                              "accession": "0001065280-25-000044",
                              "form_type": "10-K", "period_end": "2024-12-31",
                              "fiscal_label": 2024, "filing_date": "2025-01-27"},
    "blk_fy2024_dual_revenue": {"ticker": "BLK", "cik": 2012383,
                                "entity_name": "BlackRock, Inc.",
                                "accession": "0000950170-25-026584",
                                "form_type": "10-K", "period_end": "2024-12-31",
                                "fiscal_label": 2024, "filing_date": "2025-02-25"},
    "bac_fy2024": {"ticker": "BAC", "cik": 70858,
                   "entity_name": "Bank of America Corporation",
                   "accession": "0000070858-25-000139", "form_type": "10-K",
                   "period_end": "2024-12-31", "fiscal_label": 2024,
                   "filing_date": "2025-02-25"},
    "wfc_fy2024_split": {"ticker": "WFC", "cik": 72971,
                         "entity_name": "WELLS FARGO & COMPANY/MN",
                         "accession": "0000072971-25-000066", "form_type": "10-K",
                         "period_end": "2024-12-31", "fiscal_label": 2024,
                         "filing_date": "2025-02-21"},
    "gs_fy2023_10ka": {"ticker": "GS", "cik": 886982,
                       "entity_name": "The Goldman Sachs Group, Inc.",
                       "accession": "0000886982-24-000012", "form_type": "10-K/A",
                       "period_end": "2023-12-31", "fiscal_label": 2023,
                       "filing_date": "2024-02-28",
                       "amends": "0000886982-24-000006"},
}


class World(NamedTuple):
    catalog: CatalogStore
    store: FactsStore
    resolver: FactsResolver


def build_world(tmp_path: Path, *, collections: bool = False) -> World:
    """Parse the fixtures into a catalog, a facts store and a resolver.

    ``collections=True`` also fills each filing's ``collection_name``, which
    the text path needs to pick an ``as_of``-eligible search scope (D2). It is
    off by default because the facts tests must not depend on a text index
    existing.
    """
    catalog = CatalogStore(tmp_path / "catalog.sqlite")
    store = FactsStore(tmp_path / "facts.sqlite")

    filings = []
    for name, spec in FIXTURE_FILINGS.items():
        entry = MANIFEST[name]
        paths = [FIXTURES / f for f in entry["files"]]
        submission = parse_submission(paths, accession=spec["accession"])
        facts = annual_facts(submission, date.fromisoformat(spec["period_end"]))
        collection = (f"{spec['ticker']}_{spec['fiscal_label']}" if collections
                      else None)
        filings.append(Filing(fiscal_label_source="dei",
                              primary_doc=entry["files"][0],
                              collection_name=collection, **spec))
        store.replace_submission(
            accession=spec["accession"], ticker=spec["ticker"], facts=facts,
            source_hash_value=source_hash(paths), documents=entry["files"],
            cik=spec["cik"], form_type=spec["form_type"],
            period_end=spec["period_end"], fiscal_label=spec["fiscal_label"],
        )
    catalog.upsert(filings)

    resolver = FactsResolver(catalog=catalog, store=store,
                             registry=load_registry(), statements=None,
                             today=TODAY)
    return World(catalog, store, resolver)
