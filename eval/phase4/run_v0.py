#!/usr/bin/env python3
"""Run the gold set through v1's own pipeline — the V0 baseline (P4-05).

    python eval/phase4/run_v0.py --worktree <path to a v1-baseline checkout>

v1's entry point was rewritten in Phase 3, so V0 cannot run from this tree. It
runs from a git worktree at the ``v1-baseline`` tag, as a SUBPROCESS with that
worktree as its working directory: v1 and v2 share module names (``query``,
``config``, ``retrieval``), so importing both into one interpreter would resolve
to whichever came first on the path and quietly measure the wrong system.

What this script is, therefore, is two things: a parent that prepares the
environment and a child (``_CHILD``) that runs inside the worktree and writes
rows in ``eval/runner.py``'s JSONL shape, so V0 is scored by exactly the same
scorers as V1 to V3.

Four things are set for the child, each of which would otherwise make V0
measure something other than v1:

``QDRANT_PATH``   the frozen pre-rewrite index, ``data/qdrant_v1_backup``.
                  P4-00 changed what a chunk contains, and V0 has to be v1's
                  behaviour on v1's own index (D25, T4-07).
``DATA_DIR`` etc. the main repository's ``data/``, so the child reads the
                  parsed sections and chunks v1 was built on rather than the
                  empty ``data/`` of a fresh worktree.
``ROUTING_MODEL`` / ``GENERATION_MODEL`` — the gpt-oss substitutes. v1's own
                  defaults (``llama-3.1-8b-instant``, ``llama-3.3-70b-versatile``)
                  are Enterprise-only on this key and returned 404 to every
                  request (Phase 1, D1-04b), so running v1 unmodified would
                  measure an outage. The same substitution the Phase 1 baseline
                  used, named here so the variant is reproducible.
``_V0_NO_INGEST`` neutralises v1's on-demand ingestion. v1 fetches and indexes
                  any company it cannot find, which during an evaluation would
                  rewrite the corpus mid-measurement and make the run
                  irreproducible — and would reach the network from a scorer.
                  V0 is therefore measured on the indexed corpus only, which is
                  the D21 paired subset the other variants are reported on.

The substitution and the disabled ingestion are both deviations from "v1 as it
shipped" and both are stated in the report; without them V0 would be a
measurement of a 404 and of an accidental download, not of v1.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"
RUNS_DIR = REPO_ROOT / "reports" / "phase4" / "runs"
BACKUP_INDEX = REPO_ROOT / "data" / "qdrant_v1_backup"

ROUTING_MODEL = "openai/gpt-oss-20b"
GENERATION_MODEL = "openai/gpt-oss-120b"

#: Runs inside the worktree. Kept as a string rather than a file in the
#: worktree so the tag's checkout stays exactly the tag.
_CHILD = r'''
import json, os, sys, time, traceback

items = json.loads(sys.stdin.read())

# Neutralise v1's on-demand ingestion before anything imports it. Not a source
# edit: the module is patched at runtime, the way eval/phase0/run_baseline.py
# instrumented this same pipeline.
#
# The replacement keeps v1's CONTRACT exactly — return (company_name,
# fiscal_year) when the filing is available, raise YearNotAvailable when the
# ticker is known but that year is not, return None when nothing is there — and
# only removes the fetch. A stub that returned a bare True instead produced
# "TypeError: cannot unpack non-iterable bool object" on the first three items,
# which is the right kind of failure: a shim that does not match the contract
# should break loudly, not quietly answer from the wrong year.
import routing.resolver as R
from retrieval.vector_store import list_collections

def _indexed_years(ticker):
    years = []
    for name in list_collections():
        base, _, year = name.rpartition("_")
        if base.upper() == ticker.upper() and year.isdigit():
            years.append(int(year))
    return sorted(years)

def _ensure_no_fetch(ticker, company_name, target_year=None):
    years = _indexed_years(ticker)
    if not years:
        return None
    if target_year is None:
        return (company_name, years[-1])
    if target_year in years:
        return (company_name, target_year)
    raise R.YearNotAvailable(ticker, target_year, years) if hasattr(
        R, "YearNotAvailable") else ValueError(
        f"{ticker} FY{target_year} not indexed")

_patched = []
if hasattr(R, "ensure_ticker_indexed"):
    R.ensure_ticker_indexed = _ensure_no_fetch
    _patched.append("ensure_ticker_indexed")
print(json.dumps({"_meta": {"patched": _patched}}), flush=True)

import query as Q

for item in items:
    question = item["question"]
    started = time.monotonic()
    row = {
        "item_id": item["id"], "variant": "V0", "category": item["category"],
        "question": question, "as_of": item.get("as_of"),
        "llm": {"calls": [], "by_role": {}, "total_calls": 0, "total_tokens": 0},
    }
    try:
        result = Q.ask(question)
        citations = []
        for index, citation in enumerate(getattr(result, "citations", []) or [], start=1):
            if isinstance(citation, dict):
                data = dict(citation)
            else:
                data = {k: getattr(citation, k, None)
                        for k in ("ticker", "fiscal_year", "section", "score",
                                  "accession", "filing_date")}
            citations.append({
                "kind": "text",
                "index": index,
                "ticker": str(data.get("ticker") or ""),
                "accession": str(data.get("accession") or ""),
                "filing_date": str(data.get("filing_date") or ""),
                "fiscal_label": data.get("fiscal_year") or data.get("fiscal_label") or 0,
                "section": data.get("section") or data.get("section_name") or "",
                "score": data.get("score"),
            })
        row["outcome"] = {
            # v1 has no status taxonomy at all — every return is an answer.
            # That is the baseline's defining property, not a mapping choice:
            # it cannot abstain, so it cannot abstain correctly.
            "status": "answered_text",
            "answer": getattr(result, "answer", "") or "",
            "citations": citations,
            "as_of": None,
            "query_type": getattr(result, "query_type", None),
        }
    except Exception as exc:
        row["outcome"] = None
        row["runner_error"] = f"{type(exc).__name__}: {exc}"
        row["traceback"] = traceback.format_exc()[-1500:]
    row["latency_s"] = round(time.monotonic() - started, 4)
    print(json.dumps(row, ensure_ascii=False), flush=True)
'''


def child_environment(worktree: Path) -> dict:
    env = dict(os.environ)
    env.update({
        "QDRANT_PATH": str(BACKUP_INDEX),
        "DATA_DIR": str(REPO_ROOT / "data"),
        "PARSED_DIR": str(REPO_ROOT / "data" / "parsed"),
        "CHUNKS_DIR": str(REPO_ROOT / "data" / "chunks"),
        "RAW_DIR": str(REPO_ROOT / "data" / "raw"),
        "ROUTING_MODEL": ROUTING_MODEL,
        "GENERATION_MODEL": GENERATION_MODEL,
        "PYTHONPATH": str(worktree),
    })
    return env


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--worktree", type=Path, required=True)
    ap.add_argument("--gold", type=Path, default=GOLD_PATH)
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

    from catalog.store import CatalogStore
    from config import settings
    from eval.gold.schema import read_jsonl
    from eval.runner import append_row, load_existing, run_path
    from eval.subsets import indexed_pairs, split

    if not (args.worktree / "query.py").is_file():
        print(f"error: {args.worktree} is not a v1 checkout", file=sys.stderr)
        return 2
    if not BACKUP_INDEX.is_dir():
        print(f"error: {BACKUP_INDEX} does not exist; P4-00 step 1 makes it",
              file=sys.stderr)
        return 2

    env_file = args.worktree / ".env"
    if not env_file.exists():
        # A symlink, never a copy: the key is never read by this script and
        # never lands anywhere new on disk.
        env_file.symlink_to(REPO_ROOT / ".env")
        print(f"linked {env_file} -> <repo>/.env")

    catalog = CatalogStore(Path(settings.data_dir) / "derived" / "catalog.sqlite")
    gold = read_jsonl(args.gold)
    # D21: V0 covers the items whose corpus is indexed, and reports that N.
    paired, excluded = split(gold, indexed_pairs(catalog))
    if args.limit:
        paired = paired[: args.limit]

    path = run_path("V0", args.runs_dir)
    done = load_existing(path)
    todo = [item for item in paired if str(item["id"]) not in done]

    print(f"V0 — v1 baseline, from {args.worktree}")
    print(f"  index:  {BACKUP_INDEX}")
    print(f"  models: routing={ROUTING_MODEL} generation={GENERATION_MODEL}")
    print(f"  gold:   {len(paired)} paired of {len(gold)} "
          f"({len(excluded)} excluded: corpus not indexed)")
    print(f"  to run: {len(todo)} ({len(done)} already recorded)")
    if not todo:
        return 0

    payload = json.dumps([
        {"id": item["id"], "category": item["category"],
         # v1 has no as_of parameter, so a point-in-time question can only
         # carry its date in the sentence.
         "question": (f"As of {item['as_of']}: {item['question']}"
                      if item.get("as_of") else item["question"]),
         "as_of": item.get("as_of")}
        for item in todo
    ])

    # S603: the interpreter is this process's own sys.executable by default and
    # the program is the _CHILD literal above — no shell, no untrusted input.
    process = subprocess.Popen(  # noqa: S603
        [args.python, "-c", _CHILD],
        cwd=str(args.worktree), env=child_environment(args.worktree),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(payload)
    process.stdin.close()

    written: List[str] = []
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            print(f"  child: {line[:160]}")
            continue
        if "_meta" in row:
            print(f"  patched in child: {row['_meta']['patched']}")
            continue
        append_row(path, row)
        written.append(row["item_id"])
        status = (row.get("outcome") or {}).get("status") or row.get("runner_error", "?")
        print(f"  {row['item_id']:38s} {status} {row['latency_s']:.1f}s")

    code = process.wait()
    print(f"\nchild exited {code}; wrote {len(written)} rows to {path}")
    print("Score with: python -m eval.runner --variant V0 --report-only")
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
