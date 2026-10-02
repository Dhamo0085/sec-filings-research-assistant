"""Company-resolution phrase cases (T3-01, P3-02).

``registry_lookup`` is left at its offline default in almost every case, so
these tests prove the resolution they claim rather than quietly reaching for
SEC's registry.
"""

from __future__ import annotations

import pytest

from routing.entities import ALIASES, resolve_entities

pytestmark = pytest.mark.unit


def R(question: str, **kwargs):
    return resolve_entities(question, **kwargs)


# ── tickers written as tickers ────────────────────────────────────────────────

@pytest.mark.parametrize("question,expected", [
    ("What was AAPL's revenue in 2024?", ("AAPL",)),
    ("What was $AAPL revenue in 2024?", ("AAPL",)),
    ("AAPL vs MSFT revenue", ("AAPL", "MSFT")),
    ("Compare JPM, BAC and WFC net income", ("JPM", "BAC", "WFC")),
    ("TROW revenue", ("TROW",)),
    ("GS total net revenues", ("GS",)),
])
def test_explicit_tickers(question, expected):
    assert R(question).tickers == expected


def test_a_short_ticker_in_lower_case_does_not_match_inside_a_word():
    """A case-insensitive 'GS' matches "things"; that must not resolve."""
    got = R("What are the things Apple sells?")
    assert got.tickers == ("AAPL",)
    assert "GS" not in got.tickers


def test_a_long_ticker_matches_case_insensitively():
    assert R("what was aapl revenue") .tickers == ("AAPL",)
    assert R("troW revenue").tickers == ("TROW",)


# ── curated aliases ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,expected", [
    ("What was Apple's revenue in 2024?", "AAPL"),
    ("What was Apple revenue in 2024?", "AAPL"),
    ("What was APPLE INC revenue?", "AAPL"),
    ("Microsoft's net income", "MSFT"),
    ("Microsoft Corporation net income", "MSFT"),
    ("Google's R&D spend", "GOOGL"),
    ("Alphabet's R&D spend", "GOOGL"),
    ("Amazon's operating margin", "AMZN"),
    ("Amazon.com's operating margin", "AMZN"),
    ("JPMorgan's total net revenue", "JPM"),
    ("JP Morgan's total net revenue", "JPM"),
    ("JPMorgan Chase total net revenue", "JPM"),
    ("JPMorganChase total net revenue", "JPM"),
    ("Wells Fargo's net interest income", "WFC"),
    ("Bank of America's total revenue", "BAC"),
    ("BofA's total revenue", "BAC"),
    ("Goldman's total net revenues", "GS"),
    ("Goldman Sachs' total net revenues", "GS"),
    ("The Goldman Sachs Group revenue", "GS"),
    ("BlackRock's revenue", "BLK"),
    ("State Street's fee revenue", "STT"),
    ("T. Rowe Price's assets under management", "TROW"),
    ("Invesco's revenue", "IVZ"),
    ("Netflix's revenue", "NFLX"),
])
def test_names_people_actually_type(question, expected):
    got = R(question)
    assert expected in got.tickers, f"{question!r} -> {got.tickers}"


def test_aliases_exist_because_a_substring_search_would_miss_them():
    """The three worked examples from the module docstring."""
    assert ALIASES["jpmorgan"] == "JPM"      # not a substring of the registered name
    assert ALIASES["bofa"] == "BAC"          # not a substring of anything
    assert ALIASES["google"] == "GOOGL"      # does not appear in "Alphabet Inc."


def test_possessives_and_typography_do_not_block_a_match():
    """A straight and a typographic apostrophe must behave the same.

    A bare plural ("Apples revenue") is deliberately not matched: "apples" is
    an ordinary English word, and resolving it to Apple would be the kind of
    guess this module is meant to avoid.
    """
    for question in ["Apple's revenue", "Apple’s revenue", "Apple revenue"]:
        assert "AAPL" in R(question).tickers, question
    assert R("How many apples does a grocer sell?").tickers == ()


def test_the_longest_alias_wins():
    """"Bank of America" must not resolve through a shorter overlapping key."""
    got = R("What was Bank of America's total revenue?")
    assert got.tickers == ("BAC",)
    assert got.matched["BAC"] == "bank of america"


def test_one_mention_produces_one_ticker():
    """"JPMorgan Chase" must not yield JPM twice, or JPM plus a "chase" hit."""
    got = R("JPMorgan Chase's total net revenue")
    assert got.tickers == ("JPM",)


def test_two_companies_keep_their_order():
    got = R("Compare Apple and Microsoft R&D spend")
    assert got.tickers == ("AAPL", "MSFT")


def test_the_display_name_comes_back_for_the_answer_template():
    got = R("Goldman's revenue")
    assert got.names["GS"] == "The Goldman Sachs Group Inc."


# ── catalog tickers (filers added by on-demand ingestion) ─────────────────────

def test_a_catalog_only_ticker_resolves_without_a_registry_call():
    got = R("What was NVDA's revenue in 2025?", catalog_tickers=["NVDA", "TSLA"])
    assert got.tickers == ("NVDA",)


def test_a_catalog_ticker_is_not_matched_inside_a_word():
    got = R("What are the vital statistics?", catalog_tickers=["V", "ITA"])
    assert got.tickers == ()


# ── unresolved mentions are reported, never dropped ───────────────────────────

def test_an_unresolvable_company_is_reported():
    """v1 dropped it and answered about Apple alone with no indication."""
    got = R("Compare Apple and SpaceX's revenue")
    assert got.tickers == ("AAPL",)
    assert "SpaceX" in got.unresolved


def test_a_question_with_no_resolvable_company_says_so():
    got = R("What was Stripe's revenue in 2024?")
    assert got.tickers == ()
    assert got.resolved_any is False
    assert "Stripe" in got.unresolved


@pytest.mark.parametrize("question", [
    "What was Apple's revenue in fiscal 2024?",
    "Compare Apple and Microsoft net income",
    "How did Amazon's operating margin change from 2022 to 2024?",
    "What risks does JPMorgan disclose?",
])
def test_a_fully_resolved_question_reports_nothing_unresolved(question):
    """The false-positive control for the capitalised-phrase heuristic."""
    got = R(question)
    assert got.unresolved == (), f"{question!r} -> {got.unresolved}"


def test_leading_question_words_are_not_mistaken_for_companies():
    got = R("What was the revenue? Which filing? How much?")
    assert got.unresolved == ()


def test_unresolved_detection_can_be_switched_off():
    got = R("Compare Apple and SpaceX", detect_unresolved=False)
    assert got.unresolved == ()


# ── the injected registry ─────────────────────────────────────────────────────

def test_an_injected_registry_resolves_an_unbundled_filer():
    calls = []

    def fake_registry(mention: str):
        calls.append(mention)
        if "costco" in mention.lower():
            return {"ticker": "COST", "title": "COSTCO WHOLESALE CORP /NEW",
                    "cik": "0000909832"}
        return None

    got = R("What was Costco's revenue in 2024?", registry_lookup=fake_registry)
    assert got.tickers == ("COST",)
    assert got.names["COST"] == "COSTCO WHOLESALE CORP /NEW"
    assert calls, "the registry was never consulted"


def test_the_registry_is_not_consulted_for_a_mention_already_resolved():
    calls = []

    def fake_registry(mention: str):
        calls.append(mention)
        return None

    R("What was Apple's revenue in fiscal 2024?", registry_lookup=fake_registry)
    assert calls == [], f"wasted lookups: {calls}"


def test_the_default_is_offline():
    """The default resolves nothing extra, so a unit test cannot reach SEC."""
    got = R("What was Costco's revenue in 2024?")
    assert got.tickers == ()
    assert "Costco" in got.unresolved


def test_a_registry_outage_does_not_lose_the_mentions_that_worked():
    def broken_registry(_mention: str):
        raise RuntimeError("SEC registry unreachable")

    got = R("Compare Apple and Costco revenue", registry_lookup=broken_registry)
    assert got.tickers == ("AAPL",)
    assert "Costco" in got.unresolved


# ── robustness ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", ["", "   ", "?", "2024", "$$$", "a" * 500])
def test_resolution_never_raises(question):
    resolve_entities(question)


def test_matched_phrases_are_reported_for_the_trace():
    got = R("What was AAPL's revenue?")
    assert got.matched["AAPL"].strip("$'s") == "AAPL"
