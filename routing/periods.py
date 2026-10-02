"""Deterministic period parsing: what span of time is the question about? (P3-02)

    from routing.periods import parse_period
    parse_period("What was Apple's revenue in fiscal 2024?")
    # PeriodRequest(kind=FISCAL_LABEL, labels=(2024,), ...)

    parse_period("As of March 1 2024, what was Apple's latest annual revenue?")
    # PeriodRequest(kind=LATEST, as_of='2024-03-01', ...)

    parse_period("What was Apple's Q3 revenue?")
    # PeriodRequest(kind=NONE, abstain_reason=UNSUPPORTED_PERIOD_TYPE, ...)

No LLM. Period phrasing is a closed, well-behaved grammar, and the router only
needs the model for intent and metric wording (P3-03); spending a free-tier
request to learn that "fiscal 2024" means 2024 would be waste and would make
the answer non-reproducible.

Three distinctions this module exists to keep straight
------------------------------------------------------
**A date in the question is not always ``as_of``.** "As of March 1, 2024" is a
point-in-time constraint on what was *public* (D2, G2). "For the fiscal year
ended September 28, 2024" is the *period being asked about*. Reading the second
as the first silently hides every filing published afterwards and makes the
answer look like a look-ahead bug; reading the first as the second answers a
different question. They are separate fields here and separate patterns.

**A fiscal year is not a calendar year.** D6: the fiscal label is the
company's own (``dei:DocumentFiscalYearFocus``), and a calendar-year phrasing
maps through ``period_end`` instead. Apple's "fiscal 2024" ended 2024-09-28;
its "calendar 2024" is a different question. A *bare* year ("in 2024") is read
as the fiscal label, which is what a 10-K reader means, and is marked
``bare=True`` so the answer template can state the period-end date and let the
reader see which year it got (D6 requires showing it either way).

**"Latest" is not a year.** "Most recent" has to be resolved against the
catalog *at the as_of date*, not against the clock, or "as of March 2024, what
was the latest annual revenue" would answer with a filing from November 2024.
So it returns ``LATEST`` and the catalog decides. Only genuinely clock-relative
phrases ("last year", "this year") use the injected clock.

The clock is injected everywhere (``today=``) because a test that calls
``date.today()`` passes in 2026 and fails in 2027.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import List, Optional, Tuple

from answering.outcome import AbstainReason

# EDGAR electronic filing became general in 1993, and a fiscal year ending
# early in the next calendar year can legitimately be labelled ahead of the
# clock (Microsoft's FY2026 10-K was filed in July 2026). Anything outside this
# is not a fiscal year the corpus could hold.
EARLIEST_FISCAL_YEAR = 1993
FUTURE_LABEL_SLACK = 1


class PeriodKind(StrEnum):
    NONE = "none"                    # no period named; the caller decides
    FISCAL_LABEL = "fiscal_label"    # one or more of the filer's own labels
    CALENDAR_YEAR = "calendar_year"  # explicitly calendar-phrased
    PERIOD_END = "period_end"        # "the fiscal year ended 2024-09-28"
    LATEST = "latest"                # resolve against the catalog, at as_of


@dataclass(frozen=True)
class PeriodRequest:
    """What the question asks for, in time.

    ``abstain_reason`` set means the question names a period this project does
    not serve; the caller abstains with it rather than guessing a nearby one.
    """

    kind: PeriodKind = PeriodKind.NONE
    labels: Tuple[int, ...] = ()
    period_end: Optional[str] = None
    as_of: Optional[str] = None
    abstain_reason: Optional[AbstainReason] = None
    #: True when the year was written bare ("in 2024") rather than as "fiscal
    #: 2024", so the answer can be explicit about which year it resolved to.
    bare: bool = False
    #: True when the span came from a clock-relative phrase ("last year").
    relative: bool = False
    #: The phrases that produced this, for the trace and for failure analysis.
    matched: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_range(self) -> bool:
        return len(self.labels) > 1

    def describe(self) -> str:
        if self.abstain_reason is not None:
            return f"unsupported period ({self.abstain_reason.value})"
        if self.kind is PeriodKind.LATEST:
            return "the latest filed year"
        if self.kind is PeriodKind.PERIOD_END:
            return f"the year ending {self.period_end}"
        if self.kind is PeriodKind.CALENDAR_YEAR:
            years = ", ".join(str(y) for y in self.labels)
            return f"calendar {years}"
        if self.kind is PeriodKind.FISCAL_LABEL:
            if self.is_range:
                return f"fiscal {self.labels[0]} to {self.labels[-1]}"
            return f"fiscal {self.labels[0]}"
        return "no period named"


# ── sub-annual phrasing (D10: annual filings only) ───────────────────────────
#
# Word-anchored on purpose. `\bquarter` does not match inside "headquarters"
# (both sides are word characters, so there is no boundary), but spelling the
# patterns out keeps that from being an accident of regex trivia.
_SUB_ANNUAL = (
    re.compile(r"\bq[1-4]\b", re.I),
    re.compile(r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\b", re.I),
    re.compile(r"\bquarterly\b", re.I),
    re.compile(r"\bquarter\s+(?:ended|ending|of)\b", re.I),
    re.compile(r"\b10-?q\b", re.I),
    re.compile(r"\b(?:three|six|nine)\s+months?\s+(?:ended|ending)\b", re.I),
    re.compile(r"\b(?:h[12]|first\s+half|second\s+half|half[\s-]year)\b", re.I),
    re.compile(r"\binterim\s+(?:results?|period|financial)\b", re.I),
    re.compile(r"\b(?:month|monthly)\s+(?:ended|ending)\b", re.I),
)

# ── dates ─────────────────────────────────────────────────────────────────────

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "March 1, 2024" / "March 1 2024" / "Mar 1st, 2024"
_DATE_MDY = re.compile(
    rf"\b(?P<month>{_MONTH_ALT})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})\b",
    re.I,
)
# "1 March 2024" / "1st of March, 2024"
_DATE_DMY = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<month>{_MONTH_ALT}),?\s+(?P<year>\d{{4}})\b",
    re.I,
)
# "2024-03-01" only. A slashed date is deliberately not accepted: 03/01/2024 is
# 1 March in most of the world and 3 January in the US, and silently choosing
# one would move an as_of cutoff by two months.
_DATE_ISO = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")

_AS_OF_LEAD = r"(?:as\s+of|as\s+at|as\s+was\s+known\s+(?:on|at)|on\s+the\s+date|knowing\s+only)"
_PERIOD_END_LEAD = r"(?:(?:fiscal\s+)?(?:year|period)\s+(?:ended|ending)|for\s+the\s+year\s+to)"


def _iso(match: re.Match) -> Optional[str]:
    """The matched date as an ISO string, or None if it is not a real date."""
    groups = match.groupdict()
    year = int(groups["year"])
    month_raw = groups["month"]
    month = int(month_raw) if month_raw.isdigit() else _MONTHS[month_raw.lower()]
    try:
        return date(year, month, int(groups["day"])).isoformat()
    except ValueError:
        return None       # 31 February, and similar


def _find_date_after(text: str, lead_pattern: str) -> Tuple[Optional[str], str]:
    """The first date introduced by ``lead_pattern``, with the matched phrase.

    The lead is required: an unanchored date search cannot tell an as_of cutoff
    from the period being asked about, which is the distinction in the module
    docstring.
    """
    for date_re in (_DATE_ISO, _DATE_MDY, _DATE_DMY):
        combined = re.compile(
            rf"{lead_pattern}[\s,:]*(?:the\s+)?(?P<date>{date_re.pattern})",
            re.I,
        )
        match = combined.search(text)
        if not match:
            continue
        inner = date_re.search(match.group("date"))
        if inner is None:
            continue
        iso = _iso(inner)
        if iso:
            return iso, match.group(0).strip()
    return None, ""


# ── years ─────────────────────────────────────────────────────────────────────

_FISCAL_LEAD = re.compile(
    r"\b(?:fiscal\s+year|fiscal|fy)\s*[-:]?\s*(?P<year>\d{4}|\d{2})\b", re.I
)
_CALENDAR_LEAD = re.compile(
    r"\bcalendar(?:\s+year)?\s*[-:]?\s*(?P<year>\d{4})\b", re.I
)
_BARE_YEAR = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b")

# "2022 to 2024", "2022-2024", "from 2022 through 2024", "between 2022 and 2024"
_RANGE = re.compile(
    r"\b(?:from\s+|between\s+)?"
    r"(?:fiscal\s+|fy\s*)?(?P<start>(?:19|20)\d{2})\s*"
    r"(?:to|through|thru|until|till|and|-|–|—|=>|→)\s*"
    r"(?:fiscal\s+|fy\s*)?(?P<end>(?:19|20)\d{2})\b",
    re.I,
)

_LATEST = re.compile(
    r"\b(?:latest|most\s+recent(?:ly)?|newest|current|last\s+reported"
    r"|last\s+(?:annual\s+)?(?:report|filing|10-k))\b",
    re.I,
)
_LAST_YEAR = re.compile(r"\blast\s+year\b", re.I)
_THIS_YEAR = re.compile(r"\bthis\s+year\b", re.I)
_PRIOR_YEAR = re.compile(r"\b(?:prior|previous)\s+year\b", re.I)
_LAST_N_YEARS = re.compile(
    r"\b(?:last|past|previous|trailing)\s+(?P<n>\d+|two|three|four|five)\s+(?:fiscal\s+)?years?\b",
    re.I,
)
_WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5}


def _expand_two_digit(year: int) -> int:
    """"FY24" means 2024. Two-digit years are only ever this century here."""
    return 2000 + year if year < 100 else year


def _plausible(label: int, today: date) -> bool:
    return EARLIEST_FISCAL_YEAR <= label <= today.year + FUTURE_LABEL_SLACK


def parse_period(question: str, *, today: Optional[date] = None) -> PeriodRequest:
    """Parse the period and any ``as_of`` out of a question. Never raises.

    Resolution order matters and is deliberate:

    1. **Sub-annual phrasing wins over everything.** "Q3 2024" names a year,
       and answering it with the annual figure would be a wrong answer rather
       than a refusal (D10).
    2. **``as_of`` is extracted and removed** before years are read, so its own
       year ("as of March 1, 2024") is not also taken as the period asked about.
    3. Explicit "year ended <date>", then fiscal, then calendar, then ranges,
       then bare years, then relative phrases, then "latest".
    """
    # UTC rather than the host's local date: the clock is only a fallback
    # (every test injects one), and a local-midnight boundary would make
    # "last year" depend on which machine answered.
    today = today or datetime.now(timezone.utc).date()
    text = question or ""
    matched: List[str] = []

    for pattern in _SUB_ANNUAL:
        hit = pattern.search(text)
        if hit:
            return PeriodRequest(
                kind=PeriodKind.NONE,
                abstain_reason=AbstainReason.UNSUPPORTED_PERIOD_TYPE,
                matched=(hit.group(0).strip(),),
            )

    as_of, as_of_phrase = _find_date_after(text, _AS_OF_LEAD)
    if as_of:
        matched.append(as_of_phrase)
        # Remove the as_of clause so its year cannot be read as the period.
        text = text.replace(as_of_phrase, " ", 1)

    def done(**kwargs) -> PeriodRequest:
        return PeriodRequest(as_of=as_of, matched=tuple(matched), **kwargs)

    # 3a. "for the fiscal year ended September 28, 2024"
    ended, ended_phrase = _find_date_after(text, _PERIOD_END_LEAD)
    if ended:
        matched.append(ended_phrase)
        return done(kind=PeriodKind.PERIOD_END, period_end=ended)

    # 3b. a range, before single years, so "2022 to 2024" is not read as 2022.
    range_hit = _RANGE.search(text)
    if range_hit:
        start = int(range_hit.group("start"))
        end = int(range_hit.group("end"))
        if start > end:
            start, end = end, start
        labels = tuple(range(start, end + 1))
        matched.append(range_hit.group(0).strip())
        bad = [y for y in labels if not _plausible(y, today)]
        if bad:
            return done(kind=PeriodKind.NONE,
                        abstain_reason=_year_reason(bad[0], today))
        bare = not re.search(r"\b(?:fiscal|fy)\b", range_hit.group(0), re.I)
        return done(kind=PeriodKind.FISCAL_LABEL, labels=labels, bare=bare)

    # 3c. explicitly fiscal, then explicitly calendar.
    fiscal = [_expand_two_digit(int(m.group("year"))) for m in _FISCAL_LEAD.finditer(text)]
    if fiscal:
        matched.extend(m.group(0).strip() for m in _FISCAL_LEAD.finditer(text))
        bad = [y for y in fiscal if not _plausible(y, today)]
        if bad:
            return done(kind=PeriodKind.NONE, abstain_reason=_year_reason(bad[0], today))
        return done(kind=PeriodKind.FISCAL_LABEL, labels=tuple(sorted(set(fiscal))))

    calendar = [int(m.group("year")) for m in _CALENDAR_LEAD.finditer(text)]
    if calendar:
        matched.extend(m.group(0).strip() for m in _CALENDAR_LEAD.finditer(text))
        bad = [y for y in calendar if not _plausible(y, today)]
        if bad:
            return done(kind=PeriodKind.NONE, abstain_reason=_year_reason(bad[0], today))
        return done(kind=PeriodKind.CALENDAR_YEAR, labels=tuple(sorted(set(calendar))))

    # 3d. bare years ("in 2024", "2023 and 2024").
    bare_years = [int(m.group("year")) for m in _BARE_YEAR.finditer(text)]
    if bare_years:
        matched.extend(m.group(0) for m in _BARE_YEAR.finditer(text))
        bad = [y for y in bare_years if not _plausible(y, today)]
        if bad:
            return done(kind=PeriodKind.NONE, abstain_reason=_year_reason(bad[0], today))
        return done(kind=PeriodKind.FISCAL_LABEL,
                    labels=tuple(sorted(set(bare_years))), bare=True)

    # 3e. clock-relative spans.
    span = _LAST_N_YEARS.search(text)
    if span:
        raw = span.group("n").lower()
        n = _WORD_NUMBERS.get(raw, int(raw) if raw.isdigit() else 0)
        matched.append(span.group(0).strip())
        if n >= 1:
            # "the last three years" of annual reports means the three most
            # recently *reported* years, which the catalog decides; the clock
            # only bounds how many.
            end = today.year - 1
            labels = tuple(range(end - n + 1, end + 1))
            if all(_plausible(y, today) for y in labels):
                return done(kind=PeriodKind.FISCAL_LABEL, labels=labels, relative=True)

    for pattern, offset in ((_LAST_YEAR, 1), (_PRIOR_YEAR, 1), (_THIS_YEAR, 0)):
        hit = pattern.search(text)
        if hit:
            matched.append(hit.group(0).strip())
            return done(kind=PeriodKind.FISCAL_LABEL,
                        labels=(today.year - offset,), relative=True)

    # 3f. "latest" / "most recent" — resolved against the catalog at as_of, not
    # against the clock (see the module docstring).
    latest = _LATEST.search(text)
    if latest:
        matched.append(latest.group(0).strip())
        return done(kind=PeriodKind.LATEST)

    return done(kind=PeriodKind.NONE)


def _year_reason(label: int, today: date) -> AbstainReason:
    """Why a named year cannot be served.

    A year beyond the filing horizon is a *future period* — there is no 10-K
    for it and there will not be one for months. A year before EDGAR is
    ``period_not_covered``: it is a real past year this corpus does not hold.
    """
    if label > today.year + FUTURE_LABEL_SLACK:
        return AbstainReason.FUTURE_PERIOD
    return AbstainReason.PERIOD_NOT_COVERED
