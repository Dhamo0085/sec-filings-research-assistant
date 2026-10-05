"""The demo pre-flight: its claims about itself must be true (P5-00).

No server and no network here. What is pinned is the part a reader relies on
when they read the table: that there really are eight demo queries, that
exactly one of them needs a model, that the set matches what `docs/DEMO.md`
tells the presenter to type, and that the LLM-state row cannot quietly report
PASS for a provider that is down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.demo_check import DEMO_QUERIES, Row, _llm_looks_usable, _print_table

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_there_are_eight_demo_queries_with_unique_ids():
    assert len(DEMO_QUERIES) == 8
    assert len({q[0] for q in DEMO_QUERIES}) == 8


def test_exactly_one_demo_query_needs_a_model_and_it_is_last():
    needs_llm = [q[0] for q in DEMO_QUERIES if q[5]]
    assert needs_llm == ["D8"], (
        "the demo's whole premise is that the facts path costs no quota; "
        "if a second query needs a model, docs/DEMO.md is wrong"
    )
    assert DEMO_QUERIES[-1][0] == "D8", "the model call must come last"


def test_every_demo_query_names_at_least_one_accepted_status():
    for qid, _label, question, _as_of, expected, _llm in DEMO_QUERIES:
        assert expected, qid
        assert question.strip(), qid
        assert set(expected) <= {"answered", "answered_text", "abstained",
                                 "clarification_needed"}, qid


def test_the_demo_covers_every_outcome_shape_worth_showing():
    statuses = {s for q in DEMO_QUERIES for s in q[4]}
    assert {"answered", "answered_text", "abstained"} <= statuses
    assert any(q[3] for q in DEMO_QUERIES), "no point-in-time query in the demo"


def test_the_questions_match_the_ones_docs_demo_md_tells_the_presenter_to_type():
    """A demo guide that drifts from the pre-flight is worse than neither."""
    guide = (REPO_ROOT / "docs" / "DEMO.md").read_text(encoding="utf-8")
    for qid, _label, question, _as_of, _expected, _llm in DEMO_QUERIES:
        assert question in guide, f"{qid} is not in docs/DEMO.md"


def test_the_transcripts_cover_every_demo_query():
    transcripts = (REPO_ROOT / "docs" / "demo" / "TRANSCRIPTS.md").read_text(encoding="utf-8")
    for qid, _label, question, _as_of, _expected, _llm in DEMO_QUERIES:
        assert f"## {qid} —" in transcripts, f"{qid} has no plan-B transcript"
        assert question in transcripts, f"{qid}'s transcript is for another question"


@pytest.mark.parametrize("llm", [
    "ok", {"status": "ok"}, {"state": "available"}, {"available": True},
    {"providers": {"gemini": True}}, {"providers": ["gemini"]},
])
def test_a_usable_llm_state_reads_as_usable(llm):
    assert _llm_looks_usable(llm) is True


@pytest.mark.parametrize("llm", [
    "unavailable", "not_configured", {"status": "down"}, {"state": "error"},
    {"available": False}, {"providers": {}}, {}, None,
])
def test_an_unusable_llm_state_does_not_read_as_usable(llm):
    assert _llm_looks_usable(llm) is False


def test_the_table_renders_both_outcomes(capsys):
    _print_table("t", [Row("a", True, "fine", 1.0), Row("b", False, "broken", 2.0)])
    out = capsys.readouterr().out
    assert "PASS" in out and "FAIL" in out and "broken" in out
