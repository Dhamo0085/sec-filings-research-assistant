"""facts/documents.py — which documents of a submission carry facts.

The interesting logic is ``parse_input_files``, which is pure, and the
fetcher's caching and offline behaviour. Both are exercised without a network:
the fetcher takes an injected session, so these tests also prove it makes no
request when a file is already cached — the property the whole re-run story
depends on.
"""
from __future__ import annotations

import pytest

from facts.documents import (
    DocumentFetcher,
    _accession_nodash,
    parse_input_files,
)

pytestmark = pytest.mark.unit

# The real FilingSummary.xml block for Wells Fargo FY2024 (two iXBRL documents
# plus the linkbases). This shape is why the module reads FilingSummary at all.
WFC_SUMMARY = """<?xml version="1.0"?>
<FilingSummary>
  <InputFiles>
    <File doctype="10-K" isDefinitelyFs="true" isUsgaap="true"
          original="wfc-20241231.htm">wfc-20241231.htm</File>
    <File doctype="10-K" isDefinitelyFs="true" isUsgaap="true"
          original="wfc-20241231_d2.htm">wfc-20241231_d2.htm</File>
    <File>wfc-20241231.xsd</File>
    <File>wfc-20241231_cal.xml</File>
    <File>wfc-20241231_lab.xml</File>
  </InputFiles>
  <MyReports><Report/></MyReports>
</FilingSummary>
"""


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200) -> None:
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self.status_code = status


class FakeSession:
    """Records every URL requested, so "no request" is assertable."""

    def __init__(self, routes: dict, default_status: int = 404) -> None:
        self.routes = routes
        self.default_status = default_status
        self.urls: list[str] = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        for suffix, body in self.routes.items():
            if url.endswith(suffix):
                return FakeResponse(body if isinstance(body, bytes)
                                    else body.encode("utf-8"))
        return FakeResponse(b"", self.default_status)


class FakeFiling:
    def __init__(self, **kw):
        self.ticker = kw.get("ticker", "WFC")
        self.cik = kw.get("cik", 72971)
        self.accession = kw.get("accession", "0000072971-25-000066")
        self.primary_doc = kw.get("primary_doc", "wfc-20241231.htm")


# ── parse_input_files ──────────────────────────────────────────────────────

def test_input_files_keeps_only_ixbrl_documents():
    """Linkbases carry no facts; fetching them would multiply requests by five."""
    assert parse_input_files(WFC_SUMMARY) == [
        "wfc-20241231.htm", "wfc-20241231_d2.htm",
    ]


def test_input_files_handles_an_html_extension():
    xml = ('<FilingSummary><InputFiles>'
           '<File doctype="10-K">a.html</File>'
           '<File>b.xsd</File>'
           '</InputFiles></FilingSummary>')
    assert parse_input_files(xml) == ["a.html"]


def test_input_files_keeps_a_document_with_no_doctype():
    """A filer that omits doctype must not be silently skipped."""
    xml = ('<FilingSummary><InputFiles>'
           '<File>only.htm</File>'
           '</InputFiles></FilingSummary>')
    assert parse_input_files(xml) == ["only.htm"]


def test_input_files_deduplicates():
    xml = ('<FilingSummary><InputFiles>'
           '<File>a.htm</File><File>a.htm</File>'
           '</InputFiles></FilingSummary>')
    assert parse_input_files(xml) == ["a.htm"]


@pytest.mark.parametrize("payload", ["", "<FilingSummary/>", "not xml at all"])
def test_input_files_without_a_block_returns_nothing(payload):
    assert parse_input_files(payload) == []


def test_accession_formatting():
    assert _accession_nodash("0000072971-25-000066") == "000007297125000066"


# ── the fetcher ────────────────────────────────────────────────────────────

def test_ensure_downloads_the_documents_FilingSummary_names(tmp_path):
    session = FakeSession({
        "FilingSummary.xml": WFC_SUMMARY,
        "wfc-20241231.htm": b"<html>wrapper</html>",
        "wfc-20241231_d2.htm": b"<html>exhibit</html>",
    })
    fetcher = DocumentFetcher(tmp_path, session=session)
    paths = fetcher.ensure(FakeFiling())
    assert [p.name for p in paths] == ["wfc-20241231.htm", "wfc-20241231_d2.htm"]
    assert all(p.exists() for p in paths)
    assert fetcher.requests_made == 3
    # One directory per accession, so exhibits keep the SEC's own names.
    assert paths[0].parent.name == "WFC_0000072971-25-000066"


def test_a_cached_document_costs_no_request(tmp_path):
    session = FakeSession({
        "FilingSummary.xml": WFC_SUMMARY,
        "wfc-20241231.htm": b"<html>wrapper</html>",
        "wfc-20241231_d2.htm": b"<html>exhibit</html>",
    })
    first = DocumentFetcher(tmp_path, session=session)
    first.ensure(FakeFiling())
    assert first.requests_made == 3

    second_session = FakeSession({})          # every route 404s
    second = DocumentFetcher(tmp_path, session=second_session)
    paths = second.ensure(FakeFiling())
    assert [p.name for p in paths] == ["wfc-20241231.htm", "wfc-20241231_d2.htm"]
    assert second.requests_made == 0, "a cached filing must make no request"
    assert second_session.urls == []


def test_offline_returns_nothing_rather_than_fetching(tmp_path):
    session = FakeSession({"FilingSummary.xml": WFC_SUMMARY})
    fetcher = DocumentFetcher(tmp_path, offline=True, session=session)
    assert fetcher.ensure(FakeFiling()) == []
    assert session.urls == [], "offline must not touch the network"


def test_missing_filing_summary_falls_back_to_the_primary_document(tmp_path):
    """Filings older than FilingSummary.xml still have one document."""
    session = FakeSession({"old-10k.htm": b"<html>old</html>"})
    fetcher = DocumentFetcher(tmp_path, session=session)
    paths = fetcher.ensure(FakeFiling(primary_doc="old-10k.htm"))
    assert [p.name for p in paths] == ["old-10k.htm"]


def test_no_summary_and_no_primary_document_yields_nothing(tmp_path):
    fetcher = DocumentFetcher(tmp_path, session=FakeSession({}))
    assert fetcher.ensure(FakeFiling(primary_doc=None)) == []


def test_max_documents_caps_a_pathological_submission(tmp_path):
    files = "".join(f'<File doctype="10-K">d{i}.htm</File>' for i in range(20))
    session = FakeSession({
        "FilingSummary.xml": f"<FilingSummary><InputFiles>{files}</InputFiles></FilingSummary>",
        **{f"d{i}.htm": b"x" for i in range(20)},
    })
    fetcher = DocumentFetcher(tmp_path, session=session)
    paths = fetcher.ensure(FakeFiling(), max_documents=3)
    assert len(paths) == 3


def test_a_failed_document_request_is_skipped_not_fatal(tmp_path):
    session = FakeSession({
        "FilingSummary.xml": WFC_SUMMARY,
        "wfc-20241231.htm": b"<html>wrapper</html>",
        # the exhibit 404s
    })
    fetcher = DocumentFetcher(tmp_path, session=session)
    paths = fetcher.ensure(FakeFiling())
    assert [p.name for p in paths] == ["wfc-20241231.htm"]


def test_base_url_shape(tmp_path):
    fetcher = DocumentFetcher(tmp_path, offline=True, session=FakeSession({}))
    assert fetcher.base_url(72971, "0000072971-25-000066") == (
        "https://www.sec.gov/Archives/edgar/data/72971/000007297125000066")


def test_stats_are_reported(tmp_path):
    session = FakeSession({"FilingSummary.xml": WFC_SUMMARY,
                           "wfc-20241231.htm": b"abc",
                           "wfc-20241231_d2.htm": b"de"})
    fetcher = DocumentFetcher(tmp_path, session=session)
    fetcher.ensure(FakeFiling())
    stats = fetcher.stats()
    assert stats["requests_made"] == 3
    assert stats["bytes_fetched"] == len(WFC_SUMMARY.encode()) + 3 + 2
