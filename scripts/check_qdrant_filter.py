#!/usr/bin/env python3
"""Does Qdrant local mode honour payload filters? (P1-10, settles the K9 caveat)

    python scripts/check_qdrant_filter.py --collection AAPL_2024

Phase 0 tested this against a SYNTHETIC collection and found the filter
honoured, so K9 was marked REFUTED — but with two caveats recorded in the
report: the collection was not real, and ``retrieval/vector_store.py`` carries
a comment asserting the opposite for ``scroll_by_section``:

    "query_points() with a payload filter is silently ignored in local Qdrant
     (no payload indexes), so we fall back to scroll + Python filter."

Both cannot be right. This script runs the real ``hybrid_search()`` against a
real ingested collection, with and without ``chunk_type_filter``, and reports
the chunk_type distribution of each. It also records the pinned qdrant-client
version, because the answer may be version-specific.

Exit codes: 0 filter honoured · 1 filter ignored (K9 confirmed) · 2 cannot run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _qdrant_version() -> str:
    try:
        import importlib.metadata as md
        return md.version("qdrant-client")
    except Exception:
        return "unknown"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collection", default=None,
                    help="collection to probe (default: first one with table chunks)")
    ap.add_argument("--query", default="total net sales revenue",
                    help="query text to embed")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--out", default="reports/phase1/qdrant_filter_check.json")
    args = ap.parse_args(argv)

    try:
        from ingestion.embedder import encode_query
        from retrieval.vector_store import hybrid_search, list_collections
    except Exception as exc:
        print(f"cannot import the retrieval stack: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    try:
        collections = sorted(list_collections())
    except Exception as exc:
        print(f"cannot list collections: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not collections:
        print("no collections indexed; run `make ingest` first", file=sys.stderr)
        return 2

    target = args.collection or collections[0]
    if target not in collections:
        print(f"collection {target!r} not found. Available: {collections}",
              file=sys.stderr)
        return 2

    print(f"qdrant-client  : {_qdrant_version()}")
    print(f"collection     : {target}")
    print(f"query          : {args.query!r}\n")

    dense, sparse = encode_query(args.query)
    sparse_indices = list(getattr(sparse, "indices", []) or [])
    sparse_values = [float(v) for v in (getattr(sparse, "values", []) or [])]

    def _search(chunk_type_filter: Optional[str]) -> List[Dict]:
        return hybrid_search(
            collection_name=target,
            query_dense=list(dense),
            query_sparse_indices=sparse_indices,
            query_sparse_values=sparse_values,
            top_k=args.top_k,
            chunk_type_filter=chunk_type_filter,
        )

    try:
        unfiltered = _search(None)
        filtered = _search("table")
    except Exception as exc:
        print(f"search failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    def _dist(hits: List[Dict]) -> Dict[str, int]:
        return dict(Counter((h.get("payload") or {}).get("chunk_type", "?")
                            for h in hits))

    u_dist, f_dist = _dist(unfiltered), _dist(filtered)
    leaked = sorted(t for t in f_dist if t != "table")

    print(f"unfiltered hits: {len(unfiltered)}  distribution: {u_dist}")
    print(f"filtered hits  : {len(filtered)}  distribution: {f_dist}")

    if not filtered:
        verdict = "INCONCLUSIVE"
        print("\nINCONCLUSIVE: the filtered search returned nothing. The collection "
              "may hold no table chunks, so this says nothing about the filter.")
        rc = 2
    elif leaked:
        verdict = "K9_CONFIRMED"
        print(f"\nK9 CONFIRMED: chunk_type_filter='table' returned non-table "
              f"chunk types {leaked}. The 'table-only' retrieval pass is not "
              f"table-only in local mode.")
        rc = 1
    else:
        verdict = "K9_REFUTED"
        print("\nK9 REFUTED: every hit in the filtered search is a table chunk, "
              "against a real ingested collection.")
        rc = 0

    payload = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "qdrant_client_version": _qdrant_version(),
        "collection": target,
        "query": args.query,
        "top_k": args.top_k,
        "unfiltered_distribution": u_dist,
        "filtered_distribution": f_dist,
        "non_table_types_in_filtered_result": leaked,
        "verdict": verdict,
        "note": (
            "Phase 0 reached the same verdict against a synthetic collection. "
            "retrieval/vector_store.py's scroll_by_section comment claims "
            "payload filters are silently ignored in local mode; if the verdict "
            "here is K9_REFUTED that comment is stale for query_points() and "
            "should be corrected, which is why this check exists."
        ),
    }
    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
