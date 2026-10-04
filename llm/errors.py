"""Typed LLM errors (P1-04, spec section 9 rule 3).

Why typed errors matter here: v1's routing/classifier.py caught every
exception and returned a default classification with no tickers, which
query.ask() then turned into "Which company are you asking about?". Phase 0
found that in production — all five live queries returned that message in
~0.2 s because the configured model was not available to the key. A provider
outage was indistinguishable from a user forgetting to name a company (F1).

Each error carries what a caller needs to decide: retry, fail over, or stop.
"""

from __future__ import annotations

from typing import Optional


class LLMError(RuntimeError):
    """Base class for every LLM failure."""

    def __init__(self, message: str, *, provider: str = "", model: str = "") -> None:
        self.provider = provider
        self.model = model
        where = f" [{provider}:{model}]" if provider or model else ""
        super().__init__(f"{message}{where}")


class LLMAuthError(LLMError):
    """The key is missing, invalid, or lacks access to the requested model.

    Not retryable and not worth failing over on the same provider: a 401, or a
    404 "model does not exist or you do not have access to it", means the
    configuration is wrong. Phase 0's live outage was exactly this.
    """


class LLMRateLimited(LLMError):
    """A rate or quota limit was hit. `retry_after` is seconds, when known."""

    def __init__(self, message: str, *, retry_after: Optional[float] = None,
                 provider: str = "", model: str = "", daily: bool = False) -> None:
        self.retry_after = retry_after
        self.daily = daily
        super().__init__(message, provider=provider, model=model)


class LLMUnavailable(LLMError):
    """Transport failure or a 5xx: the provider is reachable-but-broken."""


class LLMBadOutput(LLMError):
    """The response did not match the requested schema.

    One repair retry is allowed (spec section 9 rule 5); after that this
    propagates so the caller reports `llm_bad_output` rather than guessing.
    """

    def __init__(self, message: str, *, raw: str = "", provider: str = "",
                 model: str = "") -> None:
        self.raw = raw
        super().__init__(message, provider=provider, model=model)


class LLMEmptyOutput(LLMBadOutput):
    """The model returned no answer text.

    Distinguished from a malformed reply because the cause and the remedy
    differ. Reasoning models (Groq's gpt-oss family) emit their chain of
    thought before the answer and bill it against max_tokens, so a budget that
    is merely generous can still be consumed entirely by reasoning, leaving
    message.content empty with finish_reason="length".

    P1-00 measured this on v1's unmodified classifier: with max_tokens=200,
    three of four questions returned 0 characters of content, which v1's
    `except Exception` turned into "Which company are you asking about?".
    Raising the budget to 700 fixed two of them and "What are Apple's
    reportable segments?" still overflowed.

    A repair retry cannot help -- the model would reason just as long again --
    so this is a reason to FAIL OVER to the next entry, which may not be a
    reasoning model. A malformed-but-present reply still gets the repair retry.
    """


class LLMBudgetExceeded(LLMRateLimited):
    """Our own configured budget stopped the call before the provider did.

    Distinct from LLMRateLimited so a runner can tell "we chose to stop" from
    "the provider refused", and mark remaining work not_run (CLAUDE.md rule 7).
    """


class LLMPromptTooLarge(LLMError):
    """The prompt exceeds every candidate provider's per-minute token limit.

    Deliberately NOT a subclass of :class:`LLMRateLimited`. A rate limit says
    "not now"; this says "not ever, as built". P4-16 exists because the two
    were conflated: a ~831,000-token prompt was refused by all three providers
    on size, each refusal recorded as a budget stop, and the last raised as
    ``LLMBudgetExceeded`` — so the runner marked the item ``llm_rate_limited``
    and a paced retry looked like the fix. It recovered 1 item of 15.

    Raised before any request, so it consumes no failover attempt.
    """
