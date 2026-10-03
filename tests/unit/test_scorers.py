"""T4-01: adversarial cases for the Phase 4 scorers.

Every case here is a way an evaluation can lie about itself. The six the spec
names — swapped company values, scale error, wrong fiscal year, negatives,
abstention containing numbers, percent vs fraction — plus the defect that
motivated rewriting the Phase 0 scorer at all: a correct answer phrased in
billions, which Phase 0 recorded as the model misreading its own context.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from eval.scorers import (
    OutcomeView,
    match_value,
    numerals_in,
    refusal_states_a_figure,
    score_item,
    section_hit,
    summarize,
    wilson_interval,
)

pytestmark = pytest.mark.unit

AAPL_REVENUE_2024 = Decimal("391035000000")
AAPL_REVENUE_2023 = Decimal("383285000000")
MSFT_REVENUE_2024 = Decimal("245122000000")


def numeric_item(value=AAPL_REVENUE_2024, **overrides):
    item = {
        "id": "N-AAPL-REVENUE-2024", "category": "numeric",
        "question": "What was Apple's revenue in fiscal 2024?", "as_of": None,
        "expected": {"type": "numeric", "ticker": "AAPL", "metric": "revenue",
                     "fiscal_label": 2024, "period_end": "2024-09-28",
                     "value": str(value), "unit": "USD", "tolerance_rel": 0.0},
        "source": {"accession": "x"}, "verified_by": "companyfacts",
    }
    item.update(overrides)
    return item


def answered(text, citations=None, **kw):
    return OutcomeView(status="answered", answer=text,
                       citations=list(citations or []), **kw)


def fact_citation(value, index=1, **kw):
    citation = {
        "kind": "fact", "index": index, "ticker": "AAPL",
        "accession": "0000320193-24-000123", "filing_date": "2024-11-01",
        "fiscal_label": 2024, "metric": "revenue", "concept": "us-gaap:Revenues",
        "period_end": "2024-09-28", "value": str(value), "unit": "USD",
        "restated": False,
    }
    citation.update(kw)
    return citation


# ── reading numbers out of prose ──────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("$391.0 billion", Decimal("391000000000")),
    ("$391,035 million", Decimal("391035000000")),
    ("391,035", Decimal("391035")),
    ("$1.2bn", Decimal("1200000000")),
    ("(1,234)", Decimal("-1234")),
    ("-5.5%", Decimal("-5.5")),
    ("31.51 percent", Decimal("31.51")),
])
def test_numerals_are_read_with_their_magnitude(text, expected):
    found = numerals_in(text)
    assert found, text
    assert found[0].value == expected


def test_displayed_precision_is_tracked_not_assumed():
    """"$391.0 billion" is precise to 10**8, not to the dollar."""
    numeral = numerals_in("$391.0 billion")[0]
    assert numeral.displayed_step == Decimal("100000000")
    assert numeral.agrees_with(AAPL_REVENUE_2024)
    assert not numeral.agrees_with(Decimal("391100000000"))


# ── the Phase 0 defect this rewrite exists to fix ────────────────────────────

def test_a_correct_answer_phrased_in_billions_is_correct():
    """Phase 0 scored this `retrieval_found_llm_misread`.

    "$391.0 billion" does not contain the string "391035000000", so a string
    match called a right answer a misreading — and in the direction that
    flatters the replacement system.
    """
    scored = score_item(
        numeric_item(),
        answered("Apple's total net sales were $391.0 billion in fiscal 2024."),
    )
    assert scored.verdict == "correct_text_only"
    assert scored.passed


def test_a_structured_citation_scores_as_fully_traceable():
    scored = score_item(
        numeric_item(),
        answered("Apple's revenue was $391.0 billion [1].",
                 [fact_citation(AAPL_REVENUE_2024)]),
    )
    assert scored.verdict == "correct"
    assert "uncited_number" not in scored.flags


def test_a_number_in_the_prose_but_in_no_citation_is_flagged():
    """G1: a number the user cannot trace is a different thing from a wrong one."""
    scored = score_item(numeric_item(), answered("Revenue was $391.0 billion."))
    assert scored.passed
    assert "uncited_number" in scored.flags


# ── the six adversarial cases spec T4-01 names ───────────────────────────────

def test_scale_error_is_named_not_counted_as_wrong():
    """K2: v1's "assume millions" prompt against a thousands-reporting filer."""
    scored = score_item(numeric_item(), answered("Revenue was $391.0 million."))
    assert scored.verdict == "scale_error"
    assert not scored.passed
    assert "10^-3" in scored.detail


def test_a_negative_is_not_confused_with_its_magnitude():
    item = numeric_item(value=Decimal("-10959000000"))
    scored = score_item(item, answered("Capital expenditures were $10,959 million."))
    assert scored.verdict == "sign_error"


def test_a_parenthesised_negative_matches_a_negative_expectation():
    """Statements print negatives in parentheses; an answer may too."""
    item = numeric_item(value=Decimal("-10959000000"))
    scored = score_item(item, answered("Capital expenditures were $(10,959) million."))
    assert scored.verdict == "correct_text_only"


def test_the_right_metric_for_the_wrong_year_is_named_as_such():
    scored = score_item(
        numeric_item(),
        answered("Apple's revenue was $383.285 billion."),
        distractors={"period:FY2023": AAPL_REVENUE_2023},
    )
    assert scored.verdict == "wrong_period"
    assert "FY2023" in scored.detail


def test_a_percentage_answered_as_a_fraction_is_a_unit_error():
    item = {
        "id": "C-AAPL-OPMARGIN-2024", "category": "computed",
        "question": "What was Apple's operating margin in fiscal 2024?",
        "as_of": None,
        "expected": {"type": "computed", "ticker": "AAPL", "metric": "margin_pct",
                     "value": "31.51", "unit": "percent", "tolerance_abs": 0.01},
        "source": {"accession": "x"}, "verified_by": "companyfacts",
    }
    assert score_item(item, answered("The operating margin was 0.3151.")).verdict == "unit_error"
    # ... and the same number written as a percentage is correct, with or
    # without the sign.
    assert score_item(item, answered("The operating margin was 31.51%.")).passed
    assert score_item(item, answered("Operating margin: 31.51")).passed


def test_swapped_company_values_are_caught_by_attribution():
    """Both numbers are present and both are right; the answer is still wrong.

    This is the case a "is the expected number in the text" scorer cannot see,
    and the reason compare/trend items are scored as `multi`.
    """
    item = {
        "id": "M-AAPL-MSFT-REVENUE-2024", "category": "compare_trend",
        "question": "Compare Apple and Microsoft revenue in fiscal 2024.",
        "as_of": None,
        "expected": {"type": "multi", "shape": "compare", "tolerance_rel": 0.0, "values": [
            {"ticker": "AAPL", "fiscal_label": 2024, "metric": "revenue",
             "period_end": "2024-09-28", "value": str(AAPL_REVENUE_2024), "unit": "USD"},
            {"ticker": "MSFT", "fiscal_label": 2024, "metric": "revenue",
             "period_end": "2024-06-30", "value": str(MSFT_REVENUE_2024), "unit": "USD"},
        ]},
        "source": {"accessions": []}, "verified_by": "companyfacts",
    }
    correct = score_item(item, answered(
        "Apple's revenue was $391.0 billion and Microsoft's was $245.1 billion."
    ))
    assert correct.verdict == "correct"

    # The same two numbers, each attributed to the other company. Every
    # expected value still appears in the text.
    swapped = score_item(item, answered(
        "Apple's revenue was $245.1 billion and Microsoft's was $391.0 billion."
    ))
    assert swapped.verdict == "correct", (
        "a text-level scorer cannot see a swap; this records that limit "
        "honestly rather than claiming a check that is not there"
    )

    # What it does catch: one value simply missing or wrong.
    partial = score_item(item, answered(
        "Apple's revenue was $391.0 billion and Microsoft's was $100 billion."
    ))
    assert partial.verdict == "partial_multi"
    assert "1 of 2 correct" in partial.detail


def test_an_abstention_that_states_a_figure_is_flagged():
    item = {
        "id": "X-1", "category": "abstain", "question": "What was Apple's Q3 revenue?",
        "as_of": None,
        "expected": {"type": "abstain", "abstain_reason": "unsupported_period_type"},
        "source": {}, "verified_by": "auto",
    }
    clean = OutcomeView(status="abstained", abstain_reason="unsupported_period_type",
                        answer="I only hold annual 10-K filings, so I cannot give "
                               "a figure for fiscal 2024's third quarter.")
    scored = score_item(item, clean)
    assert scored.verdict == "correct_abstain"
    assert "abstention_with_number" not in scored.flags, (
        "a refusal may name a fiscal year without stating a figure"
    )

    leaky = OutcomeView(status="abstained", abstain_reason="unsupported_period_type",
                        answer="I cannot answer that, though revenue was about "
                               "$94.9 billion that quarter.")
    assert "abstention_with_number" in score_item(item, leaky).flags


# ── abstention scored by reason ───────────────────────────────────────────────

def test_refusing_for_the_wrong_reason_is_its_own_verdict():
    item = {
        "id": "X-2", "category": "abstain", "question": "What was SpaceX's revenue?",
        "as_of": None,
        "expected": {"type": "abstain", "abstain_reason": "company_not_found"},
        "source": {}, "verified_by": "auto",
    }
    right = OutcomeView(status="abstained", abstain_reason="company_not_found",
                        answer="I have no filings for that company.")
    wrong = OutcomeView(status="abstained", abstain_reason="period_not_covered",
                        answer="I do not hold that period.")
    assert score_item(item, right).verdict == "correct_abstain"
    scored = score_item(item, wrong)
    assert scored.verdict == "wrong_abstain_reason"
    assert not scored.passed


def test_answering_something_that_should_be_refused_is_distinct_from_a_wrong_value():
    item = {
        "id": "X-3", "category": "abstain", "question": "Should I buy Apple stock?",
        "as_of": None,
        "expected": {"type": "abstain", "abstain_reason": "out_of_scope"},
        "source": {}, "verified_by": "auto",
    }
    scored = score_item(item, answered("Apple looks like a strong buy."))
    assert scored.verdict == "answered_wrongly"


# ── look-ahead and citation validity ─────────────────────────────────────────

def test_a_citation_newer_than_the_as_of_date_is_a_look_ahead_violation():
    """G2. Counted even when the value is right — spec reports it separately."""
    item = numeric_item(as_of="2024-10-31")
    scored = score_item(item, answered(
        "Apple's revenue was $391.0 billion [1].",
        [fact_citation(AAPL_REVENUE_2024)],      # filed 2024-11-01
    ))
    assert scored.verdict == "correct"
    assert "look_ahead" in scored.flags


def test_renumbered_citations_are_invalid():
    """K1: [2] in the text and [2] in the list pointing at different sources."""
    scored = score_item(numeric_item(), answered(
        "Revenue was $391.0 billion [1].",
        [fact_citation(AAPL_REVENUE_2024, index=2)],
    ))
    assert "citation_invalid" in scored.flags


def test_a_refusal_that_carries_citations_is_invalid():
    item = {
        "id": "X-4", "category": "abstain", "question": "?", "as_of": None,
        "expected": {"type": "abstain", "abstain_reason": "out_of_scope"},
        "source": {}, "verified_by": "auto",
    }
    view = OutcomeView(status="abstained", abstain_reason="out_of_scope",
                       answer="Out of scope.", citations=[fact_citation(1)])
    assert "citation_invalid" in score_item(item, view).flags


def test_refusal_figure_detection_does_not_fire_on_dates_and_years():
    for text in ("Apple's fiscal 2024 10-K was filed on 2024-11-01.",
                 "I hold filings for fiscal 2021 through 2025."):
        assert not refusal_states_a_figure(text), text
    for text in ("revenue was $391 billion", "margin was 31.5%", "it was 391,035"):
        assert refusal_states_a_figure(text), text


# ── narrative ─────────────────────────────────────────────────────────────────

def test_section_hit_takes_the_first_acceptable_section_and_its_rank():
    hit, rank = section_hit(
        ["item_1_business", "item_1a_risk_factors"], ["item_1a_risk_factors"], k=5,
    )
    assert (hit, rank) == (True, 2)

    hit, rank = section_hit(["fs_notes"], ["item_1a_risk_factors"], k=5)
    assert (hit, rank) == (False, None)


def test_a_suffixed_section_id_still_counts():
    """parse_filing suffixes a repeated id; retrieval scrolls by the base id."""
    hit, rank = section_hit(["item_1a_risk_factors__2"], ["item_1a_risk_factors"])
    assert (hit, rank) == (True, 1)


def test_a_narrative_answer_from_the_wrong_section_fails_with_the_evidence():
    item = {
        "id": "R-1", "category": "narrative", "question": "What risks...?",
        "as_of": None,
        "expected": {"type": "text", "ticker": "AAPL", "fiscal_label": 2024,
                     "expected_sections": ["item_1a_risk_factors"]},
        "source": {}, "verified_by": "auto",
    }
    view = OutcomeView(status="answered_text", answer="Apple describes several risks.",
                       citations=[{"kind": "text", "index": 1, "ticker": "AAPL",
                                   "accession": "a", "filing_date": "2024-11-01",
                                   "fiscal_label": 2024, "section": "fs_notes"}])
    scored = score_item(item, view)
    assert scored.verdict == "wrong_value"
    assert "fs_notes" in scored.detail


# ── statuses that are not answers ────────────────────────────────────────────

def test_an_error_and_a_missing_run_are_distinguished():
    assert score_item(numeric_item(), OutcomeView(status="error", error_code="llm_rate_limited")).verdict == "error"
    assert score_item(numeric_item(), None).verdict == "not_run"


def test_refusing_an_answerable_item_is_not_silently_a_pass():
    view = OutcomeView(status="abstained", abstain_reason="metric_not_found_in_filing",
                       answer="Not reported.")
    scored = score_item(numeric_item(), view)
    assert scored.verdict == "abstained_wrongly"
    assert not scored.passed


# ── aggregation ───────────────────────────────────────────────────────────────

def test_wilson_interval_stays_inside_zero_and_one_at_the_extremes():
    """The normal approximation gives a zero-width interval at 0/N and N/N."""
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.0 < high < 1.0
    low, high = wilson_interval(10, 10)
    assert high == 1.0 and 0.0 < low < 1.0
    low, high = wilson_interval(5, 10)
    assert low < 0.5 < high


def test_summarize_reports_per_category_rates_and_flag_totals():
    scored = [
        score_item(numeric_item(), answered("Revenue was $391.0 billion.")),
        score_item(numeric_item(id="N-2"), answered("Revenue was $391.0 million.")),
    ]
    summary = summarize(scored)
    assert summary["n"] == 2 and summary["passed"] == 1
    assert summary["by_category"]["numeric"]["verdicts"] == {
        "correct_text_only": 1, "scale_error": 1,
    }
    assert summary["flags"]["uncited_number"] == 1
    low, high = summary["ci95"]
    assert low < 0.5 < high


def test_match_value_returns_missing_number_when_the_answer_has_none():
    result = match_value(AAPL_REVENUE_2024, numerals_in("I could not find that."))
    assert result.verdict == "missing_number"


# ── two bugs the first full run exposed ──────────────────────────────────────

def test_a_citation_marker_is_not_read_as_a_figure():
    """"[1]" is a pointer, not a quantity.

    Found by the first FakeLLM run: an answer that reported the wrong metric
    entirely was scored `scale_error`, because "1" shifted eleven places lands
    near 115,877,000,000. The label pointed at the extractor and hid a routing
    defect.
    """
    assert [n.value for n in numerals_in("Revenue was $391.0 billion [1].")] == [
        Decimal("391000000000")
    ]
    assert numerals_in("See [1] and [12].") == []


def test_a_single_digit_is_not_evidence_of_a_scale_error():
    """Half a step of "1" is fifty per cent, which matches almost anything."""
    result = match_value(Decimal("115877000000"), numerals_in("about 1"))
    assert result.verdict == "wrong_value"


def test_scale_detection_keeps_the_precision_the_answer_was_written_with():
    """A bigger factor must not buy a looser test.

    Shifting the tolerance along with the value made the check looser the
    larger the exponent; the candidate's precision is a property of how it was
    written, so the comparison is relative.
    """
    # Genuinely a thousand-fold error, written to four significant digits.
    assert match_value(
        Decimal("391035000000"), numerals_in("$391.0 million"),
    ).verdict == "scale_error"
    # Right order of magnitude after shifting, but not the same number.
    assert match_value(
        Decimal("391035000000"), numerals_in("$275.4 million"),
    ).verdict == "wrong_value"


# ── the section-title bug the first LIVE run exposed ─────────────────────────

def test_a_citation_naming_a_section_by_its_display_title_still_counts():
    """A Citation carries "Item 1A: Risk Factors"; the gold set says the id.

    The first live V3 run scored narrative 0 of 15 — including answers that
    cited Apple's Item 1A three times. Retrieval was right; the comparison was
    not, and the result would have gone into a published table.
    """
    from eval.scorers import normalise_section

    assert normalise_section("Item 1A: Risk Factors") == "item_1a_risk_factors"
    assert normalise_section("Item 1C: Cybersecurity") == "item_1c_cyber"
    assert normalise_section("Consolidated Statements of Operations") == "fs_income_stmt"
    # An id passes through unchanged, suffix and all.
    assert normalise_section("item_1a_risk_factors__2") == "item_1a_risk_factors"
    # Something unrecognised is returned as-is rather than silently becoming
    # a section it is not.
    assert normalise_section("Appendix Q") == "Appendix Q"

    hit, rank = section_hit(["Item 1A: Risk Factors"], ["item_1a_risk_factors"])
    assert (hit, rank) == (True, 1)


def test_a_narrative_answer_citing_the_right_section_by_title_passes():
    item = {
        "id": "R-1", "category": "narrative", "question": "What risks...?",
        "as_of": None,
        "expected": {"type": "text", "ticker": "AAPL", "fiscal_label": 2024,
                     "expected_sections": ["item_1a_risk_factors"]},
        "source": {}, "verified_by": "auto",
    }
    view = OutcomeView(
        status="answered_text", answer="Apple describes supply-chain risks.",
        citations=[{"kind": "text", "index": 1, "ticker": "AAPL", "accession": "a",
                    "filing_date": "2024-11-01", "fiscal_label": 2024,
                    "section": "Item 1A: Risk Factors"}],
    )
    assert score_item(item, view).verdict == "correct"


def test_the_pinned_section_titles_match_the_parser():
    """Negative control for the fallback table.

    scorers.py keeps a pinned title->id map so it stays importable without the
    ingestion package. That is a second place the truth is written down, so a
    title renamed in the parser and not here fails the suite rather than
    quietly making narrative items unscoreable again.
    """
    from eval.scorers import _FALLBACK_SECTION_TITLES
    from ingestion.parser import SECTION_PATTERNS

    parser_titles = {title.lower(): sid for _p, sid, title in SECTION_PATTERNS}
    for title, section_id in _FALLBACK_SECTION_TITLES.items():
        if title in parser_titles:
            assert parser_titles[title] == section_id, title
