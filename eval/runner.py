#!/usr/bin/env python3
"""Run the gold set through a variant, resumably, with full accounting (P4-04).

    python -m eval.runner --variant V3
    python -m eval.runner --variant V3 --limit 5 --dry-run
    python -m eval.runner --variant V3 --report-only

Design, and the three things it is built around
-----------------------------------------------
**Resumable, because the budget is a free tier.** Every item is appended to
``reports/phase4/runs/<variant>.jsonl`` the moment it finishes. A re-run reads
that file and skips what is already there, so a run interrupted by a rate limit,
a daily cap or a closed laptop continues instead of restarting. Items that never
ran are reported as ``not_run`` rather than dropped, so an n/N over a partial run
always states its own N (D21's rule, applied here).

**Every LLM role is instrumented, not just the generator.** The Phase 0 and
Phase 1 runners timed the generator only, which made the router and the judge
free in every table. ``RecordingLLM`` wraps whatever client the pipeline is
given and records role, provider, model, tokens, latency, cache hit and attempt
count for every call. A numeric question that makes no model call at all records
exactly that, which is a result worth being able to show.

**Deterministic given the same inputs.** The runner does no sampling, writes
items in gold-set order, and derives nothing from the clock except the measured
latencies, which are kept out of the scored record. With ``FakeLLM`` two runs
produce identical rows, and a run resumed halfway produces the same file as a
run that was never interrupted (T4-03).

The runner scores as it goes, but scoring is also reproducible on its own from
the raw JSONL with ``--report-only``, so a scorer fix does not mean re-spending
the LLM budget.

``--fake-llm`` is for checking the runner, not for measuring the system. It
exercises the facts path fully — that path makes no model call at all — and
every narrative item comes back ``status=error`` with ``llm_bad_output``,
because the stub does not return the JSON the text path asks for. That is the
pipeline behaving correctly (G4: a dependency failure is visible, never dressed
up as an answer), and it is why no number from a ``--fake-llm`` run belongs in a
results table.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from eval.gold.schema import read_jsonl  # noqa: E402
from eval.scorers import OutcomeView, Scored, score_item, summarize  # noqa: E402
from eval.subsets import describe as describe_subset  # noqa: E402
from eval.subsets import ids as subset_ids  # noqa: E402
from eval.subsets import indexed_pairs, split  # noqa: E402

VARIANTS_PATH = REPO_ROOT / "eval" / "variants.yaml"
GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"
RUNS_DIR = REPO_ROOT / "reports" / "phase4" / "runs"


# ── LLM instrumentation ───────────────────────────────────────────────────────

@dataclass
class LLMCall:
    role: str
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False
    attempts: int = 1
    failed: Optional[str] = None

    def as_dict(self) -> Dict:
        return {
            "role": self.role, "provider": self.provider, "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "latency_s": round(self.latency_s, 4),
            "cached": self.cached, "attempts": self.attempts,
            **({"failed": self.failed} if self.failed else {}),
        }


class RecordingLLM:
    """Wraps an LLM client and records every call, by role.

    A proxy rather than a hook inside ``llm/client.py`` because the thing being
    measured is "what did answering THIS question cost", and that is a property
    of one call site in one run, not of the shared client. It also means V1's
    and V3's accounting come from the same object even though they take
    different paths through the pipeline.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: List[LLMCall] = []

    def _record(self, _role: str, _fn, *args, **kwargs):
        role, fn = _role, _fn
        started = time.monotonic()
        call = LLMCall(role=role)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            call.latency_s = time.monotonic() - started
            call.failed = f"{type(exc).__name__}: {exc}"
            self.calls.append(call)
            raise
        call.latency_s = time.monotonic() - started

        completion = result[1] if isinstance(result, tuple) and len(result) == 2 else result
        call.provider = getattr(completion, "provider", "") or ""
        call.model = getattr(completion, "model", "") or ""
        call.prompt_tokens = getattr(completion, "prompt_tokens", 0) or 0
        call.completion_tokens = getattr(completion, "completion_tokens", 0) or 0
        call.cached = bool(getattr(completion, "cached", False))
        call.attempts = int(getattr(completion, "attempts", 1) or 1)
        self.calls.append(call)
        return result

    def complete(self, *args, role: str = "", **kwargs):
        return self._record(role or kwargs.get("role", "") or "unknown",
                            self._inner.complete, *args, role=role, **kwargs)

    def complete_json(self, *args, role: str = "", **kwargs):
        return self._record(role or kwargs.get("role", "") or "unknown",
                            self._inner.complete_json, *args, role=role, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def reset(self) -> None:
        self.calls = []

    def summary(self) -> Dict:
        by_role: Dict[str, Dict] = {}
        for call in self.calls:
            bucket = by_role.setdefault(
                call.role, {"calls": 0, "tokens": 0, "latency_s": 0.0, "cached": 0}
            )
            bucket["calls"] += 1
            bucket["tokens"] += call.prompt_tokens + call.completion_tokens
            bucket["latency_s"] = round(bucket["latency_s"] + call.latency_s, 4)
            bucket["cached"] += int(call.cached)
        return {
            "calls": [c.as_dict() for c in self.calls],
            "by_role": by_role,
            "total_calls": len(self.calls),
            "total_tokens": sum(
                c.prompt_tokens + c.completion_tokens for c in self.calls
            ),
        }


# ── variants ──────────────────────────────────────────────────────────────────

@dataclass
class Variant:
    id: str
    name: str
    description: str = ""
    pipeline: str = "v2"
    flags: Dict[str, bool] = field(default_factory=dict)
    qdrant_path: Optional[str] = None
    as_of_in_question: bool = False
    requires: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""


def load_variants(path: Path = VARIANTS_PATH) -> Dict[str, Variant]:
    import yaml

    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = document.get("defaults") or {}
    out: Dict[str, Variant] = {}
    for entry in document.get("variants") or []:
        out[entry["id"]] = Variant(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            description=entry.get("description", ""),
            pipeline=entry.get("pipeline", "v2"),
            flags=dict(entry.get("flags") or {}),
            qdrant_path=entry.get("qdrant_path"),
            as_of_in_question=bool(entry.get("as_of_in_question")),
            requires=list(entry.get("requires") or []),
            provider=entry.get("provider", defaults.get("provider", "")),
            model=entry.get("model", defaults.get("model", "")),
        )
    return out


# ── asking one question ───────────────────────────────────────────────────────

def _question_for(item: Mapping, variant: Variant) -> str:
    """The question as this variant must receive it.

    V0 is v1, which has no ``as_of`` parameter, so the only way it could ever
    have been given one is in the sentence. Putting it there rather than
    dropping it keeps the point-in-time items a fair test of v1 instead of a
    guaranteed failure.
    """
    question = str(item["question"])
    if variant.as_of_in_question and item.get("as_of"):
        return f"As of {item['as_of']}: {question}"
    return question


def ask_v2(item: Mapping, variant: Variant, llm, deps_factory=None) -> Dict:
    """One gold item through query.ask, with its trace."""
    from query import Deps, ask

    deps = (deps_factory or Deps)()
    deps.llm = llm
    for name, value in variant.flags.items():
        setattr(deps, name, value)

    started = time.monotonic()
    outcome = ask(
        _question_for(item, variant),
        as_of=None if variant.as_of_in_question else item.get("as_of"),
        deps=deps,
    )
    latency = time.monotonic() - started

    payload = (
        outcome.model_dump(mode="json") if hasattr(outcome, "model_dump")
        else dict(outcome)
    )
    return {"outcome": payload, "latency_s": round(latency, 4)}


# ── distractors, so "right number, wrong thing" can be classified ────────────

def build_distractors(gold: Sequence[Mapping]) -> Dict[str, Dict[str, Decimal]]:
    """Per item: values that would be right for another year or company.

    Taken from the gold set itself rather than invented, so a distractor is
    always a number the corpus really contains. Without these a value that is
    the right metric for the wrong fiscal year is scored ``wrong_value``, and
    the failure analysis in P4-07 cannot tell a period bug from a reading bug.
    """
    by_key: Dict[tuple, Decimal] = {}
    for item in gold:
        expected = item.get("expected") or {}
        if expected.get("type") != "numeric":
            continue
        try:
            value = Decimal(str(expected["value"]))
        except (InvalidOperation, KeyError):
            continue
        by_key[(expected.get("ticker"), expected.get("metric"),
                expected.get("fiscal_label"))] = value

    out: Dict[str, Dict[str, Decimal]] = {}
    for item in gold:
        expected = item.get("expected") or {}
        if expected.get("type") != "numeric":
            continue
        ticker = expected.get("ticker")
        metric = expected.get("metric")
        label = expected.get("fiscal_label")
        distractors: Dict[str, Decimal] = {}
        for (other_ticker, other_metric, other_label), value in by_key.items():
            if other_metric != metric:
                continue
            if other_ticker == ticker and other_label != label:
                distractors[f"period:{other_ticker} FY{other_label}"] = value
            elif other_ticker != ticker and other_label == label:
                distractors[f"entity:{other_ticker} FY{other_label}"] = value
        out[str(item["id"])] = distractors
    return out


# ── the run ───────────────────────────────────────────────────────────────────

def run_path(variant_id: str, runs_dir: Path = RUNS_DIR) -> Path:
    return runs_dir / f"{variant_id}.jsonl"


def load_existing(path: Path) -> Dict[str, Dict]:
    """Rows already recorded, by item id. A malformed trailing line is dropped.

    A run killed mid-write leaves a truncated last line. Dropping it means the
    item is simply re-run; raising would make an interrupted run unresumable,
    which is the one thing this file exists to prevent.
    """
    if not path.is_file():
        return {}
    rows: Dict[str, Dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("item_id"):
            rows[row["item_id"]] = row
    return rows


def _rewrite_without(path: Path, item_ids: Sequence[str]) -> None:
    """Drop the named rows, preserving the order of the rest.

    Rewritten in place rather than appended to: ``load_existing`` keys by item
    id and the later row would win, but the file is also read by hand, and two
    rows for one item invites exactly the wrong conclusion about which one the
    numbers came from.
    """
    drop = set(item_ids)
    kept: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("item_id")) not in drop:
            kept.append(line)
    path.write_text("".join(line + "\n" for line in kept), encoding="utf-8")


def append_row(path: Path, row: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def run_variant(
    variant: Variant,
    gold: Sequence[Mapping],
    *,
    llm,
    runs_dir: Path = RUNS_DIR,
    limit: Optional[int] = None,
    resume: bool = True,
    ask_fn=ask_v2,
    deps_factory=None,
    on_item=None,
    sleep_s: float = 0.0,
    retry_errors: Sequence[str] = (),
) -> List[Dict]:
    """Ask every gold item, appending as it goes. Returns the rows for this run."""
    path = run_path(variant.id, runs_dir)
    done = load_existing(path) if resume else {}

    # A recorded row whose error_code is in ``retry_errors`` is not a result.
    # A free-tier rate limit is an outage, and leaving it in the file would
    # report the provider's quota as the variant's accuracy. Re-asking is not
    # a retry loop (CLAUDE.md rule 7): the first run stopped cleanly and this
    # is a separate, paced run over what it could not reach.
    if retry_errors:
        retryable = {
            item_id for item_id, row in done.items()
            if ((row.get("outcome") or {}).get("error_code") in retry_errors)
        }
        for item_id in retryable:
            done.pop(item_id, None)
        if retryable:
            _rewrite_without(path, retryable)

    items = list(gold)[: limit] if limit else list(gold)
    rows: List[Dict] = []

    for index, item in enumerate(items):
        item_id = str(item["id"])
        if item_id in done:
            rows.append(done[item_id])
            continue
        if sleep_s and index:
            time.sleep(sleep_s)

        recorder = llm if isinstance(llm, RecordingLLM) else RecordingLLM(llm)
        recorder.reset()
        try:
            result = ask_fn(item, variant, recorder, deps_factory)
            row = {
                "item_id": item_id,
                "variant": variant.id,
                "category": item["category"],
                "question": _question_for(item, variant),
                "as_of": item.get("as_of"),
                "outcome": result["outcome"],
                "latency_s": result["latency_s"],
                "llm": recorder.summary(),
            }
        except Exception as exc:
            # A failure to even produce an outcome is recorded as a row, not
            # lost: an item missing from the file would be silently re-run
            # forever, and the report would never show that it failed.
            row = {
                "item_id": item_id, "variant": variant.id,
                "category": item["category"],
                "question": _question_for(item, variant),
                "as_of": item.get("as_of"),
                "outcome": None,
                "runner_error": f"{type(exc).__name__}: {exc}",
                "latency_s": 0.0,
                "llm": recorder.summary(),
            }
        append_row(path, row)
        rows.append(row)
        if on_item is not None:
            on_item(row)

    return rows


# ── scoring a recorded run ────────────────────────────────────────────────────

def score_run(gold: Sequence[Mapping], rows: Iterable[Mapping]) -> List[Scored]:
    """Score recorded rows. Pure: no pipeline, no model, no clock."""
    by_id = {str(item["id"]): item for item in gold}
    recorded = {str(row["item_id"]): row for row in rows}
    distractors = build_distractors(gold)

    scored: List[Scored] = []
    for item_id, item in by_id.items():
        row = recorded.get(item_id)
        outcome = row.get("outcome") if row else None
        view = OutcomeView.from_outcome(outcome)
        if row is not None and row.get("runner_error"):
            view = OutcomeView(status="error", error_code="internal", ran=True)
        scored.append(score_item(item, view, distractors=distractors.get(item_id, {})))
    return scored


def llm_totals(rows: Iterable[Mapping]) -> Dict:
    """Token, call and latency accounting across a run, by role."""
    by_role: Dict[str, Dict] = {}
    latencies: List[float] = []
    for row in rows:
        latencies.append(float(row.get("latency_s") or 0.0))
        for role, bucket in ((row.get("llm") or {}).get("by_role") or {}).items():
            target = by_role.setdefault(
                role, {"calls": 0, "tokens": 0, "latency_s": 0.0, "cached": 0}
            )
            target["calls"] += bucket.get("calls", 0)
            target["tokens"] += bucket.get("tokens", 0)
            target["latency_s"] = round(
                target["latency_s"] + bucket.get("latency_s", 0.0), 4
            )
            target["cached"] += bucket.get("cached", 0)

    latencies.sort()

    def percentile(fraction: float) -> float:
        if not latencies:
            return 0.0
        index = min(len(latencies) - 1, round(fraction * (len(latencies) - 1)))
        return round(latencies[index], 4)

    return {
        "by_role": by_role,
        "total_calls": sum(b["calls"] for b in by_role.values()),
        "total_tokens": sum(b["tokens"] for b in by_role.values()),
        "latency_p50": percentile(0.5),
        "latency_p95": percentile(0.95),
        "latency_max": round(latencies[-1], 4) if latencies else 0.0,
        # Free tier only (D18): there is no price to multiply by, and inventing
        # one would put a fabricated number in a results table.
        "cost_usd": 0.0,
        "cost_note": "free-tier providers only (D18); no per-token price applies",
    }


def markdown_table(variant_id: str, scored: Sequence[Scored], totals: Mapping) -> str:
    summary = summarize(scored)
    lines = [
        f"### {variant_id}",
        "",
        f"**{summary['passed']} / {summary['n']}** "
        f"({summary['rate']:.1%}, 95% CI "
        f"{summary['ci95'][0]:.1%}–{summary['ci95'][1]:.1%})",
        "",
        "| category | n | passed | rate | 95% CI |",
        "|---|---:|---:|---:|---|",
    ]
    for category in sorted(summary["by_category"]):
        bucket = summary["by_category"][category]
        lines.append(
            f"| {category} | {bucket['n']} | {bucket['passed']} | "
            f"{bucket['rate']:.1%} | "
            f"{bucket['ci95'][0]:.1%}–{bucket['ci95'][1]:.1%} |"
        )

    lines += ["", "| verdict | n |", "|---|---:|"]
    verdicts: Dict[str, int] = {}
    for item in scored:
        verdicts[item.verdict] = verdicts.get(item.verdict, 0) + 1
    for verdict, count in sorted(verdicts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {verdict} | {count} |")

    lines += ["", "| flag | n |", "|---|---:|"]
    if summary["flags"]:
        for flag, count in sorted(summary["flags"].items()):
            lines.append(f"| {flag} | {count} |")
    else:
        lines.append("| (none) | 0 |")

    lines += [
        "",
        "| role | calls | tokens | cached | latency s |",
        "|---|---:|---:|---:|---:|",
    ]
    if totals.get("by_role"):
        for role in sorted(totals["by_role"]):
            bucket = totals["by_role"][role]
            lines.append(
                f"| {role} | {bucket['calls']} | {bucket['tokens']} | "
                f"{bucket['cached']} | {bucket['latency_s']:.2f} |"
            )
    else:
        lines.append("| (no model calls) | 0 | 0 | 0 | 0.00 |")

    lines += [
        "",
        f"latency p50 {totals.get('latency_p50', 0):.2f}s · "
        f"p95 {totals.get('latency_p95', 0):.2f}s · "
        f"max {totals.get('latency_max', 0):.2f}s · "
        f"cost {totals.get('cost_note', 'n/a')}",
        "",
    ]
    return "\n".join(lines)


def csv_rows(variant_id: str, scored: Sequence[Scored]) -> List[Dict]:
    return [{"variant": variant_id, **item.as_row()} for item in scored]


# ── CLI ───────────────────────────────────────────────────────────────────────

def _display(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise.

    ``--runs-dir`` outside the repo is a legitimate thing to ask for (a scratch
    comparison run), and ``Path.relative_to`` raises for it. The same crash was
    already fixed once in scripts/make_spotcheck_sheet.py; it belongs in a
    helper rather than being rediscovered a third time.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def paired_subset(gold: Sequence[Mapping]) -> Optional[Dict]:
    """The D21 subset, or None if the catalog cannot be read.

    Returning None rather than raising: a scorer re-run (``--report-only``) on a
    machine without the catalog should still produce the full-set table. The
    report then says the paired column is absent, instead of showing a number
    that quietly means something else.
    """
    try:
        from catalog.store import CatalogStore
        from config import settings

        catalog = CatalogStore(Path(settings.data_dir) / "derived" / "catalog.sqlite")
        pairs = indexed_pairs(catalog)
    except Exception:
        return None
    if not pairs:
        return None
    inside, _outside = split(gold, pairs)
    return {"ids": subset_ids(inside), "describe": describe_subset(gold, pairs)}


def _write_reports(variant_id: str, scored, totals, runs_dir: Path,
                   subset: Optional[Dict] = None) -> List[Path]:
    import csv as csv_module

    written: List[Path] = []
    runs_dir.mkdir(parents=True, exist_ok=True)

    csv_path = runs_dir / f"{variant_id}_scored.csv"
    rows = csv_rows(variant_id, scored)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv_module.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    written.append(csv_path)

    md_path = runs_dir / f"{variant_id}_summary.md"
    md_path.write_text(markdown_table(variant_id, scored, totals), encoding="utf-8")
    written.append(md_path)

    json_path = runs_dir / f"{variant_id}_summary.json"
    payload = {"variant": variant_id, "scores": summarize(scored), "llm": totals}
    if subset:
        paired = [s for s in scored if s.item_id in subset["ids"]]
        payload["paired_subset"] = {
            **subset["describe"],
            "scores": summarize(paired),
        }
    json_path.write_text(json.dumps(payload, indent=2, default=list) + "\n",
                         encoding="utf-8")
    written.append(json_path)
    return written


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--variant", required=True)
    ap.add_argument("--gold", type=Path, default=GOLD_PATH)
    ap.add_argument("--variants-file", type=Path, default=VARIANTS_PATH)
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="seconds between items, to stay under a free-tier "
                         "per-minute token limit")
    ap.add_argument("--retry-rate-limited", action="store_true",
                    help="re-ask items recorded with error_code=llm_rate_limited; "
                         "a provider outage is not a result")
    ap.add_argument("--fake-llm", action="store_true",
                    help="run against llm.fake.FakeLLM — no network, no budget")
    ap.add_argument("--report-only", action="store_true",
                    help="score and report the recorded run; ask nothing")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would run and exit")
    args = ap.parse_args(argv)

    variants = load_variants(args.variants_file)
    if args.variant not in variants:
        print(f"error: unknown variant {args.variant!r}; have "
              f"{sorted(variants)}", file=sys.stderr)
        return 2
    variant = variants[args.variant]
    gold = read_jsonl(args.gold)
    if args.limit:
        gold = gold[: args.limit]

    path = run_path(variant.id, args.runs_dir)
    done = load_existing(path)
    todo = [i for i in gold if str(i["id"]) not in done]

    print(f"{variant.id} — {variant.name}")
    print(f"  gold items: {len(gold)}; already recorded: {len(done)}; to run: {len(todo)}")
    if variant.flags:
        print(f"  flags: {variant.flags}")
    if variant.pipeline != "v2":
        print(f"  pipeline: {variant.pipeline} (requires {variant.requires})")

    if args.dry_run:
        for item in todo[:10]:
            print(f"    would ask {item['id']}: {_question_for(item, variant)[:80]}")
        if len(todo) > 10:
            print(f"    ... and {len(todo) - 10} more")
        return 0

    if not args.report_only:
        if variant.pipeline != "v2":
            print(f"error: variant {variant.id} runs the {variant.pipeline} "
                  f"pipeline, which this runner does not drive. Run it from the "
                  f"`v1-baseline` tag and place its rows at {path}.",
                  file=sys.stderr)
            return 2
        if args.fake_llm:
            from llm.fake import FakeLLM
            llm = FakeLLM()
        else:
            from llm import get_client
            llm = get_client()
        run_variant(variant, gold, llm=RecordingLLM(llm), runs_dir=args.runs_dir,
                    resume=not args.no_resume, sleep_s=args.sleep,
                    retry_errors=("llm_rate_limited",) if args.retry_rate_limited else ())

    rows = list(load_existing(path).values())
    scored = score_run(gold, rows)
    totals = llm_totals(rows)
    subset = paired_subset(gold)

    print()
    print(markdown_table(variant.id, scored, totals))

    if subset:
        paired = [s for s in scored if s.item_id in subset["ids"]]
        info = subset["describe"]
        print(f"#### {variant.id}, paired subset (D21)")
        print()
        print(f"{info['n_paired']} of {info['n_total']} items; "
              f"{info['n_excluded']} excluded because their filings are not in "
              f"the text index: "
              + ", ".join(f"{k} {'/'.join(v)}" for k, v in
                          info["missing_filings"].items()))
        print()
        print(markdown_table(f"{variant.id} (paired)", paired, totals))

    for written in _write_reports(variant.id, scored, totals, args.runs_dir, subset):
        print(f"wrote {_display(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
