#!/usr/bin/env python3
"""Compare two ``section_audit.json`` runs, pair by pair (P4-00, T4-06).

Why this exists
---------------
P4-00 changed section-boundary selection, and D25 asks for the corpus effect in
a specific shape: how many (filing, section) pairs became usable, how many
stopped being usable, and *which ones*, so every regression can be listed and
explained one by one rather than summarised away. A total alone hides a change
that gains twenty pairs and loses twenty others.

Both inputs are produced by ``scripts/audit_sections.py``; this script does no
parsing and no measurement of its own, so the comparison cannot disagree with
the audit it reports on.

Reproduce the P4-00 numbers with:

    python scripts/reparse_corpus.py --out-dir /tmp/after
    python scripts/audit_sections.py --parsed-dir /tmp/after --out-dir /tmp/after_audit
    python scripts/compare_section_audits.py \\
        reports/phase4/section_audit_before.json /tmp/after_audit/section_audit.json

Only pairs present in BOTH runs are compared, and the count is printed, so a
run over a different corpus cannot silently produce a smaller denominator.

Negative control: ``--self-test`` builds two synthetic runs whose differences
are known by construction and asserts the script reports exactly those.

Exit codes: 0 no regressions · 1 at least one pair stopped being usable · 2
could not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

OK = "ok"

Key = Tuple[str, str]


def load_rows(path: Path) -> Dict[Key, dict]:
    """Index one audit run by (filing, section_id)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data["rows"] if isinstance(data, dict) else data
    return {(r["filing"], r["section_id"]): r for r in rows}


def compare(before: Dict[Key, dict], after: Dict[Key, dict]) -> dict:
    """Gains, losses and shifts over the pairs both runs contain."""
    shared = sorted(set(before) & set(after))

    gained: List[Tuple[Key, str]] = []
    lost: List[Tuple[Key, str, int, int]] = []
    shifted: List[Tuple[Key, str, str]] = []

    for key in shared:
        was, now = before[key]["verdict"], after[key]["verdict"]
        if was == now:
            continue
        if now == OK:
            gained.append((key, was))
        elif was == OK:
            lost.append((key, now, before[key]["chars"], after[key]["chars"]))
        else:
            shifted.append((key, was, now))

    per_section: Dict[str, Dict[str, int]] = {}
    for _filing, section_id in shared:
        per_section.setdefault(section_id, {"before": 0, "after": 0})
    for key in shared:
        if before[key]["verdict"] == OK:
            per_section[key[1]]["before"] += 1
        if after[key]["verdict"] == OK:
            per_section[key[1]]["after"] += 1

    return {
        "pairs_compared": len(shared),
        "filings_compared": len({f for f, _ in shared}),
        "only_in_before": sorted(set(before) - set(after)),
        "only_in_after": sorted(set(after) - set(before)),
        "ok_before": sum(1 for k in shared if before[k]["verdict"] == OK),
        "ok_after": sum(1 for k in shared if after[k]["verdict"] == OK),
        "gained": gained,
        "lost": lost,
        "shifted": shifted,
        "per_section": per_section,
    }


def print_report(result: dict) -> None:
    print(
        f"compared {result['pairs_compared']} pairs over "
        f"{result['filings_compared']} filings"
    )
    for label in ("only_in_before", "only_in_after"):
        extra = result[label]
        if extra:
            print(f"  NOTE {len(extra)} pairs {label.replace('_', ' ')}, not compared")
    print(f"  usable: {result['ok_before']} -> {result['ok_after']} "
          f"({result['ok_after'] - result['ok_before']:+d})")

    print(f"\ngained ({len(result['gained'])}):")
    for (filing, section_id), was in result["gained"]:
        print(f"   {filing:12s} {section_id:22s} {was} -> ok")

    print(f"\nlost ({len(result['lost'])}):")
    for (filing, section_id), now, was_chars, now_chars in result["lost"]:
        print(f"   {filing:12s} {section_id:22s} ok -> {now:10s} "
              f"{was_chars:>9,d} -> {now_chars:>9,d} chars")

    shifts = Counter(f"{was} -> {now}" for _k, was, now in result["shifted"])
    print(f"\nshifted, neither side usable ({len(result['shifted'])}):")
    for change, count in sorted(shifts.items()):
        print(f"   {count:4d}  {change}")

    print(f"\n{'section':24s} {'before':>8s} {'after':>8s} {'delta':>7s}")
    for section_id, counts in sorted(result["per_section"].items()):
        delta = counts["after"] - counts["before"]
        print(f"{section_id:24s} {counts['before']:8d} {counts['after']:8d} {delta:+7d}")


def _row(filing: str, section_id: str, verdict: str, chars: int) -> dict:
    return {"filing": filing, "section_id": section_id, "verdict": verdict, "chars": chars}


def self_test() -> int:
    """Known differences, asserted exactly (CLAUDE.md rule 15)."""
    before = {
        ("A_2024", "item_1_business"): _row("A_2024", "item_1_business", "missing", 0),
        ("A_2024", "item_7_mda"): _row("A_2024", "item_7_mda", "ok", 5_000),
        ("A_2024", "item_3_legal"): _row("A_2024", "item_3_legal", "missing", 0),
        ("A_2024", "fs_notes"): _row("A_2024", "fs_notes", "ok", 5_000),
        ("B_2024", "item_1_business"): _row("B_2024", "item_1_business", "ok", 5_000),
    }
    after = {
        # one gain, one loss, one shift, one unchanged-ok, one unchanged-ok
        ("A_2024", "item_1_business"): _row("A_2024", "item_1_business", "ok", 9_000),
        ("A_2024", "item_7_mda"): _row("A_2024", "item_7_mda", "too_large", 900_000),
        ("A_2024", "item_3_legal"): _row("A_2024", "item_3_legal", "too_small", 80),
        ("A_2024", "fs_notes"): _row("A_2024", "fs_notes", "ok", 5_000),
        ("B_2024", "item_1_business"): _row("B_2024", "item_1_business", "ok", 5_000),
        # present only in the "after" run: must not be compared
        ("C_2024", "item_1_business"): _row("C_2024", "item_1_business", "ok", 5_000),
    }
    result = compare(before, after)

    failures: List[str] = []

    def check(name: str, got, want) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    check("pairs_compared", result["pairs_compared"], 5)
    check("ok_before", result["ok_before"], 3)
    check("ok_after", result["ok_after"], 3)
    check("gained", [k for k, _ in result["gained"]], [("A_2024", "item_1_business")])
    check("lost", [k for k, *_ in result["lost"]], [("A_2024", "item_7_mda")])
    check("shifted", [k for k, *_ in result["shifted"]], [("A_2024", "item_3_legal")])
    check("only_in_after", result["only_in_after"], [("C_2024", "item_1_business")])
    check("per_section item_1_business",
          result["per_section"]["item_1_business"], {"before": 1, "after": 2})
    # The totals are equal here on purpose: a net-zero change that moves four
    # pairs is exactly what a summary-only report would hide.
    check("net zero is still reported",
          (len(result["gained"]), len(result["lost"])), (1, 1))

    if failures:
        print("self-test FAILED:")
        for line in failures:
            print(f"  {line}")
        return 1
    print("self-test passed: gains, losses, shifts and uncompared pairs all reported")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("before", nargs="?", type=Path, help="section_audit.json from the earlier run")
    ap.add_argument("after", nargs="?", type=Path, help="section_audit.json from the later run")
    ap.add_argument("--json", type=Path, help="also write the comparison here")
    ap.add_argument("--self-test", action="store_true", help="run the negative control and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.before or not args.after:
        ap.error("both BEFORE and AFTER are required unless --self-test is given")

    for path in (args.before, args.after):
        if not path.is_file():
            print(f"error: {path} is not a file", file=sys.stderr)
            return 2

    result = compare(load_rows(args.before), load_rows(args.after))
    print_report(result)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, indent=2, default=list) + "\n", encoding="utf-8"
        )
        print(f"\nwrote {args.json}")

    return 1 if result["lost"] else 0


if __name__ == "__main__":
    sys.exit(main())
