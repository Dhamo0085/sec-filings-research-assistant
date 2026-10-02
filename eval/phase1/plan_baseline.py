"""Decide which baseline questions can actually run, and why not for the rest (P1-00).

    python eval/phase1/plan_baseline.py

P1-00 asks for the 25-question baseline with `not_run` counts recorded. Two
constraints make a full run impossible on this machine, and both are findings
rather than excuses:

1. **Indexing throughput.** v1 embeds with bge-base on CPU at a measured
   ~1.63 s/chunk here (AAPL: 589 chunks in 957.8 s). The bundled 12 are 35,838
   chunks, so a full index is a ~16 hour job on this 8 GB machine.
2. **Auto-ingest on miss.** A question naming an unindexed company triggers
   `ingestion.auto_ingest`, which downloads, parses, chunks and embeds that
   filer inline. Running such a question does not just fail — it silently
   starts a 20+ minute ingest inside the measurement.

So the runnable set is derived from the collections that actually exist, and
every excluded question is recorded with the reason and the collection it
needed. That keeps "not_run" honest: it says *why*, not just *how many*.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

QUESTIONS = REPO_ROOT / "eval" / "phase0" / "baseline_questions.jsonl"

# Which (ticker, fiscal_year) collections each question needs to be answerable
# without triggering an ingest. Derived by reading the questions; `None` means
# the question needs no index at all (it should abstain or be out of scope).
NEEDS: Dict[str, Optional[List[Tuple[str, int]]]] = {
    "N1": [("AAPL", 2024)],
    "N2": [("MSFT", 2024)],
    "N3": [("JPM", 2024)],
    "N4": [("GOOGL", 2024)],
    "N5": [("AMZN", 2023)],
    "N6": [("BLK", 2024)],
    "C1": [("AAPL", 2024)],
    "C2": [("GOOGL", 2023), ("GOOGL", 2024)],
    "C3": [("GS", 2024)],
    "M1": [("JPM", 2024), ("BAC", 2024)],
    "M2": [("AMZN", 2023), ("AMZN", 2024), ("AMZN", 2025)],
    "M3": [("MSFT", 2024), ("GOOGL", 2024)],
    "R1": [("JPM", 2024)],
    "R2": [("MSFT", 2024)],
    "R3": [("AAPL", 2024)],
    "R4": [("WFC", 2024)],
    "T1": [("MSFT", 2024)],
    "T2": [("AAPL", 2025)],
    "T3": [("MSFT", 2023), ("MSFT", 2024)],
    "T4": [("GOOGL", 2024)],
    "U1": [("AAPL", 2024)],
    "U2": None,          # market data, out of scope: no index needed
    "U3": None,          # fictional company: resolution fails before retrieval
    "U4": [("BLK", 2024)],
    "S1": [("NFLX", 2024)],
}

_COLLECTION_RE = re.compile(r"^(?P<ticker>[A-Z][A-Z0-9.\-]*)_(?P<year>\d{4})$")


def existing_collections() -> Set[Tuple[str, int]]:
    from retrieval.vector_store import list_collections
    out: Set[Tuple[str, int]] = set()
    for name in list_collections():
        m = _COLLECTION_RE.match(name)
        if m:
            out.add((m.group("ticker"), int(m.group("year"))))
    return out


def plan(available: Set[Tuple[str, int]]) -> Dict:
    questions = [json.loads(line) for line in
                 QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    runnable, not_run = [], []
    for q in questions:
        qid = q["id"]
        needed = NEEDS.get(qid, [])
        if needed is None:
            runnable.append({"id": qid, "category": q["category"],
                             "needs": [], "reason": "no index required"})
            continue
        missing = [f"{t}_{y}" for t, y in needed if (t, y) not in available]
        if missing:
            not_run.append({
                "id": qid, "category": q["category"],
                "needs": [f"{t}_{y}" for t, y in needed],
                "missing_collections": missing,
                "reason": (
                    "collection not indexed; running this question would trigger "
                    "auto-ingest (download, parse, chunk, embed) inside the "
                    "measurement"
                ),
            })
        else:
            runnable.append({"id": qid, "category": q["category"],
                             "needs": [f"{t}_{y}" for t, y in needed],
                             "reason": "all required collections present"})
    return {"runnable": runnable, "not_run": not_run}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="reports/phase1/baseline_v1_local/plan.json")
    args = ap.parse_args(argv)

    try:
        available = existing_collections()
    except Exception as exc:
        print(f"cannot list collections: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    result = plan(available)
    print(f"indexed collections ({len(available)}): "
          f"{', '.join(sorted(f'{t}_{y}' for t, y in available)) or 'none'}\n")
    print(f"RUNNABLE ({len(result['runnable'])}):")
    for r in result["runnable"]:
        print(f"  {r['id']:3s} {r['category']:12s} {', '.join(r['needs']) or '-'}")
    print(f"\nNOT_RUN ({len(result['not_run'])}):")
    for r in result["not_run"]:
        print(f"  {r['id']:3s} {r['category']:12s} missing: "
              f"{', '.join(r['missing_collections'])}")

    ids = ",".join(r["id"] for r in result["runnable"])
    print(f"\n--only {ids}")

    payload = {
        "planned_at_utc": datetime.now(timezone.utc).isoformat(),
        "indexed_collections": sorted(f"{t}_{y}" for t, y in available),
        "runnable_ids": [r["id"] for r in result["runnable"]],
        "not_run_ids": [r["id"] for r in result["not_run"]],
        "runnable": result["runnable"],
        "not_run": result["not_run"],
        "constraint": (
            "v1 embeds at a measured ~1.63 s/chunk on this 8 GB machine "
            "(AAPL: 589 chunks in 957.8 s), so indexing all 35,838 chunks of "
            "the bundled 12 is a ~16 hour job. Questions whose collections are "
            "absent are excluded rather than allowed to trigger auto-ingest "
            "inside the measurement."
        ),
    }
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
