"""T1-08 and T1-11 — LLM client: cache, budgets, error mapping, failover.

No network: a stub requests.Session returns scripted responses, so these run
under `make test` with sockets blocked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm.budget import BudgetTracker, Limits
from llm.cache import ResponseCache, cache_key
from llm.client import LLMClient, extract_json
from llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMRateLimited,
    LLMUnavailable,
)

pytestmark = pytest.mark.unit

PROVIDERS_YAML = """
providers:
  alpha:
    base_url: https://alpha.example/v1
    api_key_env: [ALPHA_KEY]
    models:
      a-chat:
        capabilities: [chat, json_object]
        limits: {rpm: 5, rpd: 10}
      a-nojson:
        capabilities: [chat]
        limits: {}
  beta:
    base_url: https://beta.example/v1
    api_key_env: [BETA_KEY]
    models:
      b-chat:
        capabilities: [chat, json_object]
        limits: {}
roles:
  router:
    requires: [chat, json_object]
    order: [alpha:a-chat, beta:b-chat]
  generator:
    requires: [chat]
    order: [alpha:a-chat, beta:b-chat]
"""


class _Resp:
    def __init__(self, status, payload=None, headers=None, text=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _ok(content="hello", prompt=10, completion=5):
    return _Resp(200, {
        "choices": [{"message": {"content": content, "reasoning": "secret thoughts"}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion},
    })


class _StubSession:
    """Returns queued responses; records the requests it was given."""

    def __init__(self, queue):
        self.queue = list(queue)
        self.requests = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.requests.append({"url": url, "json": json, "headers": headers})
        if not self.queue:
            raise AssertionError("stub session ran out of queued responses")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def providers_file(tmp_path) -> Path:
    p = tmp_path / "providers.yaml"
    p.write_text(PROVIDERS_YAML, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("ALPHA_KEY", "alpha-secret")
    monkeypatch.setenv("BETA_KEY", "beta-secret")


def _client(providers_file, tmp_path, queue):
    session = _StubSession(queue)
    c = LLMClient(providers_file=providers_file,
                  cache_path=tmp_path / "cache.sqlite", session=session)
    return c, session


# ── cache ───────────────────────────────────────────────────────────────────

def test_cache_hit_avoids_a_network_call(providers_file, tmp_path):
    c, session = _client(providers_file, tmp_path, [_ok("first")])
    msgs = [{"role": "user", "content": "q"}]
    first = c.complete(role="generator", messages=msgs)
    assert first.cached is False
    second = c.complete(role="generator", messages=msgs)
    assert second.cached is True
    assert second.content == "first"
    assert len(session.requests) == 1, "second call must not hit the network"


def test_cache_key_distinguishes_model_and_temperature():
    base = {"provider": "p", "model": "m",
            "messages": [{"role": "user", "content": "x"}], "temperature": 0.0}
    k1 = cache_key(**base)
    assert k1 != cache_key(**{**base, "model": "other"})
    assert k1 != cache_key(**{**base, "temperature": 0.7})
    assert k1 != cache_key(**{**base, "prompt_version": "v2"})
    assert k1 == cache_key(**base), "key must be stable"


def test_cache_roundtrip(tmp_path):
    cache = ResponseCache(tmp_path / "c.sqlite")
    assert cache.get("missing") is None
    cache.put("k", provider="p", model="m", content="body",
              prompt_tokens=3, completion_tokens=4)
    got = cache.get("k")
    assert got["content"] == "body" and got["prompt_tokens"] == 3
    assert cache.stats()["entries"] == 1


# ── error mapping ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_maps_to_auth_error(providers_file, tmp_path, status):
    c, _ = _client(providers_file, tmp_path,
                   [_Resp(status, text="bad key"), _Resp(status, text="bad key")])
    with pytest.raises(LLMAuthError):
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])


def test_model_not_available_maps_to_auth_error(providers_file, tmp_path):
    """The exact production failure Phase 0 found: 404 'model does not exist'."""
    body = ('{"error":{"message":"The model `x` does not exist or you do not '
            'have access to it.","code":"model_not_found"}}')
    c, _ = _client(providers_file, tmp_path,
                   [_Resp(404, text=body), _Resp(404, text=body)])
    with pytest.raises(LLMAuthError) as exc:
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    assert "not available" in str(exc.value)


def test_transport_error_maps_to_unavailable(providers_file, tmp_path):
    import requests
    boom = requests.ConnectionError("no route")
    c, _ = _client(providers_file, tmp_path, [boom, boom])
    with pytest.raises(LLMUnavailable):
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])


def test_server_error_retries_then_gives_up(providers_file, tmp_path, monkeypatch):
    monkeypatch.setattr("llm.client.time.sleep", lambda s: None)
    c, session = _client(providers_file, tmp_path, [_Resp(500, text="boom")] * 6)
    with pytest.raises(LLMUnavailable):
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    # 3 attempts on each of the two entries
    assert len(session.requests) == 6


def test_empty_content_is_bad_output(providers_file, tmp_path):
    """Reasoning models can spend max_tokens on reasoning and return nothing."""
    empty = _Resp(200, {"choices": [{"message": {"content": "", "reasoning": "..."}}],
                        "usage": {}})
    c, _ = _client(providers_file, tmp_path, [empty, empty])
    with pytest.raises(LLMBadOutput):
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])


def test_reasoning_field_is_never_returned_as_the_answer(providers_file, tmp_path):
    c, _ = _client(providers_file, tmp_path, [_ok("the answer")])
    out = c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    assert out.content == "the answer"
    assert "secret thoughts" not in out.content


# ── 429 / Retry-After ───────────────────────────────────────────────────────

def test_429_honours_retry_after_then_succeeds(providers_file, tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr("llm.client.time.sleep", slept.append)
    c, _ = _client(providers_file, tmp_path, [
        _Resp(429, text="slow down", headers={"retry-after": "7"}),
        _ok("recovered"),
    ])
    out = c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    assert out.content == "recovered"
    assert slept and slept[0] == 7.0, f"expected a 7s wait, got {slept}"


def test_daily_limit_does_not_retry_and_surfaces(providers_file, tmp_path, monkeypatch):
    monkeypatch.setattr("llm.client.time.sleep", lambda s: None)
    body = '{"error":{"message":"Rate limit reached per day for this model"}}'
    c, session = _client(providers_file, tmp_path, [_Resp(429, text=body)] * 2)
    with pytest.raises(LLMRateLimited) as exc:
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    assert exc.value.daily is True
    assert len(session.requests) == 2, "a daily limit must not be retried on one entry"


# ── failover (T1-11) ────────────────────────────────────────────────────────

def test_rate_limit_fails_over_to_the_next_provider(providers_file, tmp_path, monkeypatch):
    monkeypatch.setattr("llm.client.time.sleep", lambda s: None)
    body = '{"error":{"message":"rate limit reached per day"}}'
    c, _session = _client(providers_file, tmp_path, [
        _Resp(429, text=body),      # alpha:a-chat, daily -> no retry
        _ok("from beta"),           # beta:b-chat
    ])
    out = c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    assert out.content == "from beta"
    assert out.provider == "beta"


def test_server_error_fails_over(providers_file, tmp_path, monkeypatch):
    monkeypatch.setattr("llm.client.time.sleep", lambda s: None)
    c, _ = _client(providers_file, tmp_path,
                   [_Resp(503, text="x"), _Resp(503, text="x"), _Resp(503, text="x"),
                    _ok("from beta")])
    out = c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    assert out.provider == "beta"


def test_auth_error_on_one_provider_still_tries_the_next(providers_file, tmp_path):
    c, _ = _client(providers_file, tmp_path, [_Resp(401, text="bad"), _ok("from beta")])
    out = c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    assert out.provider == "beta"


def test_pinned_mode_never_fails_over(providers_file, tmp_path, monkeypatch):
    """Spec section 9 rule 10: an evaluation run pins one model."""
    monkeypatch.setattr("llm.client.time.sleep", lambda s: None)
    body = '{"error":{"message":"rate limit reached per day"}}'
    c, session = _client(providers_file, tmp_path, [_Resp(429, text=body), _ok("beta")])
    with pytest.raises(LLMRateLimited):
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}],
                   failover=False)
    assert len(session.requests) == 1, "pinned mode must not try the second entry"


def test_capability_registry_skips_a_model_lacking_json(providers_file, tmp_path,
                                                        monkeypatch):
    """A role requiring json_object must not be served by a model without it."""
    monkeypatch.setattr("config.settings", __import__("config").settings)
    import config
    monkeypatch.setattr(config.settings, "router_providers", "alpha:a-nojson,beta:b-chat")
    c, _ = _client(providers_file, tmp_path, [_ok('{"ok":true}')])
    order = c.order_for_role("router")
    assert [e.key for e in order] == ["beta:b-chat"], order


def test_env_order_overrides_the_yaml_order(providers_file, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "generator_providers", "beta:b-chat,alpha:a-chat")
    c, _ = _client(providers_file, tmp_path, [_ok("beta first")])
    assert [e.key for e in c.order_for_role("generator")] == ["beta:b-chat", "alpha:a-chat"]


def test_no_usable_entry_raises_unavailable(providers_file, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "generator_providers", "nope:nothing")
    c, _unused = _client(providers_file, tmp_path, [])
    # unknown models are allowed through, so this one fails at the base_url check
    with pytest.raises(LLMUnavailable):
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])


def test_missing_api_key_is_an_auth_error(providers_file, tmp_path, monkeypatch):
    monkeypatch.delenv("ALPHA_KEY", raising=False)
    monkeypatch.delenv("BETA_KEY", raising=False)
    import config
    monkeypatch.setattr(config.settings, "generator_providers", "alpha:a-chat")
    c, _ = _client(providers_file, tmp_path, [])
    with pytest.raises(LLMAuthError):
        c.complete(role="generator", messages=[{"role": "user", "content": "q"}])


# ── budgets ─────────────────────────────────────────────────────────────────

def test_budget_blocks_when_rpd_is_reached(providers_file, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "generator_providers", "alpha:a-chat")
    c, session = _client(providers_file, tmp_path, [_ok(f"r{i}") for i in range(12)])
    # a-chat declares rpm: 5 and rpd: 10. Advance the limiter's clock between
    # calls so the per-minute window never binds and the DAILY limit is the
    # one under test.
    for i in range(10):
        c.complete(role="generator", messages=[{"role": "user", "content": f"q{i}"}])
        c.budget.advance_for_tests(61.0)
    with pytest.raises(LLMBudgetExceeded):
        c.complete(role="generator", messages=[{"role": "user", "content": "q-last"}])
    assert len(session.requests) == 10


def test_budget_blocks_when_rpm_is_reached(providers_file, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "generator_providers", "alpha:a-chat")
    c, session = _client(providers_file, tmp_path, [_ok(f"r{i}") for i in range(8)])
    # rpm: 5, and the clock is not advanced, so the minute window binds first.
    for i in range(5):
        c.complete(role="generator", messages=[{"role": "user", "content": f"q{i}"}])
    with pytest.raises(LLMBudgetExceeded):
        c.complete(role="generator", messages=[{"role": "user", "content": "q6"}])
    assert len(session.requests) == 5


def test_budget_tracker_rpm_window(monkeypatch):
    b = BudgetTracker()
    b.set_limits("p:m", Limits(rpm=2))
    assert b.check("p:m") is None
    b.record("p:m")
    b.record("p:m")
    assert b.check("p:m") is not None
    b.advance_for_tests(61.0)
    assert b.check("p:m") is None, "the minute window must expire"


def test_budget_unknown_limits_do_not_block():
    b = BudgetTracker()
    b.set_limits("p:m", Limits())      # all None = not yet measured
    for _ in range(100):
        b.record("p:m", tokens=1000)
    assert b.check("p:m", estimated_tokens=5000) is None, (
        "unknown limits must not be treated as zero"
    )


def test_budget_learns_from_headers():
    b = BudgetTracker()
    b.learn_from_headers("p:m", {"x-ratelimit-limit-requests": "1000",
                                 "x-ratelimit-limit-tokens": "250000"})
    limits = b.get_limits("p:m")
    assert limits.rpd == 1000 and limits.tpm == 250000


def test_budget_ignores_unparseable_headers():
    b = BudgetTracker()
    b.set_limits("p:m", Limits(rpd=7))
    b.learn_from_headers("p:m", {"x-ratelimit-limit-requests": "lots"})
    assert b.get_limits("p:m").rpd == 7, "a bad header must not overwrite a known limit"


# ── JSON handling ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ('{"a":1}', {"a": 1}),
    ('```json\n{"a":1}\n```', {"a": 1}),
    ('prose then {"a":1} after', {"a": 1}),
    ('  {"a": {"b": 2}}  ', {"a": {"b": 2}}),
])
def test_extract_json_variants(raw, expected):
    assert extract_json(raw) == expected


@pytest.mark.parametrize("raw", ["not json at all", "", "[1,2,3]", "{broken"])
def test_extract_json_rejects_non_objects(raw):
    with pytest.raises(LLMBadOutput):
        extract_json(raw)


def test_json_object_request_sets_response_format(providers_file, tmp_path):
    c, session = _client(providers_file, tmp_path, [_ok('{"ok":true}')])
    c.complete(role="router", messages=[{"role": "user", "content": "q"}],
               json_object=True)
    assert session.requests[0]["json"]["response_format"] == {"type": "json_object"}


def test_complete_json_repairs_once(providers_file, tmp_path):
    c, session = _client(providers_file, tmp_path, [_ok("sorry, no json"), _ok('{"ok":1}')])
    data, _completion = c.complete_json(
        role="router", messages=[{"role": "user", "content": "q"}])
    assert data == {"ok": 1}
    assert len(session.requests) == 2
    # the repair turn must show the model its own bad reply
    assert any("not valid JSON" in m["content"]
               for m in session.requests[1]["json"]["messages"])


def test_complete_json_raises_after_a_failed_repair(providers_file, tmp_path):
    c, _ = _client(providers_file, tmp_path, [_ok("nope"), _ok("still nope")])
    with pytest.raises(LLMBadOutput):
        c.complete_json(role="router", messages=[{"role": "user", "content": "q"}])


def test_no_secret_in_the_recorded_request_headers(providers_file, tmp_path):
    """The key must be sent, but tests must never assert on its value."""
    c, session = _client(providers_file, tmp_path, [_ok()])
    c.complete(role="generator", messages=[{"role": "user", "content": "q"}])
    auth = session.requests[0]["headers"]["Authorization"]
    assert auth.startswith("Bearer ")
