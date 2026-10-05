"""The one thing every answer path returns (spec 6.5, P3-01).

    from answering.outcome import Outcome, Citation, Status, AbstainReason

    Outcome.abstain(AbstainReason.PERIOD_NOT_FILED_AS_OF,
                    "Apple's fiscal 2024 10-K was filed on 2024-11-01, after "
                    "your as-of date of 2024-06-30.",
                    query_type=QueryType.NUMERIC_FACT, as_of="2024-06-30")

Why this module carries validation rather than just field names
---------------------------------------------------------------
The project's four user-facing guarantees are all of the form "this
combination of fields must never be produced". Phase 0 found v1 shipping
exactly such combinations live: an LLM outage rendered as "Which company are
you asking about?" (F1), and renumbered citations pointed at the wrong source
(K1). Those are not bugs in one function, they are a missing type.

So the constructors below are the only way to build an ``Outcome``, and the
validators refuse:

* **G1 traceable numbers** — ``status=answered`` without a ``fact`` citation,
  or without a ``definition_note`` naming the concept and period used (D5).
* **G2 no look-ahead** — any citation whose ``filing_date`` is after ``as_of``.
  The check lives here, in the type, as well as in the property test T3-03,
  because the resolver applying ``as_of`` correctly and the answer citing only
  what the resolver returned are two different things that can drift apart.
* **G3 abstain, don't guess** — ``status=abstained`` without a reason, or
  *with* citations. An abstention that cites sources reads like a partial
  answer; the reason and its message carry everything the user gets.
* **G4 errors are visible** — ``status=error`` without an ``error_code``, or a
  dependency failure dressed up as a clarification.

Plus one structural fix for K1: citation indices must be exactly 1..n in
order, so the markers in the answer text and the citation list cannot disagree
about what ``[2]`` means.

Backward compatibility
----------------------
The v1 UI reads ``citations[].index``, ``.ticker``, ``.fiscal_year``,
``.section`` and ``.score``, plus top-level ``query``, ``query_type``,
``chunks_used``, ``status`` and ``error_code``. The canonical shape here is
spec 6.5, which names some of those differently (``fiscal_label``), so the
mapping is explicit in ``Citation.ui_fields`` and ``Outcome.to_ui_response``
rather than hidden in field aliases. P3-09 replaces the UI; until then both
shapes come from the same object.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── enums (spec 6.5) ──────────────────────────────────────────────────────────

class Status(StrEnum):
    """D9 status taxonomy."""

    ANSWERED = "answered"                        # verified facts
    ANSWERED_TEXT = "answered_text"              # text path, not fact-verified
    ABSTAINED = "abstained"
    CLARIFICATION_NEEDED = "clarification_needed"
    ERROR = "error"


class QueryType(StrEnum):
    NUMERIC_FACT = "numeric_fact"
    COMPUTED = "computed"
    COMPARE = "compare"
    TREND = "trend"
    NARRATIVE = "narrative"
    UNSUPPORTED = "unsupported"


class AbstainReason(StrEnum):
    COMPANY_NOT_FOUND = "company_not_found"
    NO_FILING_FOR_COMPANY = "no_filing_for_company"
    PERIOD_NOT_COVERED = "period_not_covered"
    PERIOD_NOT_FILED_AS_OF = "period_not_filed_as_of"
    FUTURE_PERIOD = "future_period"
    METRIC_NOT_SUPPORTED = "metric_not_supported"
    METRIC_NOT_FOUND_IN_FILING = "metric_not_found_in_filing"
    AMBIGUOUS_CONCEPT = "ambiguous_concept"
    UNSUPPORTED_PERIOD_TYPE = "unsupported_period_type"
    OUT_OF_SCOPE = "out_of_scope"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ErrorCode(StrEnum):
    LLM_AUTH = "llm_auth"
    LLM_RATE_LIMITED = "llm_rate_limited"
    LLM_PROMPT_TOO_LARGE = "llm_prompt_too_large"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_BAD_OUTPUT = "llm_bad_output"
    DATA_UNAVAILABLE = "data_unavailable"
    INTERNAL = "internal"


class CitationKind(StrEnum):
    FACT = "fact"
    TEXT = "text"


#: The only statuses that may carry citations. Everything else is a refusal or
#: a failure, and a refusal that cites sources reads as a partial answer (G3).
ANSWERING_STATUSES = frozenset({Status.ANSWERED, Status.ANSWERED_TEXT})


def _parse_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


# ── citations ─────────────────────────────────────────────────────────────────

class Citation(BaseModel):
    """One source behind an answer: a stored fact, or a chunk of filing text.

    Both kinds live in one list because that is the shape of the 6.5 response.
    ``kind`` says which fields are meaningful, and the validator enforces that
    the required ones for that kind are actually present — a half-filled fact
    citation is how an untraceable number reaches the user.
    """

    model_config = ConfigDict(extra="forbid")

    kind: CitationKind
    index: int = Field(..., ge=1, description="1-based marker used in the answer text")
    ticker: str
    accession: str
    filing_date: str
    fiscal_label: int

    # fact citations
    metric: Optional[str] = None
    concept: Optional[str] = None
    period_end: Optional[str] = None
    value: Optional[str] = Field(
        default=None,
        description="fully scaled, signed decimal STRING; never a float (spec 6.2)",
    )
    unit: Optional[str] = None
    restated: Optional[bool] = None

    # text citations
    section: Optional[str] = None

    # presentation only
    company: Optional[str] = None
    score: Optional[float] = None
    #: Not in the 6.5 field list, but EDGAR's filing-index URL is keyed by
    #: (cik, accession) and there is no accession-only permalink, so the UI
    #: cannot build the citation chip's link without it (P3-09). Optional: a
    #: citation without it simply has no link.
    cik: Optional[int] = None

    @field_validator("filing_date", "period_end")
    @classmethod
    def _iso_dates(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return v
        _parse_iso_date(v, info.field_name)
        return v

    @field_validator("value")
    @classmethod
    def _value_is_a_decimal_string(cls, v: Optional[str]) -> Optional[str]:
        # Floats lose cents on a $391,035,000,000 revenue line, so the contract
        # is a string all the way to the UI. Checked here because a float that
        # pydantic coerced to "3.91035e+11" would still be a valid str.
        if v is None:
            return v
        if not isinstance(v, str):
            raise ValueError("value must be a string, not a number")
        probe = v.lstrip("+-")
        if not probe or probe.count(".") > 1 or not probe.replace(".", "").isdigit():
            raise ValueError(f"value must be a plain decimal string, got {v!r}")
        return v

    @model_validator(mode="after")
    def _required_fields_per_kind(self) -> "Citation":
        if self.kind is CitationKind.FACT:
            missing = [
                name for name in ("metric", "concept", "period_end", "value", "unit")
                if getattr(self, name) in (None, "")
            ]
            if missing:
                raise ValueError(
                    f"a fact citation must carry {', '.join(missing)} — a number "
                    "the user cannot trace back to a concept and period is not "
                    "traceable (G1)"
                )
            if self.restated is None:
                raise ValueError("a fact citation must state restated (true or false), per D14")
        else:
            if not self.section:
                raise ValueError("a text citation must name the section it came from")
            for name in ("metric", "concept", "value", "unit"):
                if getattr(self, name) is not None:
                    raise ValueError(f"a text citation must not carry {name}")
        return self

    # ── views ────────────────────────────────────────────────────────────────

    def ui_fields(self) -> Dict[str, Any]:
        """The v1 UI's citation shape (see the module docstring).

        A fact citation has no "section", so it shows the definition it came
        from, which is the equivalent thing to click through to.
        """
        label = self.section or f"{self.metric} ({self.concept})"
        return {
            "index": self.index,
            "company": self.company or self.ticker,
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_label,
            "section": label,
            "score": self.score if self.score is not None else 0.0,
        }

    def edgar_url(self) -> Optional[str]:
        """The filing's index page on EDGAR, for the UI's citation chip (P3-09).

        The index page rather than the primary document: it lists every
        document of the submission, and Wells Fargo's facts span two of them.
        Returns ``None`` when the citation carries no CIK, since EDGAR has no
        accession-only permalink.
        """
        if self.cik is None or not self.accession:
            return None
        bare = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/"
            f"{bare}/{self.accession}-index.htm"
        )


# ── outcome ───────────────────────────────────────────────────────────────────

class Outcome(BaseModel):
    """The spec 6.5 response, with G1-G4 enforced on construction.

    Build these with the classmethods, not the constructor: they are where the
    status and its obligatory companions are set together, so no call site can
    produce "abstained with no reason" or "answered with no citation".
    """

    model_config = ConfigDict(extra="forbid")

    status: Status
    answer: str
    query_type: QueryType
    abstain_reason: Optional[AbstainReason] = None
    error_code: Optional[ErrorCode] = None
    as_of: Optional[str] = None
    definition_note: Optional[str] = None
    citations: List[Citation] = Field(default_factory=list)
    #: Admin-only (``X-Admin-Token`` plus ``?debug=1``); never serialized
    #: otherwise, which ``to_response`` enforces.
    trace: Optional[Dict[str, Any]] = None

    # Carried for the v1 UI and the CLI, not part of 6.5.
    query: str = ""
    chunks_used: int = 0

    @field_validator("as_of")
    @classmethod
    def _as_of_is_iso(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        _parse_iso_date(v, "as_of")
        return v

    @field_validator("answer")
    @classmethod
    def _answer_is_not_blank(cls, v: str) -> str:
        # An empty answer with status=answered is the shape of a silent
        # failure; every status here has something to say to the user.
        if not v.strip():
            raise ValueError("answer must not be empty")
        return v

    @model_validator(mode="after")
    def _status_invariants(self) -> "Outcome":
        if self.status is Status.ABSTAINED and self.abstain_reason is None:
            raise ValueError("status=abstained requires an abstain_reason (G3)")
        if self.status is not Status.ABSTAINED and self.abstain_reason is not None:
            raise ValueError(
                f"abstain_reason is only valid with status=abstained, not {self.status}"
            )
        if self.status is Status.ERROR and self.error_code is None:
            raise ValueError("status=error requires an error_code (G4)")
        if self.status is not Status.ERROR and self.error_code is not None:
            raise ValueError(
                f"error_code is only valid with status=error, not {self.status}"
            )
        return self

    @model_validator(mode="after")
    def _citation_invariants(self) -> "Outcome":
        if self.citations and self.status not in ANSWERING_STATUSES:
            raise ValueError(
                f"status={self.status} must carry no citations; a refusal that "
                "cites sources reads as a partial answer (G3)"
            )

        expected = list(range(1, len(self.citations) + 1))
        if [c.index for c in self.citations] != expected:
            raise ValueError(
                "citation indices must be exactly "
                f"{expected} in order, got {[c.index for c in self.citations]} — "
                "mismatched markers are Phase 0 defect K1"
            )

        if self.status is Status.ANSWERED:
            if not any(c.kind is CitationKind.FACT for c in self.citations):
                raise ValueError(
                    "status=answered requires at least one fact citation: every "
                    "number in it comes from a stored fact or the calculator (G1)"
                )
            if not (self.definition_note or "").strip():
                raise ValueError(
                    "status=answered requires a definition_note naming the "
                    "concept and period used (D5)"
                )
        if self.status is Status.ANSWERED_TEXT and not any(
            c.kind is CitationKind.TEXT for c in self.citations
        ):
            raise ValueError(
                "status=answered_text requires at least one text citation; with "
                "no evidence the outcome is an abstention, not an answer"
            )
        return self

    @model_validator(mode="after")
    def _no_look_ahead(self) -> "Outcome":
        """G2: with as_of=D, no cited filing may have filing_date > D."""
        if self.as_of is None:
            return self
        cutoff = _parse_iso_date(self.as_of, "as_of")
        violations = [
            f"[{c.index}] {c.ticker} {c.accession} filed {c.filing_date}"
            for c in self.citations
            if _parse_iso_date(c.filing_date, "filing_date") > cutoff
        ]
        if violations:
            raise ValueError(
                f"look-ahead: as_of={self.as_of} but these citations are from "
                f"filings published later: {'; '.join(violations)} (G2)"
            )
        return self

    # ── constructors ─────────────────────────────────────────────────────────

    @classmethod
    def answered(
        cls,
        answer: str,
        *,
        query_type: QueryType,
        citations: Sequence[Citation],
        definition_note: str,
        query: str = "",
        as_of: Optional[str] = None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> "Outcome":
        """A facts-path answer: every number traceable to a fact (G1)."""
        return cls(
            status=Status.ANSWERED, answer=answer, query_type=query_type,
            citations=list(citations), definition_note=definition_note,
            as_of=as_of, query=query, trace=trace,
        )

    @classmethod
    def answered_text(
        cls,
        answer: str,
        *,
        citations: Sequence[Citation],
        query: str = "",
        as_of: Optional[str] = None,
        chunks_used: int = 0,
        definition_note: Optional[str] = None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> "Outcome":
        """A text-path answer. Not fact-verified, and says so by its status."""
        return cls(
            status=Status.ANSWERED_TEXT, answer=answer,
            query_type=QueryType.NARRATIVE, citations=list(citations),
            as_of=as_of, query=query, chunks_used=chunks_used,
            definition_note=definition_note, trace=trace,
        )

    @classmethod
    def abstain(
        cls,
        reason: AbstainReason,
        message: str,
        *,
        query_type: QueryType = QueryType.UNSUPPORTED,
        query: str = "",
        as_of: Optional[str] = None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> "Outcome":
        """A refusal with a reason and no claim (G3)."""
        return cls(
            status=Status.ABSTAINED, answer=message, query_type=query_type,
            abstain_reason=reason, as_of=as_of, query=query, trace=trace,
        )

    @classmethod
    def clarification(
        cls,
        message: str,
        *,
        query_type: QueryType = QueryType.UNSUPPORTED,
        query: str = "",
        as_of: Optional[str] = None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> "Outcome":
        """A genuine ambiguity in the question — never a dependency failure.

        v1 returned this text for every failure, which is why G4 exists. The
        separation is enforced by the status invariants above: an error has an
        ``error_code`` and this does not.
        """
        return cls(
            status=Status.CLARIFICATION_NEEDED, answer=message,
            query_type=query_type, as_of=as_of, query=query, trace=trace,
        )

    @classmethod
    def error(
        cls,
        code: ErrorCode,
        message: str,
        *,
        query_type: QueryType = QueryType.UNSUPPORTED,
        query: str = "",
        as_of: Optional[str] = None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> "Outcome":
        """A dependency failure, visible as such (D7, G4). HTTP 503."""
        return cls(
            status=Status.ERROR, answer=message, query_type=query_type,
            error_code=code, as_of=as_of, query=query, trace=trace,
        )

    # ── views ────────────────────────────────────────────────────────────────

    @property
    def http_status(self) -> int:
        """503 for a dependency failure, 200 for everything else (D7)."""
        return 503 if self.status is Status.ERROR else 200

    def to_response(self, *, include_trace: bool = False) -> Dict[str, Any]:
        """The spec 6.5 JSON body.

        ``trace`` is dropped unless the caller is an admin asking for it; the
        default is the safe one so a route that forgets to pass the flag leaks
        nothing.
        """
        body: Dict[str, Any] = {
            "status": self.status.value,
            "answer": self.answer,
            "abstain_reason": self.abstain_reason.value if self.abstain_reason else None,
            "error_code": self.error_code.value if self.error_code else None,
            "query_type": self.query_type.value,
            "as_of": self.as_of,
            "definition_note": self.definition_note,
            "citations": [
                c.model_dump(mode="json", exclude_none=True) for c in self.citations
            ],
            "trace": self.trace if include_trace else None,
        }
        return body

    def to_ui_response(self, *, include_trace: bool = False) -> Dict[str, Any]:
        """6.5 plus the fields the v1 UI still reads (see the module docstring)."""
        body = self.to_response(include_trace=include_trace)
        body.update({
            "query": self.query,
            "chunks_used": self.chunks_used,
            "citations": [
                {**c.model_dump(mode="json", exclude_none=True), **c.ui_fields()}
                for c in self.citations
            ],
        })
        return body
