#!/usr/bin/env python3
"""Build the owner's narrative rating sheet from a V3 run (P4-14, T4-11).

    .venv/bin/python scripts/make_rating_sheet.py
    .venv/bin/python scripts/make_rating_sheet.py --run reports/phase4/runs_post_reindex/V3.jsonl

Fifteen narrative answers, each shown beside the passages the model was given,
so the owner can judge *supported* against the text rather than against memory
of the filing. The rubric is written into the first rows of the sheet.

**The passages are re-derived, and the sheet says so per row.** The run
transcript records which sections were cited but not the passage text, so each
sampled question is re-asked through the same pipeline with a retriever that
records what it returned. The answer comes back from the LLM cache, so this
costs no budget, and the recovered citation list is compared against the one
the run recorded: a row whose citations differ is flagged
``passages_unverified`` rather than quietly showing passages the rated answer
may not have been written from. Nothing is reconstructed by guesswork.

**Owner-only columns stay empty** (D29, CLAUDE.md rule 17). ``verdict``,
``issue`` and ``notes`` are the owner's to fill; this script never writes a
value into them, and every assisted field it does write is named ``assist_*``.

Deterministic: the sample is drawn by sorted item id with a recorded seed, and
the same run file produces a byte-identical sheet.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RUBRIC = [
    "RUBRIC — fill verdict, issue and notes. Leave every assist_* column alone.",
    "verdict=supported     every claim in the answer is stated or clearly implied "
    "by the cited passage text below.",
    "verdict=partially     some claims are supported; at least one is not.",
    "verdict=unsupported   the answer's main claim is not in the cited text.",
    "issue=none | wrong_section | missing_info | hallucination | other",
    "A passage column that reads PASSAGE NOT RECOVERED means the sheet could not "
    "show you what the model saw; rate that row only on what is shown, and say so "
    "in notes.",
    "A row flagged refused:<reason> is one the system DECLINED to answer. Leave "
    "verdict blank for it — supported/partially/unsupported do not apply to a "
    "refusal — and use issue to say whether declining was right: issue=none if "
    "the filing really does not support an answer, issue=missing_info if it does "
    "and the system should have found it.",
    "A row flagged no_citation is different: the system ANSWERED without citing "
    "anything, which is a defect. Rate it unsupported.",
]

#: the owner signs these; nothing in this file ever writes to them (D29).
OWNER_COLUMNS = ("verdict", "issue", "notes")

COLUMNS = (
    "item_id", "question", "as_of", "answer",
    "citation_index", "cited_ticker", "cited_fiscal_label", "cited_section",
    "cited_accession", "edgar_link", "assist_passage", "assist_passage_chars",
    "assist_flags", *OWNER_COLUMNS,
)


def edgar_link(cik: Optional[int], accession: Optional[str]) -> str:
    """A link to the filing index on EDGAR, which is where a human checks it."""
    if not cik or not accession:
        return ""
    plain = str(accession).replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{plain}/"
            f"{accession}-index.htm")


def citation_key(citation: dict) -> tuple:
    """What has to match for a recovered citation to be the recorded one."""
    return (citation.get("ticker"), citation.get("fiscal_label"),
            citation.get("section"), citation.get("accession"))


def sample_ids(rows: Sequence[dict], n: int, seed: int) -> List[str]:
    ids = sorted(row["item_id"] for row in rows)
    if len(ids) <= n:
        return ids
    return sorted(random.Random(seed).sample(ids, n))  # noqa: S311


def recover_passages(question: str, as_of: Optional[str]) -> tuple:
    """Re-ask one question, recording what retrieval handed the generator.

    Returns (citations, {citation_index: passage}). The LLM cache makes the
    generation free and the answer identical; if it were not identical the
    citation comparison in :func:`build_rows` would catch it.
    """
    import query as query_module
    from retrieval.retriever import retrieve as real_retrieve

    captured: List = []

    def recording_retrieve(**kwargs):
        chunks = real_retrieve(**kwargs)
        captured.extend(chunks)
        return chunks

    deps = query_module.Deps(retriever=recording_retrieve)
    outcome = query_module.ask(question, as_of=as_of, deps=deps)
    payload = outcome.to_dict() if hasattr(outcome, "to_dict") else dict(outcome)

    # Citations are 1..n in the order the answer may refer to them (G4), and
    # the retriever returns chunks in that same ranked order, so index i names
    # chunk i-1. The comparison against the recorded citations is what makes
    # that safe to rely on rather than merely likely.
    passages: Dict[int, str] = {}
    for position, chunk in enumerate(captured, start=1):
        text = getattr(chunk.chunk, "text", "") or ""
        passages[position] = text
    return payload.get("citations", []), passages


def build_rows(run_rows: Sequence[dict], ids: Sequence[str], *,
               passage_chars: int, recover: bool) -> List[dict]:
    by_id = {row["item_id"]: row for row in run_rows}
    out: List[dict] = []
    for item_id in ids:
        row = by_id[item_id]
        outcome = row["outcome"]
        recorded = outcome.get("citations") or []
        answer = outcome.get("answer") or ""

        flags: List[str] = []
        status = outcome.get("status")
        if status == "abstained":
            # A refusal legitimately has no citation: it is a decision, not a
            # defect, and it is not rated supported/partially/unsupported.
            # Distinguishing it from an uncited ANSWER matters — one is the
            # system working, the other is the failure T4-11 looks for.
            flags.append(f"refused:{outcome.get('abstain_reason')}")
        elif not recorded:
            # T4-11: an answer with no citation cannot be rated against text.
            flags.append("no_citation")
        if status == "error":
            flags.append(f"run_error:{outcome.get('error_code')}")

        passages: Dict[int, str] = {}
        if recover and recorded:
            try:
                live_citations, passages = recover_passages(row["question"],
                                                            row.get("as_of"))
            except Exception as exc:                     # reported, not hidden
                flags.append(f"recover_failed:{type(exc).__name__}")
                live_citations = []
            if [citation_key(c) for c in live_citations] != \
                    [citation_key(c) for c in recorded]:
                flags.append("passages_unverified")
                passages = {}

        if not recorded:
            out.append({
                "item_id": item_id, "question": row["question"],
                "as_of": row.get("as_of") or "", "answer": answer,
                "citation_index": "", "cited_ticker": "",
                "cited_fiscal_label": "", "cited_section": "",
                "cited_accession": "", "edgar_link": "",
                "assist_passage": "PASSAGE NOT RECOVERED",
                "assist_passage_chars": 0,
                "assist_flags": "|".join(flags),
                **dict.fromkeys(OWNER_COLUMNS, ""),
            })
            continue

        for citation in recorded:
            index = int(citation.get("index") or 0)
            passage = passages.get(index, "")
            out.append({
                "item_id": item_id,
                "question": row["question"],
                "as_of": row.get("as_of") or "",
                "answer": answer,
                "citation_index": index,
                "cited_ticker": citation.get("ticker") or "",
                "cited_fiscal_label": citation.get("fiscal_label") or "",
                "cited_section": citation.get("section") or "",
                "cited_accession": citation.get("accession") or "",
                "edgar_link": edgar_link(citation.get("cik"),
                                         citation.get("accession")),
                "assist_passage": (passage[:passage_chars]
                                   if passage else "PASSAGE NOT RECOVERED"),
                "assist_passage_chars": len(passage[:passage_chars]) if passage else 0,
                "assist_flags": "|".join(flags),
                **dict.fromkeys(OWNER_COLUMNS, ""),
            })
    return out


def _rel(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise — a path outside the
    repo is a legitimate --out (a scratch dry run), not an error."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def write_sheet(path: Path, rows: Sequence[dict], header_notes: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for note in header_notes:
            writer.writerow([note])
        writer.writerow([])
        writer.writerow(list(COLUMNS))
        for row in rows:
            writer.writerow([row[column] for column in COLUMNS])


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path,
                    default=REPO_ROOT / "reports/phase4/runs_post_reindex/V3.jsonl")
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "reports/phase4/narrative_rating_sheet.csv")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261003)
    ap.add_argument("--passage-chars", type=int, default=1500)
    ap.add_argument("--no-recover", action="store_true",
                    help="skip re-asking; every passage reads PASSAGE NOT RECOVERED")
    args = ap.parse_args(argv)

    run_rows = [json.loads(line) for line in args.run.read_text().splitlines()
                if line.strip()]
    narrative = [row for row in run_rows if row.get("category") == "narrative"]
    if not narrative:
        print(f"no narrative rows in {args.run}")
        return 1

    ids = sample_ids(narrative, args.n, args.seed)
    rows = build_rows(narrative, ids, passage_chars=args.passage_chars,
                      recover=not args.no_recover)

    notes = [
        *RUBRIC,
        f"source run: {_rel(args.run)}",
        f"sample: {len(ids)} of {len(narrative)} narrative answers, "
        f"seed {args.seed}, drawn by sorted item id",
        "passages were re-derived by re-asking each question through the same "
        "pipeline; a row flagged passages_unverified had a different citation "
        "list the second time and its passages are withheld",
    ]
    write_sheet(args.out, rows, notes)

    flagged = sorted({row["item_id"] for row in rows if row["assist_flags"]})
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()[:16]
    print(f"{len(ids)} question(s), {len(rows)} row(s) → {_rel(args.out)}")
    print(f"  passages recovered: "
          f"{sum(1 for r in rows if r['assist_passage_chars'])} of {len(rows)} rows")
    print(f"  flagged items: {flagged or 'none'}")
    print(f"  owner columns {OWNER_COLUMNS} written empty (D29)")
    print(f"  sha256[:16] {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
