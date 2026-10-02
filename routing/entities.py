"""Deterministic company resolution: which filer is the question about? (P3-02)

    from routing.entities import resolve_entities
    resolve_entities("Compare Apple and JPMorgan's R&D spend")
    # EntityResolution(tickers=('AAPL', 'JPM'), ...)

No LLM, for the same reason as ``routing/periods.py``: a company mention is a
lookup, not a judgement, and v1 spent a model call on it and then swallowed
every failure into "Which company are you asking about?" (Phase 0 defect F1).

Resolution order, strongest evidence first
------------------------------------------
1. **An explicit ticker** written as a ticker (``AAPL``, ``$AAPL``).
2. **A curated alias** — the names people actually type. "JPMorgan" is not a
   substring of "JPMorgan Chase & Co.", "BofA" is not a substring of anything,
   and "Google" does not appear in "Alphabet Inc." at all, so a substring
   search over the registered names resolves none of the three.
3. **A bundled registered name**, matched on normalised text so "Apple Inc"
   and "apple inc." both land.
4. **The catalog**, which knows every ticker this project has filings for,
   including the ones that arrived through on-demand ingestion.
5. **The SEC registry**, injected rather than imported, for any other filer.
   It is the only step that can touch the network, so it is a parameter with
   an offline-safe default and tests pass a fake.

Two false-positive traps this guards against
---------------------------------------------
**Short tickers inside ordinary words.** A case-insensitive search for ``GS``
matches "things"; for ``V`` it matches every "v". So a ticker of three or more
characters matches case-insensitively on a word boundary, while one or two
characters must appear in upper case. "gs" in lower case therefore misses —
acceptable, because "goldman" is an alias and a lower-case two-letter ticker
is not how anyone writes one.

**A mention that resolves to nothing.** Returned in ``unresolved`` rather than
dropped. v1 dropped them, and "compare Apple and SpaceX" silently answered
about Apple alone; the abstention gate (P3-07) turns an unresolved mention into
``company_not_found`` and a partial one into a stated caveat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config import COMPANIES

#: Spelling people use -> ticker. Keys are normalised (see ``_normalise``).
#: Every entry is a name a reader would type that neither the ticker nor the
#: registered name would catch. Ordered longest-first at match time so
#: "bank of america" wins over "america" and "jpmorgan chase" over "chase".
ALIASES: Dict[str, str] = {
    # Technology
    "apple": "AAPL",
    "microsoft": "MSFT",
    "msft": "MSFT",
    "alphabet": "GOOGL",
    "google": "GOOGL",
    "amazon": "AMZN",
    "amazon com": "AMZN",
    "netflix": "NFLX",
    # Banking
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "jpmorgan chase": "JPM",
    "jp morgan chase": "JPM",
    "jpmorganchase": "JPM",
    "chase": "JPM",
    "wells fargo": "WFC",
    "wells": "WFC",
    "bank of america": "BAC",
    "bofa": "BAC",
    "bank of america corp": "BAC",
    "goldman": "GS",
    "goldman sachs": "GS",
    "the goldman sachs group": "GS",
    # Asset management
    "blackrock": "BLK",
    "black rock": "BLK",
    "state street": "STT",
    "t rowe price": "TROW",
    "t rowe": "TROW",
    "troweprice": "TROW",
    "invesco": "IVZ",
}

#: Tickers shorter than this must be written in upper case to match.
_CASE_SENSITIVE_TICKER_LEN = 3

_WORD = re.compile(r"[A-Za-z0-9&.$]+")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and possessives, collapse whitespace.

    "Apple's", "APPLE INC.", "apple inc" and "Apple,  Inc" all normalise to the
    same thing, which is what makes a single alias table enough.
    """
    text = text.replace("’", "'")
    text = re.sub(r"'s\b", " ", text, flags=re.I)
    text = re.sub(r"[^a-z0-9&]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _strip_legal_suffix(name: str) -> str:
    """"Apple Inc." -> "apple"; the suffix is noise for matching."""
    normalised = _normalise(name)
    normalised = re.sub(
        r"\b(?:inc|incorporated|corp|corporation|company|co|ltd|limited|plc|"
        r"group|holdings|holding|lp|llc|sa|nv|ag)\b", " ", normalised,
    )
    normalised = re.sub(r"^the\b", " ", normalised)
    return re.sub(r"\s+", " ", normalised).strip()


_BUNDLED_TICKERS: Tuple[str, ...] = tuple(c["ticker"] for c in COMPANIES)
_BUNDLED_NAMES: Dict[str, str] = {
    _strip_legal_suffix(c["name"]): c["ticker"] for c in COMPANIES
}
_TICKER_TO_NAME: Dict[str, str] = {c["ticker"]: c["name"] for c in COMPANIES}


@dataclass(frozen=True)
class EntityResolution:
    """Which filers the question names, and which mentions did not resolve."""

    tickers: Tuple[str, ...] = ()
    #: ticker -> the display name to show the user.
    names: Dict[str, str] = field(default_factory=dict)
    #: ticker -> the phrase in the question that produced it, for the trace.
    matched: Dict[str, str] = field(default_factory=dict)
    #: Capitalised phrases that look like a company but resolved to nothing.
    unresolved: Tuple[str, ...] = ()

    @property
    def resolved_any(self) -> bool:
        return bool(self.tickers)


#: What a registry lookup must return: a dict with at least ``ticker``, as
#: ``ingestion.registry.resolve_company`` does, or None.
RegistryLookup = Callable[[str], Optional[dict]]


def _no_registry(_mention: str) -> Optional[dict]:
    """The offline default: resolve nothing beyond the catalog."""
    return None


def sec_registry_lookup(mention: str) -> Optional[dict]:
    """``ingestion.registry.resolve_company``, imported lazily.

    Pass this explicitly to allow network resolution of an unbundled filer. It
    is not the default because importing it eagerly, or calling it from a unit
    test, reaches for ``www.sec.gov``.
    """
    from ingestion.registry import resolve_company
    return resolve_company(mention)


def _ticker_candidates(question: str) -> List[Tuple[str, str]]:
    """(ticker, matched text) for tickers written as tickers."""
    found: List[Tuple[str, str]] = []
    for token in _WORD.findall(question):
        bare = token.lstrip("$").rstrip(".")
        if not bare:
            continue
        upper = bare.upper()
        if upper not in _BUNDLED_TICKERS:
            continue
        if len(upper) < _CASE_SENSITIVE_TICKER_LEN and bare != upper:
            continue          # "gs" in "things" / a lower-case two-letter word
        found.append((upper, token))
    return found


def _alias_candidates(question: str, extra: Dict[str, str]) -> List[Tuple[str, str]]:
    """(ticker, matched phrase) from the alias table and the bundled names.

    Matching is longest key first, so "bank of america" is tried before
    "america" could be and "jpmorgan chase" before "chase" — a shorter key
    overlapping an already-matched span is skipped, so one mention cannot
    produce two tickers.

    The result is then ordered by where each phrase appears in the question,
    not by key length: "Compare Apple and Microsoft" must come back as
    (AAPL, MSFT), because the answer template and the citation numbering
    follow the order the user asked in.
    """
    haystack = f" {_normalise(question)} "
    table = {**_BUNDLED_NAMES, **ALIASES, **extra}
    consumed: List[Tuple[int, int]] = []
    hits: List[Tuple[int, str, str]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < c_end and c_start < end for c_start, c_end in consumed)

    for phrase in sorted(table, key=len, reverse=True):
        if not phrase:
            continue
        needle = f" {phrase} "
        at = haystack.find(needle)
        while at != -1:
            start, end = at + 1, at + 1 + len(phrase)
            if not overlaps(start, end):
                consumed.append((start, end))
                hits.append((start, table[phrase], phrase))
                break
            at = haystack.find(needle, at + 1)

    hits.sort(key=lambda h: h[0])
    return [(ticker, phrase) for _at, ticker, phrase in hits]


#: Words that begin a capitalised phrase without being part of a company name.
_SENTENCE_LEAD = frozenset({
    "what", "when", "where", "which", "who", "why", "how", "did", "does", "do",
    "is", "was", "were", "are", "compare", "summarise", "summarize", "tell",
    "show", "give", "list", "describe", "explain", "in", "the", "a", "an",
    "as", "of", "for", "and", "or", "by", "on", "at", "from", "to", "item",
    "i", "fiscal", "fy", "q1", "q2", "q3", "q4",
})

_CAPITALISED_PHRASE = re.compile(r"\b([A-Z][A-Za-z0-9.&'’-]*(?:\s+[A-Z][A-Za-z0-9.&'’-]*)*)")


def _unresolved_mentions(question: str, consumed: Iterable[str]) -> Tuple[str, ...]:
    """Capitalised phrases that are probably a company and resolved to nothing.

    Deliberately conservative: it exists so "compare Apple and SpaceX" says
    SpaceX was dropped, not to guess at every noun. A phrase counts only if it
    is not already consumed by a resolved mention, is not a sentence-leading
    question word, and is not a bare number or a known metric word.
    """
    taken = {_normalise(c) for c in consumed}
    out: List[str] = []
    for match in _CAPITALISED_PHRASE.finditer(question):
        # Report the company, not the grammar: "SpaceX's" is the mention
        # "SpaceX", and an abstention message that says it could not find
        # "SpaceX's" reads like a different kind of failure.
        phrase = re.sub(r"['’]s$", "", match.group(1).strip(" .,;:"))
        norm = _normalise(phrase)
        if not norm or norm in taken:
            continue
        words = norm.split()
        if words[0] in _SENTENCE_LEAD:
            # Drop the leading question word and retry the remainder, so
            # "What about SpaceX" still surfaces SpaceX.
            words = words[1:]
            if not words:
                continue
            norm = " ".join(words)
            phrase = " ".join(phrase.split()[1:])
            if norm in taken:
                continue
        if len(norm) < 3 or norm.isdigit():
            continue
        if any(norm == _normalise(t) for t in taken):
            continue
        if phrase and phrase not in out:
            out.append(phrase)
    return tuple(out)


def resolve_entities(
    question: str,
    *,
    catalog_tickers: Optional[Sequence[str]] = None,
    registry_lookup: RegistryLookup = _no_registry,
    detect_unresolved: bool = True,
) -> EntityResolution:
    """Resolve every company mention in ``question``. Never raises.

    ``catalog_tickers`` are the tickers the catalog holds filings for; they are
    matched as tickers even when they are not bundled, so a filer added by
    on-demand ingestion is reachable without a registry call.
    ``registry_lookup`` is tried last and only for a mention nothing else
    resolved; the default resolves nothing, so this function is offline unless
    a caller opts in.
    """
    question = question or ""
    tickers: List[str] = []
    names: Dict[str, str] = {}
    matched: Dict[str, str] = {}

    def add(ticker: str, phrase: str, name: Optional[str] = None) -> None:
        ticker = ticker.upper()
        if ticker in matched:
            return
        tickers.append(ticker)
        matched[ticker] = phrase
        names[ticker] = name or _TICKER_TO_NAME.get(ticker, ticker)

    extra_names: Dict[str, str] = {}
    for ticker in catalog_tickers or ():
        upper = str(ticker).upper()
        if upper not in _BUNDLED_TICKERS:
            extra_names[_normalise(upper)] = upper

    for ticker, phrase in _ticker_candidates(question):
        add(ticker, phrase)
    # Catalog-only tickers, written as tickers.
    for token in _WORD.findall(question):
        bare = token.lstrip("$").rstrip(".")
        key = _normalise(bare)
        if key in extra_names and (
            len(bare) >= _CASE_SENSITIVE_TICKER_LEN or bare == bare.upper()
        ):
            add(extra_names[key], token)

    for ticker, phrase in _alias_candidates(question, {}):
        add(ticker, phrase)

    unresolved = _unresolved_mentions(question, matched.values()) if detect_unresolved else ()

    still_unresolved: List[str] = []
    for mention in unresolved:
        record = None
        try:
            record = registry_lookup(mention)
        except Exception:
            # A registry outage must not lose the mentions that did resolve.
            record = None
        if record and record.get("ticker"):
            add(str(record["ticker"]), mention, record.get("title"))
        else:
            still_unresolved.append(mention)

    return EntityResolution(
        tickers=tuple(tickers),
        names=names,
        matched=matched,
        unresolved=tuple(still_unresolved),
    )
