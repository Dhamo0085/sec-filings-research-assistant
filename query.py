"""The pipeline: a question in, an Outcome out (spec section 5, P3-03/04/05/07).

    python query.py "What was Apple's revenue in FY2024?"
    python query.py --as-of 2024-03-01 "What was Apple's latest annual revenue?"

    from query import ask
    outcome = ask("What was Apple's revenue in fiscal 2024?")

Shape of the thing
------------------
``ask`` is a dispatcher and nothing else. Every decision lives in a module
that can be tested on its own, and every exit is an ``Outcome`` built by
``answering/``:

    route()                 which path, which metric, which period, which filer
    facts path              resolve -> calculate -> deterministic template
    text path               catalog-eligible collections -> retrieve -> generate
    abstention gate         condition -> reason -> message
    typed LLM errors        -> status=error, HTTP 503

The one rule that shapes the code more than any other is **G4: failures are
visible**. v1 wrapped the whole pipeline in ``except Exception`` and returned
the clarification text, so an outage and a vague question were the same
response (Phase 0 defect F1). Here the only ``except`` clauses map a named
failure onto a named ``error_code``; nothing turns an exception into an
answer, and nothing turns it into a question back to the user.

Dependencies are injected (``llm=``, ``catalog=``, ``resolver=``,
``retriever=``) so the offline end-to-end test (T3-08) runs the real
dispatcher over fixture stores and a fake model, rather than testing a
different code path from the one that ships.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from answering.abstain import abstain_for, clarification_no_company, error_for, reason_from_facts
from answering.facts_answer import (
    compare_answer,
    computed_answer,
    numeric_answer,
    trend_answer,
)
from answering.outcome import AbstainReason, ErrorCode, Outcome, QueryType
from answering.text_answer import answer_from_text, eligible_collections
from facts import calc as calc_module
from facts.calc import CalculationError, Operand
from facts.errors import FactsError
from facts.resolve import Abstain, PeriodSelector, Resolution
from llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMBudgetExceeded,
    LLMError,
    LLMRateLimited,
    LLMUnavailable,
)
from routing.periods import PeriodKind, PeriodRequest
from routing.router import Computation, Path, Route, route

#: Exception type -> 6.5 error_code. Ordered most specific first because
#: LLMBudgetExceeded subclasses LLMRateLimited.
ERROR_CODES: Tuple[Tuple[type, ErrorCode], ...] = (
    (LLMAuthError, ErrorCode.LLM_AUTH),
    (LLMBudgetExceeded, ErrorCode.LLM_RATE_LIMITED),
    (LLMRateLimited, ErrorCode.LLM_RATE_LIMITED),
    (LLMBadOutput, ErrorCode.LLM_BAD_OUTPUT),
    (LLMUnavailable, ErrorCode.LLM_UNAVAILABLE),
    (FactsError, ErrorCode.DATA_UNAVAILABLE),
)


def error_code_for(exc: BaseException) -> ErrorCode:
    for exc_type, code in ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    return ErrorCode.INTERNAL


@dataclass
class Deps:
    """Everything the pipeline talks to, injectable for tests and ablations.

    The defaults are lazy: constructing ``Deps()`` must not open a database or
    load an ONNX model, because ``api/app.py`` imports this module at startup
    and ``make test`` runs with no ``.env`` (spec P1-03, T1-05).
    """

    llm: Any = None
    catalog: Any = None
    resolver: Any = None
    retriever: Optional[Callable[..., List[Any]]] = None
    registry: Any = None

    def get_llm(self):
        if self.llm is None:
            from llm import get_client
            self.llm = get_client()
        return self.llm

    def get_catalog(self):
        if self.catalog is None:
            from catalog.store import CatalogStore
            from config import settings
            self.catalog = CatalogStore(settings.catalog_path)
        return self.catalog

    def get_resolver(self):
        if self.resolver is None:
            from facts.resolve import FactsResolver
            self.resolver = FactsResolver()
        return self.resolver

    def get_retriever(self):
        if self.retriever is None:
            from retrieval.retriever import retrieve
            self.retriever = retrieve
        return self.retriever


def _selector_for(period: PeriodRequest, label: Optional[int] = None) -> PeriodSelector:
    """A routing period request as the resolver's selector (P2-07)."""
    if label is not None:
        return PeriodSelector.fiscal_label(label)
    if period.kind is PeriodKind.PERIOD_END and period.period_end:
        return PeriodSelector.period_end(period.period_end)
    if period.kind is PeriodKind.CALENDAR_YEAR and period.labels:
        return PeriodSelector.calendar_year(period.labels[0])
    if period.kind is PeriodKind.FISCAL_LABEL and period.labels:
        return PeriodSelector.fiscal_label(period.labels[0])
    return PeriodSelector.latest()


def _selectors_for(period: PeriodRequest) -> List[PeriodSelector]:
    """One selector per period the question asks about, oldest first."""
    if period.kind is PeriodKind.FISCAL_LABEL and len(period.labels) > 1:
        return [PeriodSelector.fiscal_label(y) for y in period.labels]
    return [_selector_for(period)]


def _company_name(route_: Route, ticker: str, catalog=None) -> str:
    """The name to show the user for a ticker.

    The resolver's own name first, then the catalog's registered entity name,
    then the bare ticker. The catalog step matters for filers that are
    reachable by alias but are not in ``config.COMPANIES`` — Netflix arrived
    through on-demand ingestion, so without it an answer reads "NFLX reported
    …" while the Apple half of the same comparison reads "Apple Inc.".
    """
    name = route_.entities.names.get(ticker)
    if name and name != ticker:
        return name
    if catalog is not None:
        try:
            filings = catalog.for_ticker(ticker)
        except Exception as exc:
            logger.debug(f"could not read an entity name for {ticker}: {exc}")
            filings = []
        if filings and filings[0].entity_name:
            return filings[0].entity_name
    return ticker


def _cik_for(catalog, ticker: str) -> Optional[int]:
    try:
        filings = catalog.for_ticker(ticker)
    except Exception:
        return None
    return filings[0].cik if filings else None


def _abstain_from_facts(
    refusal: Abstain, route_: Route, ticker: str, *, question: str,
    catalog, period_label: str,
) -> Outcome:
    """Turn the resolver's typed refusal into the user-facing one (G3)."""
    reason = reason_from_facts(refusal.reason)
    # The years this system can answer, not every year on EDGAR — see
    # _available_for.
    available: Sequence[int] = _available_for(catalog, [ticker], route_.as_of,
                                              kind="facts")

    candidates = ""
    if refusal.candidate_values:
        candidates = ", ".join(f"{k} = {v}" for k, v in sorted(refusal.candidate_values.items()))
    elif refusal.candidates:
        candidates = ", ".join(refusal.candidates)

    filing_date = ""
    if reason is AbstainReason.PERIOD_NOT_FILED_AS_OF:
        filing_date = _first_filing_date(catalog, ticker, route_.period)

    return abstain_for(
        reason,
        query_type=route_.intent,
        query=question,
        as_of=route_.as_of,
        company=_company_name(route_, ticker, catalog),
        ticker=ticker,
        period=period_label,
        period_phrase=route_.period.describe(),
        metric=route_.metric or "that measure",
        candidates=candidates,
        available=available,
        filing_date=filing_date,
        trace=route_.trace(),
    )


def _first_filing_date(catalog, ticker: str, period: PeriodRequest) -> str:
    """When the filing the asker wanted actually became public.

    A ``period_not_filed_as_of`` message is only useful if it says when the
    information did become available, so this looks the filing up *without*
    the as_of filter — deliberately, and only to name the date.
    """
    try:
        wanted = set(period.labels)
        for filing in catalog.for_ticker(ticker):
            if not wanted or filing.fiscal_label in wanted:
                return filing.filing_date
    except Exception:
        pass
    return ""


# ── the facts path ────────────────────────────────────────────────────────────

def _resolve_one(deps: Deps, route_: Route, ticker: str,
                 selector: PeriodSelector, metric: str):
    return deps.get_resolver().resolve(ticker, metric, selector, as_of=route_.as_of)


def _answer_from_facts(deps: Deps, route_: Route, question: str) -> Outcome:
    catalog = deps.get_catalog()
    tickers = list(route_.tickers)
    metric = route_.metric or "revenue"
    selectors = _selectors_for(route_.period)

    # Resolve everything first. A partial result is still an answer, but only
    # if the gap is stated (v1 answered "compare Apple and SpaceX" about Apple
    # alone with no indication); a total failure is an abstention with the
    # first filer's reason, which is the specific one worth showing.
    resolved: List[Resolution] = []
    first_refusal: Optional[Tuple[Abstain, str, str]] = None

    for ticker in tickers:
        for selector in selectors:
            outcome = _resolve_one(deps, route_, ticker, selector, metric)
            if isinstance(outcome, Resolution):
                resolved.append(outcome)
            elif first_refusal is None:
                first_refusal = (outcome, ticker, selector.describe())

    if not resolved:
        refusal, ticker, period_label = first_refusal or (
            Abstain(AbstainReason.METRIC_NOT_FOUND_IN_FILING.value), tickers[0],
            route_.period.describe(),
        )
        return _abstain_from_facts(refusal, route_, ticker, question=question,
                                   catalog=catalog, period_label=period_label)

    cik = _cik_for(catalog, resolved[0].ticker)
    companies = {t: _company_name(route_, t, catalog) for t in tickers}
    ciks = {t: _cik_for(catalog, t) for t in tickers}

    if route_.computation is not None:
        computed = _compute(deps, route_, resolved, question, companies, ciks)
        if computed is not None:
            return computed
        # The calculation could not be made (a unit or period mismatch, a zero
        # or negative base). The figures themselves are still sound, so they
        # are reported rather than withheld — the guard refused the arithmetic,
        # not the facts.

    if route_.intent is QueryType.COMPARE and len(resolved) > 1:
        return compare_answer(resolved, question=question, companies=companies,
                              ciks=ciks, as_of=route_.as_of)
    if len(resolved) > 1:
        return trend_answer(resolved, question=question,
                            company=companies.get(resolved[0].ticker),
                            cik=cik, as_of=route_.as_of)
    return numeric_answer(resolved[0], question=question,
                          company=companies.get(resolved[0].ticker), cik=cik,
                          as_of=route_.as_of)


def _compute(
    deps: Deps, route_: Route, resolved: List[Resolution], question: str,
    companies: Dict[str, str], ciks: Dict[str, Optional[int]],
) -> Optional[Outcome]:
    """Run the requested calculation, or return None to fall back to the facts.

    Returns ``None`` rather than raising when the calculator's guards refuse
    (a zero base, a unit mismatch): those guards exist so a meaningless number
    is not produced, and the right response is to report the underlying
    figures, not to fail the whole question.
    """
    computation = route_.computation
    try:
        if computation is Computation.MARGIN_PCT and route_.metric_denominator:
            pair = _margin_operands(deps, route_, resolved)
            if pair is None:
                return None
            (numerator_op, numerator_res), (denominator_op, denominator_res) = pair
            calculation = calc_module.margin_pct(numerator_op, denominator_op)
            return computed_answer(
                calculation, [numerator_res, denominator_res],
                question=question, company=companies.get(resolved[0].ticker),
                cik=ciks.get(resolved[0].ticker), as_of=route_.as_of,
            )

        ordered = sorted(resolved, key=lambda r: (r.end_date or "", r.fiscal_label))
        if len(ordered) < 2:
            return None
        earlier, later = ordered[0], ordered[-1]
        operands = (Operand.from_resolution(later), Operand.from_resolution(earlier))

        if computation is Computation.CAGR_PCT:
            years = later.fiscal_label - earlier.fiscal_label
            if years < 1:
                return None
            calculation = calc_module.cagr_pct(*operands, years=years)
        elif computation is Computation.DIFFERENCE:
            calculation = calc_module.difference(*operands)
        elif computation is Computation.TOTAL:
            calculation = calc_module.total([Operand.from_resolution(r) for r in ordered])
        elif computation is Computation.RATIO:
            calculation = calc_module.ratio(*operands)
        else:
            calculation = calc_module.growth_pct(*operands)

        used = ordered if computation is Computation.TOTAL else [earlier, later]
        return computed_answer(
            calculation, used, question=question,
            company=companies.get(resolved[0].ticker),
            cik=ciks.get(resolved[0].ticker), as_of=route_.as_of,
        )
    except CalculationError as exc:
        logger.info(f"calculation refused ({exc}); reporting the underlying figures")
        return None


def _margin_operands(deps: Deps, route_: Route, resolved: List[Resolution]):
    """((numerator_operand, fact), (denominator_operand, fact)) or None.

    The denominator is resolved for the *same* fiscal year as the numerator,
    never for whatever the period request happened to select: an operating
    margin computed from one year's income and another year's revenue would
    be a plausible-looking number with no meaning.
    """
    numerator_res = resolved[0]
    selector = _selector_for(route_.period, numerator_res.fiscal_label)
    denominator_res = _resolve_one(deps, route_, numerator_res.ticker, selector,
                                   route_.metric_denominator)
    if not isinstance(denominator_res, Resolution):
        return None
    return ((Operand.from_resolution(numerator_res), numerator_res),
            (Operand.from_resolution(denominator_res), denominator_res))


# ── the text path ─────────────────────────────────────────────────────────────

def _collection_of(chunk) -> str:
    """Which collection a retrieved chunk came from.

    v1 names collections ``{TICKER}_{fiscal_year}`` and the chunk carries
    both, so this reconstructs the name rather than requiring a payload field
    that older indexed collections do not have.
    """
    return f"{chunk.ticker}_{chunk.fiscal_year}"


def _answer_from_text(deps: Deps, route_: Route, question: str,
                      *, history: str = "") -> Outcome:
    catalog = deps.get_catalog()
    labels = list(route_.period.labels) or None
    collections, by_collection = eligible_collections(
        catalog, route_.tickers, fiscal_labels=labels, as_of=route_.as_of,
    )

    if not collections:
        indexed = _available_for(catalog, route_.tickers, route_.as_of, kind="text")
        # "I don't have {period} for {company}" has to read as English when no
        # period was named at all, which is the normal case for a narrative
        # question — and the hint must offer *indexed* years, not years we
        # merely have facts for.
        period = (route_.period.describe() if route_.period.labels or
                  route_.period.period_end
                  else ("any indexed filing text" if not indexed
                        else "filing text for that period"))
        return abstain_for(
            AbstainReason.PERIOD_NOT_COVERED,
            query_type=QueryType.NARRATIVE, query=question, as_of=route_.as_of,
            company=(_company_name(route_, route_.tickers[0], catalog)
                     if route_.tickers else None),
            period=period, available=indexed,
            trace=route_.trace(),
        )

    retrieved = deps.get_retriever()(
        query=question,
        tickers=list(route_.tickers),
        years=labels or [],
        focus=route_.focus,
        collections=collections,
    )
    scope = ", ".join(sorted(collections))
    return answer_from_text(
        question, retrieved=retrieved, by_collection=by_collection,
        collection_of=_collection_of, llm=deps.get_llm(), as_of=route_.as_of,
        scope_description=scope, trace=route_.trace(), history=history,
    )


def _available_for(catalog, tickers: Sequence[str], as_of: Optional[str],
                   *, kind: str = "any") -> List[int]:
    """Years this system can actually answer about, for the refusal's hint.

    ``kind`` picks which capability is being offered: ``"facts"`` for years
    with a built facts store, ``"text"`` for years with an indexed text
    collection, ``"any"`` for either. The distinction is not cosmetic — Wells
    Fargo has facts for five years and no text index at all, so offering
    "fiscal 2021 to 2025" under a refusal of a *narrative* question sends the
    reader back to ask four more questions that will each refuse.

    NOT every year in the catalog: the catalog holds a filer's whole EDGAR
    history (483 rows for the bundled set, back to the 1990s), while only the
    filings that have facts built or text indexed can be answered. Offering
    the rest produced "I do have fiscal 1994, 1995, 1996 ..." under a refusal
    — a list of years that would each refuse in turn, which is worse than no
    hint at all.
    """
    years: set = set()
    for ticker in tickers:
        try:
            filings = catalog.for_ticker(ticker, as_of=as_of)
        except Exception as exc:
            # The hint is a courtesy; a catalog hiccup drops it, not the refusal.
            logger.debug(f"could not list filings for {ticker}: {exc}")
            continue
        for f in filings:
            has = {"facts": bool(f.facts_built_at),
                   "text": bool(f.collection_name),
                   "any": bool(f.facts_built_at or f.collection_name)}[kind]
            if has:
                years.add(f.fiscal_label)
    return sorted(years)


# ── the dispatcher ────────────────────────────────────────────────────────────

def ask(
    question: str,
    *,
    as_of: Optional[str] = None,
    tickers: Optional[Sequence[str]] = None,
    years: Optional[Sequence[int]] = None,
    history: str = "",
    deps: Optional[Deps] = None,
) -> Outcome:
    """Answer ``question``, or say exactly why not. Never raises.

    ``tickers`` and ``years`` are the UI's filter chips: an explicit choice by
    the user, so they override what the router read out of the sentence. They
    are applied after routing rather than before, so the intent, metric and
    focus are still decided from the question itself.

    ``history`` is earlier conversation. It is deliberately **not** part of
    what gets routed. v1's chat endpoint prepended prior turns to the question
    so its LLM classifier could resolve "compare to last year", and with a
    rules-first router that is actively harmful: a previous answer mentioning
    "fiscal 2024 (year ended 2024-09-28)" injects three years into the period
    parse and turns a plain figure request into a trend. Observed in the UI
    walkthrough — the same question asked twice came back as a trend over
    every year the earlier answer named. The history reaches the text
    generator as context and nothing else.
    """
    deps = deps or Deps()
    try:
        return _ask_inner(question, as_of=as_of, deps=deps,
                          tickers=tickers, years=years, history=history)
    except LLMError as exc:
        code = error_code_for(exc)
        logger.error(f"dependency failure [{code.value}]: {type(exc).__name__}: {exc}")
        return error_for(code, query=question, as_of=as_of)
    except FactsError as exc:
        logger.error(f"facts store failure: {type(exc).__name__}: {exc}")
        return error_for(ErrorCode.DATA_UNAVAILABLE, query=question, as_of=as_of)
    except Exception as exc:
        # The catch-all stays, but it produces status=error, never an answer
        # and never a clarification. That distinction is defect F1.
        logger.exception(f"internal failure: {type(exc).__name__}: {exc}")
        return error_for(ErrorCode.INTERNAL, query=question, as_of=as_of)


def _ask_inner(
    question: str, *, as_of: Optional[str], deps: Deps,
    tickers: Optional[Sequence[str]] = None,
    years: Optional[Sequence[int]] = None,
    history: str = "",
) -> Outcome:
    catalog_tickers: Sequence[str] = ()
    try:
        catalog_tickers = deps.get_catalog().tickers()
    except Exception as exc:
        logger.warning(f"catalog unavailable for ticker resolution: {exc}")

    route_ = route(
        question,
        registry=deps.registry,
        llm=deps.get_llm(),
        catalog_tickers=catalog_tickers,
        today=None,
        as_of_override=as_of,
    )
    route_ = _apply_filters(route_, tickers, years)
    logger.info(f"route: {route_.trace()}")

    if route_.path is Path.CLARIFY:
        return clarification_no_company(query=question, as_of=route_.as_of,
                                        trace=route_.trace())
    if route_.path is Path.ABSTAIN:
        ticker = route_.tickers[0] if route_.tickers else ""
        catalog = deps.get_catalog()
        return abstain_for(
            route_.abstain_reason or AbstainReason.OUT_OF_SCOPE,
            query_type=route_.intent, query=question, as_of=route_.as_of,
            company=(_company_name(route_, ticker, catalog) if ticker
                     else (route_.entities.unresolved[0]
                           if route_.entities.unresolved else None)),
            ticker=ticker,
            period=route_.period.describe(),
            period_phrase=_sub_annual_phrase(route_),
            metric=route_.metric or _unknown_metric_phrase(question),
            supported=_supported_metrics(deps),
            available=(_available_for(catalog, route_.tickers, route_.as_of)
                       if route_.tickers else ()),
            trace=route_.trace(),
        )
    if route_.path is Path.FACTS:
        return _answer_from_facts(deps, route_, question)
    return _answer_from_text(deps, route_, question, history=history)


def _apply_filters(
    route_: Route,
    tickers: Optional[Sequence[str]],
    years: Optional[Sequence[int]],
) -> Route:
    """Override the routed filers and years with the caller's explicit choice.

    A filter cannot rescue a question the router refused: an out-of-scope
    question or a quarterly period stays refused, because narrowing the
    companies does not make "should I buy this" answerable.
    """
    if not tickers and not years:
        return route_
    if route_.path in (Path.ABSTAIN, Path.CLARIFY) and route_.abstain_reason is not None:
        return route_

    import dataclasses

    changes: Dict[str, Any] = {}
    rationale = list(route_.rationale)
    if tickers:
        upper = tuple(str(t).upper() for t in tickers)
        changes["entities"] = dataclasses.replace(
            route_.entities, tickers=upper,
            names={t: route_.entities.names.get(t, t) for t in upper},
        )
        rationale.append(f"tickers overridden by the caller: {', '.join(upper)}")
    if years:
        labels = tuple(sorted({int(y) for y in years}))
        changes["period"] = PeriodRequest(
            kind=PeriodKind.FISCAL_LABEL, labels=labels,
            as_of=route_.period.as_of, bare=True,
            matched=route_.period.matched,
        )
        rationale.append(f"years overridden by the caller: "
                         f"{', '.join(str(y) for y in labels)}")
    changes["rationale"] = tuple(rationale)

    updated = dataclasses.replace(route_, **changes)
    # With a filer now named, a clarification is no longer the right outcome.
    if updated.path is Path.CLARIFY and updated.tickers:
        updated = dataclasses.replace(updated, path=Path.TEXT,
                                      intent=QueryType.NARRATIVE)
    return updated


def _sub_annual_phrase(route_: Route) -> str:
    matched = route_.period.matched
    return f"“{matched[0]}”" if matched else "A quarterly or interim period"


def _unknown_metric_phrase(_question: str) -> str:
    return "the measure you asked for"


def _supported_metrics(deps: Deps) -> str:
    try:
        from facts.concepts import load_registry
        names = (deps.registry or load_registry()).metric_names()
    except Exception:
        return "the standard annual-report line items"
    readable = [n.replace("_", " ") for n in names]
    return ", ".join(readable[:-1]) + f" and {readable[-1]}" if len(readable) > 1 else readable[0]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print(outcome: Outcome) -> None:
    print("\n" + "=" * 68)
    print(f"QUERY : {outcome.query}")
    print(f"STATUS: {outcome.status.value}"
          + (f" ({outcome.abstain_reason.value})" if outcome.abstain_reason else "")
          + (f" ({outcome.error_code.value})" if outcome.error_code else ""))
    if outcome.as_of:
        print(f"AS OF : {outcome.as_of}")
    print("=" * 68)
    print(f"\n{outcome.answer}\n")
    if outcome.definition_note:
        print(f"Definition used: {outcome.definition_note}\n")
    if outcome.citations:
        print("-" * 68)
        print("SOURCES:")
        for c in outcome.citations:
            where = c.section or f"{c.metric} ({c.concept})"
            print(f"  [{c.index}] {c.company or c.ticker} FY{c.fiscal_label} — {where}")
            print(f"       {c.accession}, filed {c.filing_date}"
                  + ("  [restated]" if c.restated else ""))
    print("=" * 68 + "\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Ask a question about SEC 10-K filings")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--as-of", default=None,
                        help="ISO date; only filings public on or before it are used")
    args = parser.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level="INFO", colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")

    outcome = ask(" ".join(args.question), as_of=args.as_of)
    _print(outcome)
    return 0 if outcome.status.value != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
