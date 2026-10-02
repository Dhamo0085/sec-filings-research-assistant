"""
Phase 0 / Step 1.1 — K1: citation renumbering in generation.synthesizer.synthesize().

Measurement only.  No source file is modified.

The suspected defect is the remap loop (generation/synthesizer.py, lines 127-134):
it rewrites the sub-answer text with successive str.replace() calls.  When a
citation is shifted into a number that a *later* iteration also searches for,
the same marker is rewritten more than once.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import install_heavy_stubs, REPO_ROOT

install_heavy_stubs()

from models import QueryResult  # noqa: E402

CAPTURED: list[dict] = []
OUT = REPO_ROOT / "reports" / "phase0" / "k1_citation_remap.json"


def _citation(idx: int, ticker: str = "AAPL", year: int = 2024) -> dict:
    return {
        "index": idx,
        "company": f"{ticker} Inc.",
        "ticker": ticker,
        "fiscal_year": year,
        "section": f"section_{idx}",
    }


class _FakeCompletions:
    def create(self, **kwargs):
        CAPTURED.append(kwargs)
        msg = type("M", (), {"content": "SYNTHESIZED ANSWER"})()
        choice = type("C", (), {"message": msg})()
        return type("R", (), {"choices": [choice]})()


class _FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


def _run_synthesize(monkeypatch, sub2_citation_count: int, sub1_citation_count: int = 1):
    """Drive synthesize() with fully faked decomposition, retrieval and generation.

    sub-answer 1 gets `sub1_citation_count` citations -> sets the offset for
    sub-answer 2, which carries `sub2_citation_count` citations.
    """
    import generation.synthesizer as S

    CAPTURED.clear()

    subs = [
        {"ticker": "AAPL", "year": 2024, "question": "Q1"},
        {"ticker": "MSFT", "year": 2024, "question": "Q2"},
    ]
    monkeypatch.setattr(S, "decompose_query", lambda *a, **k: subs)
    monkeypatch.setattr(S, "decompose_temporal", lambda *a, **k: subs)
    monkeypatch.setattr(S, "retrieve", lambda *a, **k: [])

    # sub-answer 1: text "A [1]." with N citations
    text1 = "A " + " ".join(f"[{i}]" for i in range(1, sub1_citation_count + 1)) + "."
    cits1 = [_citation(i, "AAPL") for i in range(1, sub1_citation_count + 1)]
    # sub-answer 2: "B [1] C [2] D [3]." style
    letters = ["B", "C", "D", "E", "F"]
    text2 = (
        " ".join(f"{letters[i - 1]} [{i}]" for i in range(1, sub2_citation_count + 1))
        + "."
    )
    cits2 = [_citation(i, "MSFT") for i in range(1, sub2_citation_count + 1)]

    results = {
        "Q1": QueryResult(query="Q1", answer=text1, citations=cits1, chunks_used=[], query_type="sub_question"),
        "Q2": QueryResult(query="Q2", answer=text2, citations=cits2, chunks_used=[], query_type="sub_question"),
    }
    monkeypatch.setattr(S, "generate_answer", lambda query, retrieved, query_type: results[query])
    monkeypatch.setattr(S, "_get_client", lambda: _FakeClient())

    result = S.synthesize(
        query="compare", tickers=["AAPL", "MSFT"], years=[2024], query_type="multi_doc"
    )
    assert CAPTURED, "synthesis LLM call was never made"
    synthesis_input = CAPTURED[0]["messages"][1]["content"]
    return synthesis_input, result, text2


FINDINGS: list[dict] = []


@pytest.mark.parametrize(
    "offset,n_cits,expected_markers",
    [
        (1, 3, ["[2]", "[3]", "[4]"]),
        (2, 3, ["[3]", "[4]", "[5]"]),
    ],
)
def test_second_subanswer_markers_stay_distinct(monkeypatch, offset, n_cits, expected_markers):
    """After remapping with `offset`, the 2nd sub-answer's N citations must map
    to N DISTINCT consecutive markers."""
    synthesis_input, result, original = _run_synthesize(
        monkeypatch, sub2_citation_count=n_cits, sub1_citation_count=offset
    )

    # Isolate the second sub-answer block from the synthesis input.
    block = synthesis_input.split("---")[-1]
    present = [m for m in expected_markers if m in block]
    distinct_found = sorted({m for m in expected_markers if m in block})

    FINDINGS.append(
        {
            "offset": offset,
            "sub2_citation_count": n_cits,
            "original_sub2_text": original,
            "remapped_sub2_block": block.strip(),
            "expected_markers": expected_markers,
            "markers_actually_present": present,
            "distinct_markers_present": len(distinct_found),
            "citation_indices_in_result": [c["index"] for c in result.citations],
            "verdict": "PASS" if len(present) == n_cits else "FAIL",
        }
    )
    OUT.write_text(json.dumps(FINDINGS, indent=2), encoding="utf-8")

    print(f"\n--- offset={offset}, {n_cits} citations ---")
    print(f"ORIGINAL  sub-answer 2 text : {original}")
    print(f"REMAPPED  sub-answer 2 block:\n{block.strip()}")
    print(f"EXPECTED markers: {expected_markers}")
    print(f"PRESENT  markers: {present}")
    print(f"citations list indices: {[c['index'] for c in result.citations]}")

    assert present == expected_markers, (
        f"K1 CONFIRMED: expected distinct markers {expected_markers} in the remapped "
        f"sub-answer, found {present}. Remapped block: {block.strip()!r}"
    )
