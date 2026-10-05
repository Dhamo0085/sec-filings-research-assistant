"""Validate narrative retrieval items against the section audit (P4-13, T4-10).

A narrative item's whole expectation is the *section* an answer must come
from. If that section is not one the parser recovered for that exact filing,
the item is not a hard question — it is an unanswerable one, and every arm of
the ablation scores zero on it equally. A set carrying such items would push
``section_hit@k`` down uniformly and make D27's thresholds, which compare two
arms, harder to clear for reasons that have nothing to do with the reranker.

So each item is checked against the live audit:

* the filing must be in the audit at all (an item naming a filing the corpus
  does not hold cannot be scored);
* **every** expected section must be ``ok`` for that filing — not merely
  present. ``too_small``, ``too_large`` and ``missing`` all mean the slice is
  not the section it claims to be (D23), and an item resting on one would be
  scoring retrieval against a boundary defect;
* the item must be in the narrative category and name at least one section.

Problems are returned, not raised, so one pass lists everything wrong with a
drafted file instead of stopping at the first bad item.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

#: the audit verdict that means "this slice is the section it claims to be"
USABLE = "ok"


def load_audit(path: Path) -> Dict[Tuple[str, int, str], str]:
    """(ticker, fiscal_year, section_id) → verdict, from a section audit JSON."""
    payload = json.loads(Path(path).read_text())
    return {(row["ticker"], int(row["fiscal_year"]), row["section_id"]): row["verdict"]
            for row in payload["rows"]}


def audited_filings(audit: Mapping[Tuple[str, int, str], str]) -> set:
    return {(ticker, year) for (ticker, year, _section) in audit}


def validate_item(item: Mapping,
                  audit: Mapping[Tuple[str, int, str], str]) -> List[str]:
    """Everything wrong with one item, as sentences naming the item's id."""
    problems: List[str] = []
    item_id = item.get("id", "<no id>")

    if item.get("category") != "narrative":
        problems.append(f"{item_id}: category is {item.get('category')!r}, "
                        f"not 'narrative'")
    expected = item.get("expected") or {}
    if expected.get("type") != "text":
        problems.append(f"{item_id}: expected.type is {expected.get('type')!r}, "
                        f"not 'text'")
    sections = list(expected.get("expected_sections") or ())
    if not sections:
        problems.append(f"{item_id}: names no expected section, so there is "
                        f"nothing for section_hit@k to be true of")
    ticker = expected.get("ticker")
    year = expected.get("fiscal_label")
    if not ticker or year is None:
        problems.append(f"{item_id}: does not name a (ticker, fiscal_label)")
        return problems

    year = int(year)
    if (ticker, year) not in audited_filings(audit):
        problems.append(f"{item_id}: {ticker} FY{year} is not in the section "
                        f"audit, so the item cannot be scored")
        return problems

    for section in sections:
        verdict = audit.get((ticker, year, section))
        if verdict is None:
            problems.append(f"{item_id}: section {section!r} is not audited for "
                            f"{ticker} FY{year}")
        elif verdict != USABLE:
            problems.append(f"{item_id}: section {section!r} is {verdict!r} in "
                            f"{ticker} FY{year}, not {USABLE!r}")
    return problems


def validate(items: Iterable[Mapping],
             audit: Mapping[Tuple[str, int, str], str]) -> List[str]:
    """Every problem across a set, plus the duplicate-id check.

    Duplicate ids matter here for the same reason they matter in the gold
    schema: results are joined to items by id, so a repeat silently drops one
    item's score instead of changing a count.
    """
    items = list(items)
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for item in items:
        seen[item.get("id", "<no id>")] = seen.get(item.get("id", "<no id>"), 0) + 1
        problems.extend(validate_item(item, audit))
    problems.extend(f"{item_id}: appears {n} times" for item_id, n in
                    sorted(seen.items()) if n > 1)
    return problems


def read_items(path: Path) -> List[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines()
            if line.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    repo_root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", type=Path,
                    default=repo_root / "eval/gold/narrative_retrieval_v1.jsonl")
    ap.add_argument("--audit", type=Path,
                    default=repo_root / "reports/phase4/section_audit_live/section_audit.json")
    ap.add_argument("--min-items", type=int, default=40,
                    help="D27 needs at least this many before the rule is applied")
    args = ap.parse_args(argv)

    items = read_items(args.items)
    audit = load_audit(args.audit)
    problems = validate(items, audit)

    for problem in problems:
        print(f"  ! {problem}")
    print(f"{len(items)} item(s) checked against {args.audit.name}: "
          f"{len(problems)} problem(s)")
    if len(items) < args.min_items:
        print(f"  ! {len(items)} items is below D27's minimum of {args.min_items}")
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
