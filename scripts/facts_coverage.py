#!/usr/bin/env python3
"""Coverage and ambiguity report for the facts store (P2-11).

    python scripts/facts_coverage.py
    python scripts/facts_coverage.py --csv reports/phase2/coverage.csv

Resolves every (ticker, metric, fiscal label) the facts store could answer and
records what came back. The point is not the pass rate — it is the list of
**every filer/metric that resolves to `ambiguous` or `conflict`**, because each
one is a decision the owner has to make rather than a bug to be smoothed over.

An `ambiguous_concept` row means the filer tags two different concepts for the
same metric and nothing in the registry chooses between them. Spec 6.4 rule 3
says abstain, and P2-05 says the fix is a per-filer override **with evidence**.
So this report is the worklist for writing those overrides, and the count of
`ambiguous` rows going down is the measure of progress.

A `conflict` validation status means the value was resolved but could not be
found on the rendered statement. That is weaker evidence than it sounds — the
rendered text only exists for parsed tickers, and a statement may print a
rounded figure — so conflicts are listed for inspection, not treated as
failures.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from catalog.store import CatalogStore  # noqa: E402
from config import settings  # noqa: E402
from facts.concepts import load_registry  # noqa: E402
from facts.resolve import FactsResolver, PeriodSelector  # noqa: E402
from facts.store import FactsStore  # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ticker", action="append", dest="tickers")
    ap.add_argument("--all-tickers", action="store_true",
                    help="include the cross-check-only filers (P2-09: NVDA, "
                         "TSLA, V, COST, META). Excluded by default because "
                         "they are deliberately outside the project's corpus "
                         "(not in config.COMPANIES, no sector entry, no "
                         "overrides), so mixing them in moves the headline "
                         "coverage number for the bundled set.")
    ap.add_argument("--metric", action="append", dest="metrics")
    ap.add_argument("--out", default="reports/phase2/facts_coverage.json")
    ap.add_argument("--csv", default="reports/phase2/facts_coverage.csv")
    args = ap.parse_args(argv)

    store = FactsStore(Path(settings.facts_db_path))
    catalog = CatalogStore(Path(settings.catalog_path))
    registry = load_registry()
    resolver = FactsResolver(catalog=catalog, store=store, registry=registry)

    # Only the (ticker, label) pairs the store actually holds: resolving years
    # that were never built would fill the report with period_not_covered rows
    # that say nothing about coverage of what we have.
    built = store.built_submissions()
    pairs = sorted({(s["ticker"], s["fiscal_label"]) for s in built
                    if s["fiscal_label"]})
    tickers = {t.upper() for t in (args.tickers or [])}
    if not tickers and not args.all_tickers:
        from config import COMPANIES
        tickers = {c["ticker"].upper() for c in COMPANIES} | {"NFLX"}
    if tickers:
        pairs = [p for p in pairs if p[0] in tickers]
    metrics = args.metrics or registry.metric_names()

    rows: List[Dict] = []
    for ticker, label in pairs:
        for metric in metrics:
            outcome = resolver.resolve(ticker, metric,
                                       PeriodSelector.fiscal_label(label))
            if outcome.resolved:
                rows.append({
                    "ticker": ticker, "fiscal_label": label, "metric": metric,
                    "status": "resolved",
                    "concept": outcome.concept,
                    "value": str(outcome.value),
                    "unit": outcome.unit or "",
                    "selection": outcome.selection,
                    "validation_status": outcome.validation_status,
                    "validation_detail": outcome.validation_detail,
                    "restated": outcome.restated,
                    "accession": outcome.accession,
                    "detail": "",
                    "candidates": "",
                })
            else:
                rows.append({
                    "ticker": ticker, "fiscal_label": label, "metric": metric,
                    "status": outcome.reason,
                    "concept": "", "value": "", "unit": "",
                    "selection": "", "validation_status": "",
                    "validation_detail": "",
                    "restated": False, "accession": "",
                    "detail": outcome.detail,
                    "candidates": "; ".join(
                        f"{c}={outcome.candidate_values.get(c, '?')}"
                        for c in outcome.candidates),
                })

    status_counts = Counter(r["status"] for r in rows)
    validation_counts = Counter(r["validation_status"] for r in rows
                                if r["status"] == "resolved")
    ambiguous = [r for r in rows if r["status"] == "ambiguous_concept"]
    conflicts = [r for r in rows if r["validation_status"] == "conflict"]

    total = len(rows)
    resolved = status_counts.get("resolved", 0)
    print(f"facts db : {store.path}")
    print(f"scope    : {len(pairs)} (ticker, fiscal label) pair(s) x "
          f"{len(metrics)} metric(s) = {total} resolutions\n")
    print(f"{'STATUS':28s} {'N':>5s}  SHARE")
    for status, count in status_counts.most_common():
        print(f"  {status:26s} {count:5d}  {count / total:6.1%}")
    print(f"\nresolved = {resolved}/{total} ({resolved / total:.1%})")
    print(f"{'validation':28s} " + ", ".join(
        f"{k}={v}" for k, v in validation_counts.most_common()))

    if ambiguous:
        print(f"\n{len(ambiguous)} AMBIGUOUS (needs an override with evidence, "
              f"P2-05):")
        by_pair: Dict[str, List[str]] = {}
        for row in ambiguous:
            by_pair.setdefault(f"{row['ticker']} {row['metric']}", []).append(
                str(row["fiscal_label"]))
        for key, labels in sorted(by_pair.items()):
            sample = next(r for r in ambiguous
                          if f"{r['ticker']} {r['metric']}" == key)
            print(f"  {key:28s} FY{','.join(sorted(labels))}")
            print(f"      {sample['candidates']}")

    if conflicts:
        print(f"\n{len(conflicts)} CONFLICT (resolved but not found on the "
              f"rendered statement):")
        for row in conflicts[:25]:
            print(f"  {row['ticker']:6s} FY{row['fiscal_label']} "
                  f"{row['metric']:20s} {row['value']:>18s} {row['concept']}")
        if len(conflicts) > 25:
            print(f"  ... and {len(conflicts) - 25} more (see the CSV)")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "facts_db": str(store.path),
        "pairs": len(pairs),
        "metrics": metrics,
        "resolutions": total,
        "status_counts": dict(status_counts),
        "validation_counts": dict(validation_counts),
        "ambiguous": ambiguous,
        "conflicts": conflicts,
        "rows": rows,
    }
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")

    if args.csv:
        csv_path = REPO_ROOT / args.csv
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {csv_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
