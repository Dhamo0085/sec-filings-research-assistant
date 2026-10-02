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
