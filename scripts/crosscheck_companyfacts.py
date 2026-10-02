#!/usr/bin/env python3
"""Cross-check the facts store against SEC companyfacts (P2-09, T2-10).

    python scripts/crosscheck_companyfacts.py
    python scripts/crosscheck_companyfacts.py --offline

This is the only *independent* test of the extractor. Everything else in
Phase 2 checks our own extraction against our own expectations; companyfacts is
the SEC's own rendering of the same filings, produced by a different pipeline
(D0.2 — it is the oracle, never the fact source).

**Comparison is by accession** (`accn`), as P2-09 requires. companyfacts
carries every value a concept ever had, including the comparative columns
restated in later filings, so matching on (ticker, concept, end_date) alone
makes a restatement look like an extraction error. (accn, concept, end_date,
unit) identifies one fact in one filing.

Discrepancies are **classified**, not counted:

* ``scale_error``  — the ratio is a power of ten. The failure mode this whole
  project exists to prevent (K2: v1's "assume millions" prompt against a filer
  reporting in thousands).
* ``sign_error``   — equal magnitude, opposite sign. An expense booked as
  income.
* ``rounding``     — differs within the precision the filer declared
  (``decimals``), e.g. a value tagged to the nearest million.
* ``mismatch``     — anything else, listed in full for inspection.

T2-10's thresholds: >= 99.5% exact on comparable pairs, zero scale errors, zero
sign errors.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from catalog.store import CatalogStore  # noqa: E402
from config import require_edgar_email, settings  # noqa: E402
from facts.concepts import load_registry  # noqa: E402
from facts.store import FactsStore  # noqa: E402

CACHE = Path(settings.data_dir).parent / ".cache" / "companyfacts"
_MIN_REQUEST_INTERVAL = 1.0 / 5.0
THRESHOLD_EXACT = Decimal("0.995")


def _user_agent() -> str:
    return f"sec-filings-research-assistant (contact: {require_edgar_email()})"


class CompanyFacts:
    def __init__(self, *, offline: bool = False) -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self._session = requests.Session()
        self._last = 0.0
        self.requests_made = 0

    def load(self, cik: int) -> Optional[Dict]:
        path = CACHE / f"CIK{int(cik):010d}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        if self.offline:
            return None
        gap = time.monotonic() - self._last
        if gap < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - gap)
        self._last = time.monotonic()
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
        resp = self._session.get(url, headers={"User-Agent": _user_agent(),
                                               "Accept-Encoding": "gzip, deflate"},
                                 timeout=180)
        self.requests_made += 1
        if resp.status_code != 200:
            print(f"  ! companyfacts CIK{cik:010d} -> HTTP {resp.status_code}",
                  file=sys.stderr)
            return None
        path.write_text(resp.text, encoding="utf-8")
        return resp.json()


def index_oracle(payload: Dict) -> Dict[Tuple[str, str, str, str, str], Decimal]:
    """(accn, concept, start, end, unit) -> value, from one companyfacts document.

    ``start`` is part of the key and leaving it out was a real defect in the
    first version of this script, worth recording because it produced 26
    "mismatches" that were all this bug. One filing can tag the same concept
    for an annual and a quarterly window **ending on the same day**: Microsoft's
    FY2024 10-K reports CommonStockDividendsPerShareDeclared as 3 for
    2023-07-01..2024-06-30 and 0.75 for 2024-04-01..2024-06-30. Keyed without
    the start date the second row overwrote the first, and the comparison then
    reported our correct annual 3.00 as disagreeing with "the oracle's 0.75".
    """
    out: Dict[Tuple[str, str, str, str, str], Decimal] = {}
    for taxonomy, concepts in (payload.get("facts") or {}).items():
        for name, body in concepts.items():
            concept = f"{taxonomy}:{name}"
            for unit, rows in (body.get("units") or {}).items():
                for row in rows:
                    accn = row.get("accn")
                    end = row.get("end")
                    value = row.get("val")
                    if not accn or not end or value is None:
                        continue
                    start = row.get("start") or ""
                    try:
                        out[(accn, concept, start, end, unit)] = Decimal(str(value))
                    except InvalidOperation:
                        continue
    return out


def _unit_keys(unit: Optional[str]) -> List[str]:
    """Our unit label vs companyfacts'. ``USD/shares`` is theirs too."""
    if not unit:
        return []
    keys = [unit]
    if unit == "USD/shares":
        keys.append("USD-per-shares")
    if unit == "pure":
        keys.append("pure")
    return keys


def classify(ours: Decimal, theirs: Decimal,
             decimals: Optional[str]) -> Tuple[str, str]:
    """Return (classification, note)."""
    if ours == theirs:
        return "exact", ""
    if ours == -theirs and ours != 0:
        return "sign_error", "equal magnitude, opposite sign"
    if theirs != 0 and ours != 0:
        ratio = (ours / theirs).copy_abs()
        for power in range(-12, 13):
            if power == 0:
                continue
            if ratio == Decimal(1).scaleb(power):
                return "scale_error", f"ours is 1e{power} x theirs"
    # Declared precision: decimals="-6" means the filer tagged to the nearest
    # million, so a difference below that is the filing's own rounding.
    if decimals not in (None, "", "INF"):
        try:
            tolerance = Decimal(1).scaleb(-int(decimals))
        except ValueError:
            tolerance = None
        if tolerance is not None and abs(ours - theirs) <= tolerance:
            return "rounding", f"within the declared precision (decimals={decimals})"
    # companyfacts stores some tiny values with fewer significant digits than
    # the filing tags: Microsoft's par value is 0.00000625 in the document and
    # 0.000006 in the oracle. Ours is the more precise of the two, so this is
    # the oracle rounding, not a disagreement about the fact.
    if theirs != 0 and abs(ours - theirs) <= abs(theirs) * Decimal("0.05") \
            and abs(theirs) < Decimal("0.001"):
        return "oracle_precision", (
            f"the oracle carries fewer significant digits ({theirs}) than the "
            f"filing tags ({ours})")
    return "mismatch", f"ours {ours} vs oracle {theirs}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--ticker", action="append", dest="tickers")
    ap.add_argument("--out", default="reports/phase2/crosscheck.csv")
    ap.add_argument("--summary", default="reports/phase2/crosscheck.json")
    args = ap.parse_args(argv)

    store = FactsStore(Path(settings.facts_db_path))
    catalog = CatalogStore(Path(settings.catalog_path))
    oracle = CompanyFacts(offline=args.offline)

    submissions = store.built_submissions()
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        submissions = [s for s in submissions if s["ticker"] in wanted]

    cik_by_ticker: Dict[str, List[int]] = {}
    for filing in catalog.all_filings():
        cik_by_ticker.setdefault(filing.ticker, [])
        if filing.cik not in cik_by_ticker[filing.ticker]:
            cik_by_ticker[filing.ticker].append(filing.cik)

    rows: List[Dict] = []
    oracle_cache: Dict[int, Dict] = {}
    tickers_missing_oracle: List[str] = []

    for submission in submissions:
        ticker = submission["ticker"]
        accession = submission["accession"]
        index: Dict[Tuple[str, str, str, str, str], Decimal] = {}
        for cik in cik_by_ticker.get(ticker, []):
            if cik not in oracle_cache:
                payload = oracle.load(cik)
                oracle_cache[cik] = index_oracle(payload) if payload else {}
            index.update(oracle_cache[cik])
        if not index:
            tickers_missing_oracle.append(ticker)
            continue

        for fact in store.query(accession=accession):
            if not fact.end_date:
                continue
            theirs = None
            matched_unit = ""
            start = fact.start_date or ""
            for unit in _unit_keys(fact.unit):
                key = (accession, fact.concept, start, fact.end_date, unit)
                if key in index:
                    theirs, matched_unit = index[key], unit
                    break
            if theirs is None:
                # Not comparable: companyfacts does not carry this
                # (accn, concept, end, unit). Counted separately — it is a
                # coverage gap in the oracle, not a disagreement.
                rows.append({
                    "ticker": ticker, "accession": accession,
                    "concept": fact.concept, "end_date": fact.end_date,
                    "unit": fact.unit or "", "ours": str(fact.value),
                    "oracle": "", "classification": "not_in_oracle",
                    "note": "", "decimals": fact.decimals or "",
                    "start_date": start,
                })
                continue
            classification, note = classify(fact.value, theirs, fact.decimals)
            rows.append({
                "ticker": ticker, "accession": accession,
                "concept": fact.concept, "end_date": fact.end_date,
                "unit": matched_unit, "ours": str(fact.value),
                "oracle": str(theirs), "classification": classification,
                "note": note, "decimals": fact.decimals or "",
                "start_date": start,
            })

    # The registry concepts are the ones that can reach a user as an answer.
    # Reporting them separately is not cherry-picking: the corpus-wide number
    # is printed first and in full, and the two measure different things. A
    # rounded narrative mention of UnrecognizedTaxBenefits disagreeing with the
    # oracle's table value costs nobody an answer; revenue disagreeing would.
    registry_concepts = {c for m in load_registry().metrics.values()
                         for c in m.all_candidates()}
    for row in rows:
        row["on_registry"] = row["concept"] in registry_concepts

    counts = Counter(r["classification"] for r in rows)
    comparable = sum(v for k, v in counts.items() if k != "not_in_oracle")
    # "exact" counts only true equality. rounding and oracle_precision are
    # agreements within a stated precision and are reported separately rather
    # than folded in, so the headline number cannot be flattered by widening a
    # tolerance.
    exact = counts.get("exact", 0)
    rate = (Decimal(exact) / Decimal(comparable)) if comparable else Decimal(0)

    print(f"facts db   : {store.path}")
    print(f"submissions: {len(submissions)}")
    print(f"facts       : {len(rows)}")
    print(f"comparable  : {comparable} (both we and the oracle have the "
          f"(accn, concept, end, unit))\n")
    print(f"{'CLASSIFICATION':18s} {'N':>7s}  SHARE OF COMPARABLE")
    for name, count in counts.most_common():
        share = f"{count / comparable:.3%}" if comparable and name != "not_in_oracle" else "-"
        print(f"  {name:16s} {count:7d}  {share}")
    print(f"\nexact on comparable pairs: {exact}/{comparable} = {rate:.4%}")

    reg_rows = [r for r in rows
                if r["on_registry"] and r["classification"] != "not_in_oracle"]
    reg_counts = Counter(r["classification"] for r in reg_rows)
    reg_exact = reg_counts.get("exact", 0)
    reg_rate = (Decimal(reg_exact) / Decimal(len(reg_rows))) if reg_rows else Decimal(0)
    print(f"  ...of which the {len(registry_concepts)} concepts the metric "
          f"registry uses: {reg_exact}/{len(reg_rows)} = {reg_rate:.4%}")
    if reg_counts:
        print("     registry classifications: "
              + ", ".join(f"{k}={v}" for k, v in reg_counts.most_common()))
    agreeing = exact + counts.get("rounding", 0) + counts.get("oracle_precision", 0)
    print(f"  agreeing within the filer's declared precision: "
          f"{agreeing}/{comparable} = {Decimal(agreeing) / Decimal(comparable):.4%}"
          if comparable else "")

    scale_errors = counts.get("scale_error", 0)
    sign_errors = counts.get("sign_error", 0)
    mismatches = [r for r in rows if r["classification"] == "mismatch"]
    passed = (rate >= THRESHOLD_EXACT and scale_errors == 0 and sign_errors == 0)
    print(f"T2-10 thresholds: exact >= 99.5% {'PASS' if rate >= THRESHOLD_EXACT else 'FAIL'}"
          f" | scale errors {scale_errors} {'PASS' if not scale_errors else 'FAIL'}"
          f" | sign errors {sign_errors} {'PASS' if not sign_errors else 'FAIL'}")

    if mismatches:
        print(f"\n{len(mismatches)} unclassified mismatch(es):")
        for row in mismatches[:30]:
            print(f"  {row['ticker']:6s} {row['end_date']} {row['concept'][:56]:58s} "
                  f"{row['note']}")
        if len(mismatches) > 30:
            print(f"  ... and {len(mismatches) - 30} more (see the CSV)")
    if tickers_missing_oracle:
        print(f"\nno oracle available for: "
              f"{', '.join(sorted(set(tickers_missing_oracle)))}")

    # The CSV carries the rows that CONSTITUTE the check — the comparable
    # pairs — and not the `not_in_oracle` ones, which record only that the
    # oracle has no row to compare against. With all 33,512 rows this artifact
    # was 4.6 MB against the repository's own 5 MB hygiene limit, so one more
    # filer would have broken `make hygiene`. The counts for every class,
    # including not_in_oracle, stay in the JSON summary beside it, and the
    # concepts the oracle lacks are listed there too.
    comparable_rows = [r for r in rows if r["classification"] != "not_in_oracle"]
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(comparable_rows[0])
                                if comparable_rows else
                                ["ticker", "accession", "concept", "start_date",
                                 "end_date", "unit", "ours", "oracle",
                                 "classification", "note", "decimals",
                                 "on_registry"])
        writer.writeheader()
        writer.writerows(comparable_rows)
    size_mb = out.stat().st_size / 1e6
    print(f"\nwrote {out.relative_to(REPO_ROOT)} ({size_mb:.1f} MB, "
          f"{len(comparable_rows)} comparable row(s); the "
          f"{len(rows) - len(comparable_rows)} not_in_oracle rows are "
          f"summarised in the JSON)")

    summary = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "submissions": len(submissions),
        "facts": len(rows),
        "comparable": comparable,
        "counts": dict(counts),
        "exact_rate": float(rate),
        "registry_concepts": sorted(registry_concepts),
        "registry_comparable": len(reg_rows),
        "registry_counts": dict(reg_counts),
        "registry_exact_rate": float(reg_rate),
        "agreeing_within_declared_precision": agreeing,
        "thresholds": {
            "exact_min": float(THRESHOLD_EXACT),
            "exact_pass": bool(rate >= THRESHOLD_EXACT),
            "scale_errors": scale_errors,
            "sign_errors": sign_errors,
            "passed": bool(passed),
        },
        "mismatches": mismatches,
        "tickers_missing_oracle": sorted(set(tickers_missing_oracle)),
        "not_in_oracle_concepts": sorted({
            r["concept"] for r in rows if r["classification"] == "not_in_oracle"
        }),
        "edgar_requests": oracle.requests_made,
    }
    summary_path = REPO_ROOT / args.summary
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {summary_path.relative_to(REPO_ROOT)}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
