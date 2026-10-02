"""The SEC response cache must refuse to store an error page (P3-00b).

``.cache/company_tickers.json`` in this working copy holds SEC's
"Request Rate Threshold Exceeded" interstitial rather than JSON. SEC serves
that page with **HTTP 200**, so a ``status_code != 200`` check does not see it.
Both SEC fetchers wrote the body to their cache before validating it, which
turns one throttled request into a permanent-looking parse failure.

The last two tests are the negative controls (CLAUDE.md rule 15): they drive
``catalog.build.EdgarFetcher`` and ``facts.documents`` with a fake session that
returns the real error page with HTTP 200, and assert that nothing is written.
Reverting the guard makes them fail — the pre-fix behaviour was exactly "write,
then raise".
"""

from __future__ import annotations

import json

import pytest

from ingestion.sec_cache import (
    SecResponseError,
    is_poisoned_json_cache,
    looks_like_html,
    sec_error_reason,
    validated_document,
    validated_json,
)

pytestmark = pytest.mark.unit

# The real page, trimmed to the parts that identify it. The full copy is in
# .cache/company_tickers.json; this keeps the fixture small and in the test.
SEC_RATE_LIMIT_PAGE = (
    b'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
    b'"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">\n'
    b'<html xmlns="http://www.w3.org/1999/xhtml">\n<head>\n'
    b'<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />\n'
    b"<title>SEC.gov | Request Rate Threshold Exceeded</title>\n</head>\n<body>\n"
    b'<div id="header">U.S. Securities and Exchange Commission</div>\n'
    b"<h1>Automated access to our sites must comply with SEC.gov's Privacy "
    b"and Security Policy.</h1>\n"
    b"<p>Reference ID: 0.517fd117.1790911889.30b33154</p>\n</body>\n</html>\n"
)

GOOD_SUBMISSIONS = {
    "cik": "0000320193",
    "name": "Apple Inc.",
    "filings": {"recent": {"accessionNumber": [], "form": []}},
}


# ── the detector ──────────────────────────────────────────────────────────────

def test_the_real_error_page_is_detected():
    assert sec_error_reason(SEC_RATE_LIMIT_PAGE) is not None


def test_a_real_json_response_is_not_flagged():
    assert sec_error_reason(json.dumps(GOOD_SUBMISSIONS)) is None
    assert not looks_like_html(json.dumps(GOOD_SUBMISSIONS))


def test_a_filing_document_mentioning_risk_and_rates_is_not_flagged():
    """The markers must not fire on ordinary filing prose."""
    filing = (
        b"<html><body><p>Item 1A. Risk Factors</p><p>Our request rate for "
        b"capital exceeded the threshold set by our board, and automated "
        b"access to our systems is governed by our security policy.</p>"
        b"</body></html>"
    )
    assert sec_error_reason(filing) is None
    assert validated_document(filing) == filing


def test_validated_json_rejects_the_error_page():
    with pytest.raises(SecResponseError, match="error page"):
        validated_json(SEC_RATE_LIMIT_PAGE, url="https://www.sec.gov/files/x.json")


def test_validated_json_rejects_html_that_is_not_the_error_page():
    with pytest.raises(SecResponseError, match="HTML document"):
        validated_json(b"<!DOCTYPE html><html><body>maintenance</body></html>")


def test_validated_json_rejects_a_json_array():
    """SEC's submission and companyfacts payloads are objects, never arrays."""
    with pytest.raises(SecResponseError, match="JSON object"):
        validated_json(b"[1, 2, 3]")


def test_validated_json_accepts_a_real_payload():
    assert validated_json(json.dumps(GOOD_SUBMISSIONS))["name"] == "Apple Inc."


def test_validated_document_rejects_the_error_page_and_empty_bodies():
    with pytest.raises(SecResponseError, match="error page"):
        validated_document(SEC_RATE_LIMIT_PAGE)
    with pytest.raises(SecResponseError, match="empty response"):
        validated_document(b"   \n")


def test_poisoned_cache_file_is_identified(tmp_path):
    bad = tmp_path / "company_tickers.json"
    bad.write_bytes(SEC_RATE_LIMIT_PAGE)
    assert is_poisoned_json_cache(bad) is not None

    good = tmp_path / "CIK0000320193.json"
    good.write_text(json.dumps(GOOD_SUBMISSIONS), encoding="utf-8")
    assert is_poisoned_json_cache(good) is None


# ── negative controls: the fetchers must not write the page ───────────────────

class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.content = body
        self.status_code = status

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class _FakeSession:
    """Returns one canned response and records the URLs it was asked for."""

    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status
        self.urls: list = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return _FakeResponse(self.body, self.status)


def test_catalog_fetcher_does_not_cache_the_error_page(tmp_path, monkeypatch):
    from catalog import build as catalog_build

    monkeypatch.setattr(catalog_build, "_user_agent", lambda: "test test@example.com")
    session = _FakeSession(SEC_RATE_LIMIT_PAGE)
    fetcher = catalog_build.EdgarFetcher(cache_dir=tmp_path, session=session)

    assert fetcher.submissions(320193) is None, "a throttled fetch must not look like data"
    assert session.urls, "the fetch was not attempted"
    assert list(tmp_path.iterdir()) == [], (
        f"the error page was cached: {[p.name for p in tmp_path.iterdir()]}"
    )


def test_catalog_fetcher_still_caches_a_good_response(tmp_path, monkeypatch):
    from catalog import build as catalog_build

    monkeypatch.setattr(catalog_build, "_user_agent", lambda: "test test@example.com")
    session = _FakeSession(json.dumps(GOOD_SUBMISSIONS).encode())
    fetcher = catalog_build.EdgarFetcher(cache_dir=tmp_path, session=session)

    data = fetcher.submissions(320193)
    assert data is not None and data["name"] == "Apple Inc."
    assert (tmp_path / "CIK0000320193.json").is_file()

    # And a second call is served from that cache without a request.
    before = len(session.urls)
    assert fetcher.submissions(320193)["name"] == "Apple Inc."
    assert len(session.urls) == before, "the cached response was not reused"


def test_catalog_fetcher_discards_a_cache_entry_that_is_an_error_page(tmp_path, monkeypatch):
    """An entry written before this guard existed must not be trusted on read."""
    from catalog import build as catalog_build

    monkeypatch.setattr(catalog_build, "_user_agent", lambda: "test test@example.com")
    (tmp_path / "CIK0000320193.json").write_bytes(SEC_RATE_LIMIT_PAGE)
    session = _FakeSession(json.dumps(GOOD_SUBMISSIONS).encode())
    fetcher = catalog_build.EdgarFetcher(cache_dir=tmp_path, session=session)

    data = fetcher.submissions(320193)
    assert data is not None and data["name"] == "Apple Inc.", (
        "the poisoned cache entry was returned as data"
    )
    assert session.urls, "the poisoned entry was not refetched"


def test_document_fetcher_does_not_cache_the_error_page(tmp_path, monkeypatch):
    from facts import documents as facts_documents

    monkeypatch.setattr(facts_documents, "_user_agent", lambda: "test test@example.com")
    session = _FakeSession(SEC_RATE_LIMIT_PAGE)
    fetcher = facts_documents.DocumentFetcher(cache_dir=tmp_path, session=session)

    assert fetcher.document_names(320193, "0000320193-24-000123", "AAPL") == []
    cached_files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert cached_files == [], f"the error page was cached: {cached_files}"
