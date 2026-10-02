"""
Query entry point — routes a question through the full pipeline.

    python query.py "What was Apple's revenue in FY2024?"
    python query.py "Compare Microsoft and Google R&D spend in 2024"
    python query.py "How did Amazon's operating margin trend from 2023 to 2025?"
"""

import re
import sys

from loguru import logger

from config import settings
from generation.generator import generate_answer
from generation.synthesizer import synthesize
from llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMError,
    LLMRateLimited,
    LLMUnavailable,
)
from models import QueryResult
from retrieval.retriever import retrieve
from routing.resolver import classify_and_ensure

# Catches the model's own "not found" phrasing so a refused single_doc
# answer can trigger one broadened retry instead of being accepted as final.
# Deliberately over-inclusive (false positives just cost one extra, usually-
# redundant retry; false negatives silently ship a wrong "not found").
_REFUSAL_PATTERN = re.compile(
    r"cannot be found|cannot be determined|can.t be found|"
    r"does not (?:explicitly )?(?:mention|state|provide|discuss|contain)|"
    r"not explicitly (?:mentioned|stated)|no relevant information|"
    r"is not (?:mentioned|provided|available|explicitly stated) in the",
    re.IGNORECASE,
)


def _is_refusal(answer: str) -> bool:
    return bool(_REFUSAL_PATTERN.search(answer))


# Spec section 6.5 error_code values, by exception type. Ordered most specific
# first because LLMBudgetExceeded subclasses LLMRateLimited.
_ERROR_CODES = (
    (LLMAuthError, "llm_auth"),
    (LLMBudgetExceeded, "llm_rate_limited"),
    (LLMRateLimited, "llm_rate_limited"),
    (LLMBadOutput, "llm_bad_output"),
    (LLMUnavailable, "llm_unavailable"),
)

_ERROR_MESSAGES = {
    "llm_auth": (
        "I can't answer right now: the language model rejected our credentials "
        "or the configured model isn't available to this key. This is a "
        "configuration problem, not a problem with your question."
    ),
    "llm_rate_limited": (
        "I can't answer right now: the free-tier request limit for the language "
        "model has been reached. Please try again later."
    ),
    "llm_unavailable": (
        "I can't answer right now: the language model is unreachable. "
        "Please try again shortly."
    ),
    "llm_bad_output": (
        "I can't answer right now: the language model returned a malformed "
        "response. Please try again."
    ),
    "internal": (
        "I can't answer right now because of an internal error. "
        "The details have been logged."
    ),
}


def _error_result(query: str, exc: BaseException) -> QueryResult:
    """Build the status=error outcome for a dependency failure (D7, G4)."""
    code = "internal"
    for exc_type, mapped in _ERROR_CODES:
        if isinstance(exc, exc_type):
            code = mapped
            break
    logger.error(f"Pipeline dependency failure [{code}]: {type(exc).__name__}: {exc}")
    return QueryResult(
        query=query,
        answer=_ERROR_MESSAGES[code],
        citations=[],
        chunks_used=[],
        query_type="error",
        status="error",
        error_code=code,
    )


def ask(query: str) -> QueryResult:
    """Run a query through the full RAG pipeline and return a QueryResult.

    A dependency failure (LLM auth, rate limit, outage, malformed output)
    returns status="error" with an error_code rather than a plausible-looking
    answer or a request for clarification. v1 returned the clarification
    message for every failure, which Phase 0 found live in production (F1).
    """
    try:
        return _ask_inner(query)
    except LLMError as exc:
        return _error_result(query, exc)
    except Exception as exc:
        return _error_result(query, exc)


def _ask_inner(query: str) -> QueryResult:
    # 1 — Classify (auto-ingesting any company outside the bundled 12)
    classification = classify_and_ensure(query)
    logger.info(
        f"Query type : {classification.query_type}\n"
        f"Tickers    : {classification.tickers}\n"
        f"Years      : {classification.years}\n"
        f"Reasoning  : {classification.reasoning}"
    )

    # 2 — Route
    if classification.query_type == "out_of_scope":
        return QueryResult(
            query=query,
            answer="This query is outside the scope of the available SEC 10-K filings.",
            citations=[],
            chunks_used=[],
            query_type="out_of_scope",
            status="abstained",
        )

    # A company was named but couldn't be resolved to any SEC filer (e.g. a
    # private company like SpaceX) or has no 10-K on file — without this,
    # an empty tickers list falls through to _target_collections()'s
    # "years only" branch, which searches EVERY indexed company for the
    # given years. That's the right behavior for a genuinely cross-company
    # query, but wrong here: it silently returns irrelevant citations from
    # unrelated companies and burns two Groq calls (including the refusal
    # retry) on a query we already know we can't answer.
    if not classification.tickers and (
        classification.failed_lookups or classification.ingest_failed or classification.year_not_available
    ):
        parts = []
        if classification.failed_lookups:
            names = ", ".join(classification.failed_lookups)
            parts.append(
                f"I couldn't find any SEC filings for {names} — it doesn't appear "
                f"to be a public company that files with the SEC, or its filings "
                f"aren't available here."
            )
        if classification.ingest_failed:
            names = ", ".join(classification.ingest_failed)
            parts.append(
                f"I found that {names} files with the SEC, but hit a technical "
                f"problem fetching or indexing its 10-K just now — please try "
                f"again in a moment."
            )
        if classification.year_not_available:
            details = "; ".join(classification.year_not_available)
            parts.append(f"I don't have that fiscal year for: {details}.")
        return QueryResult(
            query=query,
            answer=" ".join(parts),
            citations=[],
            chunks_used=[],
            query_type="unresolved_company",
            status="abstained",
        )

    # A single_doc/summarization query is defined as being about ONE
    # specific company — if the classifier landed here with no ticker at
    # all (and no failed lookup to explain it, e.g. no company named in the
    # first place), the same "search every company" fallback applies. Ask
    # instead of guessing.
    if classification.query_type in ("single_doc", "summarization") and not classification.tickers:
        return QueryResult(
            query=query,
            answer="Which company are you asking about? I can only answer from indexed SEC 10-K filings for a specific, named company.",
            citations=[],
            chunks_used=[],
            query_type="clarification_needed",
            status="clarification_needed",
        )

    if classification.query_type in ("multi_doc", "temporal"):
        result = synthesize(
            query=query,
            tickers=classification.tickers,
            years=classification.years,
            query_type=classification.query_type,
            focus=classification.focus,
        )
    else:
        # single_doc or summarization — standard retrieval.
        # "single_doc" queries can still span multiple years (e.g. "most
        # recent" expands to all 3 years, or the user lists several years
        # explicitly without phrasing it as a trend). top_k must scale with
        # that, or years/tickers beyond the first few get starved out by the
        # fixed default — capped at 5x to bound context size/cost.
        n_targets = max(1, len(classification.tickers)) * max(1, len(classification.years))
        top_k = settings.rerank_top_k * min(n_targets, 5)
        retrieved = retrieve(
            query=query,
            tickers=classification.tickers,
            years=classification.years,
            top_k=top_k,
            focus=classification.focus,
        )
        result = generate_answer(
            query=query,
            retrieved=retrieved,
            query_type=classification.query_type,
        )

        # One bounded retry: a narrow focus boost can mis-target the wrong
        # section, or top_k can just be too tight — before accepting "not
        # found" as final, retry once with the focus restriction dropped and
        # top_k widened. Capped at a single retry so a genuinely
        # out-of-scope query still fails fast rather than doubling Groq
        # cost on every miss.
        if _is_refusal(result.answer):
            logger.info(f"Refusal detected, retrying with broadened search: '{query[:60]}'")
            retried = retrieve(
                query=query,
                tickers=classification.tickers,
                years=classification.years,
                top_k=min(top_k * 2, 15),
                focus="other",
            )
            retried_result = generate_answer(
                query=query,
                retrieved=retried,
                query_type=classification.query_type,
            )
            if not _is_refusal(retried_result.answer):
                result = retried_result

    # Partial failure: some named company mentions resolved fine and the
    # query above proceeded on those, but at least one other explicitly
    # named company didn't (unlike the all-failed case above, which
    # short-circuits before ever reaching here). Without this, e.g.
    # "compare Apple and SpaceX's revenue" would silently answer about
    # Apple alone with no indication SpaceX was ever dropped — the same
    # kind of unexplained gap this whole fix started from, just for a
    # query where SOME of it could still be answered.
    if classification.tickers and (
        classification.failed_lookups or classification.ingest_failed or classification.year_not_available
    ):
        caveats = []
        if classification.failed_lookups:
            caveats.append(f"couldn't find SEC filings for {', '.join(classification.failed_lookups)}")
        if classification.ingest_failed:
            caveats.append(f"hit a technical problem indexing {', '.join(classification.ingest_failed)}")
        if classification.year_not_available:
            caveats.append(f"don't have the requested year for {', '.join(classification.year_not_available)}")
        result.answer += f"\n\n*Note: I {'; '.join(caveats)}, so this only covers the companies I could find.*"

    return result


def _print_result(result: QueryResult) -> None:
    print("\n" + "=" * 60)
    print(f"QUERY: {result.query}")
    print("=" * 60)
    print(f"\n{result.answer}\n")

    if result.citations:
        print("-" * 60)
        print("SOURCES:")
        for c in result.citations:
            print(
                f"  [{c['index']}] {c['company']} ({c['ticker']}) "
                f"FY{c['fiscal_year']} - {c['section']}"
            )

    print("-" * 60)
    print(f"Query type : {result.query_type}")
    print(f"Chunks used: {len(result.chunks_used)}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    # Ensure UTF-8 output on Windows (cp1252 terminals crash on LLM Unicode)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()
    logger.add(sys.stderr, level="DEBUG", colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")

    if len(sys.argv) < 2:
        print('Usage: python query.py "Your financial question here"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    result   = ask(question)
    _print_result(result)
