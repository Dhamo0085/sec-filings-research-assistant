"""
Phase 0 / Step 2.5 + 2.6 — independent cross-check of the iXBRL extraction.

Oracle: SEC's own `companyfacts` API (data.sec.gov), which is produced by SEC
from the filings' XBRL exhibits and is INDEPENDENT of our parser.  Per D1 this
is a cross-check oracle only, never the runtime fact source.

Compares every (filing, concept) row our extractor marked found against the
companyfacts value for the same accession + period.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "reports" / "phase0"
CACHE = REPO_ROOT / ".cache" / "companyfacts"
CACHE.mkdir(parents=True, exist_ok=True)
HEADERS = {"User-Agent": "Financial-RAG Phase0 audit phase0-audit@example.com",
           "Accept-Encoding": "gzip, deflate"}


def companyfacts(cik: int) -> dict | None:
    f = CACHE / f"CIK{cik:010d}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                     headers=HEADERS, timeout=60)
    time.sleep(1.1)
    if r.status_code != 200:
        print(f"  ! companyfacts CIK {cik}: HTTP {r.status_code}")
        return None
    f.write_text(r.text, encoding="utf-8")
    return r.json()


def lookup(facts: dict, concept: str, accession: str, period_end: str):
    """Find the companyfacts entry for this concept filed under this accession."""
    for taxonomy in ("us-gaap", "ifrs-full", "dei"):
        node = facts.get("facts", {}).get(taxonomy, {}).get(concept)
        if not node:
            continue
        for unit, entries in node.get("units", {}).items():
            # exact accession + period match first
            for e in entries:
                if e.get("accn") == accession and e.get("end") == period_end:
                    if e.get("start") is None or (e.get("fp") == "FY" and e.get("form") == "10-K"):
                        return e.get("val"), unit, "accn+period"
            for e in entries:
                if e.get("end") == period_end and e.get("form") == "10-K" and e.get("fp") == "FY":
                    return e.get("val"), unit, "period+form"
    return None, None, "not-found"


def main() -> None:
    manifest = json.loads((OUT / "filing_manifest.json").read_text(encoding="utf-8"))
    cik_by_acc = {r["accession_number"]: r["cik"] for r in manifest["all_10k_filings"]}

    rows = [r for r in csv.DictReader((OUT / "ixbrl_coverage.csv").open(encoding="utf-8"))
            if r["found"] == "True"]

    results, cache = [], {}
    for r in rows:
        cik = cik_by_acc.get(r["accession"])
        if cik is None:
            continue
        if cik not in cache:
            print(f"fetching companyfacts for CIK {cik} ...")
            cache[cik] = companyfacts(cik)
        facts = cache[cik]
        if not facts:
            continue
        ours = float(r["value_usd"])
        theirs, unit, how = lookup(facts, r["matched_concept"], r["accession"], r["period_end"])
        if theirs is None:
            status, diff = "NOT_IN_COMPANYFACTS", None
        else:
            diff = abs(ours - float(theirs))
            rel = diff / max(abs(float(theirs)), 1e-9)
            if rel <= 0.005:
                status = "MATCH"
            elif any(abs(ours - float(theirs) * k) / max(abs(float(theirs) * k), 1e-9) <= 0.005
                     for k in (1e3, 1e-3, 1e6, 1e-6)):
                status = "SCALE_ERROR"
            elif abs(ours + float(theirs)) / max(abs(float(theirs)), 1e-9) <= 0.005:
                status = "SIGN_ERROR"
            else:
                status = "MISMATCH"
        results.append({
            "ticker": r["ticker"], "fiscal_year": r["fiscal_year"],
            "accession": r["accession"], "concept_group": r["concept_group"],
            "matched_concept": r["matched_concept"], "our_value_usd": ours,
            "companyfacts_value": theirs, "companyfacts_unit": unit,
            "match_method": how, "abs_diff": diff, "status": status,
            "our_scale": r["scale"], "our_context": r["context_id"],
        })

    with (OUT / "ixbrl_crosscheck.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)

    from collections import Counter
    tally = Counter(x["status"] for x in results)
    print(f"\nCross-checked {len(results)} (filing, concept) pairs against SEC companyfacts:")
    for k, v in tally.most_common():
        print(f"  {k:22s} {v}")
    print("\nNon-matching rows:")
    for x in results:
        if x["status"] != "MATCH":
            print(f"  {x['ticker']}_{x['fiscal_year']:4} {x['concept_group']:34s} "
                  f"ours={x['our_value_usd']:>20,.2f}  sec={x['companyfacts_value']}  "
                  f"[{x['status']}]")


if __name__ == "__main__":
    main()
