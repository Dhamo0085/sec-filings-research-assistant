#!/usr/bin/env python3
"""Score a run on the **frozen** D21 paired subset — the 69 items V0 was asked.

    .venv/bin/python scripts/score_d21_subset.py --runs-dir reports/phase4/runs_post_reindex

``eval/subsets.py`` derives the paired subset from the catalog's live
``collection_name``, which is the right behaviour: it cannot drift away from
what is actually indexed. P4-12 indexed BAC, IVZ, STT, TROW and WFC, so that
derived subset grew from 69 items to 79, and a "paired" column computed after
the re-index is no longer paired with V0 — V0 runs against the frozen
``data/qdrant_v1_backup`` (25 collections) and can only ever be asked the
original 69.

So this reads the 69 from V0's own recorded summary rather than recomputing
them, and reports every variant on exactly those items. Two different numbers
answer two different questions and both belong in the report:

* the **live** paired subset (79) — what the system can be asked today;
* the **frozen** D21 subset (69) — the only set on which V0 is a fair control.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parent.parent


def wilson(passed: int, n: int) -> tuple:
    """Wilson 95% interval — the same one eval/scorers.py reports."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    p = passed / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def frozen_ids(v0_summary: Path, gold_ids: Sequence[str]) -> Set[str]:
    """The 69 ids V0 was scored on: every gold id minus V0's recorded exclusions."""
    payload = json.loads(v0_summary.read_text())
    excluded = set(payload["paired_subset"]["excluded_ids"])
    return {i for i in gold_ids if i not in excluded}


def score(rows: List[dict], ids: Set[str]) -> dict:
    rows = [r for r in rows if r["item_id"] in ids]
    passed = sum(1 for r in rows if r["passed"] == "True")
    by_cat: Dict[str, dict] = {}
    for r in rows:
        cat = by_cat.setdefault(r["category"], {"n": 0, "passed": 0, "verdicts": {}})
        cat["n"] += 1
        cat["passed"] += r["passed"] == "True"
        cat["verdicts"][r["verdict"]] = cat["verdicts"].get(r["verdict"], 0) + 1
    for cat in by_cat.values():
        cat["rate"] = cat["passed"] / cat["n"] if cat["n"] else 0.0
        cat["ci95"] = wilson(cat["passed"], cat["n"])
    flags: Dict[str, int] = {}
    for r in rows:
        for flag in filter(None, (r["flags"] or "").split("|")):
            flags[flag] = flags.get(flag, 0) + 1
    return {"n": len(rows), "passed": passed,
            "rate": passed / len(rows) if rows else 0.0,
            "ci95": wilson(passed, len(rows)),
            "by_category": by_cat, "flags": flags,
            "missing_ids": sorted(ids - {r["item_id"] for r in rows})}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-dir", type=Path,
                    default=REPO_ROOT / "reports/phase4/runs_post_reindex")
    ap.add_argument("--v0-summary", type=Path,
                    default=REPO_ROOT / "reports/phase4/runs/V0_summary.json",
                    help="the pre-re-index V0 run, which defines the frozen 69")
    ap.add_argument("--variants", default="V1,V2,V3")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the table as JSON as well as printing it")
    args = ap.parse_args(argv)

    gold_path = REPO_ROOT / "eval/gold/gold_v1.jsonl"
    gold_ids = [json.loads(line)["id"]
                for line in gold_path.read_text().splitlines() if line.strip()]
    ids = frozen_ids(args.v0_summary, gold_ids)
    if len(ids) != 69:
        print(f"! the frozen subset is {len(ids)} items, not 69 — "
              f"check {args.v0_summary}")

    print(f"Frozen D21 paired subset: {len(ids)} of {len(gold_ids)} gold items\n")
    out: Dict[str, dict] = {"n_frozen": len(ids), "ids": sorted(ids), "variants": {}}

    # V0 first, read from its own run, so the table carries the control.
    sources = [("V0", args.v0_summary.parent / "V0_scored.csv")]
    sources += [(v, args.runs_dir / f"{v}_scored.csv")
                for v in args.variants.split(",") if v]

    print(f"{'variant':8} {'n':>4} {'passed':>7} {'rate':>7}  95% CI")
    for variant, path in sources:
        if not path.is_file():
            print(f"{variant:8} {'-':>4} {'not run':>7}")
            continue
        with path.open() as fh:
            rows = list(csv.DictReader(fh))
        result = score(rows, ids)
        out["variants"][variant] = result
        lo, hi = result["ci95"]
        print(f"{variant:8} {result['n']:>4} {result['passed']:>7} "
              f"{result['rate']:>6.1%}  {lo:.1%}–{hi:.1%}"
              + ("   ! missing: " + ", ".join(result["missing_ids"])
                 if result["missing_ids"] else ""))

    cats = sorted({c for v in out["variants"].values() for c in v["by_category"]})
    print(f"\n{'category':14}" + "".join(f"{v:>12}" for v, _ in sources))
    for cat in cats:
        line = f"{cat:14}"
        for variant, _ in sources:
            stats = out["variants"].get(variant, {}).get("by_category", {}).get(cat)
            cell = f"{stats['passed']}/{stats['n']}" if stats else "-"
            line += f"{cell:>12}"
        print(line)

    if args.out:
        args.out.write_text(json.dumps(out, indent=2) + "\n")
        print(f"\nwrote {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
