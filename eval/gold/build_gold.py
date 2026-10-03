#!/usr/bin/env python3
"""Build ``eval/gold/gold_v1.jsonl`` from the plan, the facts store and the oracle (P4-01).

    python -m eval.gold.build_gold                 # offline, from the cache
    python -m eval.gold.build_gold --check         # rebuild and diff, write nothing
    python -m eval.gold.build_gold --allow-network # fetch missing companyfacts

What this does, and what it refuses to do
-----------------------------------------
Every numeric, computed, compare/trend and ``as_of`` item named in
``eval/gold/plan.py`` is resolved through the same ``FactsResolver`` the product
uses, and then **independently verified against SEC companyfacts** — a different
pipeline over the same filings (D4). The oracle decides ``verified_by``:

``companyfacts``  the oracle holds this exact (accession, concept, period, unit)
                  and agrees to the digit. The strongest tier available without
                  the owner.
``auto``          the oracle has no comparable row, so nothing independent
                  confirms the value. Reported separately and never counted in
                  a headline metric (spec section 11).

``owner`` is never written here. It is set at the P4-02 gate, by a person.

A value the oracle holds and *disagrees* with is not downgraded to ``auto`` and
written anyway — the row is **rejected** and the script exits non-zero. An
extractor disagreement is a defect to fix, and silently shipping the value as a
gold answer would bake that defect into every number Phase 4 reports.

The script is deterministic: the plan is a committed table, not a sample, so
rebuilding on the same store produces a byte-identical file. ``--check`` proves
that without writing.

Offline by default: it reads ``.cache/companyfacts/`` and makes no request
unless ``--allow-network`` is given.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from catalog.store import CatalogStore  # noqa: E402
from config import settings  # noqa: E402
from eval.gold import plan as PLAN  # noqa: E402
from eval.gold.schema import (  # noqa: E402
    CATEGORY_TARGETS,
    CatalogDates,
    category_counts,
    read_jsonl,
    validate,
    write_jsonl,
)
from facts import calc  # noqa: E402
from facts.resolve import FactsResolver, PeriodSelector  # noqa: E402
from facts.resolve import _plain as plain_decimal  # noqa: E402

GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"
SEEDS_DIR = REPO_ROOT / "eval" / "gold" / "seeds"
ORACLE_NAME = "sec_companyfacts"

#: A numeric gold value is exact or it is not gold. The resolver and the oracle
#: read the same iXBRL facts, so anything other than equality is a disagreement
#: to investigate, not a tolerance to widen. P2-09 measured 99.9787% bit-exact
#: corpus-wide, so this is a bar the extractor already clears.
NUMERIC_TOLERANCE_REL = 0.0

#: Computed answers are rounded for display (facts/calc.py rounds percentages to
#: two places), so a percentage point of slack is the rendering, not the maths.
PERCENT_TOLERANCE_ABS = 0.01
RATIO_TOLERANCE_ABS = 0.0001


class BuildError(Exception):
    """A planned item could not be produced. Never swallowed."""


# ── the oracle ────────────────────────────────────────────────────────────────

class Oracle:
    """SEC companyfacts, indexed per CIK, for independent verification."""

    def __init__(self, *, allow_network: bool) -> None:
        from scripts.crosscheck_companyfacts import CompanyFacts, index_oracle

        self._facts = CompanyFacts(offline=not allow_network)
        self._index_oracle = index_oracle
        self._by_cik: Dict[int, Optional[Dict]] = {}

    def _index(self, cik: int) -> Optional[Dict]:
        if cik not in self._by_cik:
            payload = self._facts.load(cik)
            self._by_cik[cik] = self._index_oracle(payload) if payload else None
        return self._by_cik[cik]

    def lookup(self, cik: int, resolution) -> Optional[Decimal]:
        """The oracle's own value for exactly this fact, or None if it has none.

        Keyed the way P2-09 keys it, including ``start``: one filing can tag the
        same concept for an annual and a quarterly window ending on the same
        day, and dropping ``start`` made the quarterly row overwrite the annual
        one. The unit is tried under both the plain name and companyfacts'
        per-share spelling.
        """
        index = self._index(cik)
        if not index:
            return None
        unit = resolution.unit or ""
        unit_keys = [unit, unit.replace("/", "-per-")] if unit else [""]
        for unit_key in unit_keys:
            key = (
                resolution.accession,
                resolution.concept,
                resolution.start_date or "",
                resolution.end_date or "",
                unit_key,
            )
            if key in index:
                return index[key]
        return None


# ── resolving one planned fact ────────────────────────────────────────────────

class Fact:
    """A resolved fact plus the oracle's verdict on it."""

    def __init__(self, resolution, verified_by: str) -> None:
        self.r = resolution
        self.verified_by = verified_by

    @property
    def source(self) -> Dict:
        source = {
            "accession": self.r.accession,
            "concept": self.r.concept,
            "filing_date": self.r.filing_date,
            "form_type": self.r.form_type,
            "selection": self.r.selection,
        }
        if self.verified_by == "companyfacts":
            source["oracle"] = ORACLE_NAME
        return source


def resolve_fact(
    resolver: FactsResolver, oracle: Oracle, ciks: Dict[str, int],
    ticker: str, metric: str, fiscal_label: int,
) -> Fact:
    """Resolve one fact and ask the oracle about it.

    ``ciks`` is keyed by ACCESSION, not by ticker. Keying it by ticker was a
    real bug in the first version of this script, and BlackRock found it: BLK
    files under CIK 1364742 up to FY2023 and 2012383 from FY2024 (D24), so one
    CIK per ticker sent the FY2024 lookup to the wrong companyfacts document.
    The oracle then had "no comparable row" and the item was quietly written as
    ``auto`` — a verification silently skipped, which is worse than one that
    fails.
    """
    outcome = resolver.resolve(
        ticker=ticker, metric=metric,
        period=PeriodSelector.fiscal_label(fiscal_label),
    )
    if not outcome.resolved:
        raise BuildError(
            f"{ticker} {metric} FY{fiscal_label}: the resolver abstained "
            f"({outcome.reason}: {outcome.detail or 'no detail'}). The plan "
            f"names this item, so either the plan or the registry is wrong."
        )

    cik = ciks.get(outcome.accession)
    if cik is None:
        raise BuildError(
            f"{ticker} {metric} FY{fiscal_label}: accession "
            f"{outcome.accession} is not in the catalog, so no CIK is known "
            f"and the oracle cannot be consulted."
        )
    theirs = oracle.lookup(cik, outcome)
    if theirs is None:
        return Fact(outcome, "auto")
    if theirs != outcome.value:
        raise BuildError(
            f"{ticker} {metric} FY{fiscal_label}: we read {outcome.value}, "
            f"SEC companyfacts reads {theirs} for the same accession, concept, "
            f"period and unit. This is an extractor disagreement; fix it rather "
            f"than writing either value into the gold set."
        )
    return Fact(outcome, "companyfacts")


# ── item builders ─────────────────────────────────────────────────────────────

def _numeric_expected(fact: Fact) -> Dict:
    return {
        "type": "numeric",
        "ticker": fact.r.ticker,
        "metric": fact.r.metric,
        "fiscal_label": fact.r.fiscal_label,
        "period_end": fact.r.end_date,
        "value": plain_decimal(fact.r.value),
        "unit": fact.r.unit,
        "tolerance_rel": NUMERIC_TOLERANCE_REL,
    }


def build_numeric(resolver, oracle, ciks) -> List[Dict]:
    items: List[Dict] = []
    for row in PLAN.NUMERIC_PLAN:
        fact = resolve_fact(resolver, oracle, ciks, row.ticker, row.metric, row.fiscal_label)
        phrase = PLAN.metric_phrase(row.ticker, row.metric)
        name = PLAN.DISPLAY_NAMES[row.ticker]
        items.append({
            "id": f"N-{row.ticker}-{row.metric.upper().replace('_', '')}-{row.fiscal_label}",
            "category": "numeric",
            "question": f"What was {name}'s {phrase} in fiscal {row.fiscal_label}?",
            "as_of": None,
            "expected": _numeric_expected(fact),
            "source": fact.source,
            "verified_by": fact.verified_by,
            "notes": row.why,
        })
    return items


def build_computed(resolver, oracle, ciks) -> List[Dict]:
    items: List[Dict] = []
    for row in PLAN.COMPUTED_PLAN:
        name = PLAN.DISPLAY_NAMES[row.ticker]

        if row.kind == "margin_pct":
            numerator, denominator, label = row.args
            top = resolve_fact(resolver, oracle, ciks, row.ticker, numerator, label)
            bottom = resolve_fact(resolver, oracle, ciks, row.ticker, denominator, label)
            result = calc.margin_pct(top.r, bottom.r)
            margin_name = {
                "operating_income": "operating margin",
                "net_income": "net margin",
                "gross_profit": "gross margin",
            }[numerator]
            question = f"What was {name}'s {margin_name} in fiscal {label}?"
            item_id = f"C-{row.ticker}-{margin_name.split()[0].upper()}MARGIN-{label}"
            unit, tolerance = "percent", PERCENT_TOLERANCE_ABS
            operands = [top, bottom]

        elif row.kind == "growth_pct":
            metric, earlier_label, later_label = row.args
            earlier = resolve_fact(resolver, oracle, ciks, row.ticker, metric, earlier_label)
            later = resolve_fact(resolver, oracle, ciks, row.ticker, metric, later_label)
            result = calc.growth_pct(later.r, earlier.r)
            phrase = PLAN.metric_phrase(row.ticker, metric)
            question = (f"How did {name}'s {phrase} change from fiscal "
                        f"{earlier_label} to fiscal {later_label}?")
            item_id = f"C-{row.ticker}-{metric.upper().replace('_', '')}GROWTH-{later_label}"
            unit, tolerance = "percent", PERCENT_TOLERANCE_ABS
            operands = [earlier, later]

        elif row.kind == "cagr_pct":
            metric, earlier_label, later_label, years = row.args
            earlier = resolve_fact(resolver, oracle, ciks, row.ticker, metric, earlier_label)
            later = resolve_fact(resolver, oracle, ciks, row.ticker, metric, later_label)
            result = calc.cagr_pct(later.r, earlier.r, years)
            phrase = PLAN.metric_phrase(row.ticker, metric)
            question = (f"What was {name}'s {phrase} CAGR from fiscal "
                        f"{earlier_label} to fiscal {later_label}?")
            item_id = f"C-{row.ticker}-{metric.upper().replace('_', '')}CAGR-{later_label}"
            unit, tolerance = "percent", PERCENT_TOLERANCE_ABS
            operands = [earlier, later]

        elif row.kind == "ratio":
            numerator, denominator, label = row.args
            top = resolve_fact(resolver, oracle, ciks, row.ticker, numerator, label)
            bottom = resolve_fact(resolver, oracle, ciks, row.ticker, denominator, label)
            result = calc.ratio(top.r, bottom.r)
            question = (f"What was {name}'s ratio of total liabilities to "
                        f"stockholders' equity in fiscal {label}?")
            item_id = f"C-{row.ticker}-DEBTEQUITY-{label}"
            unit, tolerance = "ratio", RATIO_TOLERANCE_ABS
            operands = [top, bottom]

        elif row.kind == "difference":
            minuend, subtrahend, label = row.args
            first = resolve_fact(resolver, oracle, ciks, row.ticker, minuend, label)
            second = resolve_fact(resolver, oracle, ciks, row.ticker, subtrahend, label)
            result = calc.difference(first.r, second.r)
            question = (f"What was {name}'s operating cash flow less capital "
                        f"expenditures in fiscal {label}?")
            item_id = f"C-{row.ticker}-FCF-{label}"
            unit, tolerance = first.r.unit, NUMERIC_TOLERANCE_REL
            operands = [first, second]

        else:  # pragma: no cover — the plan is a closed table
            raise BuildError(f"unknown computed kind {row.kind!r}")

        # A computed item is only as verified as its weakest operand: if the
        # oracle could not confirm one input, it has not confirmed the result.
        verified_by = (
            "companyfacts"
            if all(o.verified_by == "companyfacts" for o in operands)
            else "auto"
        )
        expected = {
            "type": "computed",
            "ticker": row.ticker,
            "metric": row.kind,
            "value": plain_decimal(result.value),
            "unit": unit,
        }
        if unit in ("percent", "ratio"):
            expected["tolerance_abs"] = tolerance
        else:
            expected["tolerance_rel"] = tolerance

        items.append({
            "id": item_id,
            "category": "computed",
            "question": question,
            "as_of": None,
            "expected": expected,
            "source": {
                "operands": [
                    {"accession": o.r.accession, "concept": o.r.concept,
                     "metric": o.r.metric, "fiscal_label": o.r.fiscal_label,
                     "value": plain_decimal(o.r.value)}
                    for o in operands
                ],
                "operation": row.kind,
                **({"oracle": ORACLE_NAME} if verified_by == "companyfacts" else {}),
            },
            "verified_by": verified_by,
            "notes": row.why,
        })
    return items


def build_compare_trend(resolver, oracle, ciks) -> List[Dict]:
    items: List[Dict] = []
    for row in PLAN.COMPARE_PLAN:
        facts: List[Fact] = []
        if row.kind == "compare":
            label = row.labels[0]
            for ticker in row.tickers:
                facts.append(resolve_fact(resolver, oracle, ciks, ticker, row.metric, label))
            names = " and ".join(PLAN.DISPLAY_NAMES[t] for t in row.tickers)
            phrase = PLAN.METRIC_PHRASES[row.metric]
            question = f"Compare {names} {phrase} in fiscal {label}."
            item_id = f"M-{'-'.join(row.tickers)}-{row.metric.upper().replace('_', '')}-{label}"
        else:
            ticker = row.tickers[0]
            for label in row.labels:
                facts.append(resolve_fact(resolver, oracle, ciks, ticker, row.metric, label))
            name = PLAN.DISPLAY_NAMES[ticker]
            phrase = PLAN.metric_phrase(ticker, row.metric)
            question = (f"How has {name}'s {phrase} moved from fiscal "
                        f"{row.labels[0]} to fiscal {row.labels[-1]}?")
            item_id = (f"M-{ticker}-{row.metric.upper().replace('_', '')}-TREND-"
                       f"{row.labels[0]}-{row.labels[-1]}")

        verified_by = (
            "companyfacts"
            if all(f.verified_by == "companyfacts" for f in facts)
            else "auto"
        )
        items.append({
            "id": item_id,
            "category": "compare_trend",
            "question": question,
            "as_of": None,
            "expected": {
                "type": "multi",
                "shape": row.kind,
                # Every value carries the company and year it belongs to:
                # attributing the right number to the wrong filer is the
                # failure a single-number scorer cannot see (spec section 11).
                "values": [
                    {"ticker": f.r.ticker, "fiscal_label": f.r.fiscal_label,
                     "metric": f.r.metric, "period_end": f.r.end_date,
                     "value": plain_decimal(f.r.value), "unit": f.r.unit}
                    for f in facts
                ],
                "tolerance_rel": NUMERIC_TOLERANCE_REL,
            },
            "source": {
                "accessions": [f.r.accession for f in facts],
                "concepts": sorted({f.r.concept for f in facts}),
                **({"oracle": ORACLE_NAME} if verified_by == "companyfacts" else {}),
            },
            "verified_by": verified_by,
            "notes": row.why,
        })
    return items


def _day_before(iso_date: str) -> str:
    from datetime import date, timedelta

    year, month, day = (int(part) for part in iso_date.split("-"))
    return (date(year, month, day) - timedelta(days=1)).isoformat()


def build_as_of(resolver, oracle, ciks, catalog: CatalogDates) -> List[Dict]:
    """Point-in-time items, with every date taken from the catalog.

    The dates are derived, never typed in. A look-ahead item is the day before
    the filing was actually filed, so it is a real test by construction rather
    than by someone remembering a date correctly; an answerable item is a date
    comfortably after. The schema validator re-checks both against the catalog
    (T4-02), which catches the case where the catalog later changes.
    """
    items: List[Dict] = []
    for row in PLAN.AS_OF_PLAN:
        filed = catalog.filing_dates.get((row.ticker, row.fiscal_label))
        if filed is None:
            raise BuildError(
                f"{row.ticker} FY{row.fiscal_label}: no original 10-K in the "
                f"catalog, so no point-in-time item can be built from it."
            )
        name = PLAN.DISPLAY_NAMES[row.ticker]
        phrase = PLAN.metric_phrase(row.ticker, row.metric)

        if row.stance == "look_ahead":
            as_of = _day_before(filed)
            items.append({
                "id": f"A-{row.ticker}-{row.metric.upper().replace('_', '')}-{row.fiscal_label}-LOOKAHEAD",
                "category": "as_of",
                "question": (f"As of {as_of}, what was {name}'s {phrase} for "
                             f"fiscal {row.fiscal_label}?"),
                "as_of": as_of,
                "expected": {
                    "type": "abstain",
                    "abstain_reason": "period_not_filed_as_of",
                    "ticker": row.ticker,
                    "fiscal_label": row.fiscal_label,
                },
                "source": {
                    "filing_date": filed,
                    "basis": "catalog filing_date (SEC submissions); as_of is "
                             "the day before it",
                },
                # Not "companyfacts": this item has no numeric value for an
                # oracle to confirm. What makes it a valid test is the filing
                # DATE, which the schema validator re-checks against the
                # catalog on every run (T4-02). It stays "auto" until the
                # owner signs it off at the P4-02 gate.
                "verified_by": "auto",
                "notes": row.why,
            })
            continue

        fact = resolve_fact(resolver, oracle, ciks, row.ticker, row.metric, row.fiscal_label)
        # A date a clear year after filing: late enough that no plausible
        # amendment window makes the answer ambiguous, and still a date on
        # which only this filing and earlier ones existed.
        year, month, day = (int(part) for part in filed.split("-"))
        as_of = f"{year + 1:04d}-{month:02d}-{day:02d}"
        expected = _numeric_expected(fact)
        expected["fiscal_label"] = row.fiscal_label
        items.append({
            "id": f"A-{row.ticker}-{row.metric.upper().replace('_', '')}-{row.fiscal_label}-ASOF",
            "category": "as_of",
            "question": (f"As of {as_of}, what was {name}'s {phrase} for "
                         f"fiscal {row.fiscal_label}?"),
            "as_of": as_of,
            "expected": expected,
            "source": {**fact.source, "filing_date_checked": filed},
            "verified_by": fact.verified_by,
            "notes": row.why,
        })
    return items


# ── assembly ──────────────────────────────────────────────────────────────────

def build(allow_network: bool = False) -> Tuple[List[Dict], List[str]]:
    """The whole gold set, plus the notes worth printing."""
    catalog_store = CatalogStore(Path(settings.data_dir) / "derived" / "catalog.sqlite")
    catalog = CatalogDates.from_catalog(catalog_store)
    # By accession: a ticker can have more than one CIK (D24).
    ciks = {f.accession: f.cik for f in catalog_store.all_filings()}

    resolver = FactsResolver()
    oracle = Oracle(allow_network=allow_network)

    items: List[Dict] = []
    items += build_numeric(resolver, oracle, ciks)
    items += build_computed(resolver, oracle, ciks)
    items += build_compare_trend(resolver, oracle, ciks)
    items += build_as_of(resolver, oracle, ciks, catalog)
    items += read_jsonl(SEEDS_DIR / "narrative.jsonl")
    items += read_jsonl(SEEDS_DIR / "abstain.jsonl")

    notes: List[str] = []
    counts = category_counts(items)
    for category, target in CATEGORY_TARGETS.items():
        if counts[category] != target:
            notes.append(
                f"category {category}: {counts[category]} items, spec section 11 "
                f"targets {target}"
            )
    return items, notes


def _summarise(items: Sequence[Dict]) -> None:
    counts = category_counts(items)
    print(f"\n{len(items)} items")
    print(f"  {'category':14s} {'built':>6s} {'target':>7s}")
    for category, target in CATEGORY_TARGETS.items():
        flag = "" if counts[category] == target else "   <-- off target"
        print(f"  {category:14s} {counts[category]:6d} {target:7d}{flag}")

    by_verification: Dict[str, int] = {}
    for item in items:
        key = str(item.get("verified_by"))
        by_verification[key] = by_verification.get(key, 0) + 1
    print("\n  verified_by: " + ", ".join(
        f"{k}={v}" for k, v in sorted(by_verification.items())
    ))

    tickers = [
        str(item["expected"].get("ticker"))
        for item in items
        if isinstance(item.get("expected"), dict) and item["expected"].get("ticker")
    ]
    print("  sectors:     " + ", ".join(
        f"{k}={v}" for k, v in sorted(PLAN.sector_coverage(tickers).items())
    ))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=GOLD_PATH)
    ap.add_argument("--allow-network", action="store_true",
                    help="fetch companyfacts documents missing from .cache/")
    ap.add_argument("--check", action="store_true",
                    help="rebuild and compare with the committed file; write nothing")
    args = ap.parse_args(argv)

    try:
        items, notes = build(allow_network=args.allow_network)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    catalog_store = CatalogStore(Path(settings.data_dir) / "derived" / "catalog.sqlite")
    problems = validate(items, CatalogDates.from_catalog(catalog_store))

    _summarise(items)
    for note in notes:
        print(f"\n  NOTE {note}")

    if problems:
        print(f"\n{len(problems)} schema problems:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    if args.check:
        if not args.out.exists():
            print(f"\n{args.out} does not exist", file=sys.stderr)
            return 1
        rebuilt = {item["id"]: item for item in items}
        committed = {item["id"]: item for item in read_jsonl(args.out)}
        if rebuilt == committed:
            print(f"\n{args.out} matches a fresh build")
            return 0
        only_new = sorted(set(rebuilt) - set(committed))
        only_old = sorted(set(committed) - set(rebuilt))
        changed = sorted(
            i for i in set(rebuilt) & set(committed) if rebuilt[i] != committed[i]
        )
        print(f"\n{args.out} differs from a fresh build:", file=sys.stderr)
        for label, ids in (("only in rebuild", only_new),
                           ("only in committed file", only_old),
                           ("changed", changed)):
            if ids:
                print(f"  {label}: {', '.join(ids)}", file=sys.stderr)
        return 1

    write_jsonl(args.out, items)
    print(f"\nwrote {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
