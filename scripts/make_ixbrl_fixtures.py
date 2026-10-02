#!/usr/bin/env python3
"""Build the trimmed iXBRL test fixtures (P2-01).

    python scripts/make_ixbrl_fixtures.py
    python scripts/make_ixbrl_fixtures.py --check     # verify, write nothing

Spec section 10 asks for "small trimmed real iXBRL excerpts ... each <= 200 KB"
covering the cases that broke Phase 0's assumptions. Hand-writing them would
make the fixtures a statement of what this extractor expects rather than of
what filers actually publish — a test that can only confirm its author. So each
fixture is cut mechanically from the real cached primary document: the facts for
a chosen set of concepts, plus exactly the ``xbrli:context`` and ``xbrli:unit``
elements those facts reference, plus the ``ix:hidden`` cover-page block.

``--check`` re-parses each written fixture and asserts the expected values
survive the trim, so a fixture cannot quietly stop containing the thing it was
built to demonstrate.

The source documents are the Phase 0 cache (``.cache/filings/``, gitignored and
never committed); the fixtures are committed. Re-running needs that cache, so
the script is for regenerating fixtures, not for the test run itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lxml import etree  # noqa: E402

from facts.extract import (  # noqa: E402
    DEI_CONCEPTS,
    _attr,
    _local_name,
    _parse_html,
    annual_facts,
    parse_submission,
)

CACHE = REPO_ROOT / ".cache" / "filings"
OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "ixbrl"
MAX_FIXTURE_BYTES = 200 * 1024


@dataclass
class FixtureSpec:
    name: str                       # output stem; one file, or _d1/_d2 for a pair
    why: str                        # the case this fixture exists to prove
    sources: List[str]              # cached document names, in submission order
    period_end: str
    concepts: List[str]             # local concept names to keep
    extra_concepts: List[str] = field(default_factory=list)
    expect: Dict[str, str] = field(default_factory=dict)   # concept -> Decimal str
    # Expectations on ANY fact, not just the annual consolidated one. The
    # negative-value cases need this: Apple's sign="-" NonoperatingIncomeExpense
    # facts are the comparative FY2023/FY2022 columns, while FY2024 itself is
    # +269M. Asserting them against the annual set was wrong, and the fixture
    # builder caught it.
    expect_any: Dict[str, str] = field(default_factory=dict)
    expect_dei: Dict[str, str] = field(default_factory=dict)
    expect_duration_days: Optional[int] = None
    max_facts_per_concept: int = 6


SPECS: List[FixtureSpec] = [
    FixtureSpec(
        name="aapl_fy2024",
        why=("the baseline case, and a 52/53-week filer: FY2024 runs 2023-10-01 "
             "to 2024-09-28, which is 363 days, so a 365-day window would "
             "reject every annual fact Apple reports (spec 6.4 rule 2, N7)"),
        sources=["AAPL_2024_0000320193-24-000123.htm"],
        period_end="2024-09-28",
        concepts=["RevenueFromContractWithCustomerExcludingAssessedTax",
                  "NetIncomeLoss", "Assets", "EarningsPerShareDiluted",
                  "OperatingIncomeLoss", "GrossProfit",
                  "ResearchAndDevelopmentExpense", "StockholdersEquity"],
        expect={"RevenueFromContractWithCustomerExcludingAssessedTax": "391035000000",
                "NetIncomeLoss": "93736000000",
                "Assets": "364980000000",
                "EarningsPerShareDiluted": "6.08"},
        expect_dei={"DocumentFiscalYearFocus": "2024",
                    "DocumentPeriodEndDate": "2024-09-28",
                    "DocumentType": "10-K",
                    "AmendmentFlag": "false",
                    "EntityCentralIndexKey": "0000320193"},
        expect_duration_days=363,
    ),
    FixtureSpec(
        name="nflx_fy2024_thousands",
        why=("reports in THOUSANDS (scale=3), unlike every other bundled filer; "
             "v1's prompt told the LLM to 'assume millions' (K2), which is a "
             "1000x error on this filing"),
        sources=["NFLX_2024_0001065280-25-000044.htm"],
        period_end="2024-12-31",
        concepts=["Revenues", "NetIncomeLoss", "Assets", "OperatingIncomeLoss",
                  "EarningsPerShareDiluted"],
        expect={"Revenues": "39000966000",
                "NetIncomeLoss": "8711631000",
                "Assets": "53630374000"},
        expect_dei={"DocumentFiscalYearFocus": "2024",
                    "EntityCentralIndexKey": "0001065280"},
    ),
    FixtureSpec(
        name="blk_fy2024_dual_revenue",
        why=("tags BOTH us-gaap:Revenues (12,794M, a component) and "
             "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax "
             "(20,407M, the income-statement total). A fixed global priority "
             "list picks the first and understates revenue by 37% (D0.3), so "
             "this filing must resolve as `ambiguous` without an override"),
        sources=["BLK_2024_0000950170-25-026584.htm"],
        period_end="2024-12-31",
        concepts=["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                  "NetIncomeLoss", "OperatingIncomeLoss", "Assets"],
        expect={"Revenues": "12794000000",
                "RevenueFromContractWithCustomerExcludingAssessedTax": "20407000000",
                "NetIncomeLoss": "6369000000"},
        expect_dei={"EntityCentralIndexKey": "0002012383"},
    ),
    FixtureSpec(
        name="bac_fy2024",
        why=("a bank: revenue is not a product sale, and the sector candidate "
             "list has to cope with Revenues / RevenuesNetOfInterestExpense / "
             "InterestAndDividendIncomeOperating all being tagged"),
        sources=["BAC_2024_0000070858-25-000139.htm"],
        period_end="2024-12-31",
        concepts=["Revenues", "RevenuesNetOfInterestExpense", "NetIncomeLoss",
                  "InterestAndDividendIncomeOperating", "Assets",
                  "StockholdersEquity"],
        expect={"Revenues": "101887000000",
                "NetIncomeLoss": "27132000000",
                "InterestAndDividendIncomeOperating": "146607000000"},
        expect_dei={"EntityCentralIndexKey": "0000070858"},
    ),
    FixtureSpec(
        name="wfc_fy2024_split",
        why=("the split-submission case. The 10-K wrapper defines the contexts "
             "and holds a handful of facts; the EX-13 exhibit holds 7,285 facts "
             "that reference them. Parsed alone the wrapper yields 4 "
             "consolidated facts and the exhibit's facts have no periods; "
             "pooled they yield 1,289 (spec 6.4 rule 1, T2-03)"),
        sources=["WFC_2024_0000072971-25-000066.htm", "WFC_2024_EX13_d2.htm"],
        period_end="2024-12-31",
        concepts=["RevenuesNetOfInterestExpense", "NetIncomeLoss", "Assets",
                  "InterestAndDividendIncomeOperating", "StockholdersEquity"],
        expect={"RevenuesNetOfInterestExpense": "82296000000",
                "NetIncomeLoss": "19722000000"},
        expect_dei={"EntityCentralIndexKey": "0000072971"},
    ),
    FixtureSpec(
        name="gs_fy2023_10ka",
        why=("a 10-K/A, and a finding in its own right: this amendment carries "
             "64 facts and ZERO annual financial facts — it re-files an exhibit, "
             "not the income statement. So D14 must not assume an amendment "
             "restates the numbers; resolving 'the latest filing for FY2023' to "
             "this document would return nothing at all, and the resolver has to "
             "fall back to the 10-K it amends (T2-05)"),
        sources=["GS_2023_A_0000886982-24-000012.htm"],
        period_end="2023-12-31",
        concepts=["Revenues", "RevenuesNetOfInterestExpense", "NetIncomeLoss",
                  "Assets"],
        expect_dei={"DocumentType": "10-K/A",
                    "AmendmentFlag": "true",
                    "DocumentFiscalYearFocus": "2023",
                    "DocumentPeriodEndDate": "2023-12-31",
                    "EntityCentralIndexKey": "0000886982"},
    ),
    FixtureSpec(
        name="aapl_fy2024_negatives",
        why=("negative values by two different mechanisms: sign=\"-\" on a "
             "positive numeral, and parenthesised text. Reading either as "
             "positive flips the sign of an expense"),
        sources=["AAPL_2024_0000320193-24-000123.htm"],
        period_end="2024-09-28",
        concepts=["NonoperatingIncomeExpense",
                  "OtherComprehensiveIncomeLossForeignCurrencyTransactionAndTranslationAdjustmentNetOfTax",
                  "PaymentsToAcquirePropertyPlantAndEquipment",
                  "NetCashProvidedByUsedInOperatingActivities"],
        # FY2024's own NonoperatingIncomeExpense is +269M; the negatives are the
        # FY2023 (-565M) and FY2022 (-334M) comparative columns in the same
        # filing, which is why this is expect_any rather than expect.
        expect={"NonoperatingIncomeExpense": "269000000"},
        expect_any={"NonoperatingIncomeExpense": "-565000000"},
        max_facts_per_concept=10,
    ),
    FixtureSpec(
        name="aapl_fy2024_edge_values",
        why=("ixt:fixed-zero (an em-dash that means 0, with scale=6 attached) "
             "and xsi:nil (which means 'not reported', NOT 0 — reading the two "
             "the same way would claim Apple disclosed zero commitments)"),
        sources=["AAPL_2024_0000320193-24-000123.htm"],
        period_end="2024-09-28",
        concepts=["MarketableSecuritiesCurrent", "MarketableSecuritiesNoncurrent",
                  "CommitmentsAndContingencies", "ProceedsFromIssuanceOfLongTermDebt"],
        max_facts_per_concept=10,
    ),
]


def _collect(root, kinds: Sequence[str]) -> List:
    return [el for el in root.iter() if _local_name(el) in kinds]


def _serialise(elements: Sequence) -> str:
    return "\n".join(
        etree.tostring(el, encoding="unicode", with_tail=False) for el in elements
    )


def build_one(spec: FixtureSpec) -> List[Tuple[Path, str]]:
    """Return [(output_path, html)] — one entry per source document."""
    source_paths = [CACHE / name for name in spec.sources]
    missing = [p for p in source_paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"{spec.name}: missing cached source(s) {[p.name for p in missing]}. "
            f"These live in .cache/filings (gitignored); the fixtures are what "
            f"is committed."
        )

    submission = parse_submission(source_paths)
    period_end = date.fromisoformat(spec.period_end)
    # The six cover-page concepts are always kept. They are not optional and
    # they are not all in ix:hidden: for Apple, DocumentFiscalYearFocus and
    # EntityRegistrantName are hidden while DocumentPeriodEndDate, DocumentType,
    # AmendmentFlag and EntityCentralIndexKey sit inline in the cover page that
    # this trim throws away. The first version of this script kept only the
    # hidden block and lost four of the six.
    wanted = set(spec.concepts) | set(spec.extra_concepts) | set(DEI_CONCEPTS)

    roots = {}
    for path in source_paths:
        roots[path.name] = _parse_html(path).getroot()

    def _rank(el) -> tuple:
        """Consolidated annual facts first, so the cap cannot drop them.

        45 of Apple's 54 RevenueFromContractWithCustomer facts are dimensional
        segment and product breakdowns, and BlackRock's are worse. Taking the
        first N in document order dropped the one number each fixture exists to
        demonstrate — silently, because the file still looked plausible.
        """
        ctx = submission.contexts.get(_attr(el, "contextRef"))
        if ctx is None:
            return (2, 0)
        dimensional = 1 if ctx.is_dimensional else 0
        if ctx.period_type == "instant":
            matches = 0 if (ctx.instant and abs((ctx.instant - period_end).days) <= 3) else 1
        elif ctx.period_type == "duration":
            matches = 0 if (ctx.end and abs((ctx.end - period_end).days) <= 3
                            and ctx.is_annual_duration()) else 1
        else:
            matches = 1
        return (dimensional, matches)

    # Keep the facts for the wanted concepts, capped per concept so a filer
    # that tags the same number in dozens of places does not dominate the file.
    keep_elements: Dict[str, List] = {name: [] for name in roots}
    per_concept: Dict[str, int] = {}
    candidates: List[tuple] = []
    for doc_name, root in roots.items():
        for order, el in enumerate(_collect(root, ("nonfraction", "nonnumeric"))):
            local = _attr(el, "name").rsplit(":", 1)[-1]
            if local not in wanted:
                continue
            candidates.append((_rank(el), order, doc_name, local, el))

    for _rankv, _order, doc_name, local, el in sorted(
            candidates, key=lambda c: (c[0], c[1])):
        cap = (spec.max_facts_per_concept if local not in DEI_CONCEPTS else 4)
        if per_concept.get(local, 0) >= cap:
            continue
        per_concept[local] = per_concept.get(local, 0) + 1
        keep_elements[doc_name].append(el)

    # Document order is restored so the fixture reads like the original.
    order_index = {}
    for root in roots.values():
        for order, el in enumerate(_collect(root, ("nonfraction", "nonnumeric"))):
            order_index[id(el)] = order
    for doc_name in keep_elements:
        keep_elements[doc_name].sort(key=lambda el: order_index[id(el)])

    kept_context_ids = {_attr(el, "contextRef")
                        for els in keep_elements.values() for el in els}
    kept_unit_ids = {_attr(el, "unitRef")
                     for els in keep_elements.values() for el in els}
    context_ids = {c for c in kept_context_ids if c}
    unit_ids = {u for u in kept_unit_ids if u}

    outputs: List[Tuple[Path, str]] = []
    for index, (doc_name, root) in enumerate(roots.items(), 1):
        # Contexts and units stay in the document that DEFINED them, so the
        # split fixture keeps its defining-document-vs-fact-document shape.
        contexts = [el for el in _collect(root, ("context",))
                    if _attr(el, "id") in context_ids]
        units = [el for el in _collect(root, ("unit",))
                 if _attr(el, "id") in unit_ids]
        hidden = _collect(root, ("hidden",))

        stem = spec.name if len(roots) == 1 else f"{spec.name}_d{index}"
        html = FIXTURE_TEMPLATE.format(
            title=f"{stem} (trimmed fixture)",
            why=spec.why,
            source=doc_name,
            generator=Path(__file__).name,
            hidden=_serialise(hidden),
            resources=_serialise(contexts + units),
            facts=_serialise(keep_elements[doc_name]),
        )
        outputs.append((OUT_DIR / f"{stem}.htm", html))
    return outputs


FIXTURE_TEMPLATE = """<!DOCTYPE html>
<!--
  TRIMMED TEST FIXTURE — generated by scripts/{generator}, do not hand-edit.

  Why this fixture exists:
    {why}

  Cut from the real primary document {source} (SEC EDGAR, public domain):
  the ix:hidden cover-page block, the xbrli:context and xbrli:unit elements
  the kept facts reference, and the facts themselves. Everything else — the
  rendered prose and tables — is dropped, which is why this is kilobytes
  rather than megabytes.

  Tags are lowercased because lxml's HTMLParser does no namespace processing,
  which is exactly how the extractor sees the originals (facts/extract.py).
-->
<html>
<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
<title>{title}</title>
<!-- The charset declaration above is load-bearing, and it has to be the
     http-equiv form and come first. libxml2 defaults an undeclared HTML
     document to latin-1, and filers use non-breaking spaces: Apple's
     cover-page date is "September\u00a028, 2024", which came back as
     "September\u00c2\u00a028, 2024" and failed to parse as a date. The real
     filings declare their charset properly; this fixture has to as well. -->
</head>
<body>
<div style="display:none">
<ix:header>
{hidden}
<ix:resources>
{resources}
</ix:resources>
</ix:header>
</div>
<div>
{facts}
</div>
</body>
</html>
"""


def verify(spec: FixtureSpec, paths: Sequence[Path]) -> List[str]:
    """Re-parse the written fixture and check it still shows what it is for."""
    problems: List[str] = []
    submission = parse_submission(list(paths))
    period_end = date.fromisoformat(spec.period_end)
    annual = annual_facts(submission, period_end)

    for concept, expected in spec.expect.items():
        values = {f.value for f in annual if f.concept_local == concept}
        if not values:
            problems.append(f"{concept}: absent from the annual set after trimming")
            continue
        wanted = Decimal(expected)
        if not any(v == wanted for v in values):
            problems.append(f"{concept}: expected {wanted}, fixture has "
                            f"{sorted(str(v) for v in values)}")

    for concept, expected in spec.expect_any.items():
        values = {f.value for f in submission.facts if f.concept_local == concept}
        if Decimal(expected) not in values:
            problems.append(f"{concept}: expected some fact == {expected}, "
                            f"fixture has {sorted(str(v) for v in values if v is not None)}")

    dei = submission.dei()
    for key, expected in spec.expect_dei.items():
        if dei.get(key) != expected:
            problems.append(f"dei:{key}: expected {expected!r}, got {dei.get(key)!r}")

    if spec.expect_duration_days is not None:
        durations = {f.context.duration_days for f in annual
                     if f.context and f.context.period_type == "duration"}
        if spec.expect_duration_days not in durations:
            problems.append(f"annual duration {spec.expect_duration_days}d not "
                            f"present; saw {sorted(d for d in durations if d)}")

    for path in paths:
        size = path.stat().st_size
        if size > MAX_FIXTURE_BYTES:
            problems.append(f"{path.name}: {size / 1024:.0f} KB exceeds the "
                            f"{MAX_FIXTURE_BYTES // 1024} KB limit (spec section 10)")
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="verify the fixtures already on disk; write nothing")
    ap.add_argument("--only", default="", help="comma-separated fixture names")
    ap.add_argument("--manifest", default="tests/fixtures/ixbrl/MANIFEST.json")
    args = ap.parse_args(argv)

    wanted = {n.strip() for n in args.only.split(",") if n.strip()}
    specs = [s for s in SPECS if not wanted or s.name in wanted]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest: List[Dict] = []
    failures = 0
    for spec in specs:
        if args.check:
            paths = sorted(OUT_DIR.glob(f"{spec.name}.htm")) or \
                    sorted(OUT_DIR.glob(f"{spec.name}_d*.htm"))
            if not paths:
                print(f"  MISSING {spec.name}")
                failures += 1
                continue
        else:
            outputs = build_one(spec)
            for path, html in outputs:
                path.write_text(html, encoding="utf-8")
            paths = [p for p, _ in outputs]

        problems = verify(spec, paths)
        total_kb = sum(p.stat().st_size for p in paths) / 1024
        status = "OK  " if not problems else "FAIL"
        print(f"  {status} {spec.name:30s} {len(paths)} file(s)  {total_kb:6.1f} KB")
        for problem in problems:
            print(f"         - {problem}")
        failures += bool(problems)

        manifest.append({
            "name": spec.name,
            "files": [p.name for p in paths],
            "sources": spec.sources,
            "period_end": spec.period_end,
            "why": spec.why,
            "kilobytes": round(total_kb, 1),
            "expect": spec.expect,
            "expect_dei": spec.expect_dei,
            "expect_duration_days": spec.expect_duration_days,
        })

    if not args.check:
        out = REPO_ROOT / args.manifest
        out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out.relative_to(REPO_ROOT)}")

    if failures:
        print(f"\n{failures} fixture(s) did not verify")
        return 1
    print(f"\n{len(specs)} fixture spec(s) verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
