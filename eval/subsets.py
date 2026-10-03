"""Which gold items each variant can fairly be asked (P4-05, D21).

The facts store covers all eighteen filers; the text index covers eight. So a
variant that answers from text — V0, and V1 with the facts engine off — simply
has no corpus for a question about Wells Fargo, and scoring it as a wrong answer
would measure the indexing backlog rather than the ablation.

D21's rule is that V0 reports its N explicitly and V1 to V3 are reported on that
same subset, so the comparison is paired. This module computes that subset from
the catalog's own ``collection_name``, which P3-06 populated, rather than from a
hand-kept list that would drift the moment anything else is indexed.

Both numbers are always reported: the full 80 and the paired subset. A table
that showed only one of them would be hiding either the backlog or the
comparison.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


def indexed_pairs(catalog) -> Set[Tuple[str, int]]:
    """(ticker, fiscal_label) for every filing with a text collection."""
    pairs: Set[Tuple[str, int]] = set()
    for filing in catalog.all_filings():
        if filing.collection_name:
            pairs.add((filing.ticker, filing.fiscal_label))
    return pairs


def _item_pairs(item: Mapping) -> List[Tuple[str, Optional[int]]]:
    """Every (ticker, fiscal_label) an item depends on.

    A compare item needs BOTH companies indexed; a trend needs every year. An
    item that names no company — "Should I buy Apple stock?" is routed on the
    sentence, not on a filing — depends on nothing and is always in scope.
    """
    expected = item.get("expected") or {}
    values = expected.get("values")
    if values:
        return [(str(v.get("ticker")), v.get("fiscal_label")) for v in values]
    ticker = expected.get("ticker")
    if not ticker:
        return []
    return [(str(ticker), expected.get("fiscal_label"))]


def needs_text_corpus(item: Mapping) -> bool:
    """Would answering this item require retrieved text?

    Narrative items always do. Abstention items that refuse before retrieval do
    not — but an item is only excluded from the paired subset when it genuinely
    cannot be answered, so the conservative reading is used: anything naming a
    filing is checked.
    """
    return bool(_item_pairs(item))


def in_paired_subset(item: Mapping, pairs: Set[Tuple[str, int]]) -> bool:
    """Is every filing this item needs present in the text index?"""
    required = _item_pairs(item)
    if not required:
        return True
    for ticker, label in required:
        if label is None:
            # No year named ("What risks does Apple disclose?"): any indexed
            # year of that filer will do.
            if not any(t == ticker for t, _ in pairs):
                return False
            continue
        if (ticker, int(label)) not in pairs:
            return False
    return True


def split(
    gold: Sequence[Mapping], pairs: Set[Tuple[str, int]],
) -> Tuple[List[Mapping], List[Mapping]]:
    """(items whose corpus is indexed, items whose corpus is not)."""
    inside = [item for item in gold if in_paired_subset(item, pairs)]
    outside = [item for item in gold if not in_paired_subset(item, pairs)]
    return inside, outside


def describe(gold: Sequence[Mapping], pairs: Set[Tuple[str, int]]) -> Dict:
    inside, outside = split(gold, pairs)
    missing: Dict[str, List[str]] = {}
    for item in outside:
        for ticker, label in _item_pairs(item):
            if label is not None and (ticker, int(label)) not in pairs:
                missing.setdefault(ticker, []).append(f"FY{label}")
    return {
        "n_total": len(gold),
        "n_paired": len(inside),
        "n_excluded": len(outside),
        "excluded_ids": [str(item["id"]) for item in outside],
        "missing_filings": {k: sorted(set(v)) for k, v in sorted(missing.items())},
    }


def ids(items: Iterable[Mapping]) -> Set[str]:
    return {str(item["id"]) for item in items}
