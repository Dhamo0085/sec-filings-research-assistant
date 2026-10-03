"""Deterministic scorers for the Phase 4 gold set (P4-03, spec section 11).

    from eval.scorers import score_item
    scored = score_item(gold_item, outcome)

Why these replace Phase 0's
---------------------------
The Phase 0 scorer matched the expected number against the answer text and, when
that failed, labelled the row ``retrieval_found_llm_misread``. It was wrong
about passing answers: a correct "$391.0 billion" does not contain the string
``391035000000``, so a right answer was recorded as the model misreading its
own retrieved context. That single defect makes a baseline look worse than it
is, in a direction that flatters the replacement — the worst direction for an
evaluation this project will publish.

Two things follow, and they are the design:

1. **A number is compared as a number, at the precision it was displayed.**
   ``numerals_in`` parses what the answer actually says — magnitude words,
   currency symbols, thousands separators, parenthesised negatives, percent
   signs — and ``match_value`` accepts a candidate that agrees with the
   expected value once both are rounded to the candidate's own displayed
   precision. "$391.0 billion" matches 391,035,000,000; "$391.1 billion" does
   not.
2. **A miss is classified, never just counted.** A value that is out by a
   factor of a thousand is a scale error, not a wrong answer — it is the
   specific failure v1 shipped (K2) and the one this project exists to
   prevent. A value that is right for the wrong year, or for the other company
   in a comparison, is a different defect again. ``score_item`` names which,
   so the failure analysis in P4-07 starts from evidence rather than from a
   pile of ``incorrect``.

Nothing here calls a model. Scoring an evaluation with an LLM judge would make
the headline numbers depend on the judge's taste and make them unreproducible;
the judge is used in Phase 5 for narrative *rating* only, never for pass/fail.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

# ── verdicts ──────────────────────────────────────────────────────────────────

#: Verdicts that count as a pass.
PASSING = frozenset({"correct", "correct_text_only", "correct_abstain"})

#: Every verdict score_item can return. Kept as a closed set so a typo in a
#: verdict string cannot silently create a new category in the results table.
VERDICTS: FrozenSet[str] = frozenset({
    "correct",             # right value, traceable to a citation
    "correct_text_only",   # right value in the prose, no structured citation (V0)
    "correct_abstain",     # refused, for the expected reason
    "wrong_value",         # a number, and not the right one
    "scale_error",         # right digits, wrong power of ten (K2)
    "sign_error",          # right magnitude, wrong sign
    "unit_error",          # a fraction where a percentage was asked for, or back
    "wrong_period",        # the right metric for a different year
    "wrong_entity",        # the right metric for a different company
    "missing_number",      # answered, but states no number at all
    "partial_multi",       # some values of a compare/trend right, not all
    "wrong_abstain_reason",  # refused correctly, for the wrong reason
    "abstained_wrongly",   # refused something answerable
    "answered_wrongly",    # answered something that should have been refused
    "error",               # the pipeline returned an error status
    "not_run",             # no outcome (budget, rate limit, interrupted run)
})

#: Flags are independent of the verdict: an answer can be correct AND cite a
#: filing published after the as_of date, and that is a violation worth
#: counting on its own (G2).
FLAGS: FrozenSet[str] = frozenset({
    "look_ahead",          # a citation newer than the item's as_of
    "uncited_number",      # the value is in the prose but in no citation (G1)
    "citation_invalid",    # citation indices are not 1..n, or a field is missing
    "abstention_with_number",  # a refusal that states a figure anyway
})


@dataclass(frozen=True)
class Scored:
    """One item's result. ``verdict`` is the classification; ``passed`` is derived."""

    item_id: str
    category: str
    verdict: str
    detail: str = ""
    flags: FrozenSet[str] = frozenset()
    #: Narrative only: the rank of the first correct section, 1-based, or None.
    section_rank: Optional[int] = None

    @property
    def passed(self) -> bool:
        return self.verdict in PASSING

    def as_row(self) -> Dict:
        return {
            "item_id": self.item_id,
            "category": self.category,
            "verdict": self.verdict,
            "passed": self.passed,
            "flags": ",".join(sorted(self.flags)),
            "section_rank": self.section_rank,
            "detail": self.detail,
        }


# ── reading numbers out of prose ──────────────────────────────────────────────

#: Magnitude words and their multipliers. ``bn``/``mm`` are included because
#: filings and analysts use them; ``mm`` is millions, not thousands.
MAGNITUDES: Dict[str, Decimal] = {
    "trillion": Decimal(10) ** 12, "tn": Decimal(10) ** 12,
    "billion": Decimal(10) ** 9, "bn": Decimal(10) ** 9, "b": Decimal(10) ** 9,
    "million": Decimal(10) ** 6, "mm": Decimal(10) ** 6, "m": Decimal(10) ** 6,
    "thousand": Decimal(10) ** 3, "k": Decimal(10) ** 3,
}

_NUMBER_RE = re.compile(
    r"""
    (?P<cur_outer>[$€£])?\s*            # "$(10,959)" — the symbol can sit
    (?P<open>\()?\s*                    # outside the parentheses...
    (?P<cur_inner>[$€£])?\s*            # ...or inside them: "($10,959)"
    (?P<sign>[-−–])?\s*
    (?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*(?P<close_early>\))?                # "$(10,959) million" closes here...
    \s*(?P<magnitude>(?:trillion|billion|million|thousand|tn|bn|mm|[bmk])\b)?
    \s*(?P<percent>%|\spercent\b)?
    \s*(?P<close_late>\))?                 # ..."($10,959 million)" closes here
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Numeral:
    """One number as the answer stated it."""

    value: Decimal
    raw: str
    #: Decimal places shown, AFTER applying any magnitude word. "$391.0
    #: billion" shows one decimal of a billion, so it is precise to 10**8.
    displayed_step: Decimal
    is_percent: bool = False

    def agrees_with(self, expected: Decimal) -> bool:
        """Does ``expected`` round to this numeral at the precision shown?

        Half the step, not the whole step: 391.0 billion stands for
        [390.95, 391.05) billion, so 391.035 billion agrees and 391.06 does not.
        """
        return abs(self.value - expected) <= self.displayed_step / 2


def _displayed_step(digits: str, multiplier: Decimal) -> Decimal:
    """The size of the last digit the answer actually showed."""
    _, _, fraction = digits.partition(".")
    return (Decimal(1).scaleb(-len(fraction)) if fraction else Decimal(1)) * multiplier


def numerals_in(text: str) -> List[Numeral]:
    """Every number in ``text``, with its magnitude and displayed precision.

    Deliberately permissive about how a number is written and strict about what
    it then means: the failure being guarded is a correct answer scored wrong
    because it was phrased in billions.
    """
    out: List[Numeral] = []
    for match in _NUMBER_RE.finditer(text or ""):
        digits = match.group("digits").replace(",", "")
        try:
            value = Decimal(digits)
        except InvalidOperation:          # pragma: no cover — the regex forbids it
            continue

        magnitude = (match.group("magnitude") or "").lower()
        multiplier = MAGNITUDES.get(magnitude, Decimal(1))
        value *= multiplier

        closed = match.group("close_early") or match.group("close_late")
        negative = bool(match.group("sign")) or bool(match.group("open") and closed)
        if negative:
            value = -value

        out.append(Numeral(
            value=value,
            raw=match.group(0).strip(),
            displayed_step=_displayed_step(digits, multiplier),
            is_percent=bool(match.group("percent")),
        ))
    return out


# ── comparing one value ───────────────────────────────────────────────────────

def _relative_difference(a: Decimal, b: Decimal) -> Decimal:
    if b == 0:
        return Decimal(0) if a == 0 else Decimal("Infinity")
    return abs(a - b) / abs(b)


#: Powers of ten a scale error can be out by, nearest first so the smallest
#: explanation wins. Three covers the thousands/millions confusion that is the
#: actual failure mode (K2); twelve covers a unit mistaken for a trillion.
_SCALE_FACTORS: Tuple[int, ...] = tuple(
    factor for magnitude in range(1, 13) for factor in (magnitude, -magnitude)
)


@dataclass
class ValueVerdict:
    verdict: str
    detail: str
    numeral: Optional[Numeral] = None


def match_value(
    expected: Decimal,
    candidates: Sequence[Numeral],
    *,
    tolerance_rel: float = 0.0,
    tolerance_abs: Optional[Decimal] = None,
    expect_percent: bool = False,
    distractors: Mapping[str, Decimal] = (),
) -> ValueVerdict:
    """Classify the best candidate against ``expected``.

    Checked in order of what the classification is worth knowing: an exact or
    displayed-precision match first, then the three specific defect shapes
    (scale, sign, unit), then a value that belongs to a named distractor —
    another year, or the other company in a comparison. ``wrong_value`` is the
    residue, not the default.
    """
    if not candidates:
        return ValueVerdict("missing_number", "the answer states no number")

    distractors = dict(distractors)

    # 1. correct, at whatever precision it was displayed
    for numeral in candidates:
        if numeral.agrees_with(expected):
            # A bare "31.51" in answer to "what was the margin" reads as 31.51
            # percent; the percent SIGN is not required. What is a unit error
            # is a value written as the fraction 0.3151, and that is caught in
            # step 2 below.
            return ValueVerdict("correct", f"matched {numeral.raw!r}", numeral)
        if tolerance_abs is not None and abs(numeral.value - expected) <= tolerance_abs:
            return ValueVerdict("correct", f"matched {numeral.raw!r} within tolerance", numeral)
        if tolerance_rel and _relative_difference(numeral.value, expected) <= Decimal(
            str(tolerance_rel)
        ):
            return ValueVerdict("correct", f"matched {numeral.raw!r} within tolerance", numeral)

    # 2. a percentage answered as a fraction, or the reverse
    if expect_percent:
        for numeral in candidates:
            if numeral.value != 0 and (
                _relative_difference(numeral.value * 100, expected) < Decimal("0.001")
                or _relative_difference(numeral.value / 100, expected) < Decimal("0.001")
            ):
                return ValueVerdict(
                    "unit_error",
                    f"{numeral.raw!r} is the right ratio expressed in the wrong unit "
                    f"(expected {expected} percent)",
                    numeral,
                )

    # 3. scale and sign — the two defects worth naming individually.
    #
    # Judged at the candidate's own displayed precision, like the match above.
    # "$391.0 million" against an expected 391,035,000,000 is a thousand-fold
    # scale error, but the raw ratio is 1000.0895, not 1000, because the answer
    # rounded to one decimal. Testing the exact ratio called it `wrong_value`
    # and lost the one classification this project most needs to count (K2).
    for numeral in candidates:
        if numeral.value == 0 or expected == 0:
            continue
        if numeral.agrees_with(-expected):
            return ValueVerdict("sign_error", f"{numeral.raw!r} has the opposite sign", numeral)
        for factor in _SCALE_FACTORS:
            shifted = numeral.value.scaleb(factor)
            if abs(shifted - expected) <= numeral.displayed_step.scaleb(factor) / 2:
                return ValueVerdict(
                    "scale_error",
                    f"{numeral.raw!r} is 10^{-factor} times the expected {expected}",
                    numeral,
                )

    # 4. right number, wrong thing: another period, or another company
    for label, value in distractors.items():
        for numeral in candidates:
            if numeral.agrees_with(value):
                verdict = "wrong_entity" if label.startswith("entity:") else "wrong_period"
                return ValueVerdict(
                    verdict,
                    f"{numeral.raw!r} is the value for {label.split(':', 1)[-1]}",
                    numeral,
                )

    nearest = min(candidates, key=lambda n: abs(n.value - expected))
    return ValueVerdict(
        "wrong_value",
        f"nearest number stated was {nearest.raw!r} ({nearest.value}), expected {expected}",
        nearest,
    )


# ── the outcome, read without depending on the product's types ────────────────

@dataclass
class OutcomeView:
    """What a scorer needs from an answer, from any variant.

    V0 is v1's pipeline and has no ``Outcome`` at all, so the scorers read this
    structural view instead of importing the product's type. ``from_outcome``
    builds it from a v2 ``Outcome``; a dict from a V0 transcript builds it
    directly. Scoring both through one shape is what makes the V0-to-V3
    comparison a comparison.
    """

    status: str = ""
    answer: str = ""
    abstain_reason: Optional[str] = None
    error_code: Optional[str] = None
    as_of: Optional[str] = None
    citations: List[Dict] = field(default_factory=list)
    ran: bool = True

    @classmethod
    def from_outcome(cls, outcome) -> "OutcomeView":
        if outcome is None:
            return cls(ran=False)
        if isinstance(outcome, Mapping):
            return cls(
                status=str(outcome.get("status") or ""),
                answer=str(outcome.get("answer") or ""),
                abstain_reason=outcome.get("abstain_reason"),
                error_code=outcome.get("error_code"),
                as_of=outcome.get("as_of"),
                citations=list(outcome.get("citations") or []),
                ran=True,
            )
        citations = []
        for citation in getattr(outcome, "citations", ()) or ():
            citations.append(
                citation.model_dump() if hasattr(citation, "model_dump") else dict(citation)
            )
        reason = getattr(outcome, "abstain_reason", None)
        error = getattr(outcome, "error_code", None)
        return cls(
            status=str(getattr(outcome, "status", "") or ""),
            answer=str(getattr(outcome, "answer", "") or ""),
            abstain_reason=str(reason) if reason else None,
            error_code=str(error) if error else None,
            as_of=getattr(outcome, "as_of", None),
            citations=citations,
            ran=True,
        )

    @property
    def answered(self) -> bool:
        return self.status in ("answered", "answered_text")

    @property
    def abstained(self) -> bool:
        return self.status in ("abstained", "clarification_needed")

    def fact_values(self) -> List[Decimal]:
        """Every value carried by a fact citation, as a Decimal."""
        values: List[Decimal] = []
        for citation in self.citations:
            if citation.get("kind") != "fact":
                continue
            raw = citation.get("value")
            if raw in (None, ""):
                continue
            try:
                values.append(Decimal(str(raw)))
            except InvalidOperation:
                continue
        return values

    def sections(self) -> List[str]:
        """Sections cited, in citation order, base ids only."""
        return [
            str(c.get("section") or "").split("__")[0]
            for c in self.citations
            if c.get("section")
        ]


# ── cross-cutting checks ──────────────────────────────────────────────────────

def check_citations(view: OutcomeView, as_of: Optional[str]) -> FrozenSet[str]:
    """Look-ahead and citation-validity flags (G1, G2, K1).

    Run on every item regardless of category: a correct number cited to a
    filing that was not public on the as_of date is still a look-ahead
    violation, and the spec reports that count separately from accuracy.
    """
    flags = set()

    indices = [c.get("index") for c in view.citations]
    if indices and indices != list(range(1, len(indices) + 1)):
        # K1: v1 renumbered citations so [2] in the text and [2] in the list
        # pointed at different sources.
        flags.add("citation_invalid")
    for citation in view.citations:
        if not citation.get("accession") or not citation.get("filing_date"):
            flags.add("citation_invalid")
            break

    if as_of:
        for citation in view.citations:
            filing_date = str(citation.get("filing_date") or "")
            if filing_date and filing_date > str(as_of):
                flags.add("look_ahead")
                break

    if view.abstained and view.citations:
        flags.add("citation_invalid")       # G3: a refusal carries no sources

    return frozenset(flags)


#: A refusal may mention a year or an as_of date without stating a figure.
#: Only a number that looks like a quantity counts as a figure.
_FIGURE_IN_REFUSAL_RE = re.compile(
    r"(?<![\w.])(?:"
    r"[$€£]\s*\d"                                      # "$391"
    r"|\d{1,3}(?:,\d{3})+"                              # "391,035"
    r"|\d+(?:\.\d+)?\s*%"                              # "31.5%" — no \b after
    r"|\d+(?:\.\d+)?\s*"                               # ...the per-cent sign,
    r"(?:percent|trillion|billion|million|thousand)\b"   # which is not a word char
    r")",
    re.IGNORECASE,
)


def refusal_states_a_figure(text: str) -> bool:
    """Does a refusal state a quantity anyway? (G3)

    Deliberately narrow. A refusal legitimately says "fiscal 2024" and
    "2024-11-01"; what it must not do is answer the question in passing.
    """
    return bool(_FIGURE_IN_REFUSAL_RE.search(text or ""))


# ── per-category scorers ──────────────────────────────────────────────────────

def _expected_decimal(expected: Mapping, key: str = "value") -> Decimal:
    return Decimal(str(expected[key]))


def score_numeric(
    item: Mapping, view: OutcomeView, distractors: Mapping[str, Decimal] = (),
) -> Scored:
    expected = item["expected"]
    target = _expected_decimal(expected)
    flags = set(check_citations(view, item.get("as_of")))

    if view.abstained:
        return Scored(item["id"], item["category"], "abstained_wrongly",
                      f"refused ({view.abstain_reason}) an answerable item", frozenset(flags))
    if not view.answered:
        verdict = "error" if view.status == "error" else "not_run"
        return Scored(item["id"], item["category"], verdict,
                      view.error_code or view.status, frozenset(flags))

    # Structured first: a fact citation carries the value exactly, so a match
    # there is traceable (G1). Prose is the fallback, and is recorded as such
    # rather than silently counted the same way.
    for value in view.fact_values():
        if value == target:
            return Scored(item["id"], item["category"], "correct",
                          "fact citation carries the exact value", frozenset(flags))

    result = match_value(
        target, numerals_in(view.answer),
        tolerance_rel=float(expected.get("tolerance_rel") or 0.0),
        distractors=distractors,
    )
    if result.verdict == "correct":
        flags.add("uncited_number")
        return Scored(item["id"], item["category"], "correct_text_only",
                      result.detail, frozenset(flags))
    return Scored(item["id"], item["category"], result.verdict, result.detail,
                  frozenset(flags))


def score_computed(
    item: Mapping, view: OutcomeView, distractors: Mapping[str, Decimal] = (),
) -> Scored:
    expected = item["expected"]
    target = _expected_decimal(expected)
    flags = set(check_citations(view, item.get("as_of")))

    if view.abstained:
        return Scored(item["id"], item["category"], "abstained_wrongly",
                      f"refused ({view.abstain_reason}) a computable item", frozenset(flags))
    if not view.answered:
        verdict = "error" if view.status == "error" else "not_run"
        return Scored(item["id"], item["category"], verdict,
                      view.error_code or view.status, frozenset(flags))

    tolerance_abs = expected.get("tolerance_abs")
    result = match_value(
        target, numerals_in(view.answer),
        tolerance_rel=float(expected.get("tolerance_rel") or 0.0),
        tolerance_abs=Decimal(str(tolerance_abs)) if tolerance_abs is not None else None,
        expect_percent=expected.get("unit") == "percent",
        distractors=distractors,
    )
    verdict = result.verdict
    if verdict == "correct" and not view.fact_values():
        # A computed answer must still cite the facts it was computed from.
        flags.add("uncited_number")
    return Scored(item["id"], item["category"], verdict, result.detail, frozenset(flags))


def score_multi(item: Mapping, view: OutcomeView) -> Scored:
    """Compare and trend: every value, attributed to the right company and year.

    Attribution is the whole point. An answer that states both of two companies'
    revenues but swaps them contains every correct number, and a scorer that
    only asks "is the number present" calls it correct. So each expected value
    is matched against the numbers near its own company's name where the answer
    names companies, and the result says how many were right.
    """
    expected_values = item["expected"]["values"]
    flags = set(check_citations(view, item.get("as_of")))

    if view.abstained:
        return Scored(item["id"], item["category"], "abstained_wrongly",
                      f"refused ({view.abstain_reason})", frozenset(flags))
    if not view.answered:
        verdict = "error" if view.status == "error" else "not_run"
        return Scored(item["id"], item["category"], verdict,
                      view.error_code or view.status, frozenset(flags))

    cited = view.fact_values()
    numerals = numerals_in(view.answer)
    tolerance_rel = float(item["expected"].get("tolerance_rel") or 0.0)

    correct: List[str] = []
    wrong: List[str] = []
    for entry in expected_values:
        target = Decimal(str(entry["value"]))
        label = f"{entry['ticker']} FY{entry['fiscal_label']}"
        # Every OTHER expected value is a distractor for this one: that is
        # exactly what a swapped answer looks like.
        others = {
            f"entity:{o['ticker']} FY{o['fiscal_label']}": Decimal(str(o["value"]))
            for o in expected_values if o is not entry
        }
        if any(value == target for value in cited):
            correct.append(label)
            continue
        result = match_value(target, numerals, tolerance_rel=tolerance_rel,
                             distractors=others)
        if result.verdict == "correct":
            correct.append(label)
        else:
            wrong.append(f"{label}: {result.verdict}")

    if not wrong:
        return Scored(item["id"], item["category"], "correct",
                      f"all {len(correct)} values present and attributed",
                      frozenset(flags))
    if correct:
        return Scored(item["id"], item["category"], "partial_multi",
                      f"{len(correct)} of {len(expected_values)} correct; "
                      + "; ".join(wrong), frozenset(flags))
    return Scored(item["id"], item["category"], "wrong_value",
                  "; ".join(wrong), frozenset(flags))


def score_abstain(item: Mapping, view: OutcomeView) -> Scored:
    """Refusals, scored by reason (spec section 11).

    "Refused" is not the same as "refused correctly". Abstention precision and
    recall are reported per reason, so a refusal for the wrong reason is its own
    verdict: the user is told something untrue about why they got no answer.
    """
    expected_reason = item["expected"].get("abstain_reason")
    flags = set(check_citations(view, item.get("as_of")))

    if not view.ran:
        return Scored(item["id"], item["category"], "not_run", "", frozenset(flags))
    if view.status == "error":
        return Scored(item["id"], item["category"], "error",
                      view.error_code or "", frozenset(flags))

    if view.answered:
        return Scored(item["id"], item["category"], "answered_wrongly",
                      f"answered something that should have been refused "
                      f"({expected_reason})", frozenset(flags))

    if refusal_states_a_figure(view.answer):
        flags.add("abstention_with_number")

    if view.abstain_reason == expected_reason:
        return Scored(item["id"], item["category"], "correct_abstain",
                      f"refused with {expected_reason}", frozenset(flags))
    return Scored(item["id"], item["category"], "wrong_abstain_reason",
                  f"refused with {view.abstain_reason}, expected {expected_reason}",
                  frozenset(flags))


def section_hit(
    cited_sections: Sequence[str], expected_sections: Iterable[str], k: int = 5,
) -> Tuple[bool, Optional[int]]:
    """Did any of the first ``k`` citations come from an acceptable section?

    Returns (hit, rank). The rank feeds MRR. Several sections may be acceptable
    because a topic genuinely lives in more than one — capital requirements are
    in both Item 1 and the MD&A — and pretending otherwise would score a right
    answer wrong.
    """
    wanted = {s.split("__")[0] for s in expected_sections}
    for rank, section in enumerate(cited_sections[:k], start=1):
        # Both sides are normalised: parse_filing suffixes a repeated id
        # ("item_1a_risk_factors__2") and retrieval scrolls by the base id, so
        # a caller can hand this either spelling.
        if section.split("__")[0] in wanted:
            return True, rank
    return False, None


def score_text(item: Mapping, view: OutcomeView, k: int = 5) -> Scored:
    expected = item["expected"]
    flags = set(check_citations(view, item.get("as_of")))

    if not view.ran:
        return Scored(item["id"], item["category"], "not_run", "", frozenset(flags))
    if view.status == "error":
        return Scored(item["id"], item["category"], "error",
                      view.error_code or "", frozenset(flags))
    if view.abstained:
        return Scored(item["id"], item["category"], "abstained_wrongly",
                      f"refused ({view.abstain_reason})", frozenset(flags))
    if not view.answered:
        return Scored(item["id"], item["category"], "not_run", view.status, frozenset(flags))

    hit, rank = section_hit(view.sections(), expected.get("expected_sections", ()), k=k)

    must_contain = expected.get("must_contain_numbers") or []
    if must_contain:
        numerals = numerals_in(view.answer)
        missing = [
            str(value) for value in must_contain
            if not any(n.agrees_with(Decimal(str(value))) for n in numerals)
        ]
        if missing:
            return Scored(item["id"], item["category"], "wrong_value",
                          f"answer omits required figures {missing}",
                          frozenset(flags), rank)

    if not hit:
        return Scored(
            item["id"], item["category"], "wrong_value",
            f"no citation in the top {k} came from "
            f"{sorted(expected.get('expected_sections', ()))}; cited "
            f"{view.sections()[:k]}",
            frozenset(flags), rank,
        )
    return Scored(item["id"], item["category"], "correct",
                  f"section hit at rank {rank}", frozenset(flags), rank)


# ── the entry point ───────────────────────────────────────────────────────────

_BY_EXPECTED_TYPE = {
    "numeric": score_numeric,
    "computed": score_computed,
    "multi": score_multi,
    "abstain": score_abstain,
    "text": score_text,
}


def score_item(
    item: Mapping, outcome, *, distractors: Mapping[str, Decimal] = (), k: int = 5,
) -> Scored:
    """Score one gold item against one answer.

    ``distractors`` are values that would be right for a different year or a
    different company, so that "right number, wrong thing" is classified rather
    than lumped in with "wrong number". The runner supplies them from the gold
    set itself.
    """
    view = outcome if isinstance(outcome, OutcomeView) else OutcomeView.from_outcome(outcome)
    kind = item["expected"]["type"]
    scorer = _BY_EXPECTED_TYPE[kind]

    if scorer is score_text:
        return scorer(item, view, k=k)
    if scorer is score_abstain or scorer is score_multi:
        return scorer(item, view)
    return scorer(item, view, distractors=distractors)


# ── aggregation ───────────────────────────────────────────────────────────────

def wilson_interval(successes: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """95% Wilson score interval, as spec section 11 requires for every n/N.

    Wilson rather than the normal approximation because the normal one produces
    intervals that extend past 0 or 1 and gives a zero-width interval at 0/N and
    N/N, which is exactly where a small gold set lands.
    """
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = (z / denominator) * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarize(scored: Sequence[Scored]) -> Dict:
    """Counts, rates with Wilson intervals, and flag totals."""
    by_category: Dict[str, Dict] = {}
    for item in scored:
        bucket = by_category.setdefault(
            item.category, {"n": 0, "passed": 0, "verdicts": {}}
        )
        bucket["n"] += 1
        bucket["passed"] += int(item.passed)
        bucket["verdicts"][item.verdict] = bucket["verdicts"].get(item.verdict, 0) + 1

    for bucket in by_category.values():
        low, high = wilson_interval(bucket["passed"], bucket["n"])
        bucket["rate"] = bucket["passed"] / bucket["n"] if bucket["n"] else 0.0
        bucket["ci95"] = [low, high]

    flags: Dict[str, int] = {}
    for item in scored:
        for flag in item.flags:
            flags[flag] = flags.get(flag, 0) + 1

    passed = sum(1 for item in scored if item.passed)
    low, high = wilson_interval(passed, len(scored))
    return {
        "n": len(scored),
        "passed": passed,
        "rate": passed / len(scored) if scored else 0.0,
        "ci95": [low, high],
        "by_category": by_category,
        "flags": flags,
    }
