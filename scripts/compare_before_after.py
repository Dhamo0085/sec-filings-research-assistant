#!/usr/bin/env python3
"""Pre-fix versus post-fix, per variant and per item (P4-16, D30).

    .venv/bin/python scripts/compare_before_after.py

The owner asked for both sets of numbers side by side, not a replacement.
A headline that moved is only interesting if you can see which items moved
and in which direction, so this prints the per-category totals and then every
item whose verdict changed — including any that got worse, which is the half
a summary tends to lose.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent


def verdicts(path: Path) -> Dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open() as handle:
        return {r["item_id"]: r for r in csv.DictReader(handle)}


def totals(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", type=Path,
                    default=REPO_ROOT / "reports/phase4/runs_before_fix")
    ap.add_argument("--after", type=Path,
                    default=REPO_ROOT / "reports/phase4/runs_post_reindex")
    ap.add_argument("--variants", default="V1,V2,V3")
    args = ap.parse_args(argv)

    print(f"{'variant':9}{'before':>18}{'after':>18}{'Δ':>7}   flags before → after")
    for variant in args.variants.split(","):
        b = totals(args.before / f"{variant}_summary.json")
        a = totals(args.after / f"{variant}_summary.json")
        if not (b and a):
            print(f"{variant:9}{'(missing)':>18}")
            continue
        bs, as_ = b["scores"], a["scores"]
        delta = as_["passed"] - bs["passed"]
        print(f"{variant:9}"
              f"{bs['passed']:>7}/{bs['n']:<3}({bs['rate']:>5.1%})"
              f"{as_['passed']:>7}/{as_['n']:<3}({as_['rate']:>5.1%})"
              f"{delta:>+7}   {bs.get('flags') or '{}'} → {as_.get('flags') or '{}'}")

    for variant in args.variants.split(","):
        before = verdicts(args.before / f"{variant}_scored.csv")
        after = verdicts(args.after / f"{variant}_scored.csv")
        if not (before and after):
            continue
        changed = [i for i in sorted(set(before) & set(after))
                   if before[i]["verdict"] != after[i]["verdict"]]
        if not changed:
            print(f"\n{variant}: no item changed verdict.")
            continue
        gained = [i for i in changed
                  if after[i]["passed"] == "True" and before[i]["passed"] != "True"]
        lost = [i for i in changed
                if before[i]["passed"] == "True" and after[i]["passed"] != "True"]
        print(f"\n{variant}: {len(changed)} item(s) changed verdict "
              f"({len(gained)} now pass, {len(lost)} now fail)")
        for item_id in changed:
            mark = ("  +" if item_id in gained
                    else "  -" if item_id in lost else "   ")
            print(f"{mark} {item_id:34} {before[item_id]['verdict']:20}"
                  f" → {after[item_id]['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
