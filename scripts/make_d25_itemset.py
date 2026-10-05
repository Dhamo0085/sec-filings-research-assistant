#!/usr/bin/env python3
"""The item set D25's old-index-versus-new-index arm can fairly be run on.

    .venv/bin/python scripts/make_d25_itemset.py

D25 asks what the P4-00 section-boundary rewrite was worth, by running the
same questions against the frozen v1 index and against the re-indexed one.
That comparison is only about the parser if both stores hold the same
filings — and after P4-12 they do not. ``data/qdrant_v1_backup`` holds 25
collections; the live store holds 39, having gained BAC, IVZ, STT, TROW and
WFC and dropped TSLA.

Run as-is, the old arm would be asked about five filers it has never held.
``retrieval.retriever`` intersects its target collections with the live ones
and would simply search fewer, so the old arm would score lower for a reason
that has nothing to do with section boundaries — and the report would read as
if the rewrite had earned it.

So this writes the items whose every (ticker, fiscal_label) is in **both**
stores. The stores are counted by reading each collection's ``storage.sqlite``
over a read-only URI, never with a client, because Qdrant local mode takes an
exclusive lock and the backup must not be opened.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_reindex_integrity import count_store  # noqa: E402


def collection_pairs(store: Path) -> Set[Tuple[str, int]]:
    """(ticker, fiscal_label) for every collection the store holds."""
    pairs: Set[Tuple[str, int]] = set()
    for name in count_store(store):
        ticker, _, year = name.rpartition("_")
        if ticker and year.isdigit():
            pairs.add((ticker, int(year)))
    return pairs


def item_pairs(item: dict) -> List[Tuple[str, Optional[int]]]:
    """Every filing an item depends on — the same rule eval/subsets.py uses."""
    expected = item.get("expected") or {}
    values = expected.get("values")
    if values:
        return [(str(v.get("ticker")), v.get("fiscal_label")) for v in values]
    ticker = expected.get("ticker")
    if not ticker:
        return []
    return [(str(ticker), expected.get("fiscal_label"))]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", type=Path, action="append", default=None,
                    help="an item file to filter; repeatable")
    ap.add_argument("--old", type=Path, default=REPO_ROOT / "data/qdrant_v1_backup")
    ap.add_argument("--new", type=Path, default=REPO_ROOT / "data/qdrant")
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "eval/gold/d25_shared_items.jsonl")
    args = ap.parse_args(argv)

    sources = args.items or [REPO_ROOT / "eval/gold/gold_v1.jsonl",
                             REPO_ROOT / "eval/gold/narrative_retrieval_v1.jsonl"]

    old = collection_pairs(args.old)
    new = collection_pairs(args.new)
    shared = old & new

    kept: List[dict] = []
    dropped = 0
    seen: Set[str] = set()
    for source in sources:
        for line in source.read_text().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if item["id"] in seen:
                continue
            pairs = item_pairs(item)
            if not pairs or any((t, y) not in shared for t, y in pairs):
                dropped += 1
                continue
            seen.add(item["id"])
            kept.append(item)

    kept.sort(key=lambda i: i["id"])
    args.out.write_text("".join(json.dumps(i) + "\n" for i in kept))

    print(f"old store {args.old.name}: {len(old)} filings")
    print(f"new store {args.new.name}: {len(new)} filings")
    print(f"shared:                    {len(shared)} filings "
          f"({len(old - new)} only old, {len(new - old)} only new)")
    print(f"  only in the old store: {sorted(old - new)}")
    print(f"  only in the new store: {len(new - old)} filings "
          f"({sorted({t for t, _ in new - old})})")
    print(f"\nkept {len(kept)} item(s), dropped {dropped}, "
          f"from {len(sources)} source file(s)")
    print(f"wrote {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
