"""Multi-provider, free-tier LLM client (P1-04, spec section 9).

One client for the whole project: no other module imports a provider SDK. It
speaks the OpenAI-compatible chat-completions API over plain HTTP against the
``base_url`` of each provider in ``llm/providers.yaml``, so a new provider is a
configuration change.

What it provides:

* **Ordered failover per role.** ``complete(role="router", ...)`` walks that
  role's ``provider:model`` list. A rate limit or a 5xx moves to the next
  entry; when every entry is exhausted it raises ``LLMRateLimited``.
* **Capability registry.** A role declares what it needs (e.g. ``json_object``)
  and an entry lacking it is skipped, so a role cannot be silently served by a
  model that cannot honour its contract.
* **Disk cache.** A hit costs zero tokens and never touches the network.
* **Per-(provider, model) budgets**, seeded from config and corrected from
  rate-limit response headers.
* **Typed errors** (``llm/errors.py``) instead of v1's blanket
  ``except Exception``, which turned a provider outage into the message
  "Which company are you asking about?" (Phase 0 F1).
* **Pinned mode** for evaluation: ``failover=False`` uses exactly one entry, so
  a reported metric belongs to one model (spec section 9 rule 10).

Reasoning models (Groq's ``openai/gpt-oss-*``) return their chain of thought in
a separate ``reasoning`` field; only ``message.content`` is returned here, and
the reasoning text is never parsed as the answer.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
import yaml
from loguru import logger

from config import settings
from llm.budget import BudgetTracker, Limits
from llm.cache import ResponseCache, cache_key
from llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMEmptyOutput,
    LLMRateLimited,
    LLMUnavailable,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROVIDERS_FILE = _PACKAGE_DIR / "providers.yaml"
# Owner-maintained overrides (gitignored): measured free-tier limits.
_LOCAL_LIMITS_FILE = _PACKAGE_DIR / "limits.local.yaml"

MAX_ATTEMPTS_PER_ENTRY = 3
DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class Entry:
    """One provider:model candidate."""

    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    def __str__(self) -> str:  # pragma: no cover - debug aid
        return self.key


@dataclass
class Completion:
    """A successful completion plus the accounting a report needs."""

    content: str
    provider: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cached: bool = False
    latency_s: float = 0.0
    attempts: int = 1

    @property
    def total_tokens(self) -> int:
        return (self.prompt_tokens or 0) + (self.completion_tokens or 0)


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class LLMClient:
    """The single entry point for every LLM call in the project."""

    def __init__(
        self,
        *,
        providers_file: Path = _PROVIDERS_FILE,
        cache_path: Optional[Path] = None,
        session: Optional[requests.Session] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._registry = _load_yaml(providers_file)
        self._providers: Dict[str, Any] = self._registry.get("providers", {}) or {}
        self._roles: Dict[str, Any] = self._registry.get("roles", {}) or {}
        self._timeout = timeout
        self._session = session or requests.Session()
        self._lock = threading.Lock()

        self.cache = ResponseCache(Path(cache_path or settings.llm_cache_path))
        self.budget = BudgetTracker()
        self._seed_budgets()

    # ── configuration ──────────────────────────────────────────────────────

    def _seed_budgets(self) -> None:
        local = _load_yaml(_LOCAL_LIMITS_FILE).get("limits", {}) or {}
        for provider, pcfg in self._providers.items():
            for model, mcfg in (pcfg.get("models") or {}).items():
                key = f"{provider}:{model}"
                raw = (mcfg or {}).get("limits") or {}
                seeded = Limits(
                    rpm=raw.get("rpm"), rpd=raw.get("rpd"),
                    tpm=raw.get("tpm"), tpd=raw.get("tpd"),
                )
                override = local.get(key) or {}
                if override:
                    seeded = seeded.merged_with(Limits(
                        rpm=override.get("rpm"), rpd=override.get("rpd"),
                        tpm=override.get("tpm"), tpd=override.get("tpd"),
                    ))
                self.budget.set_limits(key, seeded)

    def _model_capabilities(self, entry: Entry) -> List[str]:
        pcfg = self._providers.get(entry.provider) or {}
        mcfg = (pcfg.get("models") or {}).get(entry.model) or {}
        return list(mcfg.get("capabilities") or [])

    def _role_requirements(self, role: str) -> List[str]:
        return list(((self._roles.get(role) or {}).get("requires")) or [])

    @staticmethod
    def _parse_order(raw: str) -> List[Entry]:
        out: List[Entry] = []
        for item in (raw or "").split(","):
            item = item.strip()
            if not item or ":" not in item:
                continue
            provider, model = item.split(":", 1)
            out.append(Entry(provider.strip(), model.strip()))
        return out

    def order_for_role(self, role: str) -> List[Entry]:
        """Resolve the ordered candidate list for a role.

        Precedence: the role's own env var, then LLM_PROVIDERS, then
        providers.yaml. Entries lacking a required capability are dropped —
        a role must not be served by a model that cannot honour its contract.
        """
        env_specific = {
            "router": settings.router_providers,
            "generator": settings.generator_providers,
            "judge": settings.judge_providers,
        }.get(role)
        raw = env_specific or settings.llm_providers
        entries = self._parse_order(raw) if raw else [
            Entry(*spec.split(":", 1))
            for spec in ((self._roles.get(role) or {}).get("order") or [])
            if ":" in spec
        ]

        required = set(self._role_requirements(role))
        usable: List[Entry] = []
        for entry in entries:
            caps = set(self._model_capabilities(entry))
            if not caps:
                # Unknown model: allow it but say so, rather than silently
                # dropping a model the owner configured on purpose.
                logger.debug(f"llm: {entry.key} is not in providers.yaml; capabilities unknown")
                usable.append(entry)
                continue
            missing = required - caps
            if missing:
                logger.warning(
                    f"llm: skipping {entry.key} for role '{role}' — missing {sorted(missing)}"
                )
                continue
            usable.append(entry)
        return usable

    def _api_key(self, provider: str) -> str:
        pcfg = self._providers.get(provider) or {}
        names = pcfg.get("api_key_env") or []
        if isinstance(names, str):
            names = [names]
        for name in names:
            value = os.environ.get(name)
            if value:
                return value.strip()
            # fall back to Settings, which already read .env
            attr = getattr(settings, name.lower(), None)
            if isinstance(attr, str) and attr.strip():
                return attr.strip()
        raise LLMAuthError(
            f"No API key configured for provider '{provider}' "
            f"(looked for {list(names)})",
            provider=provider,
        )

    # ── the call ───────────────────────────────────────────────────────────

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
        order: Optional[Sequence[Entry]] = None,
    ) -> Completion:
        """Run one chat completion for `role`.

        Args:
            failover: False pins the first entry (evaluation runs, spec
                section 9 rule 10) so a metric belongs to exactly one model.

        Raises:
            LLMAuthError: configuration is wrong (bad key, no model access).
            LLMRateLimited: every candidate was rate-limited or budget-stopped.
            LLMUnavailable: every candidate failed with a transport/5xx error.
            LLMBadOutput: json_object was requested and the reply was not JSON.
        """
        candidates = list(order) if order is not None else self.order_for_role(role)
        if not candidates:
            raise LLMUnavailable(
                f"No usable provider:model for role '{role}'. Check "
                f"llm/providers.yaml and the *_PROVIDERS environment variables."
            )
        if not failover:
            candidates = candidates[:1]

        messages = [dict(m) for m in messages]
        last_rate_limit: Optional[LLMRateLimited] = None
        last_unavailable: Optional[LLMUnavailable] = None
        last_empty: Optional[LLMEmptyOutput] = None
        auth_errors: List[LLMAuthError] = []

        for entry in candidates:
            # cache first: a hit costs nothing and bypasses budgets entirely
            key = cache_key(
                provider=entry.provider, model=entry.model, messages=messages,
                temperature=temperature, prompt_version=prompt_version,
                response_format="json_object" if json_object else None,
            )
            hit = self.cache.get(key)
            if hit:
                logger.debug(f"llm: cache hit {entry.key}")
                return Completion(
                    content=hit["content"], provider=entry.provider, model=entry.model,
                    prompt_tokens=hit.get("prompt_tokens"),
                    completion_tokens=hit.get("completion_tokens"),
                    cached=True, attempts=0,
                )

            estimated = _estimate_tokens(messages) + max_tokens
            reason = self.budget.check(entry.key, estimated_tokens=estimated)
            if reason:
                logger.warning(f"llm: budget stop for {entry.key}: {reason}")
                last_rate_limit = LLMBudgetExceeded(
                    f"Local budget exhausted: {reason}",
                    provider=entry.provider, model=entry.model,
                )
                continue

            try:
                completion = self._call_entry(
                    entry, messages, temperature, max_tokens, json_object,
                )
            except LLMAuthError as exc:
                # Configuration error: do not retry this entry, but another
                # provider may still work, so keep walking.
                logger.error(f"llm: auth/access error on {entry.key}: {exc}")
                auth_errors.append(exc)
                continue
            except LLMRateLimited as exc:
                logger.warning(f"llm: rate limited on {entry.key}: {exc}")
                last_rate_limit = exc
                continue
            except LLMEmptyOutput as exc:
                # No answer text at all. A repair retry would not help (the
                # model would reason just as long again), but the next entry
                # may not be a reasoning model, so fail over. Measured in
                # P1-00; see LLMEmptyOutput's docstring.
                logger.warning(f"llm: empty output from {entry.key}: {exc}")
                last_empty = exc
                continue
            except LLMUnavailable as exc:
                logger.warning(f"llm: unavailable {entry.key}: {exc}")
                last_unavailable = exc
                continue

            self.cache.put(
                key, provider=entry.provider, model=entry.model,
                content=completion.content, prompt_version=prompt_version,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
            )
            return completion

        # Nothing worked. Report the most actionable cause.
        if last_rate_limit is not None:
            raise last_rate_limit
        if auth_errors:
            raise auth_errors[0]
        if last_unavailable is not None:
            raise last_unavailable
        if last_empty is not None:
            raise last_empty
        raise LLMUnavailable(f"All candidates failed for role '{role}'")

    def _call_entry(
        self,
        entry: Entry,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
        json_object: bool,
    ) -> Completion:
        pcfg = self._providers.get(entry.provider) or {}
        base_url = (pcfg.get("base_url") or "").rstrip("/")
        if not base_url:
            raise LLMUnavailable(
                f"Provider '{entry.provider}' has no base_url in providers.yaml",
                provider=entry.provider, model=entry.model,
            )
        api_key = self._api_key(entry.provider)

        payload: Dict[str, Any] = {
            "model": entry.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}

        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Some providers sit behind a CDN that rejects unusual clients
            # (observed in P1-00: urllib's default agent got HTTP 403 with
            # Cloudflare code 1010 from api.groq.com).
            "User-Agent": "sec-filings-research-assistant/0.2 (+local)",
            "Accept": "application/json",
        }

        attempt = 0
        while attempt < MAX_ATTEMPTS_PER_ENTRY:
            attempt += 1
            started = time.perf_counter()
            try:
                resp = self._session.post(
                    url, json=payload, headers=headers, timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise LLMUnavailable(
                    f"transport error: {type(exc).__name__}: {exc}",
                    provider=entry.provider, model=entry.model,
                ) from exc

            self.budget.learn_from_headers(entry.key, dict(resp.headers))
            latency = time.perf_counter() - started

            if resp.status_code == 200:
                content, usage = _parse_success(resp, entry)
                self.budget.record(
                    entry.key, tokens=(usage.get("total_tokens") or 0),
                )
                # NOTE: JSON is deliberately NOT validated here. complete_json()
                # owns validation and the single repair retry (spec section 9
                # rule 5); validating here would make the repair unreachable and
                # would fail over to another provider over a parse problem.
                return Completion(
                    content=content, provider=entry.provider, model=entry.model,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    latency_s=round(latency, 3), attempts=attempt,
                )

            body = (resp.text or "")[:400]

            if resp.status_code in (401, 403):
                raise LLMAuthError(
                    f"HTTP {resp.status_code}: {body}",
                    provider=entry.provider, model=entry.model,
                )
            if resp.status_code == 404 and "model" in body.lower():
                # "The model X does not exist or you do not have access to it."
                # This is the exact failure Phase 0 found in production, so it
                # must surface as a configuration error, not a transient one.
                raise LLMAuthError(
                    f"model not available to this key: {body}",
                    provider=entry.provider, model=entry.model,
                )
            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp.headers)
                daily = "per day" in body.lower() or "daily" in body.lower()
                self.budget.record(entry.key)
                if daily or attempt >= MAX_ATTEMPTS_PER_ENTRY:
                    raise LLMRateLimited(
                        f"HTTP 429: {body}", retry_after=retry_after,
                        provider=entry.provider, model=entry.model, daily=daily,
                    )
                wait = min(retry_after or 2.0 * attempt, 30.0)
                logger.info(f"llm: 429 on {entry.key}, sleeping {wait:.1f}s "
                            f"(attempt {attempt}/{MAX_ATTEMPTS_PER_ENTRY})")
                time.sleep(wait)
                continue
            if 500 <= resp.status_code < 600:
                if attempt >= MAX_ATTEMPTS_PER_ENTRY:
                    raise LLMUnavailable(
                        f"HTTP {resp.status_code}: {body}",
                        provider=entry.provider, model=entry.model,
                    )
                time.sleep(min(2.0 * attempt, 10.0))
                continue

            raise LLMUnavailable(
                f"HTTP {resp.status_code}: {body}",
                provider=entry.provider, model=entry.model,
            )

        raise LLMUnavailable(
            f"exhausted {MAX_ATTEMPTS_PER_ENTRY} attempts",
            provider=entry.provider, model=entry.model,
        )

    # ── convenience ────────────────────────────────────────────────────────

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
        """Complete and parse JSON, with at most one repair retry.

        Spec section 9 rule 5: an invalid structured output raises
        LLMBadOutput after one repair attempt, rather than being guessed at.
        """
        completion = self.complete(
            role=role, messages=messages, temperature=temperature,
            max_tokens=max_tokens, json_object=True,
            prompt_version=prompt_version, failover=failover,
        )
        try:
            return extract_json(completion.content), completion
        except LLMBadOutput:
            if not repair:
                raise
        repair_messages = [
            *messages,
            {"role": "assistant", "content": completion.content},
            {"role": "user",
             "content": "That was not valid JSON. Reply with ONLY the JSON object."},
        ]
        completion2 = self.complete(
            role=role, messages=repair_messages, temperature=temperature,
            max_tokens=max_tokens, json_object=True,
            prompt_version=prompt_version, failover=failover,
        )
        return extract_json(completion2.content), completion2

    def usage_snapshot(self) -> Dict[str, Dict[str, int]]:
        return self.budget.snapshot()


# ── helpers ────────────────────────────────────────────────────────────────

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> Dict[str, Any]:
    """Parse a JSON object from a model reply.

    Tries the whole string first and only then the outermost braces, because
    the greedy-brace shortcut alone misreads a reply that contains prose with
    braces.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(raw)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise LLMBadOutput("reply was not a JSON object", raw=raw[:400])


def _parse_success(resp: requests.Response, entry: Entry) -> Tuple[str, Dict[str, Any]]:
    try:
        data = resp.json()
    except ValueError as exc:
        raise LLMBadOutput(
            "response body was not JSON", raw=(resp.text or "")[:400],
            provider=entry.provider, model=entry.model,
        ) from exc
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMBadOutput(
            "response had no choices[0].message", raw=json.dumps(data)[:400],
            provider=entry.provider, model=entry.model,
        ) from exc

    # Reasoning models put the chain of thought in a sibling field. Only the
    # answer text is returned; the reasoning is never parsed as the answer.
    content = (message.get("content") or "").strip()
    if not content:
        finish = (data.get("choices") or [{}])[0].get("finish_reason")
        raise LLMEmptyOutput(
            f"message.content was empty (finish_reason={finish!r}); on a "
            f"reasoning model max_tokens was likely consumed by the chain of "
            f"thought before any answer was emitted",
            raw=json.dumps(data)[:400], provider=entry.provider, model=entry.model,
        )
    usage = data.get("usage") or {}
    return content, {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens")
        or (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
    }


def _retry_after_seconds(headers) -> Optional[float]:
    raw = None
    for name in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
        if headers and name in headers:
            raw = headers[name]
            break
    if raw is None:
        return None
    try:
        return float(str(raw).strip().rstrip("s"))
    except (TypeError, ValueError):
        return None


def _estimate_tokens(messages: Sequence[Dict[str, str]]) -> int:
    """Rough pre-call estimate for budget checks (~4 chars per token)."""
    chars = sum(len(m.get("content") or "") for m in messages)
    return max(1, chars // 4)


# ── module-level singleton ─────────────────────────────────────────────────

_client: Optional[LLMClient] = None
_client_lock = threading.Lock()


def get_client() -> LLMClient:
    """Process-wide client. Built lazily so importing this module is cheap."""
    global _client
    with _client_lock:
        if _client is None:
            _client = LLMClient()
        return _client


def reset_client() -> None:
    """Drop the singleton (tests, and after a settings change)."""
    global _client
    with _client_lock:
        _client = None
