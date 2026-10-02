"""LLM access for the whole project (P1-04, spec section 9).

Import the client from here; no other module should import a provider SDK.
"""

from llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMEmptyOutput,
    LLMError,
    LLMRateLimited,
    LLMUnavailable,
)

__all__ = [
    "LLMAuthError",
    "LLMBadOutput",
    "LLMBudgetExceeded",
    "LLMEmptyOutput",
    "LLMError",
    "LLMRateLimited",
    "LLMUnavailable",
    "get_client",
    "reset_client",
]


def get_client():
    """Lazy re-export so `import llm` does not pull in requests/yaml."""
    from llm.client import get_client as _get
    return _get()


def reset_client():
    from llm.client import reset_client as _reset
    return _reset()
