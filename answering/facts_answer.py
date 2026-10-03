"""Deterministic answer templates for the facts path (P3-04, D8).

    from answering.facts_answer import numeric_answer
    numeric_answer(resolution, question="What was Apple's revenue in FY2024?")
    # Outcome(status=answered, answer="Apple's total net revenue for fiscal
    #         2024 (year ended 2024-09-28) was $391.04 billion ...", ...)

No model writes these sentences (D8: the paraphrase is optional and off by
default). The reason is not style, it is that a template cannot do the thing
the model does wrong: it cannot restate a figure, cannot drop a scale, cannot
reword "total net revenue" into "revenue" and lose the definition, and cannot
produce text that disagrees with the citation beside it. Every number in the
output is formatted from the ``Decimal`` the resolver returned, by
``facts/format.py``, whose scaling is driven by the value alone (K2).

What every answer says, and why
--------------------------------
* **The period end, always.** D6: the fiscal label is the filer's own, so
  "fiscal 2024" is not self-explanatory — Apple's ended 2024-09-28 and
  Microsoft's 2024-06-30. The date is what makes the label checkable.
* **The definition used.** D5: "revenue" is a choice among concepts the filer
  tagged, so the concept and the label go in the ``definition_note`` and the
  answer names the measure in words.
* **The exact figure next to the readable one.** "$391.04 billion
  ($391,035,000,000)" — the first is for the reader, the second is what they
  will see in the filing.
* **The restatement, when there is one.** D14: a value from an amendment is
  flagged in the sentence, not only in the citation, because the citation
  chip is easy to miss and "this figure was restated" changes what the number
  means.
* **The work, for a computed answer.** The calculator's formula string is
  printed, so "revenue grew 11.0%" is checkable rather than asserted.
"""

from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from answering.outcome import Citation, CitationKind, Outcome, QueryType
from facts.calc import Calculation
from facts.format import format_exact, format_percent, format_value, format_with_exact
from facts.resolve import Resolution


def citation_from(resolution: Resolution, index: int, *,
                  company: Optional[str] = None,
                  cik: Optional[int] = None) -> Citation:
    """The 6.5 fact citation for one resolved fact."""
    return Citation(
        kind=CitationKind.FACT,
        index=index,
        ticker=resolution.ticker,
        company=company,
        metric=resolution.metric,
        concept=resolution.concept,
        period_end=resolution.end_date,
        fiscal_label=resolution.fiscal_label,
        # A decimal STRING, never a float: 391035000000 does not survive a
        # round trip through a double with cents intact (spec 6.2).
        value=format(resolution.value, "f"),
        unit=resolution.unit,
        accession=resolution.accession,
        filing_date=resolution.filing_date,
        restated=resolution.restated,
        cik=cik,
    )


def _period_phrase(resolution: Resolution) -> str:
    """"fiscal 2024 (year ended 2024-09-28)" — the label and what it means."""
    return f"fiscal {resolution.fiscal_label} (year ended {resolution.end_date})"


def _subject(resolution: Resolution, company: Optional[str]) -> str:
    return company or resolution.ticker


def _restatement_note(resolutions: Sequence[Resolution]) -> str:
    """One sentence covering whichever figures came from an amendment (D14)."""
    restated = [r for r in resolutions if r.restated]
    if not restated:
        return ""
    if len(restated) == 1:
        r = restated[0]
        return (f" This figure is restated: it comes from {r.form_type} "
                f"{r.accession}, filed {r.filing_date}, which superseded the "
                f"original filing for fiscal {r.fiscal_label}.")
    years = ", ".join(f"fiscal {r.fiscal_label}" for r in restated)
    return (f" The figures for {years} are restated: they come from amended "
            f"filings that superseded the originals.")


def _amendment_note(resolutions: Sequence[Resolution]) -> str:
    """An amendment exists but did not change this figure.

    Worth saying: the GS FY2023 10-K/A carries 64 facts and no financial ones,
    so "there is an amendment and it did not touch this" is a different fact
    about the world from "there is no amendment", and a reader checking the
    filing list will see the amendment either way.
    """
    noted = [r for r in resolutions if r.amendment_accession and not r.restated]
    if not noted:
        return ""
    r = noted[0]
    return (f" An amendment to the fiscal {r.fiscal_label} filing exists "
            f"({r.amendment_accession}) but does not restate this figure.")


def _definition_note(resolutions: Sequence[Resolution]) -> str:
    """D5: name the definition used, once per distinct measure."""
    seen: List[str] = []
    for r in resolutions:
        note = r.definition_note()
        if note not in seen:
            seen.append(note)
    return "; ".join(seen)


def _as_of_note(as_of: Optional[str]) -> str:
    if not as_of:
        return ""
    return (f" Only filings public on or before {as_of} were used.")


# ── numeric_fact ──────────────────────────────────────────────────────────────

def numeric_answer(
    resolution: Resolution,
    *,
    question: str = "",
    company: Optional[str] = None,
    cik: Optional[int] = None,
    as_of: Optional[str] = None,
) -> Outcome:
    """One company, one metric, one period."""
    subject = _subject(resolution, company)
    figure = format_with_exact(resolution.value, resolution.unit)
    text = (
        f"{subject}'s {resolution.metric_label.lower()} for "
        f"{_period_phrase(resolution)} was {figure}. [1]"
        f"{_restatement_note([resolution])}{_amendment_note([resolution])}"
        f"{_as_of_note(as_of)}"
    )
    return Outcome.answered(
        text,
        query_type=QueryType.NUMERIC_FACT,
        citations=[citation_from(resolution, 1, company=company, cik=cik)],
        definition_note=_definition_note([resolution]),
        query=question,
        as_of=as_of,
    )


# ── computed ──────────────────────────────────────────────────────────────────

def computed_answer(
    calculation: Calculation,
    resolutions: Sequence[Resolution],
    *,
    question: str = "",
    company: Optional[str] = None,
    cik: Optional[int] = None,
    as_of: Optional[str] = None,
    description: Optional[str] = None,
    query_type: QueryType = QueryType.COMPUTED,
) -> Outcome:
    """A calculator result, with its inputs cited and its formula shown.

    ``query_type`` is the *question's* classification, not the template's:
    "how did revenue grow from 2023 to 2024" is a trend question that happens
    to be answered with a calculation, and 6.5's ``query_type`` describes the
    query. The smoke run caught the two disagreeing — the router said trend
    and the response said computed.
    """
    resolutions = list(resolutions)
    subject = _subject(resolutions[0], company)
    label = description or _calculation_label(calculation, resolutions)
    value = format_value(calculation.value, calculation.unit)

    markers = " ".join(f"[{i}]" for i in range(1, len(resolutions) + 1))
    inputs = "; ".join(
        f"{_period_phrase(r)} {r.metric_label.lower()} "
        f"{format_with_exact(r.value, r.unit)}"
        for r in resolutions
    )
    text = (
        f"{subject}'s {label} was {value}. "
        f"Computed as {calculation.formula} from {inputs}. {markers}"
        f"{_restatement_note(resolutions)}{_as_of_note(as_of)}"
    )
    return Outcome.answered(
        text,
        query_type=query_type,
        citations=[
            citation_from(r, i, company=company, cik=cik)
            for i, r in enumerate(resolutions, start=1)
        ],
        definition_note=_definition_note(resolutions),
        query=question,
        as_of=as_of,
    )


def _calculation_label(calculation: Calculation, resolutions: Sequence[Resolution]) -> str:
    metric = resolutions[0].metric_label.lower() if resolutions else "value"
    years = sorted({r.fiscal_label for r in resolutions})
    span = f"fiscal {years[0]} to {years[-1]}" if len(years) > 1 else f"fiscal {years[0]}"
    return {
        "growth_pct": f"{metric} growth, {span}",
        "cagr_pct": f"{metric} compound annual growth rate, {span}",
        "margin_pct": f"{metric} margin for {span}",
        "ratio": f"{metric} ratio for {span}",
        "difference": f"change in {metric}, {span}",
        "total": f"combined {metric} for {span}",
    }.get(calculation.operation, f"{calculation.operation} for {span}")


# ── compare ───────────────────────────────────────────────────────────────────

def compare_answer(
    resolutions: Sequence[Resolution],
    *,
    question: str = "",
    companies: Optional[dict] = None,
    ciks: Optional[dict] = None,
    as_of: Optional[str] = None,
) -> Outcome:
    """Two or more companies, one metric.

    The figures are listed in the order the question named the companies, and
    the gap between the largest and smallest is stated only when every figure
    shares a unit — comparing USD with USD/shares is the unit mismatch the
    calculator refuses, and the prose must not do by hand what the calculator
    declines to do.
    """
    resolutions = list(resolutions)
    companies = companies or {}
    ciks = ciks or {}

    lines = []
    for i, r in enumerate(resolutions, start=1):
        subject = companies.get(r.ticker, r.ticker)
        lines.append(
            f"- {subject}: {format_with_exact(r.value, r.unit)} for "
            f"{_period_phrase(r)} [{i}]"
        )

    metric_label = resolutions[0].metric_label.lower()
    header = f"{metric_label.capitalize()}, as each company reports it:"
    body = "\n".join(lines)

    gap = _comparison_gap(resolutions, companies)
    text = f"{header}\n{body}{gap}{_restatement_note(resolutions)}{_as_of_note(as_of)}"

    return Outcome.answered(
        text,
        query_type=QueryType.COMPARE,
        citations=[
            citation_from(r, i, company=companies.get(r.ticker), cik=ciks.get(r.ticker))
            for i, r in enumerate(resolutions, start=1)
        ],
        definition_note=_definition_note(resolutions),
        query=question,
        as_of=as_of,
    )


def _comparison_gap(resolutions: Sequence[Resolution], companies: dict) -> str:
    units = {r.unit for r in resolutions}
    if len(resolutions) < 2 or len(units) != 1:
        return ""
    ordered = sorted(resolutions, key=lambda r: r.value)
    low, high = ordered[0], ordered[-1]
    if low.ticker == high.ticker:
        return ""
    difference = high.value - low.value
    high_name = companies.get(high.ticker, high.ticker)
    low_name = companies.get(low.ticker, low.ticker)
    note = (f"\n\n{high_name} reported "
            f"{format_with_exact(difference, high.unit)} more than {low_name}")
    if low.value > 0:
        pct = (difference / low.value) * Decimal(100)
        note += f", {format_percent(pct)} higher"
    # The periods may not line up: each company's fiscal year ends where it
    # ends, and saying so is cheaper than pretending they are comparable.
    ends = {r.end_date for r in resolutions}
    if len(ends) > 1:
        note += (". Note the fiscal years end on different dates, so the "
                 "periods are not identical")
    return note + "."


# ── trend ─────────────────────────────────────────────────────────────────────

def trend_answer(
    resolutions: Sequence[Resolution],
    *,
    question: str = "",
    company: Optional[str] = None,
    cik: Optional[int] = None,
    as_of: Optional[str] = None,
    change: Optional[Calculation] = None,
) -> Outcome:
    """One company, one metric, several periods, oldest first."""
    ordered = sorted(resolutions, key=lambda r: (r.end_date or "", r.fiscal_label))
    subject = _subject(ordered[0], company)
    metric_label = ordered[0].metric_label.lower()

    lines = [
        f"- fiscal {r.fiscal_label} (ended {r.end_date}): "
        f"{format_with_exact(r.value, r.unit)} [{i}]"
        for i, r in enumerate(ordered, start=1)
    ]
    header = (f"{subject}'s {metric_label}, fiscal {ordered[0].fiscal_label} "
              f"to {ordered[-1].fiscal_label}:")
    summary = ""
    if change is not None:
        summary = (f"\n\nOver that span it {_direction(change.value)} "
                   f"{format_value(abs(change.value), change.unit)}"
                   f" ({change.formula}).")
    elif len(ordered) >= 2:
        delta = ordered[-1].value - ordered[0].value
        summary = (f"\n\nOver that span it {_direction(delta)} "
                   f"{format_with_exact(abs(delta), ordered[0].unit)}.")

    text = (f"{header}\n" + "\n".join(lines) + summary
            + _restatement_note(ordered) + _as_of_note(as_of))

    return Outcome.answered(
        text,
        query_type=QueryType.TREND,
        citations=[
            citation_from(r, i, company=company, cik=cik)
            for i, r in enumerate(ordered, start=1)
        ],
        definition_note=_definition_note(ordered),
        query=question,
        as_of=as_of,
    )


def _direction(delta: Decimal) -> str:
    if delta > 0:
        return "rose by"
    if delta < 0:
        return "fell by"
    return "was unchanged, by"


# ── a partial answer is still an answer, with the gap stated ─────────────────

def partial_note(missing: Sequence[Tuple[str, str]]) -> str:
    """One sentence naming what could not be resolved and why.

    v1 answered "compare Apple and SpaceX" about Apple alone with no
    indication anything was dropped. A partial answer has to say what is
    missing or it reads as a complete one.
    """
    if not missing:
        return ""
    parts = "; ".join(f"{what} ({why})" for what, why in missing)
    return f" Not included: {parts}."


__all__ = [
    "citation_from",
    "compare_answer",
    "computed_answer",
    "format_exact",
    "numeric_answer",
    "partial_note",
    "trend_answer",
]
