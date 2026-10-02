"""T1-01 (integration) — the K1 regression, against the real synthesize().

Phase 0 reproduced K1 by driving generation.synthesizer.synthesize() with
patched decomposition, retrieval, generation and Groq client, and asserting the
second sub-answer's markers stayed distinct. Those assertions failed then
(reports/phase0/k1_citation_remap.json). They must pass now.

No network and no real LLM: every boundary is patched.
"""
from __future__ import annotations

import pytest

from models import QueryResult

pytestmark = pytest.mark.integration

CAPTURED: list[dict] = []


def _citation(idx: int, ticker: str) -> dict:
    return {"index": idx, "company": f"{ticker} Inc.", "ticker": ticker,
            "fiscal_year": 2024, "section": f"section_{idx}"}


class _FakeCompletions:
    def create(self, **kwargs):
        CAPTURED.append(kwargs)
        msg = type("M", (), {"content": "SYNTHESIZED"})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


class _FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {"completions": _FakeCompletions()})()


def _drive(monkeypatch, sub1_n: int, sub2_n: int):
    import generation.synthesizer as S

    CAPTURED.clear()
    subs = [{"ticker": "AAPL", "year": 2024, "question": "Q1"},
            {"ticker": "MSFT", "year": 2024, "question": "Q2"}]
    monkeypatch.setattr(S, "decompose_query", lambda *a, **k: subs)
    monkeypatch.setattr(S, "decompose_temporal", lambda *a, **k: subs)
    monkeypatch.setattr(S, "retrieve", lambda *a, **k: [])
    fake_client = _FakeClient()
    monkeypatch.setattr(S, "_get_client", lambda: fake_client)

    letters = "ABCDEFGHIJKL"
    text1 = " ".join(f"{letters[i-1]} [{i}]" for i in range(1, sub1_n + 1)) + "."
    text2 = " ".join(f"{letters[i-1]} [{i}]" for i in range(1, sub2_n + 1)) + "."
    results = {
        "Q1": QueryResult(query="Q1", answer=text1,
                          citations=[_citation(i, "AAPL") for i in range(1, sub1_n + 1)],
                          chunks_used=[], query_type="sub_question"),
        "Q2": QueryResult(query="Q2", answer=text2,
                          citations=[_citation(i, "MSFT") for i in range(1, sub2_n + 1)],
                          chunks_used=[], query_type="sub_question"),
    }
    monkeypatch.setattr(S, "generate_answer",
                        lambda query, retrieved, query_type: results[query])

    out = S.synthesize(query="compare", tickers=["AAPL", "MSFT"], years=[2024],
                       query_type="multi_doc")
    assert CAPTURED, "synthesis LLM call was never made"
    return CAPTURED[0]["messages"][1]["content"], out


@pytest.mark.parametrize("sub1_n,sub2_n", [(1, 3), (2, 3), (3, 4), (1, 1), (5, 2)])
def test_second_subanswer_markers_stay_distinct(monkeypatch, sub1_n, sub2_n):
    synthesis_input, _result = _drive(monkeypatch, sub1_n, sub2_n)
    block = synthesis_input.split("---")[-1]

    expected = [f"[{i + sub1_n}]" for i in range(1, sub2_n + 1)]
    present = [m for m in expected if m in block]
    assert present == expected, (
        f"expected distinct markers {expected} in the second sub-answer, "
        f"got block {block.strip()!r}"
    )


def test_citation_list_matches_the_markers_in_the_text(monkeypatch):
    """K1's real harm: the text said [4] while the source list said [2]."""
    synthesis_input, result = _drive(monkeypatch, 1, 3)
    indices = [c["index"] for c in result.citations]
    assert indices == [1, 2, 3, 4]
    block = synthesis_input.split("---")[-1]
    for idx in indices[1:]:
        assert f"[{idx}]" in block or f"[{idx}]" in synthesis_input


def test_no_marker_collapse_across_many_citations(monkeypatch):
    synthesis_input, _ = _drive(monkeypatch, 3, 9)
    block = synthesis_input.split("---")[-1]
    markers = [t for t in block.replace("\n", " ").split() if t.startswith("[")]
    assert len(markers) == len(set(markers)), f"duplicate markers: {markers}"
