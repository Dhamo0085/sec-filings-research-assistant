#!/usr/bin/env python3
"""Retrieval ablations: what each stage of the pipeline is worth (P4-06).

    python -m eval.ablations
    python -m eval.ablations --arms hybrid,dense,bm25 --k 5
    python -m eval.ablations --index data/qdrant_v1_backup --label v1_index

No generation tokens. Every arm calls ``retrieval.retriever.retrieve`` directly
and is scored on what it put in front of the model, never on what a model then
said about it. That is the point: a generated answer mixes retrieval quality
with the generator's, and a difference between two retrieval arms would be
buried in the model's variance — and would cost free-tier budget to observe.

Routing is done ONCE per question, with ``llm=None``, and the resulting focus,
tickers and years are reused by every arm. So the arms differ only in the
retrieval switches, which is what makes them comparable. The router needs no
model for these questions (P3-11 measured all 30 smoke questions routing with
zero LLM calls), and ``llm=None`` makes that structural rather than hopeful.

Three metrics, all computable without a model:

``section_hit@k``   for a narrative item: did any of the top k chunks come from
                    a section the gold item accepts? This is the metric spec
                    section 11 names, and it is the one the P4-00 boundary
                    rewrite should move.
``MRR``             the same question, scored by rank rather than by presence,
                    so an arm that puts the right section third is not credited
                    equally with one that puts it first.
``number_in_context`` for a numeric item: does the retrieved context actually
                    contain the expected figure, in any rendering the filing
                    might print it in? This is the ceiling on what the text
                    path could possibly answer correctly, independent of the
                    generator — and the number that says whether a text-path
                    failure is a retrieval problem or a reading problem.

``--index`` points the whole run at another Qdrant store, which is the
old-index-versus-new-index arm D25 asks for: the same questions against
``data/qdrant_v1_backup`` and against the rewritten index isolate the parser's
effect from every other change. Until the re-index of P4-00 step 4 happens,
both paths hold the v1 parse and the two runs are expected to agree — which is
itself worth recording, because an unexplained difference would mean the backup
is not what it claims to be.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"
DEFAULT_OUT = REPO_ROOT / "reports" / "phase4" / "ablations"


@dataclass(frozen=True)
class Arm:
    """One retrieval configuration, and why it is in the table."""

    id: str
    why: str
    mode: str = "hybrid"
    rerank: bool = True
    focus_boost: bool = True
    parent_context: bool = True

    def kwargs(self) -> Dict:
        return {
            "mode": self.mode,
            "enable_rerank": self.rerank,
            "enable_focus_boost": self.focus_boost,
            "enable_parent_context": self.parent_context,
        }


#: The arms spec section 11 names, built up one stage at a time so each row of
#: the table is the previous row plus exactly one thing.
ARMS: Tuple[Arm, ...] = (
    Arm("bm25", "lexical only — the floor, and the arm that should win on exact figures",
        mode="bm25", rerank=False, focus_boost=False),
    Arm("dense", "embeddings only — no lexical signal at all",
        mode="dense", rerank=False, focus_boost=False),
    Arm("hybrid", "dense + BM25 with RRF fusion, nothing after it",
        mode="hybrid", rerank=False, focus_boost=False),
    Arm("hybrid_rerank", "...plus the cross-encoder",
        mode="hybrid", rerank=True, focus_boost=False),
    Arm("hybrid_rerank_focus", "...plus the focus boost — the shipped default",
        mode="hybrid", rerank=True, focus_boost=True),
    Arm("hybrid_rerank_focus_nopar",
        "the default, with parent-section context off: does handing the model "
        "the whole section help, given that P3-00 found many section slices wrong?",
        mode="hybrid", rerank=True, focus_boost=True, parent_context=False),
)


# ── what counts as a hit ──────────────────────────────────────────────────────

def renderings(value: Decimal) -> List[str]:
    """How a figure might appear in filing text.

    A filing reporting in millions prints 391,035 for 391,035,000,000, and a
    parsed table may or may not keep the separators. All three forms are
    searched, because "the number is not in the context" is a claim that has to
    survive the filer's own formatting.
    """
    out: List[str] = []
    magnitude = abs(value)
    for scale in (0, 3, 6, 9):
        scaled = magnitude.scaleb(-scale)
        if scaled != scaled.to_integral_value():
            continue
        plain = format(scaled.to_integral_value(), "f")
        if len(plain) < 2:
            continue
        out.append(plain)
        try:
            out.append(f"{int(plain):,}")
        except ValueError:
            pass
    return sorted(set(out), key=len, reverse=True)


def number_in_context(value: Decimal, chunks: Sequence) -> bool:
    haystack = "\n".join(
        (getattr(c, "parent_text", "") or "") + "\n" + (getattr(c.chunk, "text", "") or "")
        for c in chunks
    )
    return any(form in haystack for form in renderings(value))


def cited_sections(chunks: Sequence) -> List[str]:
    return [str(getattr(c.chunk, "parent_id", "") or "") for c in chunks]


# ── running one arm ───────────────────────────────────────────────────────────

@dataclass
class ArmResult:
    arm: str
    narrative_n: int = 0
    narrative_hits: int = 0
    reciprocal_ranks: List[float] = field(default_factory=list)
    numeric_n: int = 0
    numeric_in_context: int = 0
    latency_s: float = 0.0
    errors: List[str] = field(default_factory=list)
    per_item: List[Dict] = field(default_factory=list)

    @property
    def scored(self) -> int:
        return self.narrative_n + self.numeric_n

    def summary(self) -> Dict:
        return {
            "arm": self.arm,
            "errors": len(self.errors),
            "narrative_n": self.narrative_n,
            "section_hit": self.narrative_hits,
            "section_hit_rate": (self.narrative_hits / self.narrative_n
                                 if self.narrative_n else 0.0),
            "mrr": (sum(self.reciprocal_ranks) / self.narrative_n
                    if self.narrative_n else 0.0),
            "numeric_n": self.numeric_n,
            "number_in_context": self.numeric_in_context,
            "number_in_context_rate": (self.numeric_in_context / self.numeric_n
                                       if self.numeric_n else 0.0),
            "latency_s_total": round(self.latency_s, 2),
        }


def _route_once(question: str, catalog_tickers: Sequence[str]):
    from routing.router import route

    # llm=None on purpose: the rules decide these questions, and a model here
    # would make the ablation cost budget and stop being reproducible.
    return route(question, llm=None, catalog_tickers=list(catalog_tickers))


def run_arm(arm: Arm, items: Sequence[Mapping], routes: Mapping[str, object],
            k: int) -> ArmResult:
    from eval.scorers import section_hit
    from retrieval.retriever import retrieve

    result = ArmResult(arm=arm.id)
    for item in items:
        route_ = routes.get(str(item["id"]))
        if route_ is None:
            continue
        expected = item["expected"]
        started = time.monotonic()
        try:
            chunks = retrieve(
                item["question"],
                tickers=list(route_.tickers),
                years=list(getattr(route_.period, "labels", ()) or []),
                top_k=k,
                focus=route_.focus,
                **arm.kwargs(),
            )
        except Exception as exc:
            result.errors.append(f"{item['id']}: {type(exc).__name__}: {exc}")
            result.per_item.append({"item_id": item["id"], "error": str(exc)})
            continue
        result.latency_s += time.monotonic() - started

        row: Dict = {"item_id": item["id"], "category": item["category"],
                     "n_chunks": len(chunks)}

        if expected["type"] == "text":
            result.narrative_n += 1
            hit, rank = section_hit(cited_sections(chunks),
                                    expected.get("expected_sections", ()), k=k)
            result.narrative_hits += int(hit)
            result.reciprocal_ranks.append(1.0 / rank if rank else 0.0)
            row.update({"hit": hit, "rank": rank,
                        "sections": cited_sections(chunks)[:k]})
        elif expected["type"] == "numeric":
            result.numeric_n += 1
            try:
                value = Decimal(str(expected["value"]))
            except InvalidOperation:
                continue
            found = number_in_context(value, chunks)
            result.numeric_in_context += int(found)
            row.update({"number_in_context": found})

        result.per_item.append(row)
    return result


def markdown(results: Sequence[ArmResult], arms: Sequence[Arm], label: str) -> str:
    by_id = {a.id: a for a in arms}
    lines = [
        f"### Retrieval ablations — {label}",
        "",
        "| arm | section hit@k | MRR | number in context | errors | retrieval s | what it adds |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        s = result.summary()
        arm = by_id.get(result.arm)
        lines.append(
            f"| `{s['arm']}` | {s['section_hit']}/{s['narrative_n']} "
            f"({s['section_hit_rate']:.0%}) | {s['mrr']:.3f} | "
            f"{s['number_in_context']}/{s['numeric_n']} "
            f"({s['number_in_context_rate']:.0%}) | {s['errors']} | "
            f"{s['latency_s_total']:.1f} | {arm.why if arm else ''} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gold", type=Path, default=GOLD_PATH)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--arms", default="",
                    help="comma-separated arm ids; default is all of them")
    ap.add_argument("--index", default="",
                    help="a Qdrant storage path to run against, e.g. "
                         "data/qdrant_v1_backup (D25's old-vs-new arm)")
    ap.add_argument("--label", default="current index")
    args = ap.parse_args(argv)

    # Set before anything imports settings, so the whole run reads one store.
    if args.index:
        os.environ["QDRANT_PATH"] = str((REPO_ROOT / args.index).resolve())

    from catalog.store import CatalogStore
    from config import settings
    from eval.gold.schema import read_jsonl
    from eval.subsets import indexed_pairs, split

    catalog = CatalogStore(Path(settings.data_dir) / "derived" / "catalog.sqlite")
    gold = read_jsonl(args.gold)
    pairs = indexed_pairs(catalog)
    items, excluded = split(gold, pairs)
    items = [i for i in items
             if i["expected"]["type"] in ("text", "numeric")]

    chosen = [a for a in ARMS
              if not args.arms or a.id in {s.strip() for s in args.arms.split(",")}]
    if not chosen:
        print(f"error: no arm matched {args.arms!r}", file=sys.stderr)
        return 2

    print(f"store:  {settings.qdrant_path}")
    print(f"items:  {len(items)} retrievable of {len(gold)} gold "
          f"({len(excluded)} excluded: corpus not indexed)")
    print(f"arms:   {', '.join(a.id for a in chosen)}  (k={args.k})")

    catalog_tickers = catalog.tickers()
    routes = {}
    for item in items:
        try:
            routes[str(item["id"])] = _route_once(item["question"], catalog_tickers)
        except Exception as exc:
            print(f"  ! could not route {item['id']}: {exc}", file=sys.stderr)

    results = []
    for arm in chosen:
        print(f"  running {arm.id} ...", flush=True)
        results.append(run_arm(arm, items, routes, args.k))

    table = markdown(results, chosen, args.label)
    print()
    print(table)

    # An arm that scored nothing is not a result of 0%, it is a run that did
    # not happen — and a clean-looking 0/0 table is worse than no table. This
    # fired the first time the ablation was run: Qdrant local mode takes an
    # EXCLUSIVE lock on its storage folder, an evaluation run held it, and
    # every single retrieve raised while the table reported 0/0 (0%).
    dead = [r for r in results if r.scored == 0]
    if dead:
        print(f"\nERROR: {len(dead)} arm(s) scored no items at all:", file=sys.stderr)
        for result in dead:
            print(f"  {result.arm}: {len(result.errors)} errors", file=sys.stderr)
            for message in result.errors[:3]:
                print(f"    {message}", file=sys.stderr)
        print("  (Qdrant local mode allows one process at a time — check that "
              "no evaluation run is holding the store.)", file=sys.stderr)
        return 1
    total_errors = sum(len(r.errors) for r in results)
    if total_errors:
        print(f"\nNOTE {total_errors} retrieval error(s) across "
              f"{len(results)} arm(s); see the per-item JSON.", file=sys.stderr)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    slug = args.label.replace(" ", "_")
    (args.out_dir / f"{slug}.md").write_text(table, encoding="utf-8")
    (args.out_dir / f"{slug}.json").write_text(
        json.dumps({
            "label": args.label,
            "qdrant_path": str(settings.qdrant_path),
            "k": args.k,
            "n_items": len(items),
            "arms": [r.summary() for r in results],
            "per_item": {r.arm: r.per_item for r in results},
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out_dir / f'{slug}.md'}")
    print(f"wrote {args.out_dir / f'{slug}.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
