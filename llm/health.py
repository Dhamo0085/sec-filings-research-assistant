"""LLM health probe for GET /health (P1-05).

Spec P1-05 requires /health to report `llm: ok | auth_error | rate_limited |
unreachable` from a cached, no-token probe — never one probe per request,
which on a free tier would spend the day's quota on health checks.

The probe lists models (a GET, no completion, no tokens) against the first
provider configured for the generator role.
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

from loguru import logger

from llm.errors import LLMAuthError, LLMError, LLMRateLimited, LLMUnavailable

PROBE_TTL_SECONDS = 60.0

_lock = threading.Lock()
_cache: Optional[Tuple[float, str]] = None
_offset = 0.0


def _now() -> float:
    return time.monotonic() + _offset


def advance_for_tests(seconds: float) -> None:
    global _offset
    with _lock:
        _offset += seconds


def reset_probe_cache() -> None:
    global _cache, _offset
    with _lock:
        _cache = None
        _offset = 0.0


def _probe_models() -> bool:
    """GET the provider's model list. Returns True, or raises a typed error."""
    import requests

    from llm.client import get_client

    client = get_client()
    order = client.order_for_role("generator")
    if not order:
        raise LLMUnavailable("no provider configured for the generator role")
    entry = order[0]
    pcfg = client._providers.get(entry.provider) or {}
    base_url = (pcfg.get("base_url") or "").rstrip("/")
    if not base_url:
        raise LLMUnavailable(f"provider '{entry.provider}' has no base_url")
    api_key = client._api_key(entry.provider)
    resp = requests.get(
        f"{base_url}/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "sec-filings-research-assistant/0.2 (+local)",
            "Accept": "application/json",
        },
        timeout=15,
    )
    if resp.status_code in (401, 403):
        raise LLMAuthError(f"HTTP {resp.status_code}", provider=entry.provider)
    if resp.status_code == 429:
        raise LLMRateLimited("HTTP 429", provider=entry.provider)
    if resp.status_code != 200:
        raise LLMUnavailable(f"HTTP {resp.status_code}", provider=entry.provider)
    return True


def llm_state() -> str:
    """Cached LLM state string for /health."""
    global _cache
    with _lock:
        if _cache is not None and _now() - _cache[0] < PROBE_TTL_SECONDS:
            return _cache[1]

    try:
        _probe_models()
        state = "ok"
    except LLMAuthError:
        state = "auth_error"
    except LLMRateLimited:
        state = "rate_limited"
    except LLMUnavailable:
        state = "unreachable"
    except LLMError as exc:
        logger.warning(f"health: unexpected LLM error during probe: {exc}")
        state = "unreachable"
    except Exception as exc:
        # A probe must never take /health down with it.
        logger.warning(f"health: probe failed: {type(exc).__name__}: {exc}")
        state = "unreachable"

    with _lock:
        _cache = (_now(), state)
    return state
