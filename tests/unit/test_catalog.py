"""T1-06 — the filing catalog, built from recorded EDGAR fixtures.

No network: EdgarFetcher is pointed at tests/fixtures/edgar with offline=True,
so these run under `make test` with sockets blocked.

The three things Phase 0 showed must work:
  * BlackRock's history spans two CIKs and both must be merged;
  * a 10-K/A must be linked to the 10-K it amends (D14);
  * period_end and filing_date must be carried exactly, since `as_of`
    eligibility is nothing but a filing_date comparison (D2, G2).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from catalog.build import (
    EdgarFetcher,
    assign_collection_names,
    build,
    ciks_for_ticker,
    link_amendments,
    load_overrides,
    rows_from_submissions,
)
from catalog.store import CatalogStore, Filing, fiscal_label_from_period_end

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"


@pytest.fixture
def fetcher(tmp_path):
    """Offline fetcher served entirely from the recorded fixtures."""
    cache = tmp_path / "edgar"
    cache.mkdir()
    for src in FIXTURES.glob("CIK*.json"):
        (cache / src.name).write_bytes(src.read_bytes())
    return EdgarFetcher(cache_dir=cache, offline=True)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ── CIK overrides ───────────────────────────────────────────────────────────

def test_blackrock_has_two_ciks():
    ciks = ciks_for_ticker("BLK")
    assert ciks[0] == 2012383, "the post-reorganisation registrant should be primary"
    assert 1364742 in ciks, "the pre-reorganisation CIK must also be listed"


def test_single_cik_tickers():
    assert ciks_for_ticker("AAPL") == [320193]
    assert ciks_for_ticker("MSFT") == [789019]


def test_unknown_ticker_has_no_cik():
    assert ciks_for_ticker("ZZZZ") == []


def test_all_bundled_tickers_are_covered():
    from config import COMPANIES
    overrides = load_overrides()
    missing = [c["ticker"] for c in COMPANIES if c["ticker"] not in overrides]
    assert not missing, f"cik_overrides.yaml is missing {missing}"


# ── parsing submissions ─────────────────────────────────────────────────────

def test_rows_carry_dates_exactly(fetcher):
    rows = rows_from_submissions("AAPL", 320193, _fixture("CIK0000320193.json"), fetcher)
    assert rows, "no rows parsed"
    by_acc = {r.accession: r for r in rows}
    fy2024 = by_acc["0000320193-24-000123"]
    assert fy2024.period_end == "2024-09-28"
    assert fy2024.filing_date == "2024-11-01"
    assert fy2024.fiscal_label == 2024
    assert fy2024.fiscal_label_source == "period_end"
    assert fy2024.entity_name == "Apple Inc."
    assert fy2024.form_type == "10-K"


def test_only_annual_forms_are_kept(fetcher):
    rows = rows_from_submissions("AAPL", 320193, _fixture("CIK0000320193.json"), fetcher)
    assert {r.form_type for r in rows} <= {"10-K", "10-K/A"}


def test_rows_without_both_dates_are_skipped(fetcher):
    data = {
        "name": "Test Co", "cik": 1,
        "filings": {"recent": {
            "form": ["10-K", "10-K"],
            "accessionNumber": ["a-1", "a-2"],
            "reportDate": ["2024-12-31", ""],
            "filingDate": ["2025-02-01", "2025-02-01"],
            "primaryDocument": ["x.htm", "y.htm"],
        }, "files": []},
    }
    rows = rows_from_submissions("TST", 1, data, fetcher)
    assert [r.accession for r in rows] == ["a-1"], (
        "a row with no period_end cannot answer as_of questions and must be dropped"
    )


# ── amendments (D14) ────────────────────────────────────────────────────────

def test_amendment_is_linked_to_its_original(fetcher):
    rows = rows_from_submissions("JPM", 19617, _fixture("CIK0000019617-amended.json"),
                                 fetcher)
    rows = link_amendments(rows)
    originals = [r for r in rows if not r.is_amendment]
    amendments = [r for r in rows if r.is_amendment]
    assert len(originals) == 1 and len(amendments) == 2
    for a in amendments:
        assert a.amends == originals[0].accession
    assert originals[0].amends is None


def test_amendment_shares_the_fiscal_label(fetcher):
    rows = link_amendments(
        rows_from_submissions("JPM", 19617, _fixture("CIK0000019617-amended.json"),
                              fetcher))
    assert {r.fiscal_label for r in rows} == {2005}


# ── store behaviour ─────────────────────────────────────────────────────────

def _filing(**kw) -> Filing:
    base = {
        "accession": "acc-1", "ticker": "AAPL", "cik": 320193,
        "entity_name": "Apple Inc.", "form_type": "10-K",
        "period_end": "2024-09-28", "fiscal_label": 2024,
        "fiscal_label_source": "period_end", "filing_date": "2024-11-01",
        "primary_doc": "aapl-20240928.htm",
    }
    base.update(kw)
    return Filing(**base)


def test_store_roundtrip(tmp_path):
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.upsert([_filing()])
    got = store.get("acc-1")
    assert got is not None
    assert got.period_end == "2024-09-28" and got.filing_date == "2024-11-01"
    assert got.exhibit_docs == []


def test_upsert_is_idempotent(tmp_path):
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.upsert([_filing()])
    store.upsert([_filing()])
    assert store.stats()["filings"] == 1


def test_eligible_at_is_a_filing_date_comparison():
    f = _filing(filing_date="2024-11-01")
    assert f.eligible_at(None) is True
    assert f.eligible_at("2024-11-01") is True, "same-day filings are public"
    assert f.eligible_at("2024-11-02") is True
    assert f.eligible_at("2024-10-31") is False


def test_for_ticker_filters_by_as_of(tmp_path):
    store = CatalogStore(tmp_path / "c.sqlite")
    store.upsert([
        _filing(accession="fy2023", period_end="2023-09-30", fiscal_label=2023,
                filing_date="2023-11-03"),
        _filing(accession="fy2024", period_end="2024-09-28", fiscal_label=2024,
                filing_date="2024-11-01"),
    ])
    assert [f.accession for f in store.for_ticker("AAPL")] == ["fy2024", "fy2023"]
    at_march_2024 = store.for_ticker("AAPL", as_of="2024-03-01")
    assert [f.accession for f in at_march_2024] == ["fy2023"], (
        "the FY2024 10-K was not filed until 2024-11-01"
    )


def test_resolve_period_prefers_the_latest_filed(tmp_path):
    """D14: an amendment supersedes the original once public."""
    store = CatalogStore(tmp_path / "c.sqlite")
    store.upsert([
        _filing(accession="orig", filing_date="2024-11-01"),
        _filing(accession="amended", form_type="10-K/A", filing_date="2025-03-01",
                amends="orig"),
    ])
    assert store.resolve_period("AAPL", fiscal_label=2024).accession == "amended"
    # ...but not before it was filed
    assert store.resolve_period("AAPL", fiscal_label=2024,
                                as_of="2025-01-01").accession == "orig"


def test_resolve_period_latest_label(tmp_path):
    store = CatalogStore(tmp_path / "c.sqlite")
    store.upsert([
        _filing(accession="fy2023", period_end="2023-09-30", fiscal_label=2023,
                filing_date="2023-11-03"),
        _filing(accession="fy2024", period_end="2024-09-28", fiscal_label=2024,
                filing_date="2024-11-01"),
    ])
    assert store.resolve_period("AAPL").accession == "fy2024"
    assert store.resolve_period("AAPL", as_of="2024-06-01").accession == "fy2023"


def test_resolve_period_returns_none_when_nothing_is_eligible(tmp_path):
    store = CatalogStore(tmp_path / "c.sqlite")
    store.upsert([_filing(filing_date="2024-11-01")])
    assert store.resolve_period("AAPL", fiscal_label=2024, as_of="2024-01-01") is None
    assert store.resolve_period("AAPL", fiscal_label=1999) is None


def test_available_fiscal_labels_respects_as_of(tmp_path):
    store = CatalogStore(tmp_path / "c.sqlite")
    store.upsert([
        _filing(accession="a", period_end="2023-09-30", fiscal_label=2023,
                filing_date="2023-11-03"),
        _filing(accession="b", period_end="2024-09-28", fiscal_label=2024,
                filing_date="2024-11-01"),
    ])
    assert store.available_fiscal_labels("AAPL") == [2023, 2024]
    assert store.available_fiscal_labels("AAPL", as_of="2024-01-01") == [2023]


def test_eligible_collections_excludes_future_filings(tmp_path):
    """The join that makes as_of possible on the text path (D2)."""
    store = CatalogStore(tmp_path / "c.sqlite")
    store.upsert([
        _filing(accession="a", period_end="2023-09-30", fiscal_label=2023,
                filing_date="2023-11-03", collection_name="AAPL_2023"),
        _filing(accession="b", period_end="2024-09-28", fiscal_label=2024,
                filing_date="2024-11-01", collection_name="AAPL_2024"),
    ])
    assert store.eligible_collections(["AAPL"]) == ["AAPL_2024", "AAPL_2023"]
    assert store.eligible_collections(["AAPL"], as_of="2024-03-01") == ["AAPL_2023"]
    assert store.eligible_collections(["AAPL"], fiscal_labels=[2023]) == ["AAPL_2023"]


# ── collection mapping ──────────────────────────────────────────────────────

def test_collection_goes_to_the_original_not_the_amendment():
    rows = [
        _filing(accession="orig", filing_date="2024-11-01"),
        _filing(accession="amd", form_type="10-K/A", filing_date="2025-03-01"),
    ]
    linked = assign_collection_names(rows, ["AAPL_2024"])
    assert linked == 1
    by_acc = {r.accession: r for r in rows}
    assert by_acc["orig"].collection_name == "AAPL_2024"
    assert by_acc["amd"].collection_name is None, (
        "v1 indexes one collection per (ticker, year); the amendment shares it"
    )


def test_no_collection_assigned_when_none_exists():
    rows = [_filing()]
    assert assign_collection_names(rows, []) == 0
    assert rows[0].collection_name is None


# ── full offline build ──────────────────────────────────────────────────────

def test_build_merges_blackrocks_two_ciks(tmp_path, fetcher):
    store, stats = build(
        tickers=["BLK"], catalog_path=tmp_path / "c.sqlite",
        fetcher=fetcher, collections=[],
    )
    filings = store.for_ticker("BLK")
    ciks = {f.cik for f in filings}
    assert ciks == {2012383, 1364742}, (
        f"both registrants must appear, got {ciks}. Querying one CIK silently "
        f"loses part of BlackRock's 10-K history."
    )
    names = {f.entity_name for f in filings}
    assert any("BlackRock" in n for n in names)
    assert stats["edgar_requests"] == 0, "offline build must make no requests"


def test_build_is_offline_and_idempotent(tmp_path, fetcher):
    path = tmp_path / "c.sqlite"
    _s1, stats1 = build(tickers=["AAPL"], catalog_path=path, fetcher=fetcher,
                        collections=["AAPL_2024"])
    s2, stats2 = build(tickers=["AAPL"], catalog_path=path, fetcher=fetcher,
                       collections=["AAPL_2024"])
    assert stats1["filings"] == stats2["filings"]
    assert s2.stats()["with_collection"] == 1


def test_build_skips_a_ticker_with_no_cik(tmp_path, fetcher):
    _store, stats = build(tickers=["ZZZZ"], catalog_path=tmp_path / "c.sqlite",
                          fetcher=fetcher, collections=[])
    assert stats["tickers_skipped"] == 1
    assert stats["filings"] == 0


def test_offline_fetcher_makes_no_requests_for_a_missing_cache(tmp_path):
    f = EdgarFetcher(cache_dir=tmp_path / "empty", offline=True)
    assert f.submissions(999999) is None
    assert f.requests_made == 0


# ── interim fiscal label ────────────────────────────────────────────────────

@pytest.mark.parametrize("period_end,expected", [
    ("2024-09-28", 2024),     # Apple, September year end
    ("2026-06-30", 2026),     # Microsoft FY2026
    ("2024-12-31", 2024),     # calendar-year filer
    ("2025-01-03", 2025),     # 52/53-week year ending early January
])
def test_fiscal_label_from_period_end(period_end, expected):
    assert fiscal_label_from_period_end(period_end) == expected


# ── fetcher and CLI paths (coverage for catalog/build.py) ───────────────────

class _StubResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        return self._payload


class _StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        return self.responses.pop(0)


def test_fetcher_writes_and_reuses_the_cache(tmp_path, monkeypatch):
    """A second call must be served from disk, making a rebuild free."""
    monkeypatch.setenv("edgar_email", "t@example.com")
    import config
    monkeypatch.setattr(config.settings, "edgar_email", "t@example.com")
    payload = {"name": "Test Co", "cik": 1, "filings": {"recent": {}, "files": []}}
    session = _StubSession([_StubResp(200, payload)])
    f = EdgarFetcher(cache_dir=tmp_path / "c", offline=False, session=session)
    assert f.submissions(1)["name"] == "Test Co"
    assert f.requests_made == 1
    assert f.submissions(1)["name"] == "Test Co"
    assert f.requests_made == 1, "second call must come from the cache"


def test_fetcher_sends_a_contact_user_agent(tmp_path, monkeypatch):
    """SEC returns 403 without one (observed in Phase 0)."""
    import config
    monkeypatch.setattr(config.settings, "edgar_email", "owner@example.com")
    session = _StubSession([_StubResp(200, {"name": "X", "cik": 1,
                                            "filings": {"recent": {}, "files": []}})])
    f = EdgarFetcher(cache_dir=tmp_path / "c", offline=False, session=session)
    f.submissions(1)
    ua = session.calls[0]["headers"]["User-Agent"]
    assert "owner@example.com" in ua


def test_fetcher_requires_edgar_email(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "edgar_email", None)
    session = _StubSession([_StubResp(200, {})])
    f = EdgarFetcher(cache_dir=tmp_path / "c", offline=False, session=session)
    with pytest.raises(config.MissingSettingError):
        f.submissions(1)


def test_fetcher_reports_a_non_200(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "edgar_email", "t@example.com")
    session = _StubSession([_StubResp(403, None, text="forbidden")])
    f = EdgarFetcher(cache_dir=tmp_path / "c", offline=False, session=session)
    assert f.submissions(1) is None


def test_fetcher_refetches_a_corrupt_cache(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "edgar_email", "t@example.com")
    cache = tmp_path / "c"
    cache.mkdir()
    (cache / "CIK0000000001.json").write_text("{not json", encoding="utf-8")
    payload = {"name": "Recovered", "cik": 1, "filings": {"recent": {}, "files": []}}
    session = _StubSession([_StubResp(200, payload)])
    f = EdgarFetcher(cache_dir=cache, offline=False, session=session)
    assert f.submissions(1)["name"] == "Recovered"


def test_fetcher_follows_pagination_chunks(tmp_path, monkeypatch):
    """filings.recent is only the newest slice (Phase 0 missed this)."""
    import config
    monkeypatch.setattr(config.settings, "edgar_email", "t@example.com")
    fields = ["form", "accessionNumber", "reportDate", "filingDate", "primaryDocument"]
    recent = dict(zip(fields, [
        ["10-K"], ["new"], ["2024-12-31"], ["2025-02-01"], ["a.htm"]], strict=True))
    older = dict(zip(fields, [
        ["10-K"], ["old"], ["2023-12-31"], ["2024-02-01"], ["b.htm"]], strict=True))
    main_doc = {"name": "Test Co", "cik": 1,
                "filings": {"recent": recent, "files": [{"name": "chunk-1.json"}]}}
    session = _StubSession([_StubResp(200, older)])
    f = EdgarFetcher(cache_dir=tmp_path / "c", offline=False, session=session)
    rows = rows_from_submissions("TST", 1, main_doc, f)
    assert sorted(r.accession for r in rows) == ["new", "old"], (
        "older pagination chunks must be followed"
    )


def test_cli_runs_offline(tmp_path, monkeypatch, capsys):
    from catalog.build import main as build_main
    cache = tmp_path / "edgar"
    cache.mkdir()
    for src in FIXTURES.glob("CIK*.json"):
        (cache / src.name).write_bytes(src.read_bytes())
    monkeypatch.setattr("catalog.build._CACHE_DIR", cache)
    rc = build_main(["--ticker", "AAPL", "--offline",
                     "--catalog-path", str(tmp_path / "c.sqlite")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "edgar_requests" in out


def test_the_offline_cli_reads_the_patched_cache_and_nothing_else(tmp_path, monkeypatch, capsys):
    """Negative control for the test above.

    `EdgarFetcher`'s `cache_dir` default used to be bound at import, so patching
    `catalog.build._CACHE_DIR` did nothing and the build read the developer's
    real `.cache/edgar`. The test passed for the wrong reason and failed the
    first time CI ran it on a machine with no cache. With an EMPTY patched cache
    the offline build must find nothing — if it finds filings, it is reading
    something this test did not give it.
    """
    from catalog.build import main as build_main

    empty = tmp_path / "empty-edgar"
    empty.mkdir()
    monkeypatch.setattr("catalog.build._CACHE_DIR", empty)
    rc = build_main(["--ticker", "AAPL", "--offline",
                     "--catalog-path", str(tmp_path / "c.sqlite")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "filings              0" in out, out
    assert "AAPL" not in out.split("LATEST FILED")[-1], (
        "the offline build found filings with an empty cache, so it is reading "
        "a directory this test did not provide"
    )


def test_cli_reports_a_failure(tmp_path, monkeypatch):
    from catalog.build import main as build_main
    monkeypatch.setattr("catalog.build.build",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert build_main(["--offline", "--catalog-path", str(tmp_path / "c.sqlite")]) == 1


def test_store_helpers(tmp_path):
    store = CatalogStore(tmp_path / "c.sqlite")
    store.upsert([_filing()])
    store.set_collection_name("acc-1", "AAPL_2024")
    assert store.get("acc-1").collection_name == "AAPL_2024"
    store.mark_facts_built("acc-1")
    assert store.get("acc-1").facts_built_at
    assert store.tickers() == ["AAPL"]
    assert len(store.all_filings()) == 1
    assert store.to_dicts()[0]["accession"] == "acc-1"
    assert store.get("missing") is None
