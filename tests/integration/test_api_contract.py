"""T3-07 — the /query request and response contract (spec 6.5, P3-08).

Snapshot-style: the assertions name the exact field set, because the response
*is* the product's interface and a field that quietly disappears breaks every
client without failing anything else.
"""

from __future__ import annotations

import importlib
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from answering.facts_answer import numeric_answer
from answering.outcome import AbstainReason, Citation, CitationKind, ErrorCode, Outcome
from facts.resolve import Resolution

pytestmark = pytest.mark.integration

SPEC_6_5_FIELDS = {
    "status", "answer", "abstain_reason", "error_code", "query_type",
    "as_of", "definition_note", "citations", "trace",
}
#: Carried for the v1 UI (P3-01), not part of 6.5.
UI_FIELDS = {"query", "chunks_used"}

ADMIN_TOKEN = "test-admin-token"  # noqa: S105 - a fixture value, not a secret


def apple_resolution(**over) -> Resolution:
    fields = {
        "ticker": "AAPL", "metric": "revenue", "metric_label": "Total net revenue",
        "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "value": Decimal("391035000000"), "unit": "USD", "period_type": "duration",
        "start_date": "2023-10-01", "end_date": "2024-09-28", "fiscal_label": 2024,
        "accession": "0000320193-24-000123", "filing_date": "2024-11-01",
        "form_type": "10-K", "source_doc": "aapl-20240928.htm",
        "selection": "single_candidate", "candidates_considered": (),
        "validation_status": "validated",
    }
    fields.update(over)
    return Resolution(**fields)


@pytest.fixture
def api(monkeypatch):
    """A freshly reloaded app with the admin token configured."""
    import api.app
    import config
    monkeypatch.setattr(config.settings, "admin_token", ADMIN_TOKEN)
    monkeypatch.setattr(config.settings, "rate_limit_per_min", 1000)
    importlib.reload(api.app)
    return api.app


def client_for(api_module, outcome_factory):
    import api.app as module

    def fake_ask(question, as_of=None):
        return outcome_factory(question, as_of)

    module.ask = fake_ask
    api_module.ask = fake_ask
    return TestClient(api_module.app, raise_server_exceptions=False)


def answered(question, as_of):
    return numeric_answer(apple_resolution(), question=question,
                          company="Apple Inc.", cik=320193, as_of=as_of)


# ── the response shape ────────────────────────────────────────────────────────

def test_the_response_has_exactly_the_spec_fields_plus_the_ui_ones(api):
    client = client_for(api, answered)
    body = client.post("/query", json={"question": "Apple revenue fiscal 2024?"}).json()
    assert set(body) == SPEC_6_5_FIELDS | UI_FIELDS


def test_an_answered_response_carries_a_fact_citation_and_a_definition(api):
    client = client_for(api, answered)
    body = client.post("/query", json={"question": "Apple revenue fiscal 2024?"}).json()
    assert body["status"] == "answered"
    assert body["query_type"] == "numeric_fact"
    assert body["definition_note"].startswith("Total net revenue = us-gaap:")
    citation = body["citations"][0]
    assert citation["kind"] == "fact"
    assert citation["value"] == "391035000000", "a string, never a float (6.2)"
    assert citation["period_end"] == "2024-09-28"
    assert citation["accession"] == "0000320193-24-000123"
    assert citation["filing_date"] == "2024-11-01"
    assert citation["restated"] is False
    # v1 UI compatibility.
    assert citation["fiscal_year"] == 2024 and citation["company"] == "Apple Inc."


def test_an_abstention_carries_its_reason_and_no_citations(api):
    client = client_for(api, lambda q, a: Outcome.abstain(
        AbstainReason.PERIOD_NOT_FILED_AS_OF, "Not filed yet.", query=q, as_of=a))
    resp = client.post("/query", json={"question": "Apple revenue fiscal 2024?",
                                       "as_of": "2024-06-30"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "abstained"
    assert body["abstain_reason"] == "period_not_filed_as_of"
    assert body["error_code"] is None
    assert body["citations"] == []
    assert body["as_of"] == "2024-06-30"


def test_a_dependency_failure_is_a_503_with_its_code(api):
    client = client_for(api, lambda q, a: Outcome.error(
        ErrorCode.LLM_RATE_LIMITED, "Try later.", query=q))
    resp = client.post("/query", json={"question": "Apple revenue fiscal 2024?"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "llm_rate_limited"


def test_an_oversize_prompt_is_a_503_with_its_own_code(api):
    """T4-14: the API contract lists llm_prompt_too_large, and it is not
    reported as a rate limit — a client that retries rate limits must not
    retry this one, because it cannot clear."""
    client = client_for(api, lambda q, a: Outcome.error(
        ErrorCode.LLM_PROMPT_TOO_LARGE, "Too large to send.", query=q))
    resp = client.post("/query", json={"question": "JPMorgan revenue 2024?"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "llm_prompt_too_large"


def test_a_text_answer_carries_typed_text_citations(api):
    def text_outcome(question, as_of):
        return Outcome.answered_text(
            "Apple cites supplier concentration. [1]",
            citations=[Citation(
                kind=CitationKind.TEXT, index=1, ticker="AAPL",
                company="Apple Inc.", fiscal_label=2024,
                section="Item 1A: Risk Factors",
                accession="0000320193-24-000123", filing_date="2024-11-01",
                cik=320193, score=0.91,
            )],
            query=question, as_of=as_of, chunks_used=6,
        )

    client = client_for(api, text_outcome)
    body = client.post("/query", json={"question": "Apple risks?"}).json()
    assert body["status"] == "answered_text"
    assert body["chunks_used"] == 6
    citation = body["citations"][0]
    assert citation["kind"] == "text"
    assert citation["section"] == "Item 1A: Risk Factors"
    assert "value" not in citation, "a text citation carries no figure"


# ── the request shape ─────────────────────────────────────────────────────────

def test_as_of_is_accepted_and_echoed(api):
    seen = {}

    def capture(question, as_of):
        seen["as_of"] = as_of
        return Outcome.abstain(AbstainReason.OUT_OF_SCOPE, "stub",
                               query=question, as_of=as_of)

    client = client_for(api, capture)
    body = client.post("/query", json={"question": "Apple revenue?",
                                       "as_of": "2024-03-01"}).json()
    assert seen["as_of"] == "2024-03-01"
    assert body["as_of"] == "2024-03-01"


def test_as_of_may_be_omitted(api):
    client = client_for(api, answered)
    body = client.post("/query", json={"question": "Apple revenue fiscal 2024?"}).json()
    assert body["as_of"] is None


@pytest.mark.parametrize("bad", ["March 1 2024", "2024-13-01", "tomorrow", "2024/03/01"])
def test_a_malformed_as_of_is_a_422_not_a_silently_ignored_field(api, bad):
    """Ignoring it would answer a different question from the one asked."""
    client = client_for(api, answered)
    resp = client.post("/query", json={"question": "Apple revenue?", "as_of": bad})
    assert resp.status_code == 422, resp.text[:200]


def test_an_empty_as_of_is_treated_as_absent(api):
    client = client_for(api, answered)
    resp = client.post("/query", json={"question": "Apple revenue?", "as_of": ""})
    assert resp.status_code == 200
    assert resp.json()["as_of"] is None


# ── the trace is admin-only ───────────────────────────────────────────────────

def test_the_trace_is_withheld_from_an_anonymous_caller(api):
    client = client_for(api, lambda q, a: Outcome.answered_text(
        "x [1]", citations=[Citation(
            kind=CitationKind.TEXT, index=1, ticker="AAPL", fiscal_label=2024,
            section="Item 1A", accession="0000320193-24-000123",
            filing_date="2024-11-01")],
        query=q, trace={"router": "rules"}))
    body = client.post("/query?debug=1", json={"question": "Apple risks?"}).json()
    assert body["trace"] is None, "?debug=1 alone must not reveal the trace"
    assert body["status"] == "answered_text", "and must not break the response"


def test_a_wrong_admin_token_does_not_reveal_the_trace(api):
    client = client_for(api, lambda q, a: Outcome.answered_text(
        "x [1]", citations=[Citation(
            kind=CitationKind.TEXT, index=1, ticker="AAPL", fiscal_label=2024,
            section="Item 1A", accession="0000320193-24-000123",
            filing_date="2024-11-01")],
        query=q, trace={"router": "rules"}))
    body = client.post("/query?debug=1", json={"question": "Apple risks?"},
                       headers={"X-Admin-Token": "wrong"}).json()
    assert body["trace"] is None


def test_an_admin_asking_for_the_trace_gets_it_with_the_request_id(api):
    client = client_for(api, lambda q, a: Outcome.answered_text(
        "x [1]", citations=[Citation(
            kind=CitationKind.TEXT, index=1, ticker="AAPL", fiscal_label=2024,
            section="Item 1A", accession="0000320193-24-000123",
            filing_date="2024-11-01")],
        query=q, trace={"router": "rules"}))
    body = client.post("/query?debug=1", json={"question": "Apple risks?"},
                       headers={"X-Admin-Token": ADMIN_TOKEN}).json()
    assert body["trace"]["router"] == "rules"
    assert body["trace"]["request_id"]


def test_an_admin_without_debug_still_gets_no_trace(api):
    client = client_for(api, answered)
    body = client.post("/query", json={"question": "Apple revenue?"},
                       headers={"X-Admin-Token": ADMIN_TOKEN}).json()
    assert body["trace"] is None


def test_with_no_admin_token_configured_nobody_is_an_admin(monkeypatch):
    """D13 fail-closed, applied to the trace."""
    import api.app
    import config
    monkeypatch.setattr(config.settings, "admin_token", None)
    monkeypatch.setattr(config.settings, "rate_limit_per_min", 1000)
    importlib.reload(api.app)

    client = client_for(api.app, lambda q, a: Outcome.answered_text(
        "x [1]", citations=[Citation(
            kind=CitationKind.TEXT, index=1, ticker="AAPL", fiscal_label=2024,
            section="Item 1A", accession="0000320193-24-000123",
            filing_date="2024-11-01")],
        query=q, trace={"router": "rules"}))
    body = client.post("/query?debug=1", json={"question": "Apple risks?"},
                       headers={"X-Admin-Token": ""}).json()
    assert body["trace"] is None


# ── request ids ───────────────────────────────────────────────────────────────

def test_every_response_carries_a_request_id(api):
    client = client_for(api, answered)
    resp = client.post("/query", json={"question": "Apple revenue fiscal 2024?"})
    assert resp.headers["X-Request-Id"]


def test_an_inbound_request_id_is_honoured(api):
    client = client_for(api, answered)
    resp = client.post("/query", json={"question": "Apple revenue fiscal 2024?"},
                       headers={"X-Request-Id": "client-abc-123"})
    assert resp.headers["X-Request-Id"] == "client-abc-123"


def test_a_hostile_inbound_request_id_is_sanitised(api):
    """It is echoed in a response header, so it cannot be taken on trust."""
    client = client_for(api, answered)
    resp = client.post("/query", json={"question": "Apple revenue fiscal 2024?"},
                       headers={"X-Request-Id": "abc" + "!" * 5 + "def"})
    echoed = resp.headers["X-Request-Id"]
    assert echoed == "abcdef"
    assert "!" not in echoed


def test_an_overlong_request_id_is_truncated(api):
    client = client_for(api, answered)
    resp = client.post("/query", json={"question": "Apple revenue fiscal 2024?"},
                       headers={"X-Request-Id": "a" * 500})
    assert len(resp.headers["X-Request-Id"]) == 64
