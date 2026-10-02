"""T1-02 — an LLM failure must surface as an error, never as a clarification (F1).

v1's routing/classifier.py caught every exception and returned
ClassifiedQuery(query_type="single_doc", tickers=[]), which query.ask() turned
into "Which company are you asking about?". Phase 0 measured the result: all
five live queries returned that message in ~0.2 s because the configured model
was not available to the key. An outage was indistinguishable from a user
forgetting to name a company.

Spec D7 and G4: dependency failures are typed errors with status=error.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMRateLimited,
    LLMUnavailable,
)

pytestmark = pytest.mark.integration

CLARIFICATION = "Which company are you asking about?"

CASES = [
    (LLMAuthError("no access", provider="groq", model="m"), "llm_auth"),
    (LLMRateLimited("slow down", provider="groq", model="m"), "llm_rate_limited"),
    (LLMBudgetExceeded("local budget", provider="groq", model="m"), "llm_rate_limited"),
    (LLMUnavailable("5xx", provider="groq", model="m"), "llm_unavailable"),
    (LLMBadOutput("not json", provider="groq", model="m"), "llm_bad_output"),
]


@pytest.mark.parametrize("exc,expected_code", CASES, ids=[c[1] for c in CASES])
def test_classifier_failure_becomes_status_error(monkeypatch, exc, expected_code):
    import query as Q
    import routing.classifier as C

    def boom(_query):
        raise exc
    monkeypatch.setattr(C, "classify_query", boom)
    monkeypatch.setattr(Q, "classify_and_ensure", _raiser(exc))

    result = Q.ask("What were Apple total net sales in fiscal year 2024?")
    assert result.status == "error", f"expected status=error, got {result.status}"
    assert result.error_code == expected_code
    assert CLARIFICATION not in result.answer, (
        "an LLM outage must never be reported as a request for clarification (F1)"
    )
    assert not result.citations


def _raiser(exc):
    def _fn(_query):
        raise exc
    return _fn


def test_unexpected_exception_becomes_internal_error(monkeypatch):
    import query as Q
    monkeypatch.setattr(Q, "classify_and_ensure", _raiser(ValueError("surprise")))
    result = Q.ask("What were Apple total net sales in fiscal year 2024?")
    assert result.status == "error"
    assert result.error_code == "internal"
    assert CLARIFICATION not in result.answer


def test_genuine_missing_company_still_asks_for_clarification(monkeypatch):
    """The clarification path must survive for the case it was written for."""
    import query as Q
    from routing.classifier import ClassifiedQuery

    monkeypatch.setattr(Q, "classify_and_ensure",
                        lambda _q: ClassifiedQuery(query_type="single_doc", tickers=[],
                                                   years=[2024], reasoning="no company named"))
    result = Q.ask("What was the revenue last year?")
    assert result.status == "clarification_needed"
    assert result.error_code is None
    assert CLARIFICATION in result.answer


def test_api_maps_status_error_to_503(monkeypatch):
    """Spec P1-05: the API returns 503 for a dependency failure."""
    import importlib

    import api.app
    import config
    monkeypatch.setattr(config.settings, "admin_token", None)
    importlib.reload(api.app)

    from models import QueryResult

    def failing_ask(question):
        return QueryResult(query=question, answer="The language model is unavailable.",
                           citations=[], chunks_used=[], query_type="error",
                           status="error", error_code="llm_unavailable")
    monkeypatch.setattr(api.app, "ask", failing_ask)

    client = TestClient(api.app.app, raise_server_exceptions=False)
    resp = client.post("/query", json={"question": "Apple net sales fiscal 2024?"})
    assert resp.status_code == 503, resp.text[:300]
    body = resp.json()
    assert body.get("detail", {}).get("error_code") == "llm_unavailable", body


def test_api_returns_200_for_a_normal_answer(monkeypatch):
    import importlib

    import api.app
    import config
    monkeypatch.setattr(config.settings, "admin_token", None)
    importlib.reload(api.app)

    from models import QueryResult
    monkeypatch.setattr(api.app, "ask", lambda q: QueryResult(
        query=q, answer="Total net sales were $391,035 million.", citations=[],
        chunks_used=[], query_type="single_doc", status="answered_text"))
    client = TestClient(api.app.app, raise_server_exceptions=False)
    resp = client.post("/query", json={"question": "Apple net sales fiscal 2024?"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered_text"
