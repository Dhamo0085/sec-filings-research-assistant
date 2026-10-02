"""facts/build.py — the build driver: idempotence, resume, failure isolation.

T2-08 lives here: running the build twice must yield identical DB content.
Everything runs offline against the committed fixtures with a fake fetcher, so
the test exercises the real extractor and the real store — only the network is
replaced.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catalog.store import CatalogStore, Filing
from facts.build import build, build_submission
from facts.errors import FactsError
from facts.store import FactsStore

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ixbrl"
MANIFEST = {e["name"]: e for e in json.loads(
    (FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))}


class FixtureFetcher:
    """Serves fixture files as if they were a filing's downloaded documents."""

    def __init__(self, mapping: dict, *, fail_for: set | None = None) -> None:
        self.mapping = mapping          # accession -> [paths]
        self.fail_for = fail_for or set()
        self.calls: list[str] = []

    def ensure(self, filing, **_kw):
        self.calls.append(filing.accession)
        if filing.accession in self.fail_for:
            raise FactsError(f"simulated fetch failure for {filing.accession}")
        return list(self.mapping.get(filing.accession, []))

    def stats(self):
        return {"requests_made": 0, "bytes_fetched": 0}


def filing(accession, ticker, period_end, label, *, form="10-K", filed=None):
    return Filing(accession=accession, ticker=ticker, cik=1,
                  entity_name=f"{ticker} Inc.", form_type=form,
                  period_end=period_end, fiscal_label=label,
                  fiscal_label_source="dei",
                  filing_date=filed or f"{label + 1}-02-01")


@pytest.fixture
def world(tmp_path):
    aapl = MANIFEST["aapl_fy2024"]
    nflx = MANIFEST["nflx_fy2024_thousands"]
    catalog = CatalogStore(tmp_path / "catalog.sqlite")
    catalog.upsert([
        filing("ACC-AAPL", "AAPL", "2024-09-28", 2024),
        filing("ACC-NFLX", "NFLX", "2024-12-31", 2024),
    ])
    fetcher = FixtureFetcher({
        "ACC-AAPL": [FIXTURES / f for f in aapl["files"]],
        "ACC-NFLX": [FIXTURES / f for f in nflx["files"]],
    })
    return catalog, FactsStore(tmp_path / "facts.sqlite"), fetcher, tmp_path


def run(world, **kw):
    catalog, store, fetcher, _tmp = world
    import facts.build as mod
    # The driver constructs its own fetcher; swap in the fixture one.
    original = mod.DocumentFetcher
    mod.DocumentFetcher = lambda **_k: fetcher
    try:
        return build(catalog_path=catalog.path, facts_path=store.path,
                     offline=True, **kw)
    finally:
        mod.DocumentFetcher = original


# ── a first build ──────────────────────────────────────────────────────────

def test_build_stores_facts_for_each_filing(world):
    from decimal import Decimal
    _catalog, store, _fetcher, _tmp = world
    result = run(world)
    assert result["counts"] == {"built": 2}
    stats = result["store_stats"]
    assert stats["accessions"] == 2
    assert stats["tickers"] == 2
    # Not a count. The fixtures are trimmed to a handful of concepts and D22
    # then collapses duplicate instances, so any threshold here is a number
    # that goes stale the next time either changes — as it did. What matters is
    # that the right values landed and that each concept landed exactly once.
    assert stats["facts"] > 0
    for ticker, concept, expected in (
        ("AAPL", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
         "391035000000"),
        ("NFLX", "us-gaap:Revenues", "39000966000"),
    ):
        rows = [f for f in store.query(ticker=ticker) if f.concept == concept]
        assert len(rows) == 1, (
            f"{ticker} {concept} stored {len(rows)} times; D22 should leave one")
        assert rows[0].value == Decimal(expected)


def test_build_marks_the_catalog(world):
    catalog, _store, _fetcher, _tmp = world
    run(world)
    assert catalog.get("ACC-AAPL").facts_built_at is not None


# ── T2-08: identical rebuilds ──────────────────────────────────────────────

def test_second_run_is_unchanged_and_the_hash_is_identical(world):
    _catalog, store, _fetcher, _tmp = world
    first = run(world)
    hash_after_first = store.content_hash()

    second = run(world)
    assert second["counts"] == {"unchanged": 2}, (
        "a filing whose bytes have not changed must be skipped")
    assert store.content_hash() == hash_after_first
    assert first["content_hash"] == second["content_hash"]


def test_forced_rebuild_reproduces_the_same_content(world):
    """The stronger form of T2-08: rebuild everything, get the same bytes."""
    _catalog, store, _fetcher, _tmp = world
    run(world)
    before = store.content_hash()
    rebuilt = run(world, rebuild=True)
    assert rebuilt["counts"] == {"built": 2}
    assert store.content_hash() == before


def test_changing_a_document_changes_the_hash_and_triggers_a_rebuild(world, tmp_path):
    """The negative control: idempotence must not be "it never rebuilds"."""
    catalog, store, fetcher, _tmp = world
    run(world)
    before = store.content_hash()

    # Point AAPL at a different real fixture; its bytes differ, so the source
    # hash differs, so the build must redo it.
    blk = MANIFEST["blk_fy2024_dual_revenue"]
    fetcher.mapping["ACC-AAPL"] = [FIXTURES / f for f in blk["files"]]
    catalog.upsert([filing("ACC-AAPL", "AAPL", "2024-12-31", 2024)])
    result = run(world)
    assert result["counts"].get("built") == 1
    assert store.content_hash() != before


# ── failure isolation ──────────────────────────────────────────────────────

def test_one_failing_filing_does_not_stop_the_others(world):
    _catalog, store, fetcher, _tmp = world
    fetcher.fail_for = {"ACC-AAPL"}
    result = run(world)
    assert result["counts"]["fetch_failed"] == 1
    assert result["counts"]["built"] == 1
    assert store.stats()["accessions"] == 1
    failed = [r for r in result["rows"] if r["status"] == "fetch_failed"]
    assert "simulated fetch failure" in failed[0]["error"]


def test_a_filing_with_no_documents_is_recorded_not_crashed(world):
    _catalog, _store, fetcher, _tmp = world
    fetcher.mapping["ACC-NFLX"] = []
    result = run(world)
    assert result["counts"]["no_documents"] == 1


def test_an_unknown_transform_fails_only_that_submission(world, tmp_path):
    """P2-02's fail-loud policy, at build scope.

    This is what happened for real to five filings during P2-03: the run has to
    report them individually rather than abort, or one odd filer costs the
    other sixty downloads.
    """
    _catalog, store, fetcher, _tmp = world
    weird = tmp_path / "weird.htm"
    weird.write_text(
        '<!DOCTYPE html><html><head>'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
        '</head><body><ix:header><ix:resources>'
        '<xbrli:context id="c1"><xbrli:period>'
        '<xbrli:startDate>2024-01-01</xbrli:startDate>'
        '<xbrli:endDate>2024-12-31</xbrli:endDate>'
        '</xbrli:period></xbrli:context></ix:resources></ix:header>'
        '<ix:nonFraction name="us-gaap:Revenues" contextRef="c1" unitRef="usd" '
        'format="ixt:num-klingon-decimal">1</ix:nonFraction></body></html>',
        encoding="utf-8")
    fetcher.mapping["ACC-NFLX"] = [weird]

    result = run(world)
    assert result["counts"]["extract_failed"] == 1
    assert result["counts"]["built"] == 1
    row = next(r for r in result["rows"] if r["status"] == "extract_failed")
    assert "num-klingon-decimal" in row["error"]
    assert store.stats()["accessions"] == 1


# ── scope ──────────────────────────────────────────────────────────────────

def test_filings_per_ticker_is_respected(world):
    catalog, _store, fetcher, _tmp = world
    nflx = MANIFEST["nflx_fy2024_thousands"]
    catalog.upsert([filing("ACC-NFLX-OLD", "NFLX", "2023-12-31", 2023)])
    fetcher.mapping["ACC-NFLX-OLD"] = [FIXTURES / f for f in nflx["files"]]

    result = run(world, filings_per_ticker=1)
    built = {r["accession"] for r in result["rows"]}
    assert "ACC-NFLX-OLD" not in built, "only the newest filing was asked for"
    assert "ACC-NFLX" in built


def test_ticker_filter_is_respected(world):
    result = run(world, tickers=["AAPL"])
    assert {r["ticker"] for r in result["rows"]} == {"AAPL"}


def test_amendments_for_built_periods_are_included(world):
    catalog, _store, fetcher, _tmp = world
    gs = MANIFEST["gs_fy2023_10ka"]
    catalog.upsert([
        filing("ACC-GS", "GS", "2023-12-31", 2023),
        filing("ACC-GS-A", "GS", "2023-12-31", 2023, form="10-K/A",
               filed="2024-02-28"),
    ])
    paths = [FIXTURES / f for f in gs["files"]]
    fetcher.mapping["ACC-GS"] = paths
    fetcher.mapping["ACC-GS-A"] = paths

    result = run(world)
    accessions = {r["accession"] for r in result["rows"]}
    assert {"ACC-GS", "ACC-GS-A"} <= accessions, (
        "D14 needs both sides of a restatement, even when the amendment "
        "restates nothing")


def test_no_amendments_flag_excludes_them(world):
    catalog, _store, fetcher, _tmp = world
    gs = MANIFEST["gs_fy2023_10ka"]
    catalog.upsert([
        filing("ACC-GS", "GS", "2023-12-31", 2023),
        filing("ACC-GS-A", "GS", "2023-12-31", 2023, form="10-K/A",
               filed="2024-02-28"),
    ])
    fetcher.mapping["ACC-GS"] = [FIXTURES / f for f in gs["files"]]
    fetcher.mapping["ACC-GS-A"] = [FIXTURES / f for f in gs["files"]]
    result = run(world, include_amendments=False)
    assert "ACC-GS-A" not in {r["accession"] for r in result["rows"]}


def test_build_submission_returns_a_row_rather_than_raising(world):
    """Every failure mode is a row, so one filing cannot abort a long run."""
    catalog, store, fetcher, _tmp = world
    fetcher.fail_for = {"ACC-AAPL"}
    row = build_submission(catalog.get("ACC-AAPL"), store=store,
                           fetcher=fetcher)
    assert row["status"] == "fetch_failed"
    assert row["facts_stored"] == 0
