"""Normalize citation markers at the generator boundary (D20, P3-05, T3-10).

    from generation.normalize import normalize_markers
    normalize_markers("Revenue rose 【1】 then fell ［2］")
    # ("Revenue rose [1] then fell [2]", {"cjk_brackets": 1, "fullwidth_square": 1})

Phase 1 measured this: Groq's ``gpt-oss`` models cite with **fullwidth
brackets** (``【1】``), and the whole pipeline — the remapper in
``generation/citations.py``, the UI's marker rendering, and Phase 4's
citation-validity scorer — matches only ASCII ``[1]``. Left alone, the
markers are invisible to every one of them: the answer looks cited to a
reader and uncited to the machinery.

Two design points
-----------------
**Normalize, do not silently accept.** Every substitution is counted and the
counts go into the trace (D20), so format drift stays visible rather than
being absorbed. A model that suddenly emits a new bracket style shows up as a
zero count here and a citation-validity drop in Phase 4, which is the pair of
signals needed to notice it.

**Only brackets around a bare integer.** ``【Note 7】`` is not a citation
marker and is left as it is; converting it would invent a reference. The same
rule the ASCII remapper already follows.
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

#: Opening/closing pairs observed or plausible from the providers in use.
#: Each entry is (name, opening character class, closing character class).
_BRACKET_STYLES = (
    # Groq gpt-oss, measured in Phase 1 (docs/STATE.md section 3).
    ("cjk_brackets", "【", "】"),            # 【1】
    ("fullwidth_square", "［", "］"),        # ［1］
    ("white_square", "〚", "〛"),            # 〚1〛
    ("lenticular_white", "〖", "〗"),        # 〖1〗
    ("angle_brackets", "〈", "〉"),          # 〈1〉
)

#: Digits a model may use inside a marker. Fullwidth digits travel with
#: fullwidth brackets, so a marker can be fullwidth end to end.
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９",
                                  "0123456789")

#: The body of a marker: one number, or a comma/semicolon separated list of
#: them. The list form is allowed inside every bracket style, because a model
#: that writes fullwidth brackets can also write "【1, 2】".
_MARKER_BODY = r"([0-9０-９]+(?:\s*[,;]\s*[0-9０-９]+)*)"

_PATTERNS = tuple(
    (name, re.compile(f"{re.escape(open_ch)}\\s*{_MARKER_BODY}\\s*{re.escape(close_ch)}"))
    for name, open_ch, close_ch in _BRACKET_STYLES
)

#: An ASCII marker whose digits are fullwidth: "[１]".
_ASCII_WITH_FULLWIDTH_DIGITS = re.compile(r"\[\s*([０-９]+)\s*\]")

#: Several sources in one bracket: "[1, 2]", "[1,2,3]", "[1; 2]".
#:
#: Observed live from Gemini Flash-Lite in the P3-09 walkthrough ("...a
#: majority of supplier facilities ... located outside the U.S [1, 2]"), and
#: it defeats the single-marker machinery completely: the remapper in
#: generation/citations.py and the pruner in answering/text_answer.py both
#: match ``[(\d+)]`` only, so the second source was dropped from the citation
#: list while the text still pointed at it — a reference the reader cannot
#: follow, which is exactly what the pruning exists to prevent.
#:
#: A range ("[1-3]") is deliberately not matched: it is not a list of
#: sources, and expanding it would invent references.
_COMBINED_MARKER = re.compile(r"\[\s*(\d+(?:\s*[,;]\s*\d+)+)\s*\]")


def normalize_markers(text: str) -> Tuple[str, Dict[str, int]]:
    """Rewrite non-ASCII citation markers as ``[N]``, counting each style.

    Returns ``(normalized_text, counts)``. ``counts`` has an entry only for
    styles that actually fired, so an empty dict means the model already used
    ASCII — which is the normal case and worth being able to see.

    One pass per style, with no overlap between styles, so a reply mixing
    several is handled in a single call.
    """
    if not text:
        return text, {}

    counts: Dict[str, int] = {}

    def _replace(name: str):
        def inner(match: re.Match) -> str:
            body = match.group(1).translate(_FULLWIDTH_DIGITS)
            numbers = [n.strip() for n in re.split(r"[,;]", body)]
            if not all(n.isdigit() for n in numbers) or not numbers:
                return match.group(0)
            counts[name] = counts.get(name, 0) + 1
            if len(numbers) > 1:
                counts["combined_markers"] = counts.get("combined_markers", 0) + 1
            return " ".join(f"[{int(n)}]" for n in numbers)
        return inner

    out = text
    for name, pattern in _PATTERNS:
        out = pattern.sub(_replace(name), out)
    out = _ASCII_WITH_FULLWIDTH_DIGITS.sub(_replace("fullwidth_digits"), out)

    def _split_combined(match: re.Match) -> str:
        numbers = [n.strip() for n in re.split(r"[,;]", match.group(1))]
        counts["combined_markers"] = counts.get("combined_markers", 0) + 1
        return " ".join(f"[{int(n)}]" for n in numbers)

    # Last, so a combined marker written in fullwidth brackets has already
    # become an ASCII one by the time this runs.
    out = _COMBINED_MARKER.sub(_split_combined, out)
    return out, counts
