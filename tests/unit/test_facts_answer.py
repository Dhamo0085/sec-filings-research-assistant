"""Answer templates, as golden strings (T3-05, T3-09, P3-04).

Golden strings rather than "contains the number": the templates are the
user-facing contract, and the failure these guard against is a reword that
quietly drops the period-end date, the definition, or the restatement note.
An assertion that the output merely *contains* 391.04 would pass through all
three.

T3-09 (the Netflix thousands case) is here rather than in an end-to-end test
because the magnitude is decided at exactly this boundary: the resolver hands
over a fully scaled ``Decimal`` and the template formats it. v1's defect K2
was a prompt telling the model to "assume millions", which is a mistake no
template can make and no test above this layer would have caught.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from answering.facts_answer import (
    compare_answer,
    computed_answer,
    numeric_answer,
    partial_note,
    trend_answer,
)
from answering.outcome import CitationKind, QueryType, Status
from facts.calc import Operand, growth_pct, margin_pct
from facts.resolve import Resolution

pytestmark = pytest.mark.unit


def resolution(**over) -> Resolution:
    """Apple FY2024 revenue, the spec 6.5 worked example."""
    fields = {
        "ticker": "AAPL",
        "metric": "revenue",
        "metric_label": "Total net revenue",
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "value": Decimal("391035000000"),
        "unit": "USD",
        "period_type": "duration",
        "start_date": "2023-10-01",
        "end_date": "2024-09-28",
        "fiscal_label": 2024,
        "accession": "0000320193-24-000123",
        "filing_date": "2024-11-01",
        "form_type": "10-K",
        "source_doc": "aapl-20240928.htm",
        "selection": "single_candidate",
        "candidates_considered": ("us-gaap:Revenues",),
        "validation_status": "validated",
    }
    fields.update(over)
    return Resolution(**fields)


# ── numeric_fact ──────────────────────────────────────────────────────────────

def test_numeric_answer_is_exactly_this_string():
    out = numeric_answer(resolution(), company="Apple Inc.",
                         question="What were Apple's total net sales in fiscal 2024?")
    assert out.answer == (
        "Apple Inc.'s total net revenue for fiscal 2024 (year ended 2024-09-28) "
        "was $391.04 billion ($391,035,000,000). [1]"
    )
    assert out.status is Status.ANSWERED
    assert out.query_type is QueryType.NUMERIC_FACT


def test_the_definition_note_names_the_concept_and_period():
    """D5: the answer must always say which definition it used."""
    out = numeric_answer(resolution())
    assert out.definition_note == (
        "Total net revenue = "
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, "
        "fiscal year ended 2024-09-28"
    )


def test_the_citation_carries_the_decimal_as_a_string():
    out = numeric_answer(resolution())
    citation = out.citations[0]
    assert citation.kind is CitationKind.FACT
    assert citation.value == "391035000000"
    assert isinstance(citation.value, str)
    assert citation.period_end == "2024-09-28" and citation.filing_date == "2024-11-01"
    assert citation.restated is False


def test_the_period_end_is_always_stated_because_the_label_is_the_filers_own():
    """D6: Apple's fiscal 2024 ended in September, Microsoft's in June."""
    msft = numeric_answer(resolution(
        ticker="MSFT", end_date="2024-06-30", start_date="2023-07-01",
        accession="0000950170-24-087843", filing_date="2024-07-30",
    ), company="Microsoft Corporation")
    assert "fiscal 2024 (year ended 2024-06-30)" in msft.answer


def test_a_restated_figure_says_so_in_the_sentence_not_only_the_citation():
    out = numeric_answer(resolution(
        restated=True, form_type="10-K/A",
        accession="0000320193-25-000001", filing_date="2025-03-03",
    ))
    assert out.answer.endswith(
        "This figure is restated: it comes from 10-K/A 0000320193-25-000001, "
        "filed 2025-03-03, which superseded the original filing for fiscal 2024."
    )
    assert out.citations[0].restated is True


def test_an_amendment_that_changed_nothing_is_still_mentioned():
    """GS FY2023's 10-K/A carries 64 facts and no financial ones."""
    out = numeric_answer(resolution(amendment_accession="0000886982-24-000012"))
    assert ("An amendment to the fiscal 2024 filing exists "
            "(0000886982-24-000012) but does not restate this figure.") in out.answer


def test_an_as_of_query_says_what_it_was_limited_to():
    out = numeric_answer(resolution(), as_of="2025-01-15")
    assert out.answer.endswith("Only filings public on or before 2025-01-15 were used.")
    assert out.as_of == "2025-01-15"


# ── T3-09: the thousands-reporting filer ─────────────────────────────────────

def test_netflix_reports_in_thousands_and_the_answer_is_still_billions():
    """T3-09. Netflix tags revenue with scale=3; the extractor stores the
    fully scaled value, and the template must neither rescale nor abbreviate
    it wrongly. $39.00 billion, not $39.00 million and not $39,000,966."""
    out = numeric_answer(
        resolution(
            ticker="NFLX", value=Decimal("39000966000"), end_date="2024-12-31",
            start_date="2024-01-01", fiscal_label=2024,
            accession="0001065280-25-000044", filing_date="2025-01-27",
            concept="us-gaap:Revenues", metric_label="Total net revenue",
        ),
        company="Netflix, Inc.",
    )
    assert out.answer == (
        "Netflix, Inc.'s total net revenue for fiscal 2024 (year ended "
        "2024-12-31) was $39.00 billion ($39,000,966,000). [1]"
    )
    assert "million" not in out.answer
    assert out.citations[0].value == "39000966000"


def test_a_per_share_figure_is_never_abbreviated():
    out = numeric_answer(resolution(
        metric="eps_diluted", metric_label="Diluted earnings per share",
        value=Decimal("6.08"), unit="USD/shares", concept="us-gaap:EarningsPerShareDiluted",
    ))
    assert "$6.08 per share" in out.answer
    assert "billion" not in out.answer and "million" not in out.answer


def test_a_negative_figure_keeps_its_sign():
    out = numeric_answer(resolution(
        metric="net_income", metric_label="Net income",
        value=Decimal("-2722000000"), concept="us-gaap:NetIncomeLoss",
    ))
    assert "-$2.72 billion (-$2,722,000,000)" in out.answer


# ── computed ──────────────────────────────────────────────────────────────────

def test_a_computed_answer_shows_its_work():
    earlier = resolution(fiscal_label=2023, value=Decimal("383285000000"),
                         end_date="2023-09-30", start_date="2022-10-02",
                         accession="0000320193-23-000106", filing_date="2023-11-03")
    later = resolution()
    calc = growth_pct(Operand.from_resolution(later), Operand.from_resolution(earlier))
    out = computed_answer(calc, [earlier, later], company="Apple Inc.")
    assert out.query_type is QueryType.COMPUTED
    assert "2.02%" in out.answer
    assert calc.formula in out.answer, "the formula must be printed, not just the result"
    assert "[1] [2]" in out.answer
    assert len(out.citations) == 2


def test_a_margin_answer_names_both_inputs():
    revenue = resolution()
    operating = resolution(metric="operating_income",
                           metric_label="Operating income",
                           concept="us-gaap:OperatingIncomeLoss",
                           value=Decimal("123216000000"))
    calc = margin_pct(Operand.from_resolution(operating),
                      Operand.from_resolution(revenue))
    out = computed_answer(calc, [operating, revenue], company="Apple Inc.")
    assert "31.51%" in out.answer
    assert "Operating income" in out.definition_note
    assert "Total net revenue" in out.definition_note


# ── compare ───────────────────────────────────────────────────────────────────

def test_a_compare_answer_lists_each_company_with_its_own_period():
    aapl = resolution()
    msft = resolution(ticker="MSFT", value=Decimal("245122000000"),
                      end_date="2024-06-30", start_date="2023-07-01",
                      accession="0000950170-24-087843", filing_date="2024-07-30",
                      concept="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax")
    out = compare_answer([aapl, msft],
                         companies={"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation"})
    assert out.query_type is QueryType.COMPARE
    assert "- Apple Inc.: $391.04 billion ($391,035,000,000) for fiscal 2024 " \
           "(year ended 2024-09-28) [1]" in out.answer
    assert "- Microsoft Corporation: $245.12 billion ($245,122,000,000) for " \
           "fiscal 2024 (year ended 2024-06-30) [2]" in out.answer


def test_a_compare_answer_states_the_gap_and_that_the_years_differ():
    aapl = resolution()
    msft = resolution(ticker="MSFT", value=Decimal("245122000000"),
                      end_date="2024-06-30", accession="0000950170-24-087843",
                      filing_date="2024-07-30")
    out = compare_answer([aapl, msft],
                         companies={"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation"})
    assert "Apple Inc. reported $145.91 billion ($145,913,000,000) more than " \
           "Microsoft Corporation, 59.53% higher" in out.answer
    assert "fiscal years end on different dates" in out.answer


def test_a_compare_answer_does_not_subtract_across_different_units():
    """The calculator refuses a USD-vs-USD/shares subtraction; so must prose."""
    usd = resolution()
    per_share = resolution(ticker="MSFT", unit="USD/shares", value=Decimal("11.80"),
                           accession="0000950170-24-087843", filing_date="2024-07-30")
    out = compare_answer([usd, per_share])
    assert "more than" not in out.answer


# ── trend ─────────────────────────────────────────────────────────────────────

def test_a_trend_answer_is_ordered_oldest_first_and_states_the_change():
    years = [
        resolution(fiscal_label=2022, value=Decimal("394328000000"),
                   end_date="2022-09-24", accession="0000320193-22-000108",
                   filing_date="2022-10-28"),
        resolution(fiscal_label=2023, value=Decimal("383285000000"),
                   end_date="2023-09-30", accession="0000320193-23-000106",
                   filing_date="2023-11-03"),
        resolution(),
    ]
    out = trend_answer(list(reversed(years)), company="Apple Inc.")
    assert out.query_type is QueryType.TREND
    lines = out.answer.splitlines()
    assert lines[0] == "Apple Inc.'s total net revenue, fiscal 2022 to 2024:"
    assert lines[1].startswith("- fiscal 2022 (ended 2022-09-24)")
    assert lines[3].startswith("- fiscal 2024 (ended 2024-09-28)")
    assert [c.fiscal_label for c in out.citations] == [2022, 2023, 2024]
    assert "fell by $3.29 billion ($3,293,000,000)" in out.answer


def test_a_flat_trend_says_unchanged():
    a = resolution(fiscal_label=2023, end_date="2023-09-30",
                   accession="0000320193-23-000106", filing_date="2023-11-03")
    b = resolution()
    out = trend_answer([a, b])
    assert "was unchanged" in out.answer


# ── partial answers ───────────────────────────────────────────────────────────

def test_a_partial_answer_names_what_was_dropped():
    note = partial_note([("SpaceX", "no SEC filings"), ("fiscal 2019", "not indexed")])
    assert note == (" Not included: SpaceX (no SEC filings); "
                    "fiscal 2019 (not indexed).")
    assert partial_note([]) == ""


# ── the guarantees hold through the templates ────────────────────────────────

def test_every_template_produces_an_outcome_that_passes_its_own_validators():
    """G1-G4 are enforced in answering/outcome.py; this is the proof that the
    templates satisfy them rather than bypassing them."""
    out = numeric_answer(resolution())
    assert out.status is Status.ANSWERED
    assert out.citations and out.definition_note
    assert [c.index for c in out.citations] == [1]


def test_an_as_of_earlier_than_the_filing_cannot_be_built():
    """G2 is enforced by the Outcome, so a template cannot leak a look-ahead."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="look-ahead"):
        numeric_answer(resolution(), as_of="2024-06-30")


def test_a_computed_answer_reports_the_questions_own_query_type():
    """6.5's query_type describes the query, not the template that served it.

    "How did revenue grow from 2023 to 2024" is a trend question answered
    with a calculation; the smoke run (P3-11) caught the router saying trend
    and the response saying computed.
    """
    earlier = resolution(fiscal_label=2023, value=Decimal("383285000000"),
                         end_date="2023-09-30", accession="0000320193-23-000106",
                         filing_date="2023-11-03")
    later = resolution()
    calc = growth_pct(Operand.from_resolution(later), Operand.from_resolution(earlier))

    assert computed_answer(calc, [earlier, later]).query_type is QueryType.COMPUTED
    assert computed_answer(calc, [earlier, later],
                           query_type=QueryType.TREND).query_type is QueryType.TREND
