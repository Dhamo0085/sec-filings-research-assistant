"""One place that turns a condition into a refusal (P3-07, G3).

    from answering.abstain import abstain_for
    abstain_for(AbstainReason.PERIOD_NOT_FILED_AS_OF,
                ticker="AAPL", fiscal_label=2024, as_of="2024-06-30",
                filing_date="2024-11-01")

Why one module instead of a message at each call site
------------------------------------------------------
Phase 0 found v1's refusals scattered and inconsistent: the same missing-data
condition produced "Which company are you asking about?" in one path and an
empty answer in another, and a dependency outage produced the same text as a
genuine ambiguity (F1). Those are three different things a user needs to tell
apart, and they can only be kept apart if there is one table.

So every refusal in the system comes from here, and the table is the enum
itself: ``MESSAGES`` has an entry for every member of ``AbstainReason``, and a
test asserts that, so adding a reason without a message is a failing build
rather than a refusal that says ``None``.

What a refusal message may and may not contain
-----------------------------------------------
It **must** say what was asked for, why it cannot be served, and — where there
is one — what would work instead ("fiscal 2019 is not covered; 2022 to 2025
are"). A refusal with no next step is a dead end.

It **must not** contain a figure. T3-04 enforces that: an abstention
containing a number reads as a partial answer, and a reader skimming it will
take the number. Dates, years and counts are not figures in that sense and are
exactly what makes the message useful, so the rule the tests check is narrower
and precise: no monetary amount, no percentage.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from answering.outcome import AbstainReason, ErrorCode, Outcome, QueryType

#: One message builder per reason. Each takes the facts the caller has and
#: returns a sentence; none of them invent anything the caller did not pass.
#:
#: The wording is deliberately plain and first-person-singular, matching the
#: rest of the product's voice, and always names the thing that failed.
MESSAGES: Dict[AbstainReason, str] = {
    AbstainReason.COMPANY_NOT_FOUND: (
        "I couldn't find {company} in SEC's company registry. It may be "
        "private, may file under a different name, or may not file with the "
        "SEC at all. I can only answer from filed US annual reports."
    ),
    AbstainReason.NO_FILING_FOR_COMPANY: (
        "{company} is a registered SEC filer, but I have no 10-K on file for "
        "it. I cover annual reports only, and only for filers that have been "
        "ingested."
    ),
    AbstainReason.PERIOD_NOT_COVERED: (
        "I don't have {period} for {company}.{available}"
    ),
    # The template above reads as English for every value the caller passes
    # for ``period``, including "any indexed filing text" — which is what the
    # text path passes when a filer has facts but no indexed text at all
    # (Wells Fargo). A narrative question there must not be answered with a
    # list of years that only the numeric path can serve.
    AbstainReason.PERIOD_NOT_FILED_AS_OF: (
        "As of {as_of}, {company} had not yet filed its annual report for "
        "{period} — that filing became public on {filing_date}. Answering "
        "from it would be using information that did not exist on the date "
        "you asked about.{available}"
    ),
    AbstainReason.FUTURE_PERIOD: (
        "{period} is in the future, so there is no annual report for it yet."
        "{available}"
    ),
    AbstainReason.METRIC_NOT_SUPPORTED: (
        "I can't answer that from tagged filing data: {metric} is not one of "
        "the measures I resolve. I cover {supported}. Asking what the filing "
        "*says* about it, rather than for the figure, will search the text "
        "instead."
    ),
    AbstainReason.METRIC_NOT_FOUND_IN_FILING: (
        "{company}'s {period} annual report does not tag {metric}. That is "
        "common and usually correct — banks report no gross profit or R&D "
        "line, for instance — rather than a gap in my reading of it."
    ),
    AbstainReason.AMBIGUOUS_CONCEPT: (
        "{company} reports more than one figure that could be {metric} for "
        "{period}, and they disagree: {candidates}. I won't choose between "
        "them for you. Naming the concept you want will resolve it."
    ),
    AbstainReason.UNSUPPORTED_PERIOD_TYPE: (
        "I only cover annual reports (10-K). {period_phrase} would need "
        "quarterly or interim filings, which I don't hold."
    ),
    AbstainReason.OUT_OF_SCOPE: (
        "That's outside what I do. I answer questions about what US companies "
        "reported in their annual filings — not investment advice, price or "
        "market data, forecasts, or recommendations."
    ),
    AbstainReason.INSUFFICIENT_EVIDENCE: (
        "I searched {scope} and didn't find enough in the filing text to "
        "answer that. Rather than assemble something plausible from "
        "fragments, I'd rather say so."
    ),
}

#: Error messages, kept beside the abstentions so the contrast is visible:
#: these are failures of the system, not refusals about the data (D7, G4).
ERROR_MESSAGES: Dict[ErrorCode, str] = {
    ErrorCode.LLM_AUTH: (
        "I can't answer right now: the language model rejected our "
        "credentials, or the configured model isn't available to this key. "
        "That's a configuration problem here, not a problem with your question."
    ),
    ErrorCode.LLM_RATE_LIMITED: (
        "I can't answer right now: the free-tier request limit for the "
        "language model has been reached. Please try again later."
    ),
    ErrorCode.LLM_UNAVAILABLE: (
        "I can't answer right now: the language model is unreachable. "
        "Please try again shortly."
    ),
    ErrorCode.LLM_BAD_OUTPUT: (
        "I can't answer right now: the language model returned a response I "
        "couldn't parse. Please try again."
    ),
    ErrorCode.DATA_UNAVAILABLE: (
        "I can't answer right now: the filing data store is unavailable. "
        "This is a problem here, not with your question."
    ),
    ErrorCode.INTERNAL: (
        "I can't answer right now because of an internal error. The details "
        "have been logged."
    ),
}

CLARIFICATION_NO_COMPANY = (
    "Which company are you asking about? I answer from SEC annual reports, "
    "so I need a specific filer — by name or ticker."
)


def _trim_trailing_period(name: str) -> str:
    return name[:-1] if name.endswith(".") else name


def _available_note(available: Optional[Sequence[int]]) -> str:
    """" I do have fiscal 2022 to 2025." — a refusal needs a next step."""
    years = sorted({int(y) for y in available or ()})
    if not years:
        return ""
    if len(years) == 1:
        return f" I do have fiscal {years[0]}."
    contiguous = years == list(range(years[0], years[-1] + 1))
    listed = (f"fiscal {years[0]} to {years[-1]}" if contiguous
              else "fiscal " + ", ".join(str(y) for y in years))
    return f" I do have {listed}."


def message_for(reason: AbstainReason, **facts: Any) -> str:
    """Render the message for ``reason`` from the facts the caller has.

    Missing placeholders are filled with a neutral phrase rather than raising:
    a refusal that crashes while explaining itself is the worst of both, and
    the caller is usually in an error path already.
    """
    template = MESSAGES[reason]
    values: Dict[str, Any] = {
        # Registered names end in a period ("Apple Inc."), and every sentence
        # here supplies its own, so "for Apple Inc.." is what the first
        # walkthrough produced.
        "company": _trim_trailing_period(
            facts.get("company") or facts.get("ticker") or "that company"),
        "period": facts.get("period") or "that period",
        "period_phrase": facts.get("period_phrase") or "A quarterly or interim period",
        "metric": facts.get("metric") or "that measure",
        "supported": facts.get("supported") or "the standard annual-report line items",
        "candidates": facts.get("candidates") or "two or more tagged values",
        "as_of": facts.get("as_of") or "that date",
        "filing_date": facts.get("filing_date") or "a later date",
        "scope": facts.get("scope") or "the filings I have",
        "available": _available_note(facts.get("available")),
    }
    return template.format(**values).strip()


def abstain_for(
    reason: AbstainReason,
    *,
    query_type: QueryType = QueryType.UNSUPPORTED,
    query: str = "",
    as_of: Optional[str] = None,
    trace: Optional[Dict[str, Any]] = None,
    **facts: Any,
) -> Outcome:
    """The abstention Outcome for ``reason`` (G3: a reason and no claim)."""
    return Outcome.abstain(
        reason,
        message_for(reason, as_of=as_of, **facts),
        query_type=query_type,
        query=query,
        as_of=as_of,
        trace=trace,
    )


def error_for(
    code: ErrorCode,
    *,
    query_type: QueryType = QueryType.UNSUPPORTED,
    query: str = "",
    as_of: Optional[str] = None,
    trace: Optional[Dict[str, Any]] = None,
) -> Outcome:
    """The error Outcome for ``code`` (D7, G4: visible, and HTTP 503)."""
    return Outcome.error(
        code, ERROR_MESSAGES[code], query_type=query_type, query=query,
        as_of=as_of, trace=trace,
    )


def clarification_no_company(query: str = "", as_of: Optional[str] = None,
                             trace: Optional[Dict[str, Any]] = None) -> Outcome:
    """The one genuine clarification: no filer was named.

    This text is close to v1's, and that is the point — v1 used it for *every*
    failure, including outages. Here it means only what it says, and the
    status taxonomy keeps it distinct: a clarification has no ``error_code``
    and an error has one (G4).
    """
    return Outcome.clarification(CLARIFICATION_NO_COMPANY, query=query,
                                 as_of=as_of, trace=trace)


#: Map the facts layer's reason strings (facts/resolve.py's REASON_* constants)
#: onto the 6.5 enum. They already use the same spellings; this makes the
#: dependency explicit and fails loudly if one side drifts.
def reason_from_facts(reason: str) -> AbstainReason:
    try:
        return AbstainReason(reason)
    except ValueError as exc:
        raise ValueError(
            f"facts layer returned abstain reason {reason!r}, which is not in "
            f"the 6.5 enum: {[r.value for r in AbstainReason]}"
        ) from exc
