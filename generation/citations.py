"""Citation marker remapping (P1-07, fixes K1).

When the synthesizer merges several sub-answers into one, each sub-answer's
citation numbering starts at [1], so later sub-answers must be shifted by the
number of citations already consumed.

v1 did this with successive ``str.replace()`` calls, one per citation::

    for cit in result.citations:                       # v1, generation/synthesizer.py
        new_idx = cit["index"] + citation_offset
        remapped = remapped.replace(f"[{cit['index']}]", f"[{new_idx}]")

Each replacement rewrites the whole string, so a marker that has already been
shifted into a number a *later* iteration also searches for gets rewritten a
second time. Phase 0 reproduced this against the real ``synthesize()``
(``reports/phase0/k1_citation_remap.json``):

===========  ====================  ====================  ==================
offset       sub-answer in         v1 produced           correct
===========  ====================  ====================  ==================
1            ``B [1] C [2] D [3]`` ``B [4] C [4] D [4]`` ``B [2] C [3] D [4]``
2            ``B [1] C [2] D [3]`` ``B [5] C [4] D [5]`` ``B [3] C [4] D [5]``
===========  ====================  ====================  ==================

``str.replace`` was also substring-based, so ``[1]`` matched inside ``[10]``.

This module does the whole remap in a **single pass** with a regex and a
function replacement, so no substitution can be re-matched. Only markers whose
number is one of *this* sub-answer's citations are touched; any other bracketed
text ("[Note 7]", "a[0]", "[1]" belonging to nothing) is left verbatim.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

# A citation marker is a bracketed integer and nothing else. Anything with
# non-digit content inside the brackets is not a citation.
_MARKER = re.compile(r"\[(\d+)\]")


def remap_citations(
    text: str,
    citations: List[Dict],
    offset: int,
) -> Tuple[str, List[Dict]]:
    """Shift every citation marker in *text* by *offset*, in one pass.

    Args:
        text: a sub-answer containing ``[N]`` markers.
        citations: that sub-answer's citation dicts, each with an ``index``.
        offset: how many citations earlier sub-answers already consumed.

    Returns:
        ``(remapped_text, renumbered_citations)``. The inputs are not mutated;
        ``citations`` is returned as new dicts with ``index`` shifted and every
        other field preserved.

    Markers whose number is not in ``citations`` are left untouched: they are
    not this sub-answer's references, and silently renumbering them would
    invent a source.
    """
    if not citations:
        return text, []

    index_map = {int(c["index"]): int(c["index"]) + offset for c in citations}

    def _shift(match: re.Match) -> str:
        n = int(match.group(1))
        if n in index_map:
            return f"[{index_map[n]}]"
        return match.group(0)

    remapped = _MARKER.sub(_shift, text)
    renumbered = [{**c, "index": index_map[int(c["index"])]} for c in citations]
    return remapped, renumbered
