"""The Outcome contract (spec 6.5, P3-01) and the guarantees it enforces.

Every test here is a combination of fields that must be impossible to build.
The point of the module under test is that G1-G4 are checked by the type, so
these are the negative controls for that claim: if a validator is removed, the
matching test fails.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from answering.outcome import (
    ANSWERING_STATUSES,
    AbstainReason,
    Citation,
    CitationKind,
    ErrorCode,
    Outcome,
    QueryType,
    Status,
)

pytestmark = pytest.mark.unit


def fact_citation(index: int = 1, **over) -> Citation:
    """Apple's FY2024 revenue, the spec 6.5 example."""
    fields = {
        "kind": CitationKind.FACT,
        "index": index,
        "ticker": "AAPL",
        "metric": "revenue",
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "period_end": "2024-09-28",
        "fiscal_label": 2024,
        "value": "391035000000",
        "unit": "USD",
        "accession": "0000320193-24-000123",
        "filing_date": "2024-11-01",
        "restated": False,
        "cik": 320193,
    }
    fields.update(over)
    return Citation(**fields)


def text_citation(index: int = 1, **over) -> Citation:
    fields = {
        "kind": CitationKind.TEXT,
        "index": index,
        "ticker": "JPM",
        "fiscal_label": 2024,
        "section": "Item 1A: Risk Factors",
        "accession": "0000019617-24-000225",
        "filing_date": "2024-02-16",
    }
    fields.update(over)
    return Citation(**fields)


DEF_NOTE = ("Revenue = total net sales "
            "(us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax), "
            "fiscal year ended 2024-09-28")


# ── the happy paths ───────────────────────────────────────────────────────────

def test_a_facts_answer_is_well_formed():
    out = Outcome.answered(
        "Apple's total net sales in fiscal 2024 were $391.04 billion. [1]",
        query_type=QueryType.NUMERIC_FACT,
        citations=[fact_citation()],
        definition_note=DEF_NOTE,
        query="What were Apple's total net sales in fiscal 2024?",
    )
    assert out.status is Status.ANSWERED
    assert out.abstain_reason is None and out.error_code is None
    assert out.http_status == 200
    body = out.to_response()
    assert body["status"] == "answered"
    assert body["citations"][0]["kind"] == "fact"
    assert body["citations"][0]["value"] == "391035000000"


def test_a_text_answer_is_well_formed():
    out = Outcome.answered_text(
        "JPMorgan discloses credit, market and operational risk. [1]",
        citations=[text_citation()], chunks_used=7,
    )
    assert out.status is Status.ANSWERED_TEXT
    assert out.query_type is QueryType.NARRATIVE
    assert out.to_ui_response()["chunks_used"] == 7


def test_abstain_error_and_clarification_are_distinguishable():
    ab = Outcome.abstain(AbstainReason.PERIOD_NOT_FILED_AS_OF, "Not filed yet.",
                         as_of="2024-06-30")
    er = Outcome.error(ErrorCode.LLM_RATE_LIMITED, "Try again later.")
    cl = Outcome.clarification("Which company do you mean?")

    assert ab.abstain_reason is AbstainReason.PERIOD_NOT_FILED_AS_OF
    assert ab.error_code is None and ab.http_status == 200
    assert er.error_code is ErrorCode.LLM_RATE_LIMITED and er.abstain_reason is None
    assert er.http_status == 503, "a dependency failure must not be a 200 (D7)"
    assert cl.status is Status.CLARIFICATION_NEEDED
    assert cl.error_code is None, (
        "v1's defect F1 was a dependency failure rendered as a clarification"
    )


# ── G1: traceable numbers ─────────────────────────────────────────────────────

def test_answered_without_a_fact_citation_is_rejected():
    with pytest.raises(ValidationError, match="at least one fact citation"):
        Outcome.answered("Revenue was $391.04 billion.",
                         query_type=QueryType.NUMERIC_FACT,
                         citations=[text_citation()], definition_note=DEF_NOTE)


def test_answered_without_citations_at_all_is_rejected():
    with pytest.raises(ValidationError, match="at least one fact citation"):
        Outcome.answered("Revenue was $391.04 billion.",
                         query_type=QueryType.NUMERIC_FACT,
                         citations=[], definition_note=DEF_NOTE)


def test_answered_without_a_definition_note_is_rejected():
    """D5: an answer must always name the definition it used."""
    with pytest.raises(ValidationError, match="definition_note"):
        Outcome.answered("Revenue was $391.04 billion.",
                         query_type=QueryType.NUMERIC_FACT,
                         citations=[fact_citation()], definition_note="   ")


@pytest.mark.parametrize("missing", ["metric", "concept", "period_end", "value", "unit"])
def test_a_half_filled_fact_citation_is_rejected(missing):
    with pytest.raises(ValidationError, match=missing):
        fact_citation(**{missing: None})


def test_a_fact_citation_must_state_whether_it_is_restated():
    with pytest.raises(ValidationError, match="restated"):
        fact_citation(restated=None)


def test_a_fact_value_must_be_a_decimal_string():
    """Floats lose cents on a twelve-digit revenue line (spec 6.2)."""
    with pytest.raises(ValidationError):
        fact_citation(value=391035000000)
    with pytest.raises(ValidationError, match="decimal string"):
        fact_citation(value="391,035,000,000")
    with pytest.raises(ValidationError, match="decimal string"):
        fact_citation(value="3.91035e+11")
    assert fact_citation(value="-1234.56").value == "-1234.56"


def test_a_text_citation_must_name_a_section_and_carry_no_numbers():
    with pytest.raises(ValidationError, match="section"):
        text_citation(section=None)
    with pytest.raises(ValidationError, match="must not carry value"):
        text_citation(value="123")


# ── G2: no look-ahead ─────────────────────────────────────────────────────────

def test_a_citation_filed_after_as_of_is_rejected():
    with pytest.raises(ValidationError, match="look-ahead"):
        Outcome.answered(
            "Revenue was $391.04 billion.",
            query_type=QueryType.NUMERIC_FACT,
            citations=[fact_citation(filing_date="2024-11-01")],
            definition_note=DEF_NOTE,
            as_of="2024-06-30",
        )


def test_a_citation_filed_on_the_as_of_date_is_allowed():
    """as_of is inclusive: a filing public that day was public."""
    out = Outcome.answered(
        "Revenue was $383.29 billion.",
        query_type=QueryType.NUMERIC_FACT,
        citations=[fact_citation(filing_date="2024-06-30")],
        definition_note=DEF_NOTE, as_of="2024-06-30",
    )
    assert out.as_of == "2024-06-30"


def test_look_ahead_is_checked_on_text_citations_too():
    with pytest.raises(ValidationError, match="look-ahead"):
        Outcome.answered_text("JPMorgan discloses credit risk. [1]",
                              citations=[text_citation(filing_date="2024-02-16")],
                              as_of="2024-01-01")


def test_look_ahead_names_the_offending_citation():
    """The message has to be actionable in a failure analysis (P4-07)."""
    with pytest.raises(ValidationError) as exc:
        Outcome.answered_text("x [1]", citations=[text_citation()], as_of="2024-01-01")
    message = str(exc.value)
    assert "0000019617-24-000225" in message and "2024-02-16" in message


# ── G3: abstain, don't guess ──────────────────────────────────────────────────

def test_abstained_requires_a_reason():
    with pytest.raises(ValidationError, match="abstain_reason"):
        Outcome(status=Status.ABSTAINED, answer="No.", query_type=QueryType.UNSUPPORTED)


def test_a_reason_without_an_abstention_is_rejected():
    with pytest.raises(ValidationError, match="only valid with status=abstained"):
        Outcome(status=Status.ANSWERED_TEXT, answer="x [1]",
                query_type=QueryType.NARRATIVE, citations=[text_citation()],
                abstain_reason=AbstainReason.OUT_OF_SCOPE)


@pytest.mark.parametrize("status", [s for s in Status if s not in ANSWERING_STATUSES])
def test_a_non_answer_may_not_carry_citations(status):
    kwargs = {"status": status, "answer": "No.", "query_type": QueryType.UNSUPPORTED,
              "citations": [text_citation()]}
    if status is Status.ABSTAINED:
        kwargs["abstain_reason"] = AbstainReason.INSUFFICIENT_EVIDENCE
    if status is Status.ERROR:
        kwargs["error_code"] = ErrorCode.INTERNAL
    with pytest.raises(ValidationError, match="no citations"):
        Outcome(**kwargs)


def test_every_abstain_reason_is_constructible():
    """T3-04 needs each reason reachable; this is the type-level half of it."""
    for reason in AbstainReason:
        out = Outcome.abstain(reason, f"message for {reason.value}")
        assert out.to_response()["abstain_reason"] == reason.value


# ── G4: errors are visible ────────────────────────────────────────────────────

def test_error_requires_a_code():
    with pytest.raises(ValidationError, match="error_code"):
        Outcome(status=Status.ERROR, answer="Broken.", query_type=QueryType.UNSUPPORTED)


def test_a_code_without_an_error_is_rejected():
    with pytest.raises(ValidationError, match="only valid with status=error"):
        Outcome(status=Status.ABSTAINED, answer="No.",
                query_type=QueryType.UNSUPPORTED,
                abstain_reason=AbstainReason.OUT_OF_SCOPE,
                error_code=ErrorCode.INTERNAL)


def test_every_error_code_is_constructible():
    for code in ErrorCode:
        out = Outcome.error(code, f"message for {code.value}")
        assert out.to_response()["error_code"] == code.value
        assert out.http_status == 503


# ── K1: citation markers and the list cannot disagree ─────────────────────────

def test_citation_indices_must_be_one_to_n_in_order():
    with pytest.raises(ValidationError, match="indices must be exactly"):
        Outcome.answered_text("a [1] b [3]",
                              citations=[text_citation(1), text_citation(3)])
    with pytest.raises(ValidationError, match="indices must be exactly"):
        Outcome.answered_text("a [2] b [1]",
                              citations=[text_citation(2), text_citation(1)])
    with pytest.raises(ValidationError, match="indices must be exactly"):
        Outcome.answered_text("a [1] b [1]",
                              citations=[text_citation(1), text_citation(1)])


def test_a_zero_or_negative_index_is_rejected():
    with pytest.raises(ValidationError):
        text_citation(index=0)


def test_ten_citations_are_numbered_without_collision():
    """K1 was a renumbering collision between [1] and [10]."""
    out = Outcome.answered_text(
        " ".join(f"claim [{i}]" for i in range(1, 11)),
        citations=[text_citation(i) for i in range(1, 11)],
    )
    assert [c.index for c in out.citations] == list(range(1, 11))


# ── serialization ─────────────────────────────────────────────────────────────

def test_the_response_body_has_exactly_the_spec_fields():
    out = Outcome.answered("x [1]", query_type=QueryType.NUMERIC_FACT,
                           citations=[fact_citation()], definition_note=DEF_NOTE)
    assert set(out.to_response()) == {
        "status", "answer", "abstain_reason", "error_code", "query_type",
        "as_of", "definition_note", "citations", "trace",
    }


def test_the_trace_is_withheld_unless_it_is_asked_for():
    out = Outcome.answered_text("x [1]", citations=[text_citation()],
                                trace={"router": "rules", "tokens": 0})
    assert out.to_response()["trace"] is None, "the default must be the safe one"
    assert out.to_response(include_trace=True)["trace"]["router"] == "rules"


def test_the_ui_shape_keeps_the_v1_field_names():
    out = Outcome.answered("x [1]", query_type=QueryType.NUMERIC_FACT,
                           citations=[fact_citation()], definition_note=DEF_NOTE,
                           query="q")
    body = out.to_ui_response()
    assert body["query"] == "q"
    c = body["citations"][0]
    # The v1 UI reads exactly these.
    assert c["index"] == 1 and c["ticker"] == "AAPL" and c["fiscal_year"] == 2024
    assert c["company"] == "AAPL" and c["score"] == 0.0
    assert "revenue" in c["section"], "a fact chip shows the definition it used"


def test_statuses_and_enums_serialize_as_plain_strings():
    out = Outcome.abstain(AbstainReason.OUT_OF_SCOPE, "Out of scope.")
    body = out.to_response()
    assert isinstance(body["status"], str) and body["status"] == "abstained"
    assert isinstance(body["query_type"], str)


def test_an_empty_answer_is_rejected():
    with pytest.raises(ValidationError, match="must not be empty"):
        Outcome.abstain(AbstainReason.OUT_OF_SCOPE, "   ")


def test_as_of_must_be_an_iso_date():
    with pytest.raises(ValidationError, match="ISO date"):
        Outcome.abstain(AbstainReason.OUT_OF_SCOPE, "No.", as_of="March 1 2024")


def test_an_unknown_field_is_rejected():
    """A typo in a field name must not silently vanish into the response."""
    with pytest.raises(ValidationError):
        Outcome(status=Status.ABSTAINED, answer="No.",
                query_type=QueryType.UNSUPPORTED,
                abstain_reason=AbstainReason.OUT_OF_SCOPE, reason="typo")


def test_edgar_url_needs_a_cik():
    assert fact_citation().edgar_url() == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019324000123/0000320193-24-000123-index.htm"
    )
    assert text_citation().edgar_url() is None
