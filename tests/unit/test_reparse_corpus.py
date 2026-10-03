"""Source resolution for the offline re-parse tool (P3-00).

The one piece of real logic in ``scripts/reparse_corpus.py`` is finding a
filing's primary document today: the path recorded at parse time, or the SEC
response cache when that path is gone. Both cases are real in this corpus —
Netflix arrived through on-demand ingestion and lives only in ``.cache``, and
one parsed document points into a checkout that no longer exists.
"""

from __future__ import annotations

import json

import pytest

from scripts import reparse_corpus as rc

pytestmark = pytest.mark.unit


def test_the_recorded_path_is_used_when_it_still_exists(tmp_path):
    src = tmp_path / "primary-document.html"
    src.write_text("<html></html>", encoding="utf-8")
    doc = {"source_path": str(src), "ticker": "AAPL",
           "accession_number": "0000320193-24-000123"}
    assert rc.resolve_source(doc) == src


def test_the_sec_cache_is_the_fallback(tmp_path, monkeypatch):
    """`run_ingestion.py --skip-download` parses whatever the download
    manifest lists, which does not cover an on-demand ingest."""
    cache = tmp_path / "filings" / "NFLX_0001065280-25-000044"
    cache.mkdir(parents=True)
    (cache / "FilingSummary.xml").write_text("<x/>", encoding="utf-8")
    primary = cache / "nflx-20241231.htm"
    primary.write_text("<html>" + "x" * 5000 + "</html>", encoding="utf-8")
    monkeypatch.setattr(rc, "CACHE_FILINGS", tmp_path / "filings")

    doc = {"source_path": "/gone/primary-document.html", "ticker": "NFLX",
           "accession_number": "0001065280-25-000044"}
    assert rc.resolve_source(doc) == primary


def test_the_largest_html_file_is_taken_as_the_primary_document(tmp_path, monkeypatch):
    """A cached filing directory holds its exhibits too; the primary document
    is the biggest of them."""
    cache = tmp_path / "filings" / "WFC_0000072971-25-000066"
    cache.mkdir(parents=True)
    (cache / "wfc-20241231_d2.htm").write_text("x" * 100, encoding="utf-8")
    big = cache / "wfc-20241231.htm"
    big.write_text("x" * 9000, encoding="utf-8")
    monkeypatch.setattr(rc, "CACHE_FILINGS", tmp_path / "filings")

    doc = {"source_path": "/gone.html", "ticker": "WFC",
           "accession_number": "0000072971-25-000066"}
    assert rc.resolve_source(doc) == big


@pytest.mark.parametrize("doc", [
    {"source_path": "/gone.html", "ticker": "", "accession_number": ""},
    {"source_path": "/gone.html", "ticker": "ZZZZ",
     "accession_number": "0000000000-00-000000"},
])
def test_an_unresolvable_filing_returns_none_rather_than_guessing(doc, tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "CACHE_FILINGS", tmp_path / "nothing")
    assert rc.resolve_source(doc) is None


def test_a_cache_directory_with_no_html_resolves_to_nothing(tmp_path, monkeypatch):
    cache = tmp_path / "filings" / "AAPL_0000320193-24-000123"
    cache.mkdir(parents=True)
    (cache / "FilingSummary.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(rc, "CACHE_FILINGS", tmp_path / "filings")
    doc = {"source_path": "/gone.html", "ticker": "AAPL",
           "accession_number": "0000320193-24-000123"}
    assert rc.resolve_source(doc) is None


def test_main_reports_a_filing_it_cannot_resolve_instead_of_failing_silently(
    tmp_path, capsys, monkeypatch
):
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    (parsed / "NFLX_2024.json").write_text(json.dumps({
        "source_path": "/gone/primary-document.html", "ticker": "NFLX",
        "fiscal_year": 2024, "accession_number": "0001065280-25-000044",
        "company": "Netflix, Inc.",
    }), encoding="utf-8")
    monkeypatch.setattr(rc, "CACHE_FILINGS", tmp_path / "nothing")

    code = rc.main(["--parsed-dir", str(parsed), "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 1, "a run that re-parsed nothing is not a success"
    assert "SKIP NFLX_2024: no readable source document on disk" in out
