#!/usr/bin/env python3
"""Phase 3 smoke evaluation: 30 questions against the real stack (P3-11).

    python eval/phase3/smoke.py                    # run, resuming
    python eval/phase3/smoke.py --only S15,S19     # a subset
    python eval/phase3/smoke.py --dry-run          # route only, no LLM

This is **not** the Phase 4 evaluation. There are no accuracy metrics here
and nothing is scored against an oracle: P4-03 builds the scorers and P4-01
the gold set. The question this answers is narrower and is the P3 exit
criterion — does the assembled pipeline run end to end on real data without
crashing, and does each question come back with the *kind* of outcome it
should? A question whose status is wrong is a routing or coverage defect
worth finding now; a question whose number is wrong is Phase 4's business.

So each row records the status, the reason, the citations and the latency,
and is compared only against an expected status (and, where it is the point
of the case, an expected abstain reason). Transcripts go to
``reports/phase3/smoke/`` as JSONL plus a Markdown table.

Budget (CLAUDE.md rule 7): the facts path makes no LLM call at all, so only
the narrative questions cost quota — about 8 of the 30. The run is
**resumable**: finished rows are kept and skipped, so hitting a rate limit
means re-running later rather than starting over, and the remaining rows are
recorded as ``not_run`` instead of silently missing.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

QUESTIONS = Path(__file__).parent / "questions.jsonl"
OUT_DIR = REPO_ROOT / "reports" / "phase3" / "smoke"


def load_questions() -> List[dict]:
    return [json.loads(line) for line in
            QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_done(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("status") != "not_run":
                rows[row["id"]] = row
    return rows


def verdict(case: dict, row: dict) -> str:
    """Did the outcome have the shape the case expects?

    "ok" means the status matches and, where the case names one, the abstain
    reason matches. Nothing here checks a figure — that is Phase 4.
    """
    if row["status"] == "not_run":
        return "not_run"
    if row["status"] == "routed":
        # --dry-run only decides the route, so there is no status to compare;
        # what it proves is that routing reached a decision without the model.
        return "routed" if not row.get("used_llm") else "routed_via_llm"
    if row["status"] != case["expect_status"]:
        return "WRONG_STATUS"
    expected_reason = case.get("expect_reason")
    if expected_reason and row.get("abstain_reason") != expected_reason:
        return "WRONG_REASON"
    expected_type = case.get("expect_query_type")
    if expected_type and row.get("query_type") != expected_type:
        return "WRONG_TYPE"
    return "ok"


def run_case(case: dict, *, dry_run: bool) -> dict:
    from query import Deps, ask
    from routing.router import route

    started = time.monotonic()
    if dry_run:
        deps = Deps()
        routed = route(case["question"], llm=None,
                       catalog_tickers=deps.get_catalog().tickers(),
                       as_of_override=case.get("as_of"))
        return {
            "id": case["id"], "category": case["category"],
            "question": case["question"], "as_of": case.get("as_of"),
            "status": "routed", "query_type": routed.intent.value,
            "path": routed.path.value, "used_llm": routed.used_llm,
            "abstain_reason": (routed.abstain_reason.value
                               if routed.abstain_reason else None),
            "latency_s": round(time.monotonic() - started, 3),
        }

    outcome = ask(case["question"], as_of=case.get("as_of"))
    return {
        "id": case["id"], "category": case["category"],
        "question": case["question"], "as_of": outcome.as_of,
        "status": outcome.status.value,
        "query_type": outcome.query_type.value,
        "abstain_reason": (outcome.abstain_reason.value
                           if outcome.abstain_reason else None),
        "error_code": outcome.error_code.value if outcome.error_code else None,
        "answer": outcome.answer,
        "definition_note": outcome.definition_note,
        "citations": [c.model_dump(mode="json", exclude_none=True)
                      for c in outcome.citations],
        "chunks_used": outcome.chunks_used,
        "latency_s": round(time.monotonic() - started, 3),
    }


def write_markdown(rows: Sequence[dict], cases: Dict[str, dict], path: Path) -> None:
    lines = [
        "# Phase 3 smoke evaluation (P3-11)",
        "",
        f"Run {datetime.now(timezone.utc).isoformat(timespec='seconds')} against "
        "the real stack (real catalog, real facts store, real Qdrant index, "
        "real LLM).",
        "",
        "Status shape only — no figure is scored here; that is Phase 4 (P4-03).",
        "",
        "| id | category | question | as_of | status | reason / type | verdict | s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        case = cases[row["id"]]
        detail = row.get("abstain_reason") or row.get("error_code") or row.get("query_type", "")
        question = row["question"].replace("|", "\\|")
        lines.append(
            f"| {row['id']} | {row['category']} | {question} | "
            f"{row.get('as_of') or '-'} | {row['status']} | {detail} | "
            f"{verdict(case, row)} | {row.get('latency_s', '')} |"
        )

    counts: Dict[str, int] = {}
    for row in rows:
        counts[verdict(cases[row["id"]], row)] = counts.get(
            verdict(cases[row["id"]], row), 0) + 1
    lines += ["", "## Verdicts", ""]
    for name, count in sorted(counts.items()):
        lines.append(f"- **{name}**: {count}")

    latencies = sorted(r["latency_s"] for r in rows if r.get("latency_s") is not None)
    if latencies:
        def pct(p: float) -> float:
            return latencies[min(len(latencies) - 1, int(len(latencies) * p))]
        lines += ["", "## Latency (seconds)", "",
                  f"- p50 {pct(0.5):.2f} · p95 {pct(0.95):.2f} · max {latencies[-1]:.2f}"]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="", help="comma-separated ids")
    parser.add_argument("--dry-run", action="store_true",
                        help="route only; makes no LLM call")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--fresh", action="store_true",
                        help="ignore finished rows and re-run everything")
    args = parser.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = args.out_dir / ("routes.jsonl" if args.dry_run else "transcripts.jsonl")
    markdown = args.out_dir / ("routes.md" if args.dry_run else "RESULTS.md")

    cases = {c["id"]: c for c in load_questions()}
    wanted = [i.strip() for i in args.only.split(",") if i.strip()] or list(cases)
    done = {} if args.fresh else load_done(jsonl)

    rows: List[dict] = []
    stopped = False
    for case_id in wanted:
        case = cases[case_id]
        if case_id in done:
            rows.append(done[case_id])
            print(f"  {case_id} (cached)")
            continue
        if stopped:
            rows.append({"id": case_id, "category": case["category"],
                         "question": case["question"], "status": "not_run",
                         "as_of": case.get("as_of")})
            continue
        try:
            row = run_case(case, dry_run=args.dry_run)
        except Exception as exc:                 # recorded, never swallowed
            row = {"id": case_id, "category": case["category"],
                   "question": case["question"], "status": "crash",
                   "error_code": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
        print(f"  {case_id:4s} {row['status']:20s} "
              f"{row.get('abstain_reason') or row.get('query_type') or '':24s} "
              f"{row.get('latency_s', '')}")
        # Budget stop (CLAUDE.md rule 7): mark the rest not_run and exit
        # cleanly rather than hammering a provider that is already refusing.
        if row.get("error_code") in ("llm_rate_limited",):
            print("  rate limit reached — remaining questions marked not_run")
            stopped = True

    with jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    write_markdown(rows, cases, markdown)

    verdicts: Dict[str, int] = {}
    for row in rows:
        verdicts[verdict(cases[row["id"]], row)] = verdicts.get(
            verdict(cases[row["id"]], row), 0) + 1
    print(f"\n{jsonl}\n{markdown}")
    print("verdicts: " + "  ".join(f"{k}={v}" for k, v in sorted(verdicts.items())))
    bad = sum(v for k, v in verdicts.items() if k not in ("ok", "routed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
