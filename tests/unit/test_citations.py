"""T1-01 — citation remapping (K1).

v1 remapped a sub-answer's [N] markers with successive str.replace() calls, so
a marker shifted into a number a later iteration also searched for was rewritten
again. Phase 0 reproduced it in the real synthesizer
(reports/phase0/k1_citation_remap.json): with offset=1, [1][2][3] all collapsed
to [4]; with offset=2 they became [5][4][5]. The citations list was renumbered
correctly, so the answer text and the source list disagreed.
"""
from __future__ import annotations

import pytest

from generation.citations import remap_citations


def _cits(n: int, start: int = 1) -> list[dict]:
    return [
        {"index": i, "company": f"C{i}", "ticker": "AAPL", "fiscal_year": 2024,
         "section": f"section_{i}"}
        for i in range(start, start + n)
    ]


# ── the exact Phase 0 regressions ────────────────────────────────────────────

def test_k1_offset_1_three_citations():
    text, cits = remap_citations("B [1] C [2] D [3].", _cits(3), offset=1)
    assert text == "B [2] C [3] D [4]."
    assert [c["index"] for c in cits] == [2, 3, 4]


def test_k1_offset_2_three_citations():
    text, cits = remap_citations("B [1] C [2] D [3].", _cits(3), offset=2)
    assert text == "B [3] C [4] D [5]."
    assert [c["index"] for c in cits] == [3, 4, 5]


# ── table test: offsets 0-5 x 1-12 citations ────────────────────────────────

@pytest.mark.parametrize("offset", range(0, 6))
@pytest.mark.parametrize("n", range(1, 13))
def test_offsets_and_counts(offset, n):
    text = " ".join(f"w{i} [{i}]" for i in range(1, n + 1))
    out, cits = remap_citations(text, _cits(n), offset=offset)
    expected = " ".join(f"w{i} [{i + offset}]" for i in range(1, n + 1))
    assert out == expected
    assert [c["index"] for c in cits] == [i + offset for i in range(1, n + 1)]
    # every marker is distinct, which is what K1 destroyed
    markers = [t for t in out.split() if t.startswith("[")]
    assert len(set(markers)) == n


def test_two_digit_markers_not_confused_with_one_digit():
    """[1] and [10] must shift independently — the bug class behind K1."""
    text = "a [1] b [10] c [11] d [2]"
    out, _ = remap_citations(text, _cits(12), offset=5)
    assert out == "a [6] b [15] c [16] d [7]"


def test_marker_shifted_onto_a_later_source_index_is_not_rewritten_twice():
    """offset=1 maps 1->2 while 2 is itself a citation; one pass only."""
    out, _ = remap_citations("x [1] y [2]", _cits(2), offset=1)
    assert out == "x [2] y [3]"


# ── bracket content that is not a citation ──────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "see [Note 7] and [1]",
        "an array index a[0] and [1]",
        "[see Item 1A] plus [1]",
        "money [USD] and [1]",
    ],
)
def test_non_citation_brackets_untouched(text):
    out, _ = remap_citations(text, _cits(1), offset=3)
    assert "[4]" in out
    # the non-citation bracket survives verbatim
    head = text.rsplit("[1]", 1)[0]
    assert out.startswith(head)


def test_numeric_bracket_outside_the_citation_set_is_left_alone():
    """[9] is not one of this sub-answer's citations, so it must not move."""
    out, _ = remap_citations("a [1] b [9]", _cits(1), offset=2)
    assert out == "a [3] b [9]"


# ── invariants ──────────────────────────────────────────────────────────────

def test_offset_zero_is_identity():
    text = "a [1] b [2] c [3]"
    out, cits = remap_citations(text, _cits(3), offset=0)
    assert out == text
    assert [c["index"] for c in cits] == [1, 2, 3]


def test_idempotent_under_offset_zero():
    text = "a [1] b [2]"
    once, c1 = remap_citations(text, _cits(2), offset=0)
    twice, c2 = remap_citations(once, c1, offset=0)
    assert once == twice
    assert c1 == c2


def test_composition_equals_single_shift():
    """Applying offset 2 then 3 equals applying 5 in one go."""
    text = "a [1] b [2] c [3]"
    mid, mid_c = remap_citations(text, _cits(3), offset=2)
    end, end_c = remap_citations(mid, mid_c, offset=3)
    direct, direct_c = remap_citations(text, _cits(3), offset=5)
    assert end == direct
    assert [c["index"] for c in end_c] == [c["index"] for c in direct_c]


def test_citations_metadata_preserved():
    cits = _cits(2)
    cits[0]["section"] = "item_1a_risk_factors"
    _, out = remap_citations("a [1] b [2]", cits, offset=4)
    assert out[0]["section"] == "item_1a_risk_factors"
    assert out[0]["ticker"] == "AAPL"
    assert out[0]["index"] == 5


def test_empty_citations_returns_text_unchanged():
    out, cits = remap_citations("no markers here", [], offset=3)
    assert out == "no markers here"
    assert cits == []


def test_original_inputs_not_mutated():
    cits = _cits(2)
    before = [dict(c) for c in cits]
    remap_citations("a [1] b [2]", cits, offset=7)
    assert cits == before
