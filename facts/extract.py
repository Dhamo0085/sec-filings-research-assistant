"""Inline-XBRL extraction: documents in, typed facts out (P2-02, P2-03).

    from facts.extract import parse_submission
    sub = parse_submission([wrapper_path, ex13_path], accession="0000072971-25-000066")
    sub.facts[0].value          # Decimal, fully scaled and signed
    sub.dei()["DocumentFiscalYearFocus"]

Decision D1 rests on this module: numeric answers come from the filer's own
XBRL tags, not from an LLM reading a rendered table. Phase 0 validated the idea
(105/105 agreement with SEC ``companyfacts`` on comparable pairs) with
``eval/phase0/ixbrl_extract.py``, which is the seed for this file. What is new
here is everything that made that script a feasibility study rather than a
component:

* **Decimal, not float.** A float revenue is a wrong revenue the moment it is
  added to another one.
* **A transform registry that fails loudly.** The corpus was surveyed first
  (P2-02 asks for exactly that): the 12 cached filings carry 39,586 numeric
  facts using four distinct ``@format`` values — ``ixt:num-dot-decimal``
  (21,116), none at all (14,947), ``ixt:fixed-zero`` (3,443) and
  ``ixt-sec:numwordsen`` (80). An unlisted transform raises
  ``UnknownTransformError`` instead of being read as if it were dot-decimal.
* **``xsi:nil`` is not zero.** 32 facts in the corpus are nil, all of them
  ``us-gaap:CommitmentsAndContingencies``. Reading those as 0 would assert that
  a company disclosed zero commitments.
* **Hidden facts are collected.** ``ix:hidden`` is usually described as the
  cover page's ``dei:`` block, and for Apple it nearly is; for the banks it
  holds thousands of ``us-gaap:`` facts across 2,001 distinct concepts. Ignoring
  it would silently lose most of JPM's and BAC's tagged data.
* **Negative scales are real.** 2,725 facts use ``scale="-2"`` or ``"-4"``;
  these are percentages, and ``10**-2`` must be applied with Decimal exponent
  arithmetic rather than float multiplication.
* **Pooling across a submission.** Wells Fargo's 10-K wrapper holds 18 facts and
  its EX-13 exhibit holds 7,285; the contexts those 7,285 facts reference are
  defined in the wrapper. Neither document resolves alone.

Parsing uses lxml's **HTMLParser**, which does no namespace processing: tags
arrive as the literal lowercased string ``ix:nonfraction`` and attributes as
``contextref``. That is not a workaround to be cleaned up later — an XML parser
rejects these 2-13 MB documents over their HTML entities. Phase 0 lost a run to
this, reporting 0 facts everywhere, so every tag and attribute lookup here goes
through ``_local_name`` / ``_attr``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from lxml import etree

from facts.errors import UnknownTransformError, ValueParseError

PathLike = Union[str, Path]

# Annual duration window (spec 6.4 rule 2): 52/53-week filers end on a weekday
# near a month end, so a "year" is 350-380 days, not 365. Apple's FY2024 is 363
# days (2023-10-01 to 2024-09-28) and a 53-week year is 371.
ANNUAL_MIN_DAYS = 350
ANNUAL_MAX_DAYS = 380


# ── lxml/HTMLParser helpers ────────────────────────────────────────────────

def _local_name(el) -> str:
    """Local tag name, lowercased. ``<ix:nonFraction>`` -> ``nonfraction``."""
    tag = el.tag
    if not isinstance(tag, str):
        return ""                      # comments, PIs
    return tag.rsplit(":", 1)[-1].lower()


def _attr(el, name: str, default: str = "") -> str:
    """Attribute lookup tolerant of HTMLParser's lowercasing.

    Tries the spelling given, then lowercase, then the local part after any
    namespace prefix (``xsi:nil`` -> ``nil``), because which of the three
    survives depends on the document.
    """
    for candidate in (name, name.lower(), name.rsplit(":", 1)[-1].lower()):
        value = el.get(candidate)
        if value is not None:
            return value
    return default


def _qname_local(value: str) -> str:
    """Local part of a QName: ``ixt:num-dot-decimal`` -> ``num-dot-decimal``."""
    return value.rsplit(":", 1)[-1].strip().lower()


def _parse_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ── numeric transforms ─────────────────────────────────────────────────────
#
# Keys are the LOCAL part of @format, lowercased, so a filer's choice of
# namespace prefix (ixt, ixt-sec, iso4217-era variants) does not matter. Both
# the hyphenated Registry-3 spellings and the older run-together Registry-1/2
# spellings are listed, because a filer may use either.

# Measured, not guessed. Surveying all 250 cached documents (65 filings) found
# 648 ixt-sec:numwordsen facts using 16 distinct texts: no (192), two, one,
# three, four, six, five, ten, eight, nil (6), seven, zero, eleven,
# "three million" (1), none, fifteen. The first survey covered only 12 filings
# and missed "nil" and the scale word, which is how State Street FY2022-2025
# and Bank of America FY2021 failed to extract at all.
_ZERO_WORDS = {"zero", "no", "none", "nil", "nought", "naught"}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORDS.update(dict.fromkeys(_ZERO_WORDS, 0))

# Multipliers, which is why numwordsen cannot be a flat lookup table: "three
# million" is 3,000,000, and the flat version read it as an unknown word and
# took the whole filing down with it.
_SCALE_WORDS = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}

# Characters filers use for "nothing here": ASCII hyphen, non-breaking hyphen,
# figure dash, en dash, em dash, horizontal bar, minus sign.
_DASHES = "-‑‒–—―−"


def _strip_decoration(raw: str) -> str:
    """Remove currency symbols, spaces of every width, and wrapping parentheses."""
    text = (raw or "")
    for ch in (" ", " ", " ", " ", " ", "\t", "\n", "\r"):
        text = text.replace(ch, "")
    return text.strip()


def _signed_by_parentheses(text: str) -> Tuple[str, bool]:
    if text.startswith("(") and text.endswith(")"):
        return text[1:-1], True
    return text, False


def _decimal_from_digits(text: str, *, decimal_sep: str, group_sep: str,
                         transform: str) -> Decimal:
    body, negated = _signed_by_parentheses(_strip_decoration(text))
    body = body.replace(group_sep, "")
    if decimal_sep != ".":
        body = body.replace(decimal_sep, ".")
    body = re.sub(r"[^0-9.+-]", "", body)
    if body in ("", ".", "+", "-"):
        raise ValueParseError(
            f"transform {transform!r} found no digits in {text!r}")
    try:
        value = Decimal(body)
    except InvalidOperation as exc:
        raise ValueParseError(
            f"transform {transform!r} could not read {text!r} as a number") from exc
    return -value if negated else value


def _num_dot_decimal(text: str) -> Decimal:
    return _decimal_from_digits(text, decimal_sep=".", group_sep=",",
                                transform="num-dot-decimal")


def _num_comma_decimal(text: str) -> Decimal:
    return _decimal_from_digits(text, decimal_sep=",", group_sep=".",
                                transform="num-comma-decimal")


def _num_dot_decimal_apos(text: str) -> Decimal:
    return _decimal_from_digits(text, decimal_sep=".", group_sep="'",
                                transform="num-dot-decimal-apos")


def _num_comma_decimal_apos(text: str) -> Decimal:
    return _decimal_from_digits(text, decimal_sep=",", group_sep="'",
                                transform="num-comma-decimal-apos")


def _fixed_zero(text: str) -> Decimal:
    """``ixt:fixed-zero`` / ``ixt:zerodash``: the value is zero by declaration.

    3,443 facts in the corpus, every sampled one rendered as an em-dash with
    ``scale="6"`` — which is why the scale must not be applied blindly
    elsewhere; 0 scaled by anything is still 0, but a reader that fell back to
    "no digits, skip it" would drop a real zero and change what the resolver
    picks. The text is ignored on purpose: the transform *is* the value.
    """
    return Decimal(0)


def _no_content(text: str) -> Decimal:
    """``ixt:nocontent``: empty element, value zero (Registry 1/2)."""
    return Decimal(0)


def _num_words_en(text: str) -> Decimal:
    """``ixt-sec:numwordsen``: an English number word. 80 facts in the corpus.

    Observed values are "one", "two", "three" on counts of segments, vendors
    and subsidiaries. Hyphenated and "and"-joined compounds are handled so
    "twenty-one" and "one hundred and five" do not become a loud failure on a
    filing that happens to spell one out.
    """
    # _normalise_spaces, not _strip_decoration: the latter removes spaces, which
    # turned "one hundred" into the single unknown word "onehundred".
    body = _normalise_spaces(text).lower().replace("‐", "-")
    if body and body.strip(_DASHES) == "":
        return Decimal(0)                      # a bare dash spelled out
    # "and" is dropped as a token rather than by substring replacement, which
    # would also mangle "thousand".
    words = [w for w in re.split(r"[\s\-]+", body) if w and w != "and"]
    if not words:
        raise ValueParseError(f"numwordsen found no words in {text!r}")

    total = Decimal(0)
    current = Decimal(0)
    for word in words:
        if word in _NUMBER_WORDS:
            current += Decimal(_NUMBER_WORDS[word])
        elif word == "hundred":
            # "hundred" scales what came before it but keeps accumulating:
            # "one hundred five" is 105, not 100 then 5.
            current = (current or Decimal(1)) * 100
        elif word in _SCALE_WORDS:
            # A larger scale closes the current group: "three million" is
            # 3,000,000 and "two million five hundred" is 2,000,500.
            total += (current or Decimal(1)) * Decimal(_SCALE_WORDS[word])
            current = Decimal(0)
        else:
            raise ValueParseError(
                f"numwordsen does not know the word {word!r} (from {text!r})")
    return total + current


NUMERIC_TRANSFORMS = {
    # Registry 3 spellings
    "num-dot-decimal": _num_dot_decimal,
    "num-comma-decimal": _num_comma_decimal,
    "num-dot-decimal-apos": _num_dot_decimal_apos,
    "num-comma-decimal-apos": _num_comma_decimal_apos,
    "fixed-zero": _fixed_zero,
    "zerodash": _fixed_zero,
    "nocontent": _no_content,
    "numwordsen": _num_words_en,
    # Registry 1/2 run-together spellings of the same transforms
    "numdotdecimal": _num_dot_decimal,
    "numcommadecimal": _num_comma_decimal,
    "numdotdecimalin": _num_dot_decimal,
    "numunitdecimal": _num_dot_decimal,
    "numspacedot": _num_dot_decimal,
    "numspacecomma": _num_comma_decimal,
}

# Facts with no @format at all: 14,947 in the corpus, all plain numerals such
# as "269" or "6.11". The iXBRL spec says an unformatted value must already be
# a valid XBRL number, which is dot-decimal.
_DEFAULT_NUMERIC_TRANSFORM = _num_dot_decimal


# ── non-numeric transforms (only what DEI needs; P2-03) ────────────────────

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
_MONTHS.update({m[:3].lower(): i for m, i in list(_MONTHS.items())})


def _month_number(token: str) -> Optional[int]:
    return _MONTHS.get(token.strip(".,").lower())


def _normalise_spaces(text: str) -> str:
    """Collapse every width of space, including the ones filers actually use.

    Apple writes its cover-page date as "September\u00a028, 2024" with a
    non-breaking space, and a filing served as UTF-8 but decoded as latin-1
    turns that into "September\u00c2\u00a028, 2024". Both have to read as a
    date, so the separators are normalised and non-ASCII punctuation dropped
    before any splitting.
    """
    out = []
    for ch in (text or ""):
        if ch.isspace() or ch in ("\u00a0", "\u2007", "\u2009", "\u202f"):
            out.append(" ")
        elif ord(ch) < 128 or ch.isalnum():
            out.append(ch)
        # anything else (mojibake artefacts such as U+00C2) is dropped
    return " ".join("".join(out).split())


def _date_monthname_day_year(text: str) -> str:
    """"September 28, 2024" -> "2024-09-28". Used by DocumentPeriodEndDate."""
    parts = [p for p in re.split(r"[\s,]+", _normalise_spaces(text)) if p]
    if len(parts) < 3:
        raise ValueParseError(f"date-monthname-day-year-en cannot read {text!r}")
    month = _month_number(parts[0])
    if month is None:
        raise ValueParseError(f"unknown month name in {text!r}")
    return f"{int(parts[2]):04d}-{month:02d}-{int(parts[1]):02d}"


def _date_month_day_year(text: str) -> str:
    """"9/28/2024" or "09-28-2024" -> "2024-09-28"."""
    parts = [p for p in re.split(r"[^0-9]+", (text or "").strip()) if p]
    if len(parts) < 3:
        raise ValueParseError(f"date-month-day-year cannot read {text!r}")
    month, day, year = (int(parts[0]), int(parts[1]), int(parts[2]))
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def _date_year_month(text: str) -> str:
    parts = [p for p in re.split(r"[^0-9]+", (text or "").strip()) if p]
    if len(parts) < 2:
        raise ValueParseError(f"date-year-month cannot read {text!r}")
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}"


def _fixed_true(text: str) -> str:
    return "true"


def _fixed_false(text: str) -> str:
    return "false"


def _bool_ballot_box(text: str) -> str:
    """A checked box (U+2611/U+2612) is true, an empty one (U+2610) is false."""
    body = _strip_decoration(text)
    return "true" if any(ch in body for ch in ("☑", "☒", "x", "X")) else "false"


NONNUMERIC_TRANSFORMS = {
    "date-monthname-day-year-en": _date_monthname_day_year,
    "datemonthnamedayyearen": _date_monthname_day_year,
    "date-month-day-year": _date_month_day_year,
    "datemonthdayyear": _date_month_day_year,
    "date-year-month": _date_year_month,
    "dateyearmonth": _date_year_month,
    "datemonthdayyearen": _date_monthname_day_year,
    "fixed-true": _fixed_true,
    "fixedtrue": _fixed_true,
    "booleantrue": _fixed_true,
    "fixed-false": _fixed_false,
    "fixedfalse": _fixed_false,
    "booleanfalse": _fixed_false,
    "boolballotbox": _bool_ballot_box,
}
# Non-numeric transforms this module does not implement (ixt-sec:duryear,
# exchnameen, stateprovnameen, entityfilercategoryen, ...) are NOT fatal: their
# untransformed text is kept verbatim. That asymmetry with numeric facts is
# deliberate. A mis-transformed number silently becomes a wrong answer; a
# mis-transformed exchange name stays a readable string that no calculator
# touches, and none of the six DEI concepts P2-03 needs uses one.


# ── data model ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Context:
    """One ``xbrli:context``: who, when, and along which dimensions."""

    id: str
    entity_identifier: Optional[str] = None
    instant: Optional[date] = None
    start: Optional[date] = None
    end: Optional[date] = None
    dimensions: Tuple[Tuple[str, str], ...] = ()

    @property
    def is_dimensional(self) -> bool:
        return bool(self.dimensions)

    @property
    def period_type(self) -> Optional[str]:
        if self.instant is not None:
            return "instant"
        if self.start is not None and self.end is not None:
            return "duration"
        return None

    @property
    def duration_days(self) -> Optional[int]:
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days

    def is_annual_duration(self) -> bool:
        """A full fiscal year, allowing 52/53-week calendars (spec 6.4 rule 2)."""
        days = self.duration_days
        return days is not None and ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS


@dataclass(frozen=True)
class Unit:
    """One ``xbrli:unit``, reduced to a readable label such as ``USD/shares``."""

    id: str
    measures: Tuple[str, ...] = ()
    denominators: Tuple[str, ...] = ()

    @property
    def label(self) -> str:
        numerator = "*".join(self.measures) or "?"
        if self.denominators:
            return f"{numerator}/{'*'.join(self.denominators)}"
        return numerator


@dataclass
class Fact:
    """One numeric or non-numeric inline-XBRL fact, scaled and signed."""

    concept: str                         # "us-gaap:Revenues"
    context_id: str
    source_doc: str
    value: Optional[Decimal] = None      # None when nil or non-numeric
    text_value: Optional[str] = None     # non-numeric facts, and raw text
    is_nil: bool = False
    is_numeric: bool = True
    unit_ref: Optional[str] = None
    unit: Optional[str] = None           # resolved label, e.g. "USD"
    decimals: Optional[str] = None
    scale_raw: Optional[str] = None
    sign_raw: Optional[str] = None
    format_raw: Optional[str] = None
    element_id: Optional[str] = None
    hidden: bool = False
    # True when a registered NON-numeric transform raised and the raw text was
    # kept instead. Visible so a coverage report can list them rather than
    # letting a silently unparsed date look like a parsed one.
    transform_failed: bool = False
    context: Optional[Context] = None

    @property
    def concept_local(self) -> str:
        return self.concept.rsplit(":", 1)[-1]

    @property
    def namespace_prefix(self) -> str:
        return self.concept.rsplit(":", 1)[0] if ":" in self.concept else ""

    @property
    def is_dimensional(self) -> bool:
        return bool(self.context and self.context.is_dimensional)

    @property
    def period_type(self) -> Optional[str]:
        return self.context.period_type if self.context else None

    @property
    def start_date(self) -> Optional[date]:
        return self.context.start if self.context else None

    @property
    def end_date(self) -> Optional[date]:
        if not self.context:
            return None
        return self.context.instant if self.context.instant else self.context.end


# The six cover-page concepts P2-03 needs, by local name.
DEI_CONCEPTS = (
    "DocumentFiscalYearFocus",
    "DocumentPeriodEndDate",
    "DocumentType",
    "AmendmentFlag",
    "EntityRegistrantName",
    "EntityCentralIndexKey",
)


@dataclass
class Submission:
    """Every document of one filing, parsed and pooled."""

    accession: Optional[str]
    documents: List[str] = field(default_factory=list)
    contexts: Dict[str, Context] = field(default_factory=dict)
    units: Dict[str, Unit] = field(default_factory=dict)
    facts: List[Fact] = field(default_factory=list)

    @property
    def numeric_facts(self) -> List[Fact]:
        return [f for f in self.facts if f.is_numeric and not f.is_nil]

    @property
    def consolidated_facts(self) -> List[Fact]:
        """Non-dimensional numeric facts: the only kind v2 stores (spec 6.2)."""
        return [f for f in self.numeric_facts if not f.is_dimensional]

    def facts_for(self, concept: str) -> List[Fact]:
        """Facts for a concept, matched on the local name so prefixes don't matter."""
        wanted = concept.rsplit(":", 1)[-1]
        return [f for f in self.facts if f.concept_local == wanted]

    def dei(self) -> Dict[str, str]:
        """The P2-03 cover-page values, as strings.

        Cover-page facts live in ``ix:header/ix:hidden``, so this only works
        because hidden facts are collected. The non-dimensional fact is
        preferred when a concept appears more than once.
        """
        out: Dict[str, str] = {}
        for name in DEI_CONCEPTS:
            candidates = [f for f in self.facts_for(name) if not f.is_dimensional]
            if not candidates:
                continue
            fact = candidates[0]
            if fact.text_value is not None:
                out[name] = fact.text_value
            elif fact.value is not None:
                # DocumentFiscalYearFocus is tagged as a numeric fact by some
                # filers and non-numeric by others.
                out[name] = str(int(fact.value))
        return out

    def format_values(self) -> Dict[str, int]:
        """Distinct ``@format`` values seen, with counts (the P2-02 survey)."""
        counts: Dict[str, int] = {}
        for fact in self.facts:
            key = fact.format_raw or "<none>"
            counts[key] = counts.get(key, 0) + 1
        return counts


# ── parsing ────────────────────────────────────────────────────────────────

_CHARSET_DECLARATION = re.compile(
    rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.I)


def _html_parser(encoding: Optional[str] = None) -> etree.HTMLParser:
    return etree.HTMLParser(huge_tree=True, recover=True, encoding=encoding)


def _parse_html(path: PathLike):
    """Parse an HTML/iXBRL document, choosing the encoding deliberately.

    libxml2 falls back to latin-1 for an HTML document that declares no
    charset, so UTF-8 bytes come back mojibake: a non-breaking space
    (``\xc2\xa0``) becomes U+00C2 U+00A0, and Apple's cover-page date
    "September\u00a028, 2024" turns into something no date transform can read.
    The SEC filings themselves declare their charset and were never affected,
    which is exactly why this stayed invisible until a trimmed fixture did not.

    So: honour a declaration when there is one, and otherwise prefer UTF-8 when
    the bytes actually decode as UTF-8. Nothing is guessed — the bytes either
    decode or they do not.
    """
    data = Path(path).read_bytes()
    declared = _CHARSET_DECLARATION.search(data[:4096])
    if declared is None:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass                       # not UTF-8; let libxml2 sniff as before
        else:
            return etree.parse(str(path), _html_parser(encoding="utf-8"))
    return etree.parse(str(path), _html_parser())


def parse_contexts(root) -> Dict[str, Context]:
    out: Dict[str, Context] = {}
    for el in root.iter():
        if _local_name(el) != "context":
            continue
        cid = _attr(el, "id")
        if not cid:
            continue
        instant = start = end = None
        entity = None
        dimensions: List[Tuple[str, str]] = []
        for sub in el.iter():
            name = _local_name(sub)
            text = (sub.text or "").strip()
            if name == "instant":
                instant = _parse_date(text)
            elif name == "startdate":
                start = _parse_date(text)
            elif name == "enddate":
                end = _parse_date(text)
            elif name == "identifier":
                entity = text or None
            elif name in ("explicitmember", "typedmember"):
                dimensions.append((_attr(sub, "dimension"), text))
        out[cid] = Context(
            id=cid, entity_identifier=entity, instant=instant, start=start,
            end=end, dimensions=tuple(sorted(dimensions)),
        )
    return out


def parse_units(root) -> Dict[str, Unit]:
    """Read ``xbrli:unit`` into measure/denominator lists.

    Units matter for more than display: a ratio compared against a USD total,
    or EPS added to revenue, is a unit error the calculator must be able to
    refuse (T2-06), and it can only refuse what was recorded.
    """
    out: Dict[str, Unit] = {}
    for el in root.iter():
        if _local_name(el) != "unit":
            continue
        uid = _attr(el, "id")
        if not uid:
            continue
        measures: List[str] = []
        denominators: List[str] = []
        in_denominator = False
        for sub in el.iter():
            name = _local_name(sub)
            if name == "unitdenominator":
                in_denominator = True
            elif name == "unitnumerator":
                in_denominator = False
            elif name == "measure":
                text = (sub.text or "").strip().rsplit(":", 1)[-1]
                (denominators if in_denominator else measures).append(text)
        out[uid] = Unit(id=uid, measures=tuple(measures),
                        denominators=tuple(denominators))
    return out


def _hidden_element_ids(root) -> set:
    """Python ids of elements inside any ``ix:hidden`` block."""
    hidden = set()
    for el in root.iter():
        if _local_name(el) == "hidden":
            for sub in el.iter():
                hidden.add(id(sub))
    return hidden


def _numeric_value(el, *, source_doc: str) -> Tuple[Optional[Decimal], str, str, str]:
    """Apply format, then scale, then sign. Returns (value, format, scale, sign)."""
    format_raw = _attr(el, "format")
    scale_raw = _attr(el, "scale")
    sign_raw = _attr(el, "sign")
    concept = _attr(el, "name")

    key = _qname_local(format_raw) if format_raw else ""
    if key:
        transform = NUMERIC_TRANSFORMS.get(key)
        if transform is None:
            raise UnknownTransformError(format_raw, concept, source_doc)
    else:
        transform = _DEFAULT_NUMERIC_TRANSFORM

    raw_text = "".join(el.itertext())
    value = transform(raw_text)

    # Scale is an exponent, applied with Decimal arithmetic. scaleb keeps this
    # exact; `value * Decimal(10) ** scale` would be too, but float(10**-2)
    # would not, and that mistake is invisible until a percentage is wrong in
    # the fourth decimal place.
    if scale_raw:
        try:
            value = value.scaleb(int(scale_raw))
        except (ValueError, InvalidOperation) as exc:
            raise ValueParseError(
                f"bad @scale {scale_raw!r} on {concept} in {source_doc}") from exc

    if sign_raw == "-":
        value = -value

    return value, format_raw, scale_raw, sign_raw


def _nonnumeric_value(el) -> Tuple[Optional[str], str, bool]:
    """Returns (value, raw format, whether a registered transform failed).

    A non-numeric transform that raises must NOT take the filing down with it.
    This was learned the hard way: a single cover-page date arriving as
    "September\u00c2\u00a028, 2024" raised out of parse_document and made the
    whole submission unextractable, discarding 1,127 perfectly good numeric
    facts over one string in the header. Numeric facts stay fatal, because a
    number read wrongly becomes a wrong answer; a date kept as its own text
    stays obviously unparsed and no calculator touches it.
    """
    format_raw = _attr(el, "format")
    raw_text = "".join(el.itertext()).strip()
    key = _qname_local(format_raw) if format_raw else ""
    transform = NONNUMERIC_TRANSFORMS.get(key)
    if transform is None:
        # Kept verbatim, by design: see the note under NONNUMERIC_TRANSFORMS.
        return (raw_text or None), format_raw, False
    try:
        return transform(raw_text), format_raw, False
    except ValueParseError:
        return (raw_text or None), format_raw, True


def _is_nil(el) -> bool:
    return _attr(el, "xsi:nil").strip().lower() == "true"


def parse_document(path: PathLike) -> Tuple[Dict[str, Context], Dict[str, Unit], List[Fact]]:
    """Parse one document into (contexts, units, facts)."""
    source_doc = Path(path).name
    root = _parse_html(path).getroot()
    contexts = parse_contexts(root)
    units = parse_units(root)
    hidden = _hidden_element_ids(root)

    facts: List[Fact] = []
    for el in root.iter():
        name = _local_name(el)
        if name not in ("nonfraction", "nonnumeric"):
            continue
        concept = _attr(el, "name")
        if not concept:
            continue
        numeric = name == "nonfraction"
        nil = _is_nil(el)

        value: Optional[Decimal] = None
        text_value: Optional[str] = None
        format_raw = scale_raw = sign_raw = ""
        transform_failed = False
        if nil:
            # xsi:nil is NOT zero — it asserts "no value reported". The 32 nil
            # facts in the corpus are all CommitmentsAndContingencies, where
            # reading 0 would claim the company disclosed zero commitments.
            format_raw = _attr(el, "format")
            scale_raw = _attr(el, "scale")
            sign_raw = _attr(el, "sign")
        elif numeric:
            value, format_raw, scale_raw, sign_raw = _numeric_value(
                el, source_doc=source_doc)
            text_value = "".join(el.itertext()).strip() or None
        else:
            text_value, format_raw, transform_failed = _nonnumeric_value(el)

        facts.append(Fact(
            concept=concept,
            context_id=_attr(el, "contextRef"),
            source_doc=source_doc,
            value=value,
            text_value=text_value,
            is_nil=nil,
            is_numeric=numeric,
            unit_ref=_attr(el, "unitRef") or None,
            decimals=_attr(el, "decimals") or None,
            scale_raw=scale_raw or None,
            sign_raw=sign_raw or None,
            format_raw=format_raw or None,
            element_id=_attr(el, "id") or None,
            hidden=id(el) in hidden,
            transform_failed=transform_failed,
        ))
    return contexts, units, facts


def parse_submission(
    paths: Union[PathLike, Sequence[PathLike]],
    *,
    accession: Optional[str] = None,
) -> Submission:
    """Parse every document of one filing and pool contexts, units and facts.

    Pooling is the whole point (spec 6.4 rule 1). Wells Fargo's FY2024 wrapper
    carries 18 facts and the contexts; its EX-13 exhibit carries 7,285 facts
    that reference those contexts. Parsed separately, the wrapper has almost no
    facts and the exhibit's facts have no periods.
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = list(paths)

    submission = Submission(accession=accession)
    for path in paths:
        contexts, units, facts = parse_document(path)
        submission.documents.append(Path(path).name)
        # Later documents must not silently redefine an id an earlier one
        # already bound; first definition wins, which is the wrapper's.
        for cid, ctx in contexts.items():
            submission.contexts.setdefault(cid, ctx)
        for uid, unit in units.items():
            submission.units.setdefault(uid, unit)
        submission.facts.extend(facts)

    # Resolve references only after every document has contributed, or an
    # EX-13 fact would be bound before its wrapper's context exists.
    for fact in submission.facts:
        fact.context = submission.contexts.get(fact.context_id)
        if fact.unit_ref:
            unit = submission.units.get(fact.unit_ref)
            fact.unit = unit.label if unit else fact.unit_ref
    return submission


# ── precision preference (D22) ─────────────────────────────────────────────
#
# A filing can tag the same concept, in the same context, more than once at
# different precisions: Apple's FY2024 10-K reports UnrecognizedTaxBenefits for
# context c-21 as 22,000,000,000 with decimals="-8" (the narrative sentence,
# "$22.0 billion") and as 22,038,000,000 with decimals="-6" (the tax-footnote
# table). Both are real tagged values and neither is wrong; they are the same
# quantity stated to different precisions.
#
# Before this rule the extractor kept whichever came first in document order,
# which was the coarse one often enough to account for the ENTIRE shortfall
# against the SEC's own rendering in P2-09 (233 of 27,506 comparable values).
# companyfacts keeps the precise instance, and so should we: a reader checking
# an answer against the filing will find the table.
#
# What this rule must not do is merge values that genuinely disagree. The test
# is the precision the filer itself declared: @decimals="-8" asserts accuracy to
# the nearest 10^8, so a value carries a half-unit tolerance of 0.5 x 10^8.
# Two instances describe the same quantity only if they fall within the sum of
# their tolerances. Outside that, both are kept and the resolver reports
# `ambiguous_concept` — which is the right outcome, because something is wrong
# and this module cannot tell what.

# Sentinel for decimals="INF" (exact), which is more precise than any integer.
_DECIMALS_EXACT = 10_000


def decimals_rank(raw: Optional[str]) -> Optional[int]:
    """Comparable precision from ``@decimals``. Larger is more precise.

    ``None`` means the filer declared no precision, in which case nothing can be
    inferred and the instances are not merged.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.upper() == "INF":
        return _DECIMALS_EXACT
    try:
        return int(text)
    except ValueError:
        return None


def decimals_tolerance(raw: Optional[str]) -> Optional[Decimal]:
    """Half-unit tolerance implied by ``@decimals``, or None when undeclared.

    ``decimals="-6"`` means the value is accurate to the nearest million, so it
    may differ from the true quantity by up to half a million.
    """
    rank = decimals_rank(raw)
    if rank is None:
        return None
    if rank == _DECIMALS_EXACT:
        return Decimal(0)
    return Decimal(1).scaleb(-rank) / 2


def _instances_agree(a: "Fact", b: "Fact") -> bool:
    """Do two instances state the same quantity to their declared precisions?"""
    if a.value is None or b.value is None:
        return False
    if a.value == b.value:
        return True
    tol_a = decimals_tolerance(a.decimals)
    tol_b = decimals_tolerance(b.decimals)
    if tol_a is None or tol_b is None:
        # Undeclared precision: no basis for calling them the same quantity.
        return False
    return abs(a.value - b.value) <= tol_a + tol_b


def _precision_sort_key(fact: "Fact") -> Tuple[int, int, str, str, str]:
    """Most precise first, then a total order so a rebuild is deterministic."""
    rank = decimals_rank(fact.decimals)
    return (
        0 if rank is not None else 1,          # declared precision wins
        -(rank if rank is not None else 0),    # larger decimals first
        fact.element_id or "",
        fact.source_doc or "",
        str(fact.value),
    )


@dataclass(frozen=True)
class PrecisionMerge:
    """One instance dropped in favour of a more precisely tagged one."""

    concept: str
    context_id: str
    unit: Optional[str]
    kept_value: Decimal
    kept_decimals: Optional[str]
    dropped_value: Decimal
    dropped_decimals: Optional[str]

    @property
    def changed_the_value(self) -> bool:
        return self.kept_value != self.dropped_value


def prefer_precise_instances(
    facts: Sequence["Fact"],
) -> Tuple[List["Fact"], List[PrecisionMerge]]:
    """Collapse duplicate instances of a fact, keeping the most precise (D22).

    Grouped by (concept, context, unit) — the triple that identifies one
    quantity. Returns the kept facts in their original order, plus a record of
    every merge, so a build can report how many values the rule changed rather
    than changing them silently.
    """
    groups: Dict[Tuple[str, str, Optional[str]], List["Fact"]] = {}
    for fact in facts:
        groups.setdefault((fact.concept, fact.context_id, fact.unit), []).append(fact)

    dropped: set = set()
    merges: List[PrecisionMerge] = []
    for (concept, context_id, unit), members in groups.items():
        if len(members) == 1:
            continue
        ordered = sorted(members, key=_precision_sort_key)
        keeper = ordered[0]
        for other in ordered[1:]:
            if not _instances_agree(keeper, other):
                # A real disagreement. Keep both: the resolver will see two
                # values for one concept and abstain, which is what should
                # happen when a filing contradicts itself.
                continue
            dropped.add(id(other))
            if other.value != keeper.value:
                merges.append(PrecisionMerge(
                    concept=concept, context_id=context_id, unit=unit,
                    kept_value=keeper.value, kept_decimals=keeper.decimals,
                    dropped_value=other.value, dropped_decimals=other.decimals,
                ))
    return [f for f in facts if id(f) not in dropped], merges


def annual_facts(
    submission: Submission,
    period_end: date,
    *,
    tolerance_days: int = 3,
    prefer_precise: bool = True,
) -> List[Fact]:
    """Consolidated facts belonging to the fiscal year ending at ``period_end``.

    Spec 6.4 rule 2: duration facts must cover a full annual window
    (350-380 days, so 52/53-week filers qualify) ending at the period end;
    instant facts must sit at the period end. Transition periods and the
    comparative prior years in the same filing are excluded by the window, and
    dimensional contexts by ``consolidated_facts``.
    """
    out: List[Fact] = []
    for fact in submission.consolidated_facts:
        ctx = fact.context
        if ctx is None:
            continue
        if ctx.period_type == "instant":
            if ctx.instant and abs((ctx.instant - period_end).days) <= tolerance_days:
                out.append(fact)
        elif (ctx.period_type == "duration" and ctx.end
                and abs((ctx.end - period_end).days) <= tolerance_days
                and ctx.is_annual_duration()):
            out.append(fact)
    if prefer_precise:
        # D22. Exposed as a flag rather than applied unconditionally so T2-13
        # can assert what changes when it is off — a rule whose effect cannot
        # be switched off has not been shown to have one.
        out, _merges = prefer_precise_instances(out)
    return out


def survey_formats(paths: Iterable[PathLike]) -> Dict[str, Dict[str, int]]:
    """Distinct ``@format`` values with counts, SPLIT BY element kind (P2-02).

    The split matters. Only ``ix:nonFraction`` formats are fatal when
    unimplemented; ``ix:nonNumeric`` ones fall back to verbatim text on purpose.
    An earlier version of this function pooled the two and then reported ten
    "unsupported" formats on a corpus that extracts cleanly — every one of them
    a non-numeric transform (``ixt-sec:duryear``, ``exchnameen``, ...) that the
    extractor is right to leave alone. A survey that cries wolf is worse than
    none, because the next person adds transforms nothing needs.

    Returns ``{"nonfraction": {...}, "nonnumeric": {...}}``.
    """
    counts: Dict[str, Dict[str, int]] = {"nonfraction": {}, "nonnumeric": {}}
    for path in paths:
        root = _parse_html(path).getroot()
        for el in root.iter():
            kind = _local_name(el)
            if kind not in ("nonfraction", "nonnumeric"):
                continue
            key = _attr(el, "format") or "<none>"
            bucket = counts[kind]
            bucket[key] = bucket.get(key, 0) + 1
    return counts


def unsupported_numeric_formats(
    counts: Dict[str, Dict[str, int]],
) -> Dict[str, int]:
    """Surveyed ``ix:nonFraction`` formats that would raise UnknownTransformError.

    Takes the output of :func:`survey_formats`. A non-empty result means a build
    over those documents will stop; adding each one to NUMERIC_TRANSFORMS (with
    a test) is the fix.
    """
    numeric = counts.get("nonfraction", {})
    return {
        raw: n for raw, n in numeric.items()
        if raw != "<none>" and _qname_local(raw) not in NUMERIC_TRANSFORMS
    }


def untransformed_nonnumeric_formats(
    counts: Dict[str, Dict[str, int]],
) -> Dict[str, int]:
    """Non-numeric formats kept verbatim. Informational, never an error."""
    nonnumeric = counts.get("nonnumeric", {})
    return {
        raw: n for raw, n in nonnumeric.items()
        if raw != "<none>" and _qname_local(raw) not in NONNUMERIC_TRANSFORMS
    }
