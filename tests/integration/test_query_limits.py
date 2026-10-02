"""T1-04 — /query rate limit and question-length cap (spec section 8)."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _app(monkeypatch, *, rate_limit=20, max_chars=500):
    import api.app
    import api.ratelimit
    import config
    monkeypatch.setattr(config.settings, "rate_limit_per_min", rate_limit)
    monkeypatch.setattr(config.settings, "max_question_chars", max_chars)
    monkeypatch.setattr(config.settings, "admin_token", None)
    importlib.reload(api.ratelimit)
    importlib.reload(api.app)
    # never reach the real pipeline
    monkeypatch.setattr(api.app, "ask", _fake_result)
    return api.app.app


def _fake_result(question):
    from models import QueryResult
    return QueryResult(query=question, answer="stub", citations=[],
                       chunks_used=[], query_type="single_doc")


def test_question_over_the_cap_is_rejected(monkeypatch):
    app = _app(monkeypatch, max_chars=100)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/query", json={"question": "x" * 101})
    assert resp.status_code == 413, resp.text[:200]


def test_question_at_the_cap_is_accepted(monkeypatch):
    app = _app(monkeypatch, max_chars=100)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/query", json={"question": "x" * 100})
    assert resp.status_code == 200, resp.text[:200]


def test_whitespace_only_question_rejected(monkeypatch):
    """Whitespace-only but long enough to clear the pydantic min_length=5."""
    app = _app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/query", json={"question": "       "}).status_code == 400


def test_too_short_question_rejected_by_schema(monkeypatch):
    """QueryRequest declares min_length=5, so a 3-char body is a 422."""
    app = _app(monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/query", json={"question": "   "}).status_code == 422


def test_rate_limit_trips_after_the_configured_number(monkeypatch):
    app = _app(monkeypatch, rate_limit=3)
    client = TestClient(app, raise_server_exceptions=False)
    codes = [client.post("/query", json={"question": "hello there"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200], codes
    assert codes[3] == 429, codes
    assert codes[4] == 429, codes


def test_rate_limit_response_has_retry_after(monkeypatch):
    app = _app(monkeypatch, rate_limit=1)
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/query", json={"question": "hello there"})
    resp = client.post("/query", json={"question": "hello there"})
    assert resp.status_code == 429
    assert "retry-after" in {k.lower() for k in resp.headers}


def test_rate_limit_is_per_client(monkeypatch):
    app = _app(monkeypatch, rate_limit=1)
    a = TestClient(app, raise_server_exceptions=False, client=("1.1.1.1", 1))
    b = TestClient(app, raise_server_exceptions=False, client=("2.2.2.2", 1))
    assert a.post("/query", json={"question": "hello there"}).status_code == 200
    assert a.post("/query", json={"question": "hello there"}).status_code == 429
    assert b.post("/query", json={"question": "hello there"}).status_code == 200, (
        "a different client must have its own budget"
    )


def test_window_expiry_allows_requests_again(monkeypatch):
    import api.ratelimit as rl
    app = _app(monkeypatch, rate_limit=1)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/query", json={"question": "hello there"}).status_code == 200
    assert client.post("/query", json={"question": "hello there"}).status_code == 429
    # advance the limiter's clock past the window instead of sleeping
    rl._LIMITER.advance_for_tests(61.0)
    assert client.post("/query", json={"question": "hello there"}).status_code == 200


def test_cors_is_not_wildcard(monkeypatch):
    """Spec section 15: no wildcard CORS."""
    app = _app(monkeypatch)
    from fastapi.middleware.cors import CORSMiddleware
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            origins = mw.kwargs.get("allow_origins", [])
            assert "*" not in origins, f"wildcard CORS is configured: {origins}"
