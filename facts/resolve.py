"""Resolution: (ticker, metric, period, as_of) -> one fact or a typed abstain.

    from facts.resolve import FactsResolver, PeriodSelector
    r = FactsResolver()
    r.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024))
    r.resolve("BLK",  "revenue", PeriodSelector.latest(), as_of="2025-01-01")

This module implements spec 6.4 and carries the project's two hardest-won
rules. Both are about refusing to answer.

**Rule 3 — never silently pick.** Candidate lists are a search space, not a
priority order. If an override exists it wins; otherwise exactly one candidate
having a value is the only case that resolves; anything else returns
``ambiguous_concept`` listing every candidate and its value. BlackRock FY2024
tags ``us-gaap:Revenues`` = $12,794M and
``RevenueFromContractWithCustomerExcludingAssessedTax`` = $20,407M, and a
priority list picks the first: a 37% understatement delivered with a citation
(D0.3). The override for BLK exists precisely so that filer resolves, and it
carries its evidence.

**D2 — point in time.** Filing selection happens *before* fact selection, and
only filings public on ``as_of`` are eligible. Doing it the other way round —
find the fact, then check the date — is how look-ahead bugs happen, because the
fact is already in hand and the temptation is to keep it.

**D14 — restatements.** Among eligible filings for a period, the latest-filed
wins, which means an amendment supersedes its original. But an amendment need
not restate anything: the GS FY2023 10-K/A carries 64 facts and no financial
ones, so the resolver walks from newest to oldest and takes the first filing
that actually has the metric. ``restated`` then says whether the value came
from an amendment, and ``amendment_accession`` reports that one exists even
when it did not change this figure — those are different facts about the world
and the answer template needs both.

One consequence of how the store is built is worth stating: it holds only each
filing's **own** period, not the comparative prior-year columns printed beside
it. So FY2023 revenue comes from the FY2023 filing, never from the FY2024
filing's comparative column. That is deliberate — the two can differ after a
restatement, and silently preferring whichever was convenient would make the
answer depend on which filings happened to be built.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from catalog.store import CatalogStore, Filing
from config import settings
from facts.concepts import Metric, Registry, load_registry
from facts.store import FactsStore, StoredFact

# Spec 6.5 abstain_reason enum, as far as this module can produce it.
REASON_COMPANY_NOT_FOUND = "company_not_found"
REASON_NO_FILING = "no_filing_for_company"
REASON_PERIOD_NOT_COVERED = "period_not_covered"
REASON_PERIOD_NOT_FILED = "period_not_filed_as_of"
REASON_FUTURE_PERIOD = "future_period"
REASON_METRIC_NOT_SUPPORTED = "metric_not_supported"
REASON_METRIC_NOT_IN_FILING = "metric_not_found_in_filing"
REASON_AMBIGUOUS = "ambiguous_concept"
REASON_UNSUPPORTED_PERIOD_TYPE = "unsupported_period_type"

VALIDATED = "validated"
UNVALIDATED = "unvalidated"
CONFLICT = "conflict"

# A rendered statement has dozens of numbers in it. v1's parser produces section
# boundaries of wildly varying quality: measured across the 36 parsed documents,
# the section titled "Consolidated Statements of Operations" runs to 1,116,433
# characters for one filer, is 106 characters of bare heading for another, and is
# empty for JPMorgan in all three years. A miss against text like that says
# nothing about the fact, so below this many numeric tokens the status is
# `unvalidated` with the reason recorded, rather than `conflict`. Calling those
# conflicts reported 73 disagreements on a corpus whose values are demonstrably
# right - AMZN FY2023 revenue of 574,785M is correct and its "income statement"
# section is one heading line.
MIN_STATEMENT_NUMERALS = 20

PERIOD_KINDS = ("fiscal_label", "period_end", "latest", "calendar_year")


@dataclass(frozen=True)
class PeriodSelector:
    """Which period the caller means (spec P2-07)."""

    kind: str
    value: Optional[Union[int, str]] = None

    def __post_init__(self) -> None:
        if self.kind not in PERIOD_KINDS:
            raise ValueError(f"period kind must be one of {PERIOD_KINDS}, "
                             f"got {self.kind!r}")
        if self.kind in ("fiscal_label", "period_end", "calendar_year") \
                and self.value is None:
            raise ValueError(f"period kind {self.kind!r} needs a value")

    @classmethod
    def fiscal_label(cls, label: int) -> "PeriodSelector":
        return cls("fiscal_label", int(label))

    @classmethod
    def period_end(cls, iso_date: str) -> "PeriodSelector":
        return cls("period_end", str(iso_date))

    @classmethod
    def latest(cls) -> "PeriodSelector":
        return cls("latest")

    @classmethod
    def calendar_year(cls, year: int) -> "PeriodSelector":
        return cls("calendar_year", int(year))

    def describe(self) -> str:
        if self.kind == "latest":
            return "the latest filed year"
        if self.kind == "fiscal_label":
            return f"fiscal {self.value}"
        if self.kind == "calendar_year":
            return f"calendar {self.value}"
        return f"the year ending {self.value}"


def _plain(value: Decimal) -> str:
    """Decimal without exponent notation.

    ``str(Decimal("2.0407E+10"))`` keeps the exponent, and an abstention
    message reading "candidates: 1.2794E+10 vs 2.0407E+10" asks the reader to
    decode scientific notation before they can see that one is 12.8 billion and
    the other 20.4 billion.
    """
    return format(value, "f")


@dataclass(frozen=True)
class Abstain:
    """A refusal with a reason from the 6.5 enum. Never a value."""

    reason: str
    detail: str = ""
    candidates: Tuple[str, ...] = ()
    candidate_values: Dict[str, str] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return False


@dataclass(frozen=True)
class Resolution:
    """One fact, with everything needed to cite and explain it."""

    ticker: str
    metric: str
    metric_label: str
    concept: str
    value: Decimal
    unit: Optional[str]
    period_type: str
    start_date: Optional[str]
    end_date: Optional[str]
    fiscal_label: int
    accession: str
    filing_date: str
    form_type: str
    source_doc: Optional[str]
    selection: str                      # override | single_candidate | ...
    candidates_considered: Tuple[str, ...]
    validation_status: str
    # Why the status is what it is, so "unvalidated" reads as "no statement
    # text to check against" rather than "we did not bother".
    validation_detail: str = ""
    restated: bool = False
    amendment_accession: Optional[str] = None
    override_evidence: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return True

    def definition_note(self) -> str:
        """The 6.5 ``definition_note`` line: what was measured, and when."""
        return (f"{self.metric_label} = {self.concept}, "
                f"fiscal year ended {self.end_date}")


Outcome = Union[Resolution, Abstain]


# ── statement-section matching, for validation_status (spec 6.4 rule 4) ─────
#
# v1's parser names sections by their heading text, not by the `fs_income_stmt`
# key the spec assumes, so the mapping is by title pattern. Recorded as a
# deviation in the phase report rather than renaming v1's sections, which would
# invalidate the Phase 1 baseline artifacts.
_STATEMENT_TITLE_PATTERNS = {
    "income": (
        "statements of operations", "statement of operations",
        "statements of income", "statement of income",
        "statements of earnings", "statement of earnings",
        "income statement",
    ),
    "balance": (
        "balance sheet", "balance sheets",
        "statements of financial condition", "statement of financial condition",
    ),
    "cash_flow": ("statements of cash flows", "statement of cash flows"),
}


# A grouped numeral (1,234) or any run of four or more digits. Both forms
# occur: some parsed tables keep thousands separators and some strip them,
# which is why AAPL FY2024 revenue matched "391035" and not "391,035".
_NUMERAL_TOKEN = re.compile(r"\d{1,3}(?:,\d{3})+|\d{4,}")
_WHITESPACE_RUN = re.compile("[\\s\u00a0]+")


def _rendered_numerals(value: Decimal, scale_raw: Optional[str]) -> List[str]:
    """How this value most likely appears as printed text in the statement.

    A fact tagged ``294,866`` with ``scale="6"`` is stored as 294866000000; the
    statement prints the pre-scale numeral with thousands separators. Both the
    grouped and ungrouped forms are produced, and the sign is dropped because
    statements print negatives in parentheses or with a leading dash
    inconsistently — the magnitude is what identifies the line.
    """
    try:
        scale = int(scale_raw) if scale_raw else 0
    except ValueError:
        scale = 0
    unscaled = abs(value).scaleb(-scale)
    out: List[str] = []
    normalised = unscaled.normalize()
    # `normalize()` can produce exponent notation for large integers.
    plain = format(normalised, "f")
    if plain.endswith(".0"):
        plain = plain[:-2]
    integer, _, fraction = plain.partition(".")
    try:
        grouped = f"{int(integer):,}"
    except ValueError:
        return [plain]
    for stem in (grouped, integer):
        out.append(f"{stem}.{fraction}" if fraction else stem)
    return list(dict.fromkeys(out))


def _flatten_table(raw_table) -> str:
    """v1 stores ``raw_table`` as a list of rows of cells, not a string.

    A first version of this joined it as text and raised TypeError on the first
    real statement, because a parsed table is ``list[list[str]]``.
    """
    if not raw_table:
        return ""
    if isinstance(raw_table, str):
        return raw_table
    rows: List[str] = []
    for row in raw_table:
        if isinstance(row, str):
            rows.append(row)
        else:
            rows.append(" ".join(str(cell) for cell in (row or [])))
    return "\n".join(rows)


class StatementText:
    """Rendered statement text per (ticker, fiscal label), read from data/parsed.

    Cached per instance: a coverage sweep resolves thousands of
    (filer, metric, year) triples and would otherwise re-read and re-join the
    same multi-megabyte JSON for every one of them.
    """

    def __init__(self, parsed_dir: Optional[Path] = None) -> None:
        self.parsed_dir = Path(parsed_dir or settings.parsed_dir)
        self._cache: Dict[Tuple[str, int, str], Optional[str]] = {}

    def text_for(self, ticker: str, fiscal_label: int, statement: str) -> Optional[str]:
        key = (ticker.upper(), int(fiscal_label), statement)
        if key in self._cache:
            return self._cache[key]
        self._cache[key] = self._load(*key)
        return self._cache[key]

    def _load(self, ticker: str, fiscal_label: int, statement: str) -> Optional[str]:
        path = self.parsed_dir / f"{ticker}_{fiscal_label}.json"
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        patterns = _STATEMENT_TITLE_PATTERNS.get(statement, ())
        chunks: List[str] = []
        for section in doc.get("sections") or []:
            title = (section.get("title") or "").lower()
            if not any(p in title for p in patterns):
                continue
            for block in section.get("content_blocks") or []:
                chunks.append(block.get("text") or "")
                chunks.append(_flatten_table(block.get("raw_table")))
        return "\n".join(c for c in chunks if c) or None


# `statements=None` must mean "disabled", not "use the default" — otherwise a
# test that passes None silently reads the machine's real data/parsed directory
# and its result depends on what happens to be on disk. A dedicated sentinel
# keeps the two meanings apart. (Found by the D23 guard test, which thought it
# had disabled validation and got `validated` back.)
_DEFAULT_STATEMENTS = object()


class FactsResolver:
    def __init__(
        self,
        *,
        catalog: Optional[CatalogStore] = None,
        store: Optional[FactsStore] = None,
        registry: Optional[Registry] = None,
        statements=_DEFAULT_STATEMENTS,
        today: Optional[date] = None,
    ) -> None:
        self.catalog = catalog or CatalogStore(Path(settings.catalog_path))
        self.store = store or FactsStore(Path(settings.facts_db_path))
        self.registry = registry or load_registry()
        self.statements = (StatementText() if statements is _DEFAULT_STATEMENTS
                           else statements)
        # Injectable clock: "is fiscal 2030 in the future" must not depend on
        # the day the test suite runs (the same requirement P3-02 states).
        self._today = today

    @property
    def today(self) -> date:
        # UTC rather than local: "has fiscal 2027 happened yet" must not change
        # answer with the machine's timezone. SEC period ends are calendar
        # dates, so a day either side is never the difference for a fiscal year.
        return self._today or datetime.now(timezone.utc).date()

    # ── public API ─────────────────────────────────────────────────────────

    def resolve(
        self,
        ticker: str,
        metric: str,
        period: Optional[PeriodSelector] = None,
        as_of: Optional[str] = None,
    ) -> Outcome:
        period = period or PeriodSelector.latest()
        ticker = ticker.upper()

        try:
            spec = self.registry.metric(metric)
        except KeyError:
            return Abstain(REASON_METRIC_NOT_SUPPORTED,
                           f"{metric!r} is not in the metric registry; known: "
                           f"{', '.join(self.registry.metric_names())}")

        filings = self.catalog.for_ticker(ticker)
        if not filings:
            # The catalog is the list of filers this project knows. Not knowing
            # the ticker and knowing it but having no filings are different
            # situations for the user, so they get different reasons.
            known = ticker in set(self.catalog.tickers())
            return Abstain(REASON_NO_FILING if known else REASON_COMPANY_NOT_FOUND,
                           f"no 10-K filings in the catalog for {ticker}")

        selected = self._select_period(ticker, filings, period, as_of)
        if isinstance(selected, Abstain):
            return selected
        period_end, eligible = selected

        # D14: newest filing first, so an amendment supersedes its original —
        # but fall through when it restates nothing.
        ordered = sorted(eligible, key=lambda f: (f.filing_date, f.accession),
                         reverse=True)
        newest_amendment = next(
            (f for f in ordered if f.is_amendment), None)

        last_abstain: Optional[Abstain] = None
        for filing in ordered:
            outcome = self._resolve_in_filing(filing, spec, metric)
            if isinstance(outcome, Abstain):
                last_abstain = outcome
                continue
            amendment = (newest_amendment.accession
                         if newest_amendment is not None
                         and newest_amendment.accession != filing.accession
                         else None)
            return Resolution(
                **{**outcome, "restated": filing.is_amendment,
                   "amendment_accession": amendment},
            )

        return last_abstain or Abstain(
            REASON_METRIC_NOT_IN_FILING,
            f"{ticker} has no {metric} fact for the year ending {period_end}")

    def available_labels(self, ticker: str, as_of: Optional[str] = None) -> List[int]:
        return self.catalog.available_fiscal_labels(ticker, as_of=as_of)

    # ── period selection (D2 happens here, before any fact is looked at) ───

    def _select_period(
        self,
        ticker: str,
        filings: Sequence[Filing],
        period: PeriodSelector,
        as_of: Optional[str],
    ) -> Union[Tuple[str, List[Filing]], Abstain]:
        if period.kind == "fiscal_label":
            matching = [f for f in filings if f.fiscal_label == int(period.value)]
            if not matching:
                return self._no_such_period(ticker, filings, period)
        elif period.kind == "period_end":
            matching = [f for f in filings if f.period_end == str(period.value)]
            if not matching:
                return self._no_such_period(ticker, filings, period)
        elif period.kind == "calendar_year":
            year = str(int(period.value))
            matching = [f for f in filings if f.period_end.startswith(year)]
            if not matching:
                return self._no_such_period(ticker, filings, period)
        else:  # latest
            matching = list(filings)

        eligible = [f for f in matching if f.eligible_at(as_of)]
        if not eligible:
            # The filings exist, but none was public on as_of. This is the
            # look-ahead case and it must be distinguishable from "we have no
            # such year" — G2 is the guarantee that we never answer from a
            # filing the asker could not have seen.
            earliest = min(f.filing_date for f in matching)
            return Abstain(
                REASON_PERIOD_NOT_FILED,
                f"{ticker} {period.describe()} was not public on {as_of}; the "
                f"earliest matching filing was filed {earliest}")

        # Narrow to ONE period end, whichever selector was used. `latest` needs
        # it by definition; a fiscal label or calendar year needs it because a
        # transition year can put two period ends under one label, and an
        # answer about two periods at once is not an answer.
        period_end = max(f.period_end for f in eligible)
        eligible = [f for f in eligible if f.period_end == period_end]

        # P4-11 C. The filings exist and were public, but none of them was
        # ever extracted — the facts build keeps the newest originals per
        # ticker while the catalog lists everything EDGAR has. Falling through
        # here reached the metric lookup and refused with
        # `metric_not_found_in_filing`, which asserts something about the
        # filer ("Apple did not report revenue in 2019") instead of about this
        # project's coverage. Decided before any fact is read, so no metric
        # can change the answer.
        if not any(self.store.has_facts(f.accession) for f in eligible):
            covered = sorted({f.fiscal_label for f in filings
                              if self.store.has_facts(f.accession)})
            return Abstain(
                REASON_PERIOD_NOT_COVERED,
                f"{ticker} {period.describe()} is catalogued but its facts were "
                f"never extracted; covered: "
                f"{', '.join(str(x) for x in covered) or 'none'}")
        return period_end, eligible

    def _no_such_period(self, ticker: str, filings: Sequence[Filing],
                        period: PeriodSelector) -> Abstain:
        known = sorted({f.fiscal_label for f in filings})
        if period.kind in ("fiscal_label", "calendar_year"):
            requested = int(period.value)
            # "Future" is relative to the calendar, not to our coverage: a year
            # we simply have not ingested is period_not_covered, while a year
            # that has not happened yet is future_period, and conflating them
            # makes the abstention message wrong in one direction or the other.
            if requested > self.today.year:
                return Abstain(
                    REASON_FUTURE_PERIOD,
                    f"{period.describe()} has not happened yet (today is "
                    f"{self.today.isoformat()})")
        return Abstain(
            REASON_PERIOD_NOT_COVERED,
            f"{ticker} has no filing for {period.describe()}; covered: "
            f"{', '.join(str(x) for x in known)}")

    # ── candidate selection (spec 6.4 rule 3) ──────────────────────────────

    def _resolve_in_filing(self, filing: Filing, spec: Metric,
                           metric: str) -> Union[Dict, Abstain]:
        facts = [f for f in self.store.query(accession=filing.accession)
                 if f.period_type == spec.period_type]
        by_concept: Dict[str, List[StoredFact]] = {}
        for fact in facts:
            by_concept.setdefault(fact.concept, []).append(fact)

        candidates = spec.candidates_for(self.registry.sector_for(filing.ticker))
        override = spec.override_for(filing.ticker, filing.fiscal_label)

        if override is not None:
            hits = by_concept.get(override.concept, [])
            if not hits:
                # An override is a deliberate statement about this filer, so a
                # filing that does not carry its concept is a contradiction
                # worth surfacing — not a cue to fall back to the general rule
                # and quietly answer with a different concept.
                return Abstain(
                    REASON_METRIC_NOT_IN_FILING,
                    f"the {filing.ticker} override for {metric} names "
                    f"{override.concept}, which {filing.accession} does not "
                    f"report; the override may be stale",
                    candidates=tuple(candidates))
            values = {f.value for f in hits}
            if len(values) > 1:
                return Abstain(
                    REASON_AMBIGUOUS,
                    f"{override.concept} has {len(values)} different values in "
                    f"{filing.accession}",
                    candidates=(override.concept,),
                    candidate_values={override.concept:
                                      ", ".join(sorted(_plain(v) for v in values))})
            return self._build(filing, spec, metric, hits[0],
                               selection="override",
                               candidates=candidates,
                               override_evidence=override.evidence)

        # Tier by tier (D2-02): a later tier is consulted only when every
        # concept in the earlier ones is absent, so a definitional preference
        # never competes with the concept it is a fallback for.
        tiers = spec.tiers_for(self.registry.sector_for(filing.ticker))
        for index, tier in enumerate(tiers):
            present = {c: by_concept[c] for c in tier if c in by_concept}
            if not present:
                continue
            outcome = self._choose_within_tier(
                filing, spec, metric, present, candidates,
                tier_index=index, tier_count=len(tiers))
            if not isinstance(outcome, Abstain):
                return outcome
            # An ambiguity inside a tier is final: falling through to the next
            # tier would answer with a fallback concept to dodge a real tie,
            # which is exactly the silent pick rule 3 forbids.
            return outcome

        # Every tier was empty: the filing reports none of the candidates.
        return Abstain(
            REASON_METRIC_NOT_IN_FILING,
            f"{filing.accession} reports none of the {metric} candidates",
            candidates=tuple(candidates))

    def _choose_within_tier(self, filing: Filing, spec: Metric, metric: str,
                            present: Dict[str, List[StoredFact]],
                            candidates: Sequence[str], *,
                            tier_index: int,
                            tier_count: int) -> Union[Dict, Abstain]:
        """Spec 6.4 rule 3, applied inside one tier."""
        values = {f.value for hits in present.values() for f in hits}

        if len(values) == 1:
            # Either one concept, or several that AGREE. Agreement is not a
            # tie: JPMorgan tags us-gaap:Revenues and
            # us-gaap:RevenuesNetOfInterestExpense with the identical figure
            # every year (FY2024: 177,556M), and abstaining there would refuse
            # a number the filing states twice. The coverage sweep found 27
            # such rows.
            concept = sorted(present)[0]
            selection = ("single_candidate" if len(present) == 1
                         else "candidates_agree")
            if tier_count > 1 and tier_index > 0:
                selection += f"_tier{tier_index + 1}"
            return self._build(filing, spec, metric, present[concept][0],
                               selection=selection, candidates=candidates)

        if len(present) > 1:
            return Abstain(
                REASON_AMBIGUOUS,
                f"{filing.ticker} {filing.fiscal_label} reports {len(present)} "
                f"different {metric} concepts with different values; an "
                f"override with evidence is needed to choose between them",
                candidates=tuple(present),
                candidate_values={
                    c: ", ".join(sorted(_plain(f.value) for f in hits))
                    for c, hits in present.items()
                })

        concept = next(iter(present))
        return Abstain(
            REASON_AMBIGUOUS,
            f"{concept} has {len(values)} different values in "
            f"{filing.accession}",
            candidates=(concept,),
            candidate_values={concept:
                              ", ".join(sorted(_plain(v) for v in values))})

    def _build(self, filing: Filing, spec: Metric, metric: str,
               fact: StoredFact, *, selection: str,
               candidates: Sequence[str],
               override_evidence: Optional[str] = None) -> Dict:
        return {
            "ticker": filing.ticker,
            "metric": metric,
            "metric_label": spec.label,
            "concept": fact.concept,
            "value": fact.value,
            "unit": fact.unit,
            "period_type": fact.period_type,
            "start_date": fact.start_date,
            "end_date": fact.end_date,
            "fiscal_label": filing.fiscal_label,
            "accession": filing.accession,
            "filing_date": filing.filing_date,
            "form_type": filing.form_type,
            "source_doc": fact.source_doc,
            "selection": selection,
            "candidates_considered": tuple(candidates),
            "override_evidence": override_evidence,
            **dict(zip(("validation_status", "validation_detail"),
                       self._validation_status(filing, spec, fact), strict=True)),
        }

    # ── validation against the rendered statement (spec 6.4 rule 4) ────────

    def _validation_status(self, filing: Filing, spec: Metric,
                           fact: StoredFact) -> Tuple[str, str]:
        """Does the chosen value appear on the rendered statement? (6.4 rule 4)

        Returns (status, detail). Three outcomes, and the distinction between
        the last two is the whole point:

        * ``validated``   - the value is printed on the statement.
        * ``unvalidated`` - there is no usable statement text to check against.
          Says nothing about the fact either way.
        * ``conflict``    - there IS a statement, with plenty of numbers in it,
          and this value is not among them. Worth a human looking.
        """
        if self.statements is None:
            return UNVALIDATED, "no statement source configured"
        text = self.statements.text_for(filing.ticker, filing.fiscal_label,
                                        spec.statement)
        if not text:
            return UNVALIDATED, (
                f"no parsed {spec.statement} statement for {filing.ticker} "
                f"FY{filing.fiscal_label}")
        # "\u00a0" written as an escape: a literal non-breaking space
        # crept into this character class once and was invisible in
        # every diff it survived.
        flat = re.sub(_WHITESPACE_RUN, " ", text)
        renderings = _rendered_numerals(fact.value, fact.scale_raw)
        for rendering in renderings:
            if rendering in flat:
                return VALIDATED, f"found {rendering!r} on the statement"

        numerals = len(_NUMERAL_TOKEN.findall(flat))
        if numerals < MIN_STATEMENT_NUMERALS:
            return UNVALIDATED, (
                f"the parsed {spec.statement} section holds only {numerals} "
                f"numeric token(s) in {len(flat)} chars, so it is a heading or "
                f"a table of contents rather than the statement")
        return CONFLICT, (
            f"not found among {numerals} numeric tokens in the parsed "
            f"{spec.statement} statement; tried {renderings}")


def parse_as_of(value: Optional[str]) -> Optional[str]:
    """Validate an ``as_of`` date, so a malformed one cannot disable the filter.

    A silent ``None`` here would turn a look-ahead guard into no guard at all,
    which is the worst possible failure for G2.
    """
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"as_of must be an ISO date (YYYY-MM-DD), got "
                         f"{value!r}") from exc
