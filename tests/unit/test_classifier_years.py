"""T1-07 — fiscal years must come from what is indexed, not a hard-coded set (K5).

v1 pinned `VALID_YEARS = {2023, 2024, 2025}` in routing/classifier.py and
filtered the model's extracted years through it, so any other year was silently
dropped and the query proceeded as if no year had been asked for.

Phase 0 measured the consequences: Microsoft's FY2026 10-K (period end
2026-06-30, filed 2026-07-29) was unrequestable, and the live deployment held an
indexed NVDA_2026 collection that no year-qualified query could reach
(reports/phase0/REPORT.md section 2.6 and 7.1).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import routing.classifier as C
from llm.fake import FakeLLM

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_year_cache():
    C.reset_year_cache()
    yield
    C.reset_year_cache()


def _patch_collections(monkeypatch, names):
    monkeypatch.setattr(C, "_list_collections", lambda: list(names))


# ── available_years() ───────────────────────────────────────────────────────

def test_available_years_parses_collection_names(monkeypatch):
    _patch_collections(monkeypatch, ["AAPL_2024", "MSFT_2026", "JPM_2023", "JPM_2024"])
    assert C.available_years() == frozenset({2023, 2024, 2026})


def test_available_years_handles_dotted_and_hyphenated_tickers(monkeypatch):
    _patch_collections(monkeypatch, ["BRK.B_2024", "RDS-A_2023"])
    assert C.available_years() == frozenset({2023, 2024})


def test_available_years_ignores_malformed_names(monkeypatch):
    _patch_collections(monkeypatch, ["AAPL_2024", "scratch", "parent_store", "AAPL_20XX", "_2024"])
    assert C.available_years() == frozenset({2024})


def test_available_years_survives_a_store_failure(monkeypatch):
    def boom():
        raise RuntimeError("qdrant unavailable")
    monkeypatch.setattr(C, "_list_collections", boom)
    assert C.available_years() == frozenset()


def test_available_years_is_cached_then_refreshable(monkeypatch):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return ["AAPL_2024"]
    monkeypatch.setattr(C, "_list_collections", counting)
    C.available_years()
    C.available_years()
    assert calls["n"] == 1, "second call should be served from the TTL cache"
    C.reset_year_cache()
    C.available_years()
    assert calls["n"] == 2


# ── no hard-coded year set remains ──────────────────────────────────────────

def test_hardcoded_valid_years_constant_is_gone():
    assert not hasattr(C, "VALID_YEARS"), (
        "VALID_YEARS must not exist: a static set is what made FY2026 unreachable"
    )


# ── classify_query year extraction ──────────────────────────────────────────

def _run(monkeypatch, payload, collections=("AAPL_2024", "MSFT_2026")):
    """Drive classify_query with a FakeLLM (no network, no provider)."""
    _patch_collections(monkeypatch, collections)
    fake = FakeLLM(default=json.dumps(payload))
    monkeypatch.setattr(C, "get_llm", lambda: fake)
    result = C.classify_query("irrelevant, the model reply is faked")
    return result, fake


def _system_message(fake):
    return fake.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("year", [2026, 2023, 2019, 2024])
def test_plausible_years_survive_extraction(monkeypatch, year):
    """The K5 regression: 2026 (and anything else plausible) must survive."""
    result, _ = _run(monkeypatch, {
        "query_type": "single_doc", "tickers": ["MSFT"], "years": [year],
        "focus": "revenue", "reasoning": "x",
    })
    assert result.years == [year]


@pytest.mark.parametrize("year", [1800, 1492, 3000, 99999, 0])
def test_implausible_years_are_dropped(monkeypatch, year):
    result, _ = _run(monkeypatch, {
        "query_type": "single_doc", "tickers": ["MSFT"], "years": [year],
        "focus": "revenue", "reasoning": "x",
    })
    assert result.years == []


def test_year_we_do_not_have_is_kept_for_the_year_not_available_path(monkeypatch):
    """A year with no collection must NOT be silently dropped here.

    Availability is decided downstream (routing/resolver.py), which reports it
    as `year_not_available`. Dropping it in the classifier is what made the
    query look like it had no year at all.
    """
    result, _ = _run(monkeypatch, {
        "query_type": "single_doc", "tickers": ["AAPL"], "years": [2021],
        "focus": "revenue", "reasoning": "x",
    }, collections=("AAPL_2024",))
    assert result.years == [2021]


def test_non_integer_years_are_ignored(monkeypatch):
    result, _ = _run(monkeypatch, {
        "query_type": "single_doc", "tickers": ["AAPL"], "years": ["twenty-four", None, "2024"],
        "focus": "revenue", "reasoning": "x",
    })
    assert result.years == [2024]


def test_prompt_states_the_years_actually_indexed(monkeypatch):
    _, fake = _run(monkeypatch, {
        "query_type": "single_doc", "tickers": ["MSFT"], "years": [2026],
        "focus": "revenue", "reasoning": "x",
    }, collections=("AAPL_2024", "MSFT_2025", "MSFT_2026"))
    system = _system_message(fake)
    assert "2024" in system and "2026" in system
    assert "2023, 2024, 2025" not in system, "the static year line must be gone"


def test_prompt_degrades_gracefully_with_no_collections(monkeypatch):
    _, fake = _run(monkeypatch, {
        "query_type": "single_doc", "tickers": ["AAPL"], "years": [2024],
        "focus": "revenue", "reasoning": "x",
    }, collections=())
    system = _system_message(fake)
    assert isinstance(system, str) and len(system) > 100


def test_upper_bound_tracks_the_clock():
    """Next year is plausible (a FY2027 10-K can be filed in late 2026)."""
    nxt = datetime.now(timezone.utc).year + 1
    assert C._is_plausible_fiscal_year(nxt)
    assert not C._is_plausible_fiscal_year(nxt + 2)
