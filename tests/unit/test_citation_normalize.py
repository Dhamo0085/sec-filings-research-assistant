"""Citation-marker normalization (D20, T3-10).

Phase 1 measured Groq's gpt-oss models citing with fullwidth brackets
(``【1】``) while the remapper, the UI and Phase 4's citation scorer all
match ASCII ``[1]`` only. The markers were therefore invisible to every one of
them: cited to a reader, uncited to the machinery.
"""

from __future__ import annotations

import pytest

from generation.normalize import normalize_markers

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("raw,expected,style", [
    ("Revenue rose 【1】.", "Revenue rose [1].", "cjk_brackets"),
    ("Revenue rose ［1］.", "Revenue rose [1].", "fullwidth_square"),
    ("Revenue rose 〚1〛.", "Revenue rose [1].", "white_square"),
    ("Revenue rose 〖1〗.", "Revenue rose [1].", "lenticular_white"),
    ("Revenue rose 〈1〉.", "Revenue rose [1].", "angle_brackets"),
])
def test_each_observed_bracket_style_becomes_ascii_and_is_counted(raw, expected, style):
    out, counts = normalize_markers(raw)
    assert out == expected
    assert counts == {style: 1}


def test_fullwidth_digits_inside_fullwidth_brackets():
    out, counts = normalize_markers("Revenue rose 【１２】.")
    assert out == "Revenue rose [12]."
    assert counts == {"cjk_brackets": 1}


def test_fullwidth_digits_inside_ascii_brackets():
    out, counts = normalize_markers("Revenue rose [１].")
    assert out == "Revenue rose [1]."
    assert counts == {"fullwidth_digits": 1}


def test_ascii_text_is_untouched_and_counted_as_nothing():
    text = "Revenue rose [1] then fell [2]."
    out, counts = normalize_markers(text)
    assert out == text
    assert counts == {}, "an empty count is the signal that the model used ASCII"


def test_a_reply_that_mixes_styles_is_handled_in_one_pass():
    out, counts = normalize_markers(
        "Revenue rose 【1】, costs fell [2], and margin held ［3］."
    )
    assert out == "Revenue rose [1], costs fell [2], and margin held [3]."
    assert counts == {"cjk_brackets": 1, "fullwidth_square": 1}


def test_non_citation_brackets_are_left_alone():
    """Converting 【Note 7】 would invent a reference."""
    raw = "See 【Note 7】 and [Appendix A] and 【】 for detail."
    out, counts = normalize_markers(raw)
    assert out == raw
    assert counts == {}


def test_multiple_markers_of_one_style_are_all_counted():
    out, counts = normalize_markers("【1】 【2】 【3】")
    assert out == "[1] [2] [3]"
    assert counts == {"cjk_brackets": 3}


def test_whitespace_inside_the_marker_is_absorbed():
    out, _ = normalize_markers("Revenue rose 【 1 】.")
    assert out == "Revenue rose [1]."


def test_leading_zeros_are_normalised():
    out, _ = normalize_markers("【01】")
    assert out == "[1]"


@pytest.mark.parametrize("text", ["", None])
def test_empty_input_is_safe(text):
    out, counts = normalize_markers(text)
    assert out == text and counts == {}


# ── several sources in one bracket ───────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Supplier sites are outside the U.S [1, 2].",
     "Supplier sites are outside the U.S [1] [2]."),
    ("A claim [1,2,3].", "A claim [1] [2] [3]."),
    ("A claim [1; 2].", "A claim [1] [2]."),
    ("A claim [ 10 , 11 ].", "A claim [10] [11]."),
])
def test_a_combined_marker_is_split_into_separate_ones(raw, expected):
    """Observed live from Gemini Flash-Lite. The single-marker parser cannot
    see "[1, 2]", so source 2 was dropped from the citation list while the
    text still referred to it."""
    out, counts = normalize_markers(raw)
    assert out == expected
    assert counts["combined_markers"] == 1


def test_a_combined_marker_in_fullwidth_brackets_is_handled_too():
    out, counts = normalize_markers("A claim 【1, 2】.")
    assert out == "A claim [1] [2]."
    assert counts == {"cjk_brackets": 1, "combined_markers": 1}


def test_a_single_marker_is_not_counted_as_combined():
    _out, counts = normalize_markers("A claim [1].")
    assert "combined_markers" not in counts


def test_a_bracketed_range_is_not_a_combined_marker():
    """"[1-3]" is not a citation list; splitting it would invent sources."""
    raw = "See table [1-3] for detail."
    out, counts = normalize_markers(raw)
    assert out == raw and counts == {}
