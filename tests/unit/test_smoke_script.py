"""Unit tests for scripts/smoke.py (P1-11). No network: requests is stubbed."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import smoke  # noqa: E402

pytestmark = pytest.mark.unit


class _R:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_refuses_more_than_eight_queries(capsys):
    rc = smoke.main(["--base-url", "http://x", "--queries", "9"])
    assert rc == 2
    assert "refusing" in capsys.readouterr().err


def test_query_list_is_capped_at_eight():
    assert len(smoke.SMOKE_QUESTIONS) <= smoke.MAX_QUERIES


def test_health_failure_is_reported(monkeypatch):
    monkeypatch.setattr(smoke.requests, "get", lambda *a, **k: _R(500, text="boom"))
    res, body = smoke.check_health("http://x/", 1.0)
    assert res.ok is False and body is None


def test_health_reports_llm_state(monkeypatch):
    payload = {"status": "ok", "llm": "rate_limited", "collections_loaded": 41}
    monkeypatch.setattr(smoke.requests, "get", lambda *a, **k: _R(200, payload))
    res, _ = smoke.check_health("http://x/", 1.0)
    assert res.ok is True
    assert "llm=rate_limited" in res.detail


def test_query_503_is_a_failure_naming_the_error_code(monkeypatch):
    payload = {"detail": {"error_code": "llm_auth", "message": "no access"}}
    monkeypatch.setattr(smoke.requests, "post", lambda *a, **k: _R(503, payload))
    res = smoke.check_query("http://x/", "numeric", "q", ("answered",), 1.0, 90.0)
    assert res.ok is False
    assert "llm_auth" in res.detail


def test_query_accepts_an_allowed_status(monkeypatch):
    payload = {"query": "q", "answer": "a", "query_type": "single_doc",
               "citations": [], "status": "answered_text"}
    monkeypatch.setattr(smoke.requests, "post", lambda *a, **k: _R(200, payload))
    res = smoke.check_query("http://x/", "numeric", "q",
                            ("answered", "answered_text"), 1.0, 90.0)
    assert res.ok is True


def test_query_rejects_an_unexpected_status(monkeypatch):
    payload = {"query": "q", "answer": "a", "query_type": "single_doc",
               "citations": [], "status": "clarification_needed"}
    monkeypatch.setattr(smoke.requests, "post", lambda *a, **k: _R(200, payload))
    res = smoke.check_query("http://x/", "numeric", "q", ("answered_text",), 1.0, 90.0)
    assert res.ok is False
    assert "clarification_needed" in res.detail


def test_query_rejects_an_empty_answer(monkeypatch):
    payload = {"query": "q", "answer": "   ", "query_type": "single_doc",
               "citations": [], "status": "answered_text"}
    monkeypatch.setattr(smoke.requests, "post", lambda *a, **k: _R(200, payload))
    res = smoke.check_query("http://x/", "numeric", "q", ("answered_text",), 1.0, 90.0)
    assert res.ok is False


def test_query_rejects_a_missing_field(monkeypatch):
    payload = {"query": "q", "answer": "a", "status": "answered_text"}
    monkeypatch.setattr(smoke.requests, "post", lambda *a, **k: _R(200, payload))
    res = smoke.check_query("http://x/", "numeric", "q", ("answered_text",), 1.0, 90.0)
    assert res.ok is False and "query_type" in res.detail


def test_script_never_targets_admin_or_ingest():
    """CLAUDE.md rule 8: a hosted instance must never see these."""
    src = Path(REPO_ROOT / "scripts" / "smoke.py").read_text(encoding="utf-8")
    code = "\n".join(
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith(("#", "*"))
    )
    for forbidden in ('"/admin', "'/admin", '"/ingest', "'/ingest"):
        assert forbidden not in code, f"smoke.py must not reference {forbidden}"
