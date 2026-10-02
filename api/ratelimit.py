"""Per-client rate limiting for POST /query (P1-06, spec section 8).

A fixed-window counter keyed by client IP. Deliberately simple: this is a
local-first, single-process deployment (D18), so an in-process dict is honest
about what it can promise. It is NOT a distributed limiter — behind multiple
workers each process keeps its own budget, which is recorded as a limitation
rather than hidden.

The limiter owns its clock so tests can advance it instead of sleeping.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

WINDOW_SECONDS = 60.0


class FixedWindowLimiter:
    """Allow at most `limit` events per `WINDOW_SECONDS` per key."""

    def __init__(self) -> None:
        self._hits: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._offset = 0.0

    def _now(self) -> float:
        return time.monotonic() + self._offset

    def advance_for_tests(self, seconds: float) -> None:
        """Move the limiter's clock forward (tests only)."""
        with self._lock:
            self._offset += seconds

    def check(self, key: str, limit: int) -> Tuple[bool, int]:
        """Record a hit for `key`.

        Returns:
            (allowed, retry_after_seconds). retry_after is 0 when allowed.
        """
        if limit <= 0:
            return True, 0
        now = self._now()
        cutoff = now - WINDOW_SECONDS
        with self._lock:
            recent = [t for t in self._hits[key] if t > cutoff]
            if len(recent) >= limit:
                oldest = min(recent)
                retry_after = max(1, int(WINDOW_SECONDS - (now - oldest)) + 1)
                self._hits[key] = recent
                return False, retry_after
            recent.append(now)
            self._hits[key] = recent
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._offset = 0.0


# Module-level singleton: one budget per process.
_LIMITER = FixedWindowLimiter()


def client_key(request) -> str:
    """Identify the caller.

    Uses the socket peer address. X-Forwarded-For is deliberately NOT trusted:
    there is no reverse proxy in the local-first deployment, so honouring it
    would let any caller spoof an identity and reset their own budget.
    """
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def check_rate_limit(request, limit: int) -> Tuple[bool, int]:
    return _LIMITER.check(client_key(request), limit)


def reset_for_tests() -> None:
    _LIMITER.reset()
