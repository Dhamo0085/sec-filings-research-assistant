"""T1-02 — an LLM failure must surface as an error, never as a clarification (F1).

v1's routing/classifier.py caught every exception and returned
ClassifiedQuery(query_type="single_doc", tickers=[]), which query.ask() turned
into "Which company are you asking about?". Phase 0 measured the result: all
five live queries returned that message in ~0.2 s because the configured model
was not available to the key. An outage was indistinguishable from a user
forgetting to name a company.

Spec D7 and G4: dependency failures are typed errors with status=error.

Rewritten in P3-03/P3-08 for the pipeline that replaced the classifier. The
failure is now injected at the dependency boundary — a fake LLM that raises,
and a catalog that raises — rather than by monkeypatching a function that no
longer exists, so the test exercises the real dispatcher.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from answering.outcome import Status
from llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMRateLimited,
    LLMUnavailable,
)
from llm.fake import FakeLLM

pytestmark = pytest.mark.integration

CLARIFICATION = "Which company are you asking about?"

CASES = [
    (LLMAuthError("no access", provider="groq", model="m"), "llm_auth"),
    (LLMRateLimited("slow down", provider="groq", model="m"), "llm_rate_limited"),
    (LLMBudgetExceeded("local budget", provider="groq", model="m"), "llm_rate_limited"),
    (LLMUnavailable("5xx", provider="groq", model="m"), "llm_unavailable"),
    (LLMBadOutput("not json", provider="groq", model="m"), "llm_bad_output"),
]

#: A narrative question: it has to reach the generator, so an LLM outage is
#: actually on its path. A numeric question deliberately does NOT need the
#: model (spec section 5's degradation rule), and that is asserted separately.
NARRATIVE_QUESTION = "What does Apple say about its supply chain risks in fiscal 2024?"


class FailingCatalog:
    """A catalog whose every call raises, to exercise the data-failure path."""

    def __init__(self, exc: BaseException):
        self.exc = exc

    def tickers(self):
        return ["AAPL"]

    def for_ticker(self, *_a, **_k):
        raise self.exc

    def available_fiscal_labels(self, *_a, **_k):
        raise self.exc


class EmptyCatalog:
    def tickers(self):
        return ["AAPL"]

    def for_ticker(self, ticker, *, as_of=None):
        from catalog.store import Filing
        return [Filing(
            accession="0000320193-24-000123", ticker="AAPL", cik=320193,
            entity_name="Apple Inc.", form_type="10-K", period_end="2024-09-28",
            fiscal_label=2024, fiscal_label_source="dei", filing_date="2024-11-01",
            collection_name="AAPL_2024",
        )]

    def available_fiscal_labels(self, ticker, *, as_of=None):
        return [2024]


def one_result(**_kwargs):
    """A retriever that returns one chunk, so the generator is actually reached.

    With an empty result the text path abstains before calling the model,
    which would make an outage test pass for the wrong reason.
    """
    from models import Chunk, RetrievedChunk

    chunk = Chunk(
        parent_id="item_1a_risk_factors", doc_id="d1",
        text="Apple depends on a limited number of suppliers.",
        company="Apple Inc.", ticker="AAPL", filing_type="10-K",
        fiscal_year=2024, section_name="Item 1A: Risk Factors",
        chunk_type="text", token_count=9,
    )
    return [RetrievedChunk(chunk=chunk, score=0.9, parent_text=chunk.text)]


@pytest.mark.parametrize("exc,expected_code", CASES, ids=[c[1] for c in CASES])
def test_a_generator_failure_becomes_status_error(exc, expected_code):
    import query as Q

    deps = Q.Deps(llm=FakeLLM(raises=exc), catalog=EmptyCatalog(),
                  retriever=one_result)
    outcome = Q.ask(NARRATIVE_QUESTION, deps=deps)

    assert outcome.status is Status.ERROR, f"got {outcome.status}: {outcome.answer}"
    assert outcome.error_code.value == expected_code
    assert CLARIFICATION not in outcome.answer, (
        "an LLM outage must never be reported as a request for clarification (F1)"
    )
    assert not outcome.citations
    assert outcome.http_status == 503


def test_an_unexpected_exception_becomes_an_internal_error():
    import query as Q

    class Exploding:
        def tickers(self):
            raise ValueError("surprise")

        def for_ticker(self, *_a, **_k):
            raise ValueError("surprise")

        def available_fiscal_labels(self, *_a, **_k):
            raise ValueError("surprise")

    deps = Q.Deps(llm=FakeLLM(default="{}"), catalog=Exploding(),
                  retriever=lambda **_k: [])
    outcome = Q.ask(NARRATIVE_QUESTION, deps=deps)
    assert outcome.status is Status.ERROR
    assert outcome.error_code.value == "internal"
    assert CLARIFICATION not in outcome.answer


def test_a_facts_store_failure_is_reported_as_data_unavailable():
    """A broken store is a dependency failure, not "no filings for Apple"."""
    import query as Q
    from facts.errors import FactsError

    class BrokenResolver:
        def resolve(self, *_a, **_k):
            raise FactsError("facts.sqlite is locked")

    deps = Q.Deps(llm=FakeLLM(default="{}"), catalog=EmptyCatalog(),
                  resolver=BrokenResolver(), retriever=lambda **_k: [])
    outcome = Q.ask("What was Apple's revenue in fiscal 2024?", deps=deps)
    assert outcome.status is Status.ERROR
    assert outcome.error_code.value == "data_unavailable"


def test_a_genuine_missing_company_still_asks_for_clarification():
    """The clarification path must survive for the case it was written for."""
    import query as Q

    deps = Q.Deps(llm=FakeLLM(default="{}"), catalog=EmptyCatalog(),
                  retriever=lambda **_k: [])
    outcome = Q.ask("What was the revenue last year?", deps=deps)
    assert outcome.status is Status.CLARIFICATION_NEEDED
    assert outcome.error_code is None
    assert CLARIFICATION in outcome.answer


def test_a_numeric_question_does_not_need_the_model_at_all(tmp_path):
    """Spec section 5: with the LLM down, a parseable numeric question still
    answers from facts. The model raises on every call here, so reaching an
    answer at all proves nothing called it."""
    import query as Q
    from tests.fixture_world import build_world

    world = build_world(tmp_path)
    deps = Q.Deps(
        llm=FakeLLM(raises=LLMUnavailable("down")),
        catalog=world.catalog, resolver=world.resolver,
        retriever=lambda **_k: [],
    )
    outcome = Q.ask("What was Apple's revenue in fiscal 2024?", deps=deps)
    assert outcome.status is Status.ANSWERED, outcome.answer
    assert "391.04 billion" in outcome.answer


# ── the API surface ───────────────────────────────────────────────────────────

def _client(monkeypatch):
    import importlib

    import api.app
    import config
    monkeypatch.setattr(config.settings, "admin_token", None)
    importlib.reload(api.app)
    return api.app, TestClient(api.app.app, raise_server_exceptions=False)


def test_api_maps_status_error_to_503(monkeypatch):
    """Spec P1-05: the API returns 503 for a dependency failure."""
    api_app, client = _client(monkeypatch)

    from answering.abstain import error_for
    from answering.outcome import ErrorCode

    monkeypatch.setattr(
        api_app, "ask",
        lambda question, as_of=None: error_for(ErrorCode.LLM_UNAVAILABLE, query=question),
    )
    resp = client.post("/query", json={"question": "Apple net sales fiscal 2024?"})
    assert resp.status_code == 503, resp.text[:300]
    body = resp.json()
    assert body.get("detail", {}).get("error_code") == "llm_unavailable", body
    assert body["detail"]["request_id"], "a 503 must still be traceable"


def test_api_returns_200_for_a_normal_answer(monkeypatch):
    api_app, client = _client(monkeypatch)

    from answering.outcome import Citation, CitationKind, Outcome

    def fake_ask(question, as_of=None):
        return Outcome.answered_text(
            "Apple cites supplier concentration. [1]",
            citations=[Citation(
                kind=CitationKind.TEXT, index=1, ticker="AAPL",
                company="Apple Inc.", fiscal_label=2024,
                section="Item 1A: Risk Factors",
                accession="0000320193-24-000123", filing_date="2024-11-01",
            )],
            query=question,
        )

    monkeypatch.setattr(api_app, "ask", fake_ask)
    resp = client.post("/query", json={"question": "Apple supply chain risks?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered_text"
    assert body["citations"][0]["accession"] == "0000320193-24-000123"
    assert resp.headers["X-Request-Id"]
