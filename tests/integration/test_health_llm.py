"""T1-09 — /health reports an LLM state and must not probe per request."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from llm.errors import LLMAuthError, LLMRateLimited, LLMUnavailable

pytestmark = pytest.mark.integration

STATES = [
    (None, "ok"),
    (LLMAuthError("bad key"), "auth_error"),
    (LLMRateLimited("429"), "rate_limited"),
    (LLMUnavailable("down"), "unreachable"),
]


def _app(monkeypatch):
    import api.app
    import config
    monkeypatch.setattr(config.settings, "admin_token", None)
    importlib.reload(api.app)
    return api.app


@pytest.mark.parametrize("exc,expected", STATES, ids=[s[1] for s in STATES])
def test_health_reports_llm_state(monkeypatch, exc, expected):
    mod = _app(monkeypatch)
    import llm.health as H
    H.reset_probe_cache()

    def probe():
        if exc is not None:
            raise exc
        return True
    monkeypatch.setattr(H, "_probe_models", probe)

    client = TestClient(mod.app, raise_server_exceptions=False)
    body = client.get("/health").json()
    assert body["llm"] == expected, body


def test_probe_is_cached_not_per_request(monkeypatch):
    mod = _app(monkeypatch)
    import llm.health as H
    H.reset_probe_cache()
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return True
    monkeypatch.setattr(H, "_probe_models", probe)

    client = TestClient(mod.app, raise_server_exceptions=False)
    for _ in range(5):
        client.get("/health")
    assert calls["n"] == 1, f"expected one cached probe, got {calls['n']}"


def test_probe_cache_expires(monkeypatch):
    mod = _app(monkeypatch)
    import llm.health as H
    H.reset_probe_cache()
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return True
    monkeypatch.setattr(H, "_probe_models", probe)

    client = TestClient(mod.app, raise_server_exceptions=False)
    client.get("/health")
    H.advance_for_tests(H.PROBE_TTL_SECONDS + 1)
    client.get("/health")
    assert calls["n"] == 2


def test_health_still_reports_the_rest_when_the_probe_fails(monkeypatch):
    mod = _app(monkeypatch)
    import llm.health as H
    H.reset_probe_cache()
    monkeypatch.setattr(H, "_probe_models", _boom)
    client = TestClient(mod.app, raise_server_exceptions=False)
    body = client.get("/health").json()
    # The point: a failing LLM probe must not take /health down. Qdrant may
    # legitimately be unavailable (locked by an ingest, or absent in CI), so
    # "degraded" is an acceptable overall status here; what matters is that
    # the endpoint answered and reported the LLM state.
    assert body["status"] in ("ok", "degraded"), body
    assert body["llm"] == "unreachable"
    assert "collections_loaded" in body


def _boom():
    raise LLMUnavailable("down")
