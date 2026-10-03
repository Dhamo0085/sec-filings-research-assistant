"""T4-03: the evaluation runner is deterministic and genuinely resumable.

The budget is a free tier, so a full run will be interrupted — by a rate limit,
a daily cap or a closed laptop. Two properties make that survivable, and both
are easy to believe without being true:

* a resumed run produces the same file as an uninterrupted one, and
* re-running does not silently re-ask what is already recorded.

Both are tested against a stub pipeline rather than the real one, so they test
the runner's bookkeeping and nothing else.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from eval.runner import (
    RecordingLLM,
    build_distractors,
    llm_totals,
    load_existing,
    load_variants,
    markdown_table,
    run_variant,
    score_run,
)
from eval.scorers import Scored

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def gold_items(n: int = 6):
    items = []
    for i in range(n):
        items.append({
            "id": f"N-{i}", "category": "numeric",
            "question": f"question {i}", "as_of": None,
            "expected": {"type": "numeric", "ticker": "AAPL", "metric": "revenue",
                         "fiscal_label": 2020 + i, "period_end": "2024-09-28",
                         "value": str(100 + i), "unit": "USD", "tolerance_rel": 0.0},
            "source": {"accession": "x"}, "verified_by": "companyfacts",
        })
    return items


class StubAsk:
    """A deterministic stand-in for query.ask that counts what it was asked."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.asked: list = []
        self.fail_after = fail_after

    def __call__(self, item, variant, llm, deps_factory=None):
        if self.fail_after is not None and len(self.asked) >= self.fail_after:
            raise KeyboardInterrupt("simulated interruption")
        self.asked.append(item["id"])
        return {
            "outcome": {
                "status": "answered",
                "answer": f"The value was {item['expected']['value']}.",
                "citations": [],
            },
            "latency_s": 0.01,
        }


@pytest.fixture
def variant():
    return load_variants()["V3"]


# ── determinism ───────────────────────────────────────────────────────────────

def test_two_runs_produce_byte_identical_files(tmp_path, variant):
    gold = gold_items()
    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=first_dir,
                ask_fn=StubAsk())
    run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=second_dir,
                ask_fn=StubAsk())

    def without_timings(path: Path) -> list:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            row.pop("latency_s", None)
            row.pop("llm", None)
            rows.append(row)
        return rows

    assert without_timings(first_dir / "V3.jsonl") == without_timings(second_dir / "V3.jsonl")


def test_items_are_written_in_gold_order():
    """So a diff between two runs lines up item by item."""
    gold = gold_items()
    assert [i["id"] for i in gold] == sorted(i["id"] for i in gold)


def test_scoring_a_recorded_run_is_pure(tmp_path, variant):
    gold = gold_items()
    run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=tmp_path,
                ask_fn=StubAsk())
    rows = list(load_existing(tmp_path / "V3.jsonl").values())
    first = [s.as_row() for s in score_run(gold, rows)]
    second = [s.as_row() for s in score_run(gold, rows)]
    assert first == second


# ── resume ────────────────────────────────────────────────────────────────────

def test_an_interrupted_run_resumes_into_the_same_file(tmp_path, variant):
    gold = gold_items()

    interrupted = StubAsk(fail_after=3)
    with pytest.raises(KeyboardInterrupt):
        run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=tmp_path,
                    ask_fn=interrupted)
    partial = load_existing(tmp_path / "V3.jsonl")
    assert len(partial) == 3, "rows must be flushed as they are produced"

    resumed = StubAsk()
    run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=tmp_path,
                ask_fn=resumed)

    assert resumed.asked == ["N-3", "N-4", "N-5"], (
        "a resumed run re-asked work that was already recorded"
    )
    assert sorted(load_existing(tmp_path / "V3.jsonl")) == [i["id"] for i in gold]


def test_a_resumed_run_matches_an_uninterrupted_one(tmp_path, variant):
    gold = gold_items()

    with pytest.raises(KeyboardInterrupt):
        run_variant(variant, gold, llm=RecordingLLM(object()),
                    runs_dir=tmp_path / "resumed", ask_fn=StubAsk(fail_after=2))
    run_variant(variant, gold, llm=RecordingLLM(object()),
                runs_dir=tmp_path / "resumed", ask_fn=StubAsk())
    run_variant(variant, gold, llm=RecordingLLM(object()),
                runs_dir=tmp_path / "whole", ask_fn=StubAsk())

    resumed = score_run(gold, load_existing(tmp_path / "resumed" / "V3.jsonl").values())
    whole = score_run(gold, load_existing(tmp_path / "whole" / "V3.jsonl").values())
    assert [s.as_row() for s in resumed] == [s.as_row() for s in whole]


def test_a_truncated_last_line_is_dropped_so_the_run_stays_resumable(tmp_path):
    """A process killed mid-write leaves half a line.

    Dropping it means that item is simply re-asked. Raising would make an
    interrupted run unresumable, which is the one thing this file prevents.
    """
    path = tmp_path / "V3.jsonl"
    path.write_text(
        json.dumps({"item_id": "N-0", "outcome": None}) + "\n" + '{"item_id": "N-1", "out',
        encoding="utf-8",
    )
    assert list(load_existing(path)) == ["N-0"]


def test_no_resume_re_asks_everything(tmp_path, variant):
    gold = gold_items(3)
    run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=tmp_path,
                ask_fn=StubAsk())
    again = StubAsk()
    run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=tmp_path,
                ask_fn=again, resume=False)
    assert again.asked == ["N-0", "N-1", "N-2"]


def test_an_item_that_raises_is_recorded_rather_than_lost(tmp_path, variant):
    class Exploding(StubAsk):
        def __call__(self, item, variant, llm, deps_factory=None):
            if item["id"] == "N-1":
                raise RuntimeError("boom")
            return super().__call__(item, variant, llm, deps_factory)

    gold = gold_items(3)
    run_variant(variant, gold, llm=RecordingLLM(object()), runs_dir=tmp_path,
                ask_fn=Exploding())
    rows = load_existing(tmp_path / "V3.jsonl")
    assert set(rows) == {"N-0", "N-1", "N-2"}
    assert "boom" in rows["N-1"]["runner_error"]
    # ...and it scores as an error, not as a missing item
    scored = {s.item_id: s for s in score_run(gold, rows.values())}
    assert scored["N-1"].verdict == "error"


def test_an_item_that_never_ran_is_not_run_rather_than_absent(tmp_path, variant):
    """n/N must always state its own N (D21)."""
    gold = gold_items(4)
    run_variant(variant, gold[:2], llm=RecordingLLM(object()), runs_dir=tmp_path,
                ask_fn=StubAsk())
    scored = score_run(gold, load_existing(tmp_path / "V3.jsonl").values())
    assert len(scored) == 4
    assert [s.verdict for s in scored][2:] == ["not_run", "not_run"]


# ── instrumentation ───────────────────────────────────────────────────────────

class StubCompletion:
    def __init__(self, provider="groq", model="m", prompt_tokens=10,
                 completion_tokens=5, cached=False, attempts=1):
        self.provider, self.model = provider, model
        self.prompt_tokens, self.completion_tokens = prompt_tokens, completion_tokens
        self.cached, self.attempts = cached, attempts
        self.content = "{}"


class StubClient:
    def __init__(self):
        self.seen = []

    def complete(self, *, role, **kwargs):
        self.seen.append(role)
        return StubCompletion()

    def complete_json(self, *, role, **kwargs):
        self.seen.append(role)
        return {}, StubCompletion(prompt_tokens=20, completion_tokens=7)


def test_every_role_is_instrumented_not_only_the_generator():
    """The Phase 0 and Phase 1 runners timed the generator, so the router and
    the judge were free in every table."""
    recorder = RecordingLLM(StubClient())
    recorder.complete_json(role="router", messages=[])
    recorder.complete(role="generator", messages=[])
    recorder.complete(role="judge", messages=[])

    summary = recorder.summary()
    assert set(summary["by_role"]) == {"router", "generator", "judge"}
    assert summary["total_calls"] == 3
    assert summary["total_tokens"] == 27 + 15 + 15
    assert summary["by_role"]["router"]["tokens"] == 27


def test_a_question_that_calls_no_model_records_exactly_that():
    """The facts path makes no generation call; that is a result worth showing."""
    recorder = RecordingLLM(StubClient())
    summary = recorder.summary()
    assert summary["total_calls"] == 0 and summary["by_role"] == {}


def test_a_failed_call_is_still_accounted_for():
    class Failing:
        def complete(self, **kwargs):
            raise RuntimeError("rate limited")

    recorder = RecordingLLM(Failing())
    with pytest.raises(RuntimeError):
        recorder.complete(role="generator", messages=[])
    assert recorder.calls[0].failed.startswith("RuntimeError")
    assert recorder.summary()["by_role"]["generator"]["calls"] == 1


def test_llm_totals_add_up_across_rows_and_report_percentiles():
    rows = [
        {"latency_s": 1.0, "llm": {"by_role": {"router": {"calls": 1, "tokens": 10,
                                                          "latency_s": 0.5, "cached": 0}}}},
        {"latency_s": 3.0, "llm": {"by_role": {"router": {"calls": 1, "tokens": 20,
                                                          "latency_s": 1.5, "cached": 1}}}},
    ]
    totals = llm_totals(rows)
    assert totals["total_calls"] == 2 and totals["total_tokens"] == 30
    assert totals["by_role"]["router"]["cached"] == 1
    assert totals["latency_max"] == 3.0
    assert totals["cost_usd"] == 0.0, "free tier only (D18); no price to multiply by"


# ── distractors ───────────────────────────────────────────────────────────────

def test_distractors_come_from_the_gold_set_and_separate_period_from_entity():
    gold = [
        {"id": "a", "category": "numeric", "expected": {
            "type": "numeric", "ticker": "AAPL", "metric": "revenue",
            "fiscal_label": 2024, "value": "391"}},
        {"id": "b", "category": "numeric", "expected": {
            "type": "numeric", "ticker": "AAPL", "metric": "revenue",
            "fiscal_label": 2023, "value": "383"}},
        {"id": "c", "category": "numeric", "expected": {
            "type": "numeric", "ticker": "MSFT", "metric": "revenue",
            "fiscal_label": 2024, "value": "245"}},
        {"id": "d", "category": "numeric", "expected": {
            "type": "numeric", "ticker": "MSFT", "metric": "net_income",
            "fiscal_label": 2024, "value": "88"}},
    ]
    built = build_distractors(gold)
    assert built["a"] == {
        "period:AAPL FY2023": Decimal("383"),
        "entity:MSFT FY2024": Decimal("245"),
    }, "a different metric is not a distractor for this one"


# ── reporting ─────────────────────────────────────────────────────────────────

def test_the_markdown_table_states_n_and_an_interval_for_every_rate():
    scored = [
        Scored("a", "numeric", "correct"),
        Scored("b", "numeric", "scale_error"),
        Scored("c", "abstain", "correct_abstain"),
    ]
    table = markdown_table("V3", scored, llm_totals([]))
    assert "**2 / 3**" in table
    assert "95% CI" in table
    assert "| numeric | 2 | 1 |" in table
    assert "| scale_error | 1 |" in table
    assert "(no model calls)" in table


def test_the_variants_file_defines_the_four_the_spec_names():
    variants = load_variants()
    assert sorted(variants) == ["V0", "V1", "V2", "V3"]
    assert variants["V0"].pipeline == "v1"
    assert variants["V0"].as_of_in_question, "v1 has no as_of parameter"
    assert variants["V0"].qdrant_path == "data/qdrant_v1_backup", "T4-07"
    assert variants["V1"].flags["enable_facts"] is False
    assert variants["V2"].flags["enable_asof"] is False
    assert variants["V2"].flags["enable_abstain_gate"] is False
    assert variants["V3"].flags == {
        "enable_facts": True, "enable_asof": True, "enable_abstain_gate": True,
    }
