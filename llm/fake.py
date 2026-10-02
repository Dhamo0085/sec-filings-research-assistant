"""Deterministic fake LLM for offline tests (P1-04, spec section 9 rule 8).

Every offline test uses this instead of a provider, so `make test` runs with
sockets blocked and no .env. It is API-compatible with LLMClient for the two
methods callers use (`complete`, `complete_json`).

Two behaviours matter for the tests this project needs:

* **Determinism.** The same messages always produce the same reply, so a
  runner's resume path can be asserted to give identical results (T4-03).
* **Scripted failures.** `raises` makes the fake throw a typed error, which is
  how T1-02 checks that a provider outage becomes `status=error` with the right
  `error_code` rather than v1's "Which company are you asking about?".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from llm.client import Completion, extract_json


class FakeLLM:
    """Scripted, deterministic stand-in for LLMClient."""

    def __init__(
        self,
        *,
        responses: Optional[Dict[str, str]] = None,
        default: Optional[str] = None,
        raises: Optional[BaseException] = None,
        responder: Optional[Callable[[str, List[Dict[str, str]]], str]] = None,
        provider: str = "fake",
        model: str = "fake-model",
    ) -> None:
        """
        Args:
            responses: substring of the last user message -> reply.
            default: reply when nothing matches.
            raises: raise this instead of replying (outage simulation).
            responder: callable(role, messages) -> reply, for dynamic cases.
        """
        self.responses = responses or {}
        self.default = default
        self.raises = raises
        self.responder = responder
        self.provider = provider
        self.model = model
        self.calls: List[Dict[str, Any]] = []

    # -- LLMClient-compatible surface --------------------------------------

    def complete(
        self,
        *,
        role: str,
        messages: Sequence[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_object: bool = False,
        prompt_version: str = "",
        failover: bool = True,
        order: Optional[Sequence[Any]] = None,
    ) -> Completion:
        self.calls.append({
            "role": role, "messages": [dict(m) for m in messages],
            "temperature": temperature, "max_tokens": max_tokens,
            "json_object": json_object, "prompt_version": prompt_version,
            "failover": failover,
        })
        if self.raises is not None:
            raise self.raises

        content = self._pick(role, [dict(m) for m in messages])
        return Completion(
            content=content, provider=self.provider, model=self.model,
            prompt_tokens=_tokens(messages), completion_tokens=_tokens([{"content": content}]),
            cached=False, latency_s=0.0, attempts=1,
        )

    def complete_json(
        self,
        *,
        role: str,
        messages: Sequence[Dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 1024,
        prompt_version: str = "",
        failover: bool = True,
        repair: bool = True,
    ) -> Tuple[Dict[str, Any], Completion]:
        completion = self.complete(
            role=role, messages=messages, temperature=temperature,
            max_tokens=max_tokens, json_object=True,
            prompt_version=prompt_version, failover=failover,
        )
        return extract_json(completion.content), completion

    def usage_snapshot(self) -> Dict[str, Dict[str, int]]:
        return {f"{self.provider}:{self.model}": {
            "requests_today": len(self.calls), "tokens_today": 0,
        }}

    # -- internals ---------------------------------------------------------

    def _pick(self, role: str, messages: List[Dict[str, str]]) -> str:
        if self.responder is not None:
            return self.responder(role, messages)
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        for needle, reply in self.responses.items():
            if needle in last_user:
                return reply
        if self.default is not None:
            return self.default
        # Deterministic fallback: a stable digest so a test that does not care
        # about content still gets repeatable output.
        digest = hashlib.sha256(
            json.dumps(messages, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        return f"fake-reply-{digest}"


def _tokens(messages: Sequence[Dict[str, str]]) -> int:
    return max(1, sum(len(m.get("content") or "") for m in messages) // 4)
