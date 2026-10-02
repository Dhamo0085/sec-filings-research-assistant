"""Per-(provider, model) request and token budgets (P1-04, spec section 9 rule 4).

Each provider:model entry has its own quota, so failover can walk across models
of the same provider. Limits are seeded from llm/providers.yaml (itself seeded
from spec Appendix A), overridden by llm/limits.local.yaml, and corrected at
runtime from rate-limit response headers.

Appendix A's planning note is the reason this tracks requests as well as
tokens: on the free tiers, requests per day are the binding limit, not tokens.

Unknown limits (`null`) mean "not yet measured" and are NOT treated as zero —
the call proceeds and the response headers teach us the real number. Treating
unknown as zero would block every call on the Groq models, whose free-plan
limits are not published.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger

_MINUTE = 60.0
_DAY = 86400.0


@dataclass
class Limits:
    rpm: Optional[int] = None
    rpd: Optional[int] = None
    tpm: Optional[int] = None
    tpd: Optional[int] = None

    def merged_with(self, other: "Limits") -> "Limits":
        """`other` wins where it has a value."""
        return Limits(
            rpm=other.rpm if other.rpm is not None else self.rpm,
            rpd=other.rpd if other.rpd is not None else self.rpd,
            tpm=other.tpm if other.tpm is not None else self.tpm,
            tpd=other.tpd if other.tpd is not None else self.tpd,
        )


@dataclass
class _Usage:
    request_times: List[float] = field(default_factory=list)
    token_events: List[tuple] = field(default_factory=list)   # (timestamp, tokens)


class BudgetTracker:
    """Sliding-window accounting per provider:model key."""

    def __init__(self) -> None:
        self._limits: Dict[str, Limits] = {}
        self._usage: Dict[str, _Usage] = {}
        self._lock = threading.Lock()
        self._offset = 0.0

    # -- configuration ------------------------------------------------------
    def set_limits(self, key: str, limits: Limits) -> None:
        with self._lock:
            self._limits[key] = limits

    def get_limits(self, key: str) -> Limits:
        with self._lock:
            return self._limits.get(key, Limits())

    def learn_from_headers(self, key: str, headers: Dict[str, str]) -> None:
        """Correct limits from provider rate-limit headers.

        Providers expose these inconsistently (x-ratelimit-limit-requests,
        x-ratelimit-limit-tokens, ...), so anything unparseable is ignored
        rather than guessed at.
        """
        lowered = {k.lower(): v for k, v in (headers or {}).items()}
        found = Limits()
        for header, attr in (
            ("x-ratelimit-limit-requests", "rpd"),
            ("x-ratelimit-limit-tokens", "tpm"),
        ):
            raw = lowered.get(header)
            if raw is None:
                continue
            try:
                setattr(found, attr, int(float(str(raw).strip())))
            except (TypeError, ValueError):
                continue
        if found == Limits():
            return
        with self._lock:
            current = self._limits.get(key, Limits())
            self._limits[key] = current.merged_with(found)
        logger.debug(f"budget: learned limits for {key} from headers: {found}")

    # -- clock (tests advance it instead of sleeping) -----------------------
    def _now(self) -> float:
        return time.monotonic() + self._offset

    def advance_for_tests(self, seconds: float) -> None:
        with self._lock:
            self._offset += seconds

    # -- accounting ---------------------------------------------------------
    def _prune(self, usage: _Usage, now: float) -> None:
        day_cutoff = now - _DAY
        usage.request_times = [t for t in usage.request_times if t > day_cutoff]
        usage.token_events = [(t, n) for (t, n) in usage.token_events if t > day_cutoff]

    def check(self, key: str, estimated_tokens: int = 0) -> Optional[str]:
        """Return None if a call may proceed, else a human-readable reason."""
        limits = self.get_limits(key)
        now = self._now()
        with self._lock:
            usage = self._usage.setdefault(key, _Usage())
            self._prune(usage, now)
            minute_cutoff = now - _MINUTE
            reqs_minute = sum(1 for t in usage.request_times if t > minute_cutoff)
            reqs_day = len(usage.request_times)
            toks_minute = sum(n for (t, n) in usage.token_events if t > minute_cutoff)
            toks_day = sum(n for (_t, n) in usage.token_events)

        if limits.rpm is not None and reqs_minute >= limits.rpm:
            return f"{key}: {reqs_minute}/{limits.rpm} requests this minute"
        if limits.rpd is not None and reqs_day >= limits.rpd:
            return f"{key}: {reqs_day}/{limits.rpd} requests today"
        if limits.tpm is not None and toks_minute + estimated_tokens > limits.tpm:
            return f"{key}: {toks_minute}+{estimated_tokens}/{limits.tpm} tokens this minute"
        if limits.tpd is not None and toks_day + estimated_tokens > limits.tpd:
            return f"{key}: {toks_day}+{estimated_tokens}/{limits.tpd} tokens today"
        return None

    def record(self, key: str, tokens: int = 0) -> None:
        now = self._now()
        with self._lock:
            usage = self._usage.setdefault(key, _Usage())
            usage.request_times.append(now)
            if tokens:
                usage.token_events.append((now, tokens))
            self._prune(usage, now)

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        """Usage so far, for the phase report."""
        now = self._now()
        out: Dict[str, Dict[str, int]] = {}
        with self._lock:
            for key, usage in self._usage.items():
                self._prune(usage, now)
                out[key] = {
                    "requests_today": len(usage.request_times),
                    "tokens_today": sum(n for (_t, n) in usage.token_events),
                }
        return out

    def reset(self) -> None:
        with self._lock:
            self._usage.clear()
            self._offset = 0.0
