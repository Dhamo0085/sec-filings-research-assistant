"""
Phase 0 / Step 2.7 — revenue-concept ambiguity scan.

The Step 2.3 concept list uses a single global priority order of alternates.
BlackRock showed that this is unsafe: it tags BOTH `Revenues` (12,794M, a
component) and `RevenueFromContractWithCustomerExcludingAssessedTax`
(20,407M, the income-statement total).  This script reports EVERY revenue
candidate each filing tags for the consolidated FY period, so Phase 2 can
design concept resolution on evidence instead of a guess.
"""
from __future__ import annotations

import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ixbrl_extract import (REPO_ROOT, CONCEPTS, parse_contexts, fact_value, _ln, _attr)  # noqa: E402
from lxml import etree  # noqa: E402
from datetime import datetime  # noqa: E402

FILINGS = REPO_ROOT / ".cache" / "filings"
OUT = REPO_ROOT / "reports" / "phase0"

CANDIDATES = CONCEPTS["Revenue"]["names"] + [
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "InterestIncomeExpenseNet",
    "InterestAndDividendIncomeOperating",
    "NoninterestIncome",
    "RevenuesNetOfInterestExpense",
]


def main() -> None:
    index = json.loads((FILINGS / "index.json").read_text(encoding="utf-8"))
    report = []
    for item in index:
        p = REPO_ROOT / item["local_path"]
        root = etree.parse(str(p), etree.HTMLParser(huge_tree=True, recover=True)).getroot()
        ctxs = parse_contexts(root)
        pe = datetime.fromisoformat(item["period_end"]).date()

        hits = {}
        for el in root.iter():
            if _ln(el) != "nonfraction":
                continue
            name = _attr(el, "name").split(":")[-1]
            if name not in CANDIDATES:
                continue
            c = ctxs.get(_attr(el, "contextRef"))
            if not c or c["dimensional"] or not (c["start"] and c["end"]):
                continue
            s = datetime.fromisoformat(c["start"]).date()
            e = datetime.fromisoformat(c["end"]).date()
            if abs((e - pe).days) > 3 or abs((e - s).days - 365) > 10:
                continue
            v, _, _ = fact_value(el)
            if v is not None:
                hits.setdefault(name, set()).add(v)

        entry = {
            "ticker": item["ticker"], "fiscal_year": item["fiscal_year_v1_rule"],
            "candidates": {k: sorted(v) for k, v in hits.items()},
            "n_distinct_concepts": len(hits),
            "ambiguous": len(hits) > 1,
        }
        report.append(entry)
        print(f"\n{item['ticker']} FY{item['fiscal_year_v1_rule']}  "
              f"({'AMBIGUOUS - ' + str(len(hits)) + ' candidates' if len(hits) > 1 else str(len(hits)) + ' candidate'})")
        for k, vals in sorted(hits.items(), key=lambda kv: -max(kv[1])):
            for v in sorted(vals):
                print(f"    {k[:60]:62s} {v/1e6:>14,.0f} M")

    (OUT / "revenue_ambiguity.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    amb = [r for r in report if r["ambiguous"]]
    print(f"\n{len(amb)}/{len(report)} filings tag MORE THAN ONE consolidated FY revenue concept.")
    print("Affected:", ", ".join(f"{r['ticker']}_{r['fiscal_year']}" for r in amb) or "none")


if __name__ == "__main__":
    main()
