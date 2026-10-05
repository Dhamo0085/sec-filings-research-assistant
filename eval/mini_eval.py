#!/usr/bin/env python3
"""The offline mini-evaluation CI runs on every push (P4-08, T4-04).

    python -m eval.mini_eval
    python -m eval.mini_eval --verbose

What it is
----------
A subset of the real gold set, answered by the real pipeline, against the
committed iXBRL fixtures in ``tests/fixtures/ixbrl/`` — never ``data/``, which
is gitignored and does not exist in CI. The facts path makes **no model call**
(measured in P3-11: all 30 smoke questions routed with zero LLM calls), so this
is a genuine end-to-end check of routing, resolution, calculation, the
abstention gate and point-in-time scope, with no network and no budget.

Two thresholds, both from the spec's P4-08:

* **the numeric and computed subset must be 100%.** These are the answers the
  system is allowed to state as fact. A regression here is not a quality
  slide, it is a wrong number with a citation attached.
* **zero look-ahead violations.** G2 is a guarantee, not a metric, so the
  threshold is the only one a guarantee can have.

Why thresholds and not a golden file: a golden file records what the system
does, and would be updated by whoever broke it. These two say what it must do.

Exit codes: 0 both thresholds met · 1 a threshold missed · 2 could not run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"

#: Categories whose answers are stated as fact, and so must be perfect.
EXACT_CATEGORIES = ("numeric", "computed")


def fixture_filings() -> Set[Tuple[str, int]]:
    from tests.fixture_world import FIXTURE_FILINGS

    return {(spec["ticker"], spec["fiscal_label"])
            for spec in FIXTURE_FILINGS.values()}


def selectable(item, available: Set[Tuple[str, int]]) -> bool:
    """Can this item be answered from the fixture corpus alone?

    Every filing the item names has to be a fixture. A compare item needs both
    companies and a trend needs every year, so a partial match is excluded
    rather than scored against a corpus that cannot answer it — the mini-eval's
    job is to catch regressions, not to re-measure coverage.
    """
    expected = item.get("expected") or {}
    values = expected.get("values")
    if values:
        return all(
            (str(v.get("ticker")), int(v.get("fiscal_label"))) in available
            for v in values
        )
    ticker, label = expected.get("ticker"), expected.get("fiscal_label")
    if not ticker or label is None:
        return False
    return (str(ticker), int(label)) in available


def select(gold: Sequence, available: Set[Tuple[str, int]]) -> List:
    """The facts-path items the fixtures can answer.

    Narrative items are excluded: they need a text index, which the fixtures do
    not build, and they would need a model, which CI must not call.
    """
    return [
        item for item in gold
        if item["expected"]["type"] in ("numeric", "computed", "multi", "abstain")
        and selectable(item, available)
    ]


def run(verbose: bool = False) -> Dict:
    import tempfile

    from eval.gold.schema import read_jsonl
    from eval.scorers import OutcomeView, score_item, summarize
    from llm.fake import FakeLLM
    from query import Deps, ask
    from tests.fixture_world import TODAY, build_world

    gold = read_jsonl(GOLD_PATH)
    available = fixture_filings()
    items = select(gold, available)
    if not items:
        raise RuntimeError(
            "no gold item can be answered from the committed fixtures; either "
            "the fixtures or the gold set moved and this check is now vacuous"
        )

    with tempfile.TemporaryDirectory() as tmp:
        world = build_world(Path(tmp), collections=True)
        # A fixed "today" so "is fiscal 2030 in the future" cannot change its
        # answer with the calendar, and FakeLLM so a model call would be an
        # obvious error rather than a network request.
        world.resolver._today = TODAY

        scored = []
        for item in items:
            deps = Deps(llm=FakeLLM(), catalog=world.catalog,
                        resolver=world.resolver)
            outcome = ask(item["question"], as_of=item.get("as_of"), deps=deps)
            result = score_item(item, OutcomeView.from_outcome(outcome))
            scored.append(result)
            if verbose:
                print(f"  {result.verdict:22s} {item['id']:38s} {result.detail[:70]}")

    summary = summarize(scored)
    exact = [s for s in scored if s.category in EXACT_CATEGORIES]
    look_ahead = sum(1 for s in scored if "look_ahead" in s.flags)
    return {
        "n": len(scored),
        "summary": summary,
        "exact_n": len(exact),
        "exact_passed": sum(1 for s in exact if s.passed),
        "look_ahead_violations": look_ahead,
        "failures": [s for s in scored if not s.passed],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    try:
        result = run(verbose=args.verbose)
    except Exception as exc:
        print(f"error: could not run the mini-eval: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"\nmini-eval: {result['n']} items from the committed fixtures")
    for category, bucket in sorted(result["summary"]["by_category"].items()):
        print(f"  {category:14s} {bucket['passed']}/{bucket['n']}")

    ok = True
    exact_n, exact_passed = result["exact_n"], result["exact_passed"]
    if exact_n == 0:
        print("\nFAIL: no numeric or computed items ran; the threshold is vacuous",
              file=sys.stderr)
        ok = False
    elif exact_passed != exact_n:
        print(f"\nFAIL: numeric + computed {exact_passed}/{exact_n}, "
              f"threshold is {exact_n}/{exact_n}", file=sys.stderr)
        ok = False
    else:
        print(f"\nOK   numeric + computed {exact_passed}/{exact_n}")

    if result["look_ahead_violations"]:
        print(f"FAIL: {result['look_ahead_violations']} look-ahead violation(s), "
              f"threshold is 0", file=sys.stderr)
        ok = False
    else:
        print("OK   look-ahead violations 0")

    if result["failures"]:
        print("\nfailures:", file=sys.stderr)
        for failure in result["failures"]:
            print(f"  {failure.item_id}: {failure.verdict} — {failure.detail}",
                  file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
