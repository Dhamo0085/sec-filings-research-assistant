"""Period parsing phrase cases (T3-01, P3-02).

The clock is injected in every case. A test that calls ``date.today()`` passes
this year and fails next year, and "last year" is exactly the kind of thing
that only breaks in January.
"""

from __future__ import annotations

from datetime import date

import pytest

from answering.outcome import AbstainReason
from routing.periods import PeriodKind, parse_period

pytestmark = pytest.mark.unit

#: A fixed "today" for every case. Picked after Microsoft's FY2026 10-K
#: (filed 2026-07-29) so FY2026 is a real, already-filed year here.
TODAY = date(2026, 10, 2)


def P(question: str):
    return parse_period(question, today=TODAY)


# ── fiscal-year phrasings ─────────────────────────────────────────────────────

@pytest.mark.parametrize("question,labels", [
    ("What was Apple's revenue in fiscal 2024?", (2024,)),
    ("What was Apple's revenue in fiscal year 2024?", (2024,)),
    ("What was Apple's revenue in FY2024?", (2024,)),
    ("What was Apple's revenue in FY 2024?", (2024,)),
    ("What was Apple's revenue in fy-2024?", (2024,)),
    ("What was Apple's revenue in FY24?", (2024,)),
    ("Revenue for fiscal 2023 and fiscal 2024", (2023, 2024)),
    ("Net income in fiscal year 2025", (2025,)),
])
def test_fiscal_phrasings_resolve_to_the_filers_own_label(question, labels):
    got = P(question)
    assert got.kind is PeriodKind.FISCAL_LABEL
    assert got.labels == labels
    assert got.bare is False, "an explicit 'fiscal' phrasing is not a bare year"
    assert got.abstain_reason is None


@pytest.mark.parametrize("question,labels", [
    ("What was Apple's revenue in 2024?", (2024,)),
    ("Apple revenue 2024", (2024,)),
    ("Compare 2023 and 2024 net income", (2023, 2024)),
    ("What did Apple report for 2019?", (2019,)),
])
def test_a_bare_year_is_read_as_the_fiscal_label_and_marked_bare(question, labels):
    got = P(question)
    assert got.kind is PeriodKind.FISCAL_LABEL
    assert got.labels == labels
    assert got.bare is True, (
        "the answer template needs to know the year was bare so it can state "
        "the period-end date (D6)"
    )


@pytest.mark.parametrize("question,labels", [
    ("What was Apple's revenue in calendar 2024?", (2024,)),
    ("What was Apple's revenue in calendar year 2024?", (2024,)),
])
def test_calendar_phrasing_is_kept_distinct_from_fiscal(question, labels):
    """D6: a calendar-year phrasing maps through period_end, not the label."""
    got = P(question)
    assert got.kind is PeriodKind.CALENDAR_YEAR
    assert got.labels == labels


def test_fiscal_wins_over_a_bare_year_in_the_same_question():
    got = P("In fiscal 2024, how did that compare with the 2023 figure?")
    assert got.kind is PeriodKind.FISCAL_LABEL
    assert got.labels == (2024,)
    assert got.bare is False


# ── ranges ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "How did Apple's operating margin change from 2022 to 2024?",
    "Apple operating margin 2022 to 2024",
    "Apple operating margin 2022-2024",
    "Apple operating margin 2022–2024",
    "Apple operating margin between 2022 and 2024",
    "Apple operating margin from 2022 through 2024",
    "Apple margin from fiscal 2022 to fiscal 2024",
])
def test_ranges_expand_to_every_year_inclusive(question):
    got = P(question)
    assert got.kind is PeriodKind.FISCAL_LABEL
    assert got.labels == (2022, 2023, 2024)
    assert got.is_range is True


def test_a_backwards_range_is_normalised():
    assert P("revenue from 2024 to 2022").labels == (2022, 2023, 2024)


def test_a_range_beats_the_bare_year_reading():
    """Without range handling, "2022 to 2024" would resolve to (2022, 2024)."""
    assert P("revenue 2022 to 2024").labels == (2022, 2023, 2024)


# ── relative phrases (the injected clock) ─────────────────────────────────────

def test_last_year_uses_the_injected_clock():
    got = P("What was Apple's revenue last year?")
    assert got.kind is PeriodKind.FISCAL_LABEL
    assert got.labels == (TODAY.year - 1,)
    assert got.relative is True


def test_the_clock_really_is_injected():
    """The same question, two clocks, two answers."""
    a = parse_period("revenue last year", today=date(2024, 5, 1))
    b = parse_period("revenue last year", today=date(2026, 5, 1))
    assert a.labels == (2023,) and b.labels == (2025,)


@pytest.mark.parametrize("question,expected", [
    ("Apple revenue this year", (2026,)),
    ("Apple revenue in the prior year", (2025,)),
    ("Apple revenue in the previous year", (2025,)),
])
def test_other_relative_single_years(question, expected):
    got = P(question)
    assert got.labels == expected and got.relative is True


@pytest.mark.parametrize("question,expected", [
    ("How has Apple's revenue moved over the last three years?", (2023, 2024, 2025)),
    ("Apple revenue over the past 3 years", (2023, 2024, 2025)),
    ("Apple revenue for the last two years", (2024, 2025)),
    ("Apple revenue trailing five years", (2021, 2022, 2023, 2024, 2025)),
])
def test_relative_spans(question, expected):
    got = P(question)
    assert got.kind is PeriodKind.FISCAL_LABEL
    assert got.labels == expected and got.relative is True


# ── "latest" ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "What was Apple's latest annual revenue?",
    "What is Apple's most recent reported revenue?",
    "Apple's most recently reported net income",
    "What was Apple's revenue in its last 10-K?",
    "Apple revenue, last reported",
    "Apple's current annual revenue",
])
def test_latest_defers_to_the_catalog_rather_than_the_clock(question):
    got = P(question)
    assert got.kind is PeriodKind.LATEST
    assert got.labels == ()
    assert got.relative is False, (
        "'latest' must be resolved against the catalog at as_of, not against "
        "the clock, or an as_of query answers with a later filing"
    )


# ── as_of ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,expected", [
    ("As of March 1, 2024, what was Apple's latest annual revenue?", "2024-03-01"),
    ("As of March 1 2024, what was Apple's latest annual revenue?", "2024-03-01"),
    ("As of 1 March 2024, what was Apple's latest annual revenue?", "2024-03-01"),
    ("As of 2024-03-01, what was Apple's latest annual revenue?", "2024-03-01"),
    ("as at 2024-03-01, what was Apple's revenue?", "2024-03-01"),
    ("As of Dec 31, 2023, what was Apple's latest revenue?", "2023-12-31"),
    ("As of September 28, 2024 what did Apple report?", "2024-09-28"),
])
def test_as_of_dates(question, expected):
    assert P(question).as_of == expected


def test_the_as_of_year_is_not_also_taken_as_the_period():
    """The distinction the module exists for."""
    got = P("As of March 1, 2024, what was Apple's latest annual revenue?")
    assert got.as_of == "2024-03-01"
    assert got.kind is PeriodKind.LATEST
    assert got.labels == (), "2024 came from the as_of clause, not the question"


def test_as_of_combines_with_an_explicit_fiscal_year():
    got = P("As of 2024-03-01, what was Apple's fiscal 2023 revenue?")
    assert got.as_of == "2024-03-01"
    assert got.kind is PeriodKind.FISCAL_LABEL and got.labels == (2023,)


def test_a_date_with_no_as_of_lead_is_not_a_cutoff():
    got = P("What was Apple's revenue for fiscal 2024?")
    assert got.as_of is None


def test_a_period_end_date_is_a_period_not_a_cutoff():
    got = P("What was Apple's revenue for the fiscal year ended September 28, 2024?")
    assert got.as_of is None
    assert got.kind is PeriodKind.PERIOD_END
    assert got.period_end == "2024-09-28"


@pytest.mark.parametrize("question", [
    "What was Apple's revenue for the year ended 2024-09-28?",
    "Apple revenue for the fiscal year ending September 28, 2024",
])
def test_period_end_phrasings(question):
    got = P(question)
    assert got.kind is PeriodKind.PERIOD_END and got.period_end == "2024-09-28"


def test_an_impossible_date_is_ignored_rather_than_crashing():
    got = P("As of February 31, 2024, what was Apple's revenue?")
    assert got.as_of is None


def test_a_slashed_date_is_not_guessed():
    """03/01/2024 is 1 March or 3 January depending on the reader."""
    assert P("As of 03/01/2024, what was Apple's revenue?").as_of is None


# ── sub-annual: refuse, do not approximate (D10) ──────────────────────────────

@pytest.mark.parametrize("question", [
    "What was Apple's Q3 revenue?",
    "What was Apple's Q3 2024 revenue?",
    "What was Apple's revenue in the first quarter of 2024?",
    "Show me Apple's quarterly revenue",
    "What did Apple report for the quarter ended June 2024?",
    "What was in Apple's latest 10-Q?",
    "Apple revenue for the three months ended June 29, 2024",
    "Apple revenue for the six months ending March 2024",
    "Apple revenue H1 2024",
    "Apple revenue in the first half of 2024",
    "Apple's interim results for 2024",
])
def test_sub_annual_requests_abstain_with_unsupported_period_type(question):
    got = P(question)
    assert got.abstain_reason is AbstainReason.UNSUPPORTED_PERIOD_TYPE
    assert got.kind is PeriodKind.NONE
    assert got.labels == (), (
        "a quarterly question that resolves to the annual figure is a wrong "
        "answer, not a refusal (D10)"
    )


def test_headquarters_is_not_a_quarterly_request():
    """The obvious false positive for a 'quarter' pattern."""
    got = P("Where are Apple's headquarters in fiscal 2024?")
    assert got.abstain_reason is None
    assert got.labels == (2024,)


def test_a_quarter_mentioned_as_a_word_alone_is_not_sub_annual():
    got = P("Did Apple report a record quarter growth narrative in fiscal 2024?")
    assert got.abstain_reason is None


# ── out-of-range years ────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,reason", [
    ("What will Apple's revenue be in 2030?", AbstainReason.FUTURE_PERIOD),
    ("What was Apple's revenue in fiscal 2028?", AbstainReason.FUTURE_PERIOD),
    ("What was Apple's revenue in 1985?", AbstainReason.PERIOD_NOT_COVERED),
    ("Apple revenue from 1985 to 1990", AbstainReason.PERIOD_NOT_COVERED),
])
def test_years_outside_the_filing_horizon_abstain_with_a_reason(question, reason):
    got = P(question)
    assert got.abstain_reason is reason
    assert got.labels == (), "an unservable year must not leak into the query"


def test_next_fiscal_year_is_allowed_because_filers_label_ahead():
    """Microsoft's FY2026 10-K was filed 2026-07-29."""
    assert parse_period("MSFT revenue fiscal 2026", today=date(2026, 1, 5)).labels == (2026,)
    assert parse_period("MSFT revenue fiscal 2027", today=date(2026, 1, 5)).labels == (2027,)


# ── no period named ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("question", [
    "What risks does Apple disclose?",
    "What does Apple sell?",
    "",
])
def test_no_period_named(question):
    got = P(question)
    assert got.kind is PeriodKind.NONE
    assert got.labels == () and got.as_of is None and got.abstain_reason is None


# ── reporting ─────────────────────────────────────────────────────────────────

def test_the_matched_phrases_are_reported_for_the_trace():
    got = P("As of 2024-03-01, what was Apple's fiscal 2023 revenue?")
    joined = " | ".join(got.matched)
    assert "2024-03-01" in joined and "fiscal 2023" in joined.lower()


@pytest.mark.parametrize("question,expected", [
    ("Apple revenue fiscal 2024", "fiscal 2024"),
    ("Apple revenue calendar 2024", "calendar 2024"),
    ("Apple revenue 2022 to 2024", "fiscal 2022 to 2024"),
    ("Apple latest revenue", "the latest filed year"),
    ("Apple revenue for the year ended 2024-09-28", "the year ending 2024-09-28"),
    ("Apple Q3 revenue", "unsupported period (unsupported_period_type)"),
    ("What does Apple sell?", "no period named"),
])
def test_describe_is_readable(question, expected):
    assert P(question).describe() == expected


def test_parsing_never_raises_on_odd_input():
    for question in ["2024", "fy", "as of", "Q", "----", "99999", "fiscal 20244"]:
        parse_period(question, today=TODAY)
