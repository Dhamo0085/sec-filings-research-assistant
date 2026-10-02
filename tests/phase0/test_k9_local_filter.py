"""
Phase 0 / Step 1.4 — K9: does Qdrant LOCAL mode honour payload filters in
query_points()?

data/qdrant does not exist in this checkout, so instead of opening AAPL_2024
we build a throwaway collection in a temp directory with the same shape
retrieval/vector_store.py creates (named "dense" + "sparse" vectors, a
`chunk_type` payload field) and run the exact query_points() call
hybrid_search() makes, filtered and unfiltered.

retrieval/vector_store.py is NOT imported or modified; the call is replicated
so the test runs without fastembed.
"""
from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client import QdrantClient                      # noqa: E402
from qdrant_client.models import (                          # noqa: E402
    Distance, FieldCondition, Filter, Fusion, FusionQuery, MatchValue,
    PointStruct, Prefetch, SparseVector, SparseVectorParams, VectorParams,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "reports" / "phase0" / "k9_local_filter.json"
DIM = 8


def _build(client: QdrantClient, name: str, n: int = 60) -> None:
    client.create_collection(
        collection_name=name,
        vectors_config={"dense": VectorParams(size=DIM, distance=Distance.COSINE)},
        sparse_vectors_config={"sparse": SparseVectorParams()},
    )
    pts = []
    for i in range(n):
        ctype = ["text", "table", "footnote"][i % 3]
        pts.append(PointStruct(
            id=i,
            vector={"dense": [(i % 7) / 7 + 0.01 * j for j in range(DIM)],
                    "sparse": SparseVector(indices=[i % 11, (i + 3) % 11], values=[1.0, 0.5])},
            payload={"chunk_type": ctype, "ticker": "AAPL", "fiscal_year": 2024,
                     "section_name": f"section_{i % 5}", "text": f"chunk {i} ({ctype})"},
        ))
    client.upsert(collection_name=name, points=pts)


def _search(client: QdrantClient, name: str, chunk_type_filter: str | None, top_k: int = 10):
    """Replicates retrieval.vector_store.hybrid_search()'s query_points() call."""
    conditions = []
    if chunk_type_filter:
        conditions.append(FieldCondition(key="chunk_type",
                                         match=MatchValue(value=chunk_type_filter)))
    qfilter = Filter(must=conditions) if conditions else None
    resp = client.query_points(
        collection_name=name,
        prefetch=[
            Prefetch(query=[0.5] * DIM, using="dense", limit=top_k),
            Prefetch(query=SparseVector(indices=[1, 4], values=[1.0, 0.5]),
                     using="sparse", limit=top_k),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        query_filter=qfilter,
        with_payload=True,
    )
    return [p.payload for p in resp.points]


def test_local_mode_payload_filter():
    with tempfile.TemporaryDirectory() as td:
        client = QdrantClient(path=str(Path(td) / "qdrant"))   # LOCAL (on-disk) mode
        _build(client, "AAPL_2024")

        filtered = _search(client, "AAPL_2024", "table")
        unfiltered = _search(client, "AAPL_2024", None)

        f_dist = Counter(p["chunk_type"] for p in filtered)
        u_dist = Counter(p["chunk_type"] for p in unfiltered)
        leaked = [t for t in f_dist if t != "table"]

        finding = {
            "mode": "qdrant local (path=...) — same construction as "
                    "retrieval/vector_store.get_client() when settings.qdrant_url is unset",
            "filtered_chunk_type_distribution": dict(f_dist),
            "unfiltered_chunk_type_distribution": dict(u_dist),
            "non_table_chunks_in_filtered_result": leaked,
            "n_filtered": len(filtered),
            "K9": "CONFIRMED (filter ignored)" if leaked else "REFUTED (filter honoured)",
        }
        OUT.write_text(json.dumps(finding, indent=2), encoding="utf-8")
        print("\n" + json.dumps(finding, indent=2))

        assert not leaked, (
            f"K9 CONFIRMED: chunk_type_filter='table' returned {dict(f_dist)} "
            f"— local mode did not honour the payload filter."
        )
