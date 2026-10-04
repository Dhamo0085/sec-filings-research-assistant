"""T4-14: an oversize prompt is named, not mislabeled as a rate limit (P4-16).

Defect E cost most of a day because the symptom lied. A prompt of ~831,000
tokens was refused by every provider on size, each refusal was recorded as a
budget stop, and the last one was raised as `LLMBudgetExceeded` — a subclass
of `LLMRateLimited`, so the runner marked the item `llm_rate_limited` and a
paced retry was the obvious (and useless) response.

A prompt that no provider could ever accept is not a rate limit. It will not
succeed in a minute, an hour or tomorrow, it should consume no failover
attempt, and it must say what it is.
"""

from __future__ import annotations

import pytest

from answering.outcome import ErrorCode
from llm.errors import LLMBudgetExceeded, LLMPromptTooLarge, LLMRateLimited


def test_the_new_error_is_not_a_rate_limit():
    """The whole point. If it inherited from LLMRateLimited, every runner that
    retries rate-limited items would retry it forever."""
    exc = LLMPromptTooLarge("too big", provider="groq", model="m")
    assert not isinstance(exc, LLMRateLimited)


def test_the_error_code_exists_and_is_distinct():
    assert ErrorCode.LLM_PROMPT_TOO_LARGE.value == "llm_prompt_too_large"
    assert ErrorCode.LLM_PROMPT_TOO_LARGE != ErrorCode.LLM_RATE_LIMITED


def test_query_maps_the_exception_to_the_new_code():
    import query as Q
    assert Q.error_code_for(
        LLMPromptTooLarge("x")) is ErrorCode.LLM_PROMPT_TOO_LARGE


def test_a_genuine_rate_limit_is_still_labeled_a_rate_limit():
    """Negative control: the new code must not swallow the old one."""
    import query as Q
    assert Q.error_code_for(LLMRateLimited("x")) is ErrorCode.LLM_RATE_LIMITED
    assert Q.error_code_for(LLMBudgetExceeded("x")) is ErrorCode.LLM_RATE_LIMITED


def test_the_refusal_message_tells_the_user_it_is_not_a_wait():
    from answering.abstain import ERROR_MESSAGES
    message = ERROR_MESSAGES[ErrorCode.LLM_PROMPT_TOO_LARGE].lower()
    assert "later" not in message, "an oversize prompt never clears by waiting"


def test_an_oversize_prompt_raises_before_any_request(monkeypatch):
    """No provider is called at all, so no failover attempt is consumed."""

    client = _client_with_limits(monkeypatch, tpm=8_000)
    calls = []
    monkeypatch.setattr(client, "_call_entry",
                        lambda *a, **k: calls.append(a) or None)

    huge = "x" * (8_000 * 4 * 10)       # ~80,000 tokens against an 8,000 cap
    with pytest.raises(LLMPromptTooLarge) as caught:
        client.complete(role="generator",
                        messages=[{"role": "user", "content": huge}])

    assert calls == [], "a provider was called for a prompt none could accept"
    assert "8,000" in str(caught.value) or "8000" in str(caught.value)


def test_a_prompt_that_fits_one_provider_is_not_refused(monkeypatch):
    """Negative control: the guard fires only when EVERY candidate is too
    small. A prompt one provider could take must still be tried."""
    from llm.client import Completion

    client = _client_with_limits(monkeypatch, tpm=8_000, second_tpm=250_000)
    seen = []

    def fake_call(entry, messages, *a, **k):
        seen.append(entry.key)
        return Completion(content="ok", provider=entry.provider, model=entry.model,
                          prompt_tokens=1, completion_tokens=1, cached=False,
                          attempts=1)

    monkeypatch.setattr(client, "_call_entry", fake_call)
    medium = "x" * (50_000 * 4)         # ~50,000 tokens: too big for 8k, fits 250k
    result = client.complete(role="generator",
                             messages=[{"role": "user", "content": medium}])
    assert result.content == "ok"
    assert seen, "no provider was tried although one was large enough"


def _client_with_limits(monkeypatch, *, tpm: int, second_tpm: int | None = None):
    """An LLMClient whose candidate list has known per-minute token limits."""
    from llm.budget import Limits
    from llm.client import Entry, LLMClient

    client = LLMClient.__new__(LLMClient)

    entries = [Entry(provider="groq", model="small")]
    if second_tpm is not None:
        entries.append(Entry(provider="gemini", model="big"))

    limits = {"groq:small": Limits(tpm=tpm)}
    if second_tpm is not None:
        limits["gemini:big"] = Limits(tpm=second_tpm)

    class FakeBudget:
        def get_limits(self, key):
            return limits.get(key, Limits())

        def check(self, key, estimated_tokens=0):
            lim = limits.get(key, Limits())
            if lim.tpm is not None and estimated_tokens > lim.tpm:
                return f"{key}: 0+{estimated_tokens}/{lim.tpm} tokens this minute"
            return None

        def record(self, *a, **k):
            return None

    class FakeCache:
        def get(self, key):
            return None

        def put(self, *a, **k):
            return None

        def set(self, *a, **k):
            return None

    client.budget = FakeBudget()
    client.cache = FakeCache()
    monkeypatch.setattr(client, "order_for_role", lambda role: entries,
                        raising=False)
    return client
