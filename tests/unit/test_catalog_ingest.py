"""Catalog-driven, multi-CIK ingestion (T3-11, D24, P3-06).

The BlackRock rows below are the real ones from the built catalog
(``python -m catalog.build``, verified 2026-10-02): FY2024 and FY2025 under
BlackRock, Inc. (CIK 2012383) and FY2023 under BlackRock Finance, Inc.
(CIK 1364742, the pre-reorganisation registrant). Recorded here rather than
read from ``data/derived/`` so the test runs offline and cannot pass because
the owner's catalog happens to be in a particular state.
"""

from __future__ import annotations

import pytest

from catalog.store import CatalogStore, Filing
from ingestion.catalog_ingest import (
    ciks_covered,
    filings_to_ingest,
    link_collections,
)

pytestmark = pytest.mark.unit

BLK_ROWS = [
    {"accession": "0001193125-26-071966", "cik": 2012383,
     "entity_name": "BlackRock, Inc.", "form_type": "10-K",
     "period_end": "2025-12-31", "fiscal_label": 2025,
     "filing_date": "2026-02-25", "primary_doc": "blk-20251231.htm"},
    {"accession": "0000950170-25-026584", "cik": 2012383,
     "entity_name": "BlackRock, Inc.", "form_type": "10-K",
     "period_end": "2024-12-31", "fiscal_label": 2024,
     "filing_date": "2025-02-25", "primary_doc": "blk-20241231.htm"},
    {"accession": "0000950170-24-019271", "cik": 1364742,
     "entity_name": "BlackRock Finance, Inc.", "form_type": "10-K",
     "period_end": "2023-12-31", "fiscal_label": 2023,
     "filing_date": "2024-02-23", "primary_doc": "blk-20231231.htm"},
    {"accession": "0000950170-23-004343", "cik": 1364742,
     "entity_name": "BlackRock Finance, Inc.", "form_type": "10-K",
     "period_end": "2022-12-31", "fiscal_label": 2022,
     "filing_date": "2023-02-24", "primary_doc": "blk-20221231.htm"},
]

#: BlackRock's current CIK. The negative control restricts the catalog to it.
CURRENT_CIK = 2012383
OLD_CIK = 1364742


def blk_catalog(tmp_path, *, only_cik: int | None = None,
                collections: bool = False) -> CatalogStore:
    catalog = CatalogStore(tmp_path / "catalog.sqlite")
    rows = [r for r in BLK_ROWS if only_cik is None or r["cik"] == only_cik]
    catalog.upsert([
        Filing(ticker="BLK", fiscal_label_source="dei",
               collection_name=(f"BLK_{r['fiscal_label']}" if collections else None),
               **r)
        for r in rows
    ])
    return catalog


# ── T3-11: the dual-CIK case, and its negative control ──────────────────────

def test_the_filing_list_for_blk_includes_the_old_cik_year(tmp_path):
    """D24: BLK's FY2023 10-K is filed by a different registrant."""
    catalog = blk_catalog(tmp_path)
    filings = filings_to_ingest(catalog, "BLK", limit=3)

    labels = sorted(f.fiscal_label for f in filings)
    assert labels == [2023, 2024, 2025]
    assert ciks_covered(filings) == [OLD_CIK, CURRENT_CIK]

    fy2023 = next(f for f in filings if f.fiscal_label == 2023)
    assert fy2023.cik == OLD_CIK
    assert fy2023.accession == "0000950170-24-019271"
    assert fy2023.entity_name == "BlackRock Finance, Inc."


def test_negative_control_restricting_to_the_current_cik_drops_fy2023(tmp_path):
    """The failure this guards against, reproduced.

    Resolving the ticker through SEC's ticker file gives one CIK, and this is
    what that costs: the most recent three 10-Ks become two, with nothing
    reporting a gap.
    """
    catalog = blk_catalog(tmp_path, only_cik=CURRENT_CIK)
    filings = filings_to_ingest(catalog, "BLK", limit=3)

    labels = sorted(f.fiscal_label for f in filings)
    assert labels == [2024, 2025], "the negative control is not reproducing the bug"
    assert 2023 not in labels
    assert ciks_covered(filings) == [CURRENT_CIK]


def test_the_limit_counts_originals_and_takes_the_newest(tmp_path):
    catalog = blk_catalog(tmp_path)
    assert [f.fiscal_label for f in filings_to_ingest(catalog, "BLK", limit=2)] \
        == [2025, 2024]


def test_an_amendment_rides_along_rather_than_consuming_a_slot(tmp_path):
    """Dropping it would discard the restated figures D14 depends on."""
    catalog = blk_catalog(tmp_path)
    catalog.upsert([Filing(
        accession="0000950170-25-999999", ticker="BLK", cik=2012383,
        entity_name="BlackRock, Inc.", form_type="10-K/A",
        period_end="2024-12-31", fiscal_label=2024, fiscal_label_source="dei",
        filing_date="2025-06-01", amends="0000950170-25-026584",
    )])
    filings = filings_to_ingest(catalog, "BLK", limit=2)
    assert [f.fiscal_label for f in filings] == [2025, 2024, 2024]
    assert any(f.is_amendment for f in filings)

    without = filings_to_ingest(catalog, "BLK", limit=2, include_amendments=False)
    assert not any(f.is_amendment for f in without)


def test_as_of_filters_the_ingestion_list_too(tmp_path):
    """A point-in-time rebuild must not fetch filings that were not public."""
    catalog = blk_catalog(tmp_path)
    filings = filings_to_ingest(catalog, "BLK", limit=5, as_of="2025-01-01")
    assert [f.fiscal_label for f in filings] == [2023, 2022]


def test_an_unknown_ticker_yields_nothing_rather_than_raising(tmp_path):
    assert filings_to_ingest(blk_catalog(tmp_path), "ZZZZ", limit=3) == []


# ── linking the index to the catalog ─────────────────────────────────────────

def test_linking_records_the_collection_on_each_filing(tmp_path):
    """The join that was missing: all 483 rows in the real catalog had a null
    collection_name, so the text path's as_of scope found nothing."""
    catalog = blk_catalog(tmp_path)
    assert all(f.collection_name is None for f in catalog.for_ticker("BLK"))

    report = link_collections(catalog, ["BLK_2024", "BLK_2025"])

    linked = {f.fiscal_label: f.collection_name for f in catalog.for_ticker("BLK")}
    assert linked[2024] == "BLK_2024" and linked[2025] == "BLK_2025"
    assert linked[2023] is None
    assert len(report.linked) == 2
    assert "BLK FY2023" in report.unindexed_filings


def test_linking_is_idempotent(tmp_path):
    catalog = blk_catalog(tmp_path)
    link_collections(catalog, ["BLK_2024"])
    second = link_collections(catalog, ["BLK_2024"])
    assert second.linked == []
    assert second.already == ["BLK_2024"]


def test_a_dry_run_changes_nothing(tmp_path):
    catalog = blk_catalog(tmp_path)
    report = link_collections(catalog, ["BLK_2024"], dry_run=True)
    assert report.linked == ["BLK_2024 -> 0000950170-25-026584"]
    assert all(f.collection_name is None for f in catalog.for_ticker("BLK"))


def test_a_collection_with_no_catalog_filing_is_reported_not_ignored(tmp_path):
    """A question about that year would quietly find nothing otherwise."""
    catalog = blk_catalog(tmp_path)
    report = link_collections(catalog, ["BLK_2024", "TSLA_2025", "not-a-collection"])
    assert "TSLA_2025" in report.orphan_collections
    assert "not-a-collection" in report.orphan_collections
    assert report.ok is False


def test_an_amendment_is_not_given_the_originals_collection(tmp_path):
    """Both pointing at one collection would make a citation claim the
    amendment's text was searched when only the original was indexed."""
    catalog = blk_catalog(tmp_path)
    catalog.upsert([Filing(
        accession="0000950170-25-999999", ticker="BLK", cik=2012383,
        entity_name="BlackRock, Inc.", form_type="10-K/A",
        period_end="2024-12-31", fiscal_label=2024, fiscal_label_source="dei",
        filing_date="2025-06-01", amends="0000950170-25-026584",
    )])
    link_collections(catalog, ["BLK_2024"])
    amendment = next(f for f in catalog.for_ticker("BLK") if f.is_amendment)
    assert amendment.collection_name is None


def test_linking_then_scoping_gives_the_text_path_something_to_search(tmp_path):
    """The two halves joined: link, then ask for the as_of-eligible scope."""
    from answering.text_answer import eligible_collections

    catalog = blk_catalog(tmp_path)
    link_collections(catalog, ["BLK_2023", "BLK_2024", "BLK_2025"])

    names, lookup = eligible_collections(catalog, ["BLK"], as_of="2025-01-01")
    assert names == ["BLK_2023"], (
        "only the FY2023 10-K (filed 2024-02-23) was public on 2025-01-01"
    )
    assert lookup["BLK_2023"]["cik"] == OLD_CIK, (
        "and the citation for it carries the old registrant's CIK (D24)"
    )


# ── the CLI and the facts hand-off ───────────────────────────────────────────

def test_the_cli_reports_the_filings_and_the_ciks(tmp_path, capsys, monkeypatch):
    """`--ticker BLK` is how the owner checks the D24 case by hand."""
    import ingestion.catalog_ingest as ci

    catalog = blk_catalog(tmp_path, collections=True)
    monkeypatch.setattr(ci, "CatalogStore", lambda _p: catalog, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "catalog.store",
                        __import__("catalog.store", fromlist=["CatalogStore"]))
    import catalog.store as cs
    monkeypatch.setattr(cs, "CatalogStore", lambda _p: catalog)

    assert ci.main(["--ticker", "BLK", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "BLK: 3 filing(s) across CIK(s) [1364742, 2012383]" in out
    assert "FY2023 10-K    0000950170-24-019271 cik=1364742" in out


def test_the_cli_link_mode_reports_and_exits_zero_when_clean(tmp_path, capsys, monkeypatch):
    import catalog.store as cs
    import ingestion.catalog_ingest as ci
    import retrieval.vector_store as vs

    catalog = blk_catalog(tmp_path)
    monkeypatch.setattr(cs, "CatalogStore", lambda _p: catalog)
    monkeypatch.setattr(vs, "list_collections", lambda: ["BLK_2024", "BLK_2025"])

    assert ci.main(["--link"]) == 0
    out = capsys.readouterr().out
    assert "linked 2" in out and "orphan collections 0" in out


def test_the_cli_link_mode_exits_non_zero_on_an_orphan(tmp_path, capsys, monkeypatch):
    """A collection no filing claims means a question about that year quietly
    finds nothing, so it has to fail a scripted run."""
    import catalog.store as cs
    import ingestion.catalog_ingest as ci
    import retrieval.vector_store as vs

    catalog = blk_catalog(tmp_path)
    monkeypatch.setattr(cs, "CatalogStore", lambda _p: catalog)
    monkeypatch.setattr(vs, "list_collections", lambda: ["BLK_2024", "ZZZZ_2024"])

    assert ci.main(["--link"]) == 1
    assert "orphan collection with no catalog filing: ZZZZ_2024" in capsys.readouterr().out


def test_a_facts_build_failure_is_reported_not_swallowed(monkeypatch):
    """P3-06: "on facts failure -> text path only, flagged". The flag is the
    return value; a partial build that looks complete is what must not happen."""
    import facts.build as fb
    import ingestion.catalog_ingest as ci

    def boom(**_kwargs):
        raise RuntimeError("facts.sqlite is locked")

    monkeypatch.setattr(fb, "build", boom)
    result = ci.facts_for_ticker("BLK")
    assert result["ok"] is False
    assert "facts.sqlite is locked" in result["error"]
    assert result["ticker"] == "BLK"


def test_a_facts_build_success_reports_its_counts(monkeypatch):
    import facts.build as fb
    import ingestion.catalog_ingest as ci

    monkeypatch.setattr(fb, "build", lambda **_k: {"counts": {"built": 3},
                                                   "facts_db": "derived/facts.sqlite"})
    result = ci.facts_for_ticker("BLK")
    assert result == {"ok": True, "counts": {"built": 3},
                      "facts_path": "derived/facts.sqlite"}
