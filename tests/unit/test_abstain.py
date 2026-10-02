"""Every abstain reason is reachable, says the right thing, and claims nothing (T3-04).

The two properties that matter are checked over the whole enum rather than
case by case, so a reason added later cannot slip through without a message or
with a figure in it.
"""

from __future__ import annotations

import re

import pytest

from answering.abstain import (
    ERROR_MESSAGES,
    MESSAGES,
    abstain_for,
    clarification_no_company,
    error_for,
    message_for,
    reason_from_facts,
)
from answering.outcome import AbstainReason, ErrorCode, Status
from facts import resolve as facts_resolve

pytestmark = pytest.mark.unit

#: A monetary amount or a percentage. Deliberately not "any digit": a year, a
#: date and a count are what make a refusal useful, and banning them would
#: force the messages to be vaguer, not safer.
_FIGURE = re.compile(r"\$\s*[\d,]+|\b\d[\d,]*\.\d+\b|\b\d[\d,]*\s*%|\b\d{1,3}(?:,\d{3})+\b")


# ── completeness ──────────────────────────────────────────────────────────────

def test_every_abstain_reason_has_a_message():
    """Adding a reason without a message must fail the build, not ship a
    refusal that says None."""
    assert set(MESSAGES) == set(AbstainReason)


def test_every_error_code_has_a_message():
    assert set(ERROR_MESSAGES) == set(ErrorCode)


@pytest.mark.parametrize("reason", list(AbstainReason))
def test_every_reason_is_reachable_and_renders(reason):
    out = abstain_for(reason, company="Apple Inc.", ticker="AAPL",
                      period="fiscal 2024", metric="revenue")
    assert out.status is Status.ABSTAINED
    assert out.abstain_reason is reason
    assert out.answer.strip()
    assert out.citations == [], "an abstention that cites sources reads as an answer"


@pytest.mark.parametrize("reason", list(AbstainReason))
def test_no_abstention_contains_a_figure(reason):
    """A number in a refusal gets read as the answer."""
    text = message_for(reason, company="Apple Inc.", period="fiscal 2024",
                       metric="revenue", as_of="2024-06-30",
                       filing_date="2024-11-01", available=[2022, 2023, 2024, 2025])
    found = _FIGURE.findall(text)
    assert not found, f"{reason.value} message contains a figure: {found}"


@pytest.mark.parametrize("reason", list(AbstainReason))
def test_a_message_renders_with_no_facts_at_all(reason):
    """A refusal that crashes while explaining itself is the worst outcome."""
    text = message_for(reason)
    assert text.strip() and "{" not in text


# ── the messages say something useful ────────────────────────────────────────

def test_a_look_ahead_refusal_explains_the_point_in_time_rule():
    text = message_for(
        AbstainReason.PERIOD_NOT_FILED_AS_OF, company="Apple Inc.",
        period="fiscal 2024", as_of="2024-06-30", filing_date="2024-11-01",
    )
    assert "2024-06-30" in text and "2024-11-01" in text
    assert "did not exist on the date you asked about" in text


def test_a_period_refusal_offers_what_is_available():
    text = message_for(AbstainReason.PERIOD_NOT_COVERED, company="Apple Inc.",
                       period="fiscal 2019", available=[2022, 2023, 2024, 2025])
    assert "I do have fiscal 2022 to 2025." in text


def test_a_non_contiguous_available_range_is_listed_not_collapsed():
    text = message_for(AbstainReason.PERIOD_NOT_COVERED, company="BlackRock Inc.",
                       period="fiscal 2019", available=[2023, 2025])
    assert "fiscal 2023, 2025" in text


def test_a_single_available_year_reads_naturally():
    text = message_for(AbstainReason.PERIOD_NOT_COVERED, company="X",
                       period="fiscal 2019", available=[2024])
    assert "I do have fiscal 2024." in text


def test_no_available_years_adds_no_dangling_sentence():
    text = message_for(AbstainReason.PERIOD_NOT_COVERED, company="X",
                       period="fiscal 2019")
    assert "I do have" not in text


def test_an_ambiguity_refusal_refuses_to_choose_and_says_so():
    text = message_for(AbstainReason.AMBIGUOUS_CONCEPT, company="BlackRock Inc.",
                       period="fiscal 2024", metric="revenue",
                       candidates="us-gaap:Revenues and "
                                  "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax")
    assert "I won't choose between them" in text
    assert "us-gaap:Revenues" in text


def test_an_unsupported_metric_refusal_points_at_the_text_path():
    text = message_for(AbstainReason.METRIC_NOT_SUPPORTED, metric="deferred revenue",
                       supported="revenue, net income and ten other line items")
    assert "what the filing *says* about it" in text


def test_a_missing_metric_refusal_says_the_gap_is_probably_the_filing():
    text = message_for(AbstainReason.METRIC_NOT_FOUND_IN_FILING,
                       company="JPMorgan Chase & Co.", period="fiscal 2024",
                       metric="gross profit")
    assert "banks report no gross profit" in text


# ── errors are not abstentions ───────────────────────────────────────────────

@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_code_produces_a_503_outcome(code):
    out = error_for(code)
    assert out.status is Status.ERROR
    assert out.error_code is code
    assert out.http_status == 503
    assert out.abstain_reason is None


def test_an_outage_is_not_dressed_up_as_a_clarification():
    """v1's F1: every failure rendered as "Which company are you asking about?"."""
    outage = error_for(ErrorCode.LLM_UNAVAILABLE)
    clarify = clarification_no_company()
    assert outage.answer != clarify.answer
    assert clarify.error_code is None and outage.error_code is not None
    assert clarify.status is Status.CLARIFICATION_NEEDED


def test_the_clarification_is_only_for_a_missing_company():
    out = clarification_no_company(query="What was revenue in 2024?")
    assert "Which company" in out.answer
    assert out.query == "What was revenue in 2024?"


# ── the facts layer and the 6.5 enum must not drift apart ────────────────────

@pytest.mark.parametrize("constant", [
    name for name in dir(facts_resolve) if name.startswith("REASON_")
])
def test_every_facts_layer_reason_maps_onto_the_enum(constant):
    """facts/resolve.py has its own REASON_* strings; if one is renamed and
    the enum is not, this fails instead of a refusal losing its reason."""
    value = getattr(facts_resolve, constant)
    assert reason_from_facts(value) is AbstainReason(value)


def test_an_unknown_reason_string_raises_rather_than_being_passed_through():
    with pytest.raises(ValueError, match=r"not in the 6\.5 enum"):
        reason_from_facts("something_new")
