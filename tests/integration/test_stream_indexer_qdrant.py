"""T2-11, real-store half: the streaming indexer against a real local Qdrant.

tests/unit/test_stream_indexer.py proves the batching and resume logic with a
fake store, which is where the precise assertions belong. It cannot prove the
wiring: that `QdrantVectorStore.existing_point_ids` really reads back the ids
`upsert_chunks` wrote, and that resume therefore works against the store the
indexer actually uses. A fake that agreed with itself would pass either way.

Qdrant local mode is a file store, so this stays inside the offline guarantee —
no socket is opened. The embedder is still fake: loading an ONNX model would
make this a minute-long test for no extra coverage.
"""
from __future__ import annotations

import uuid

import pytest

from models import Chunk

pytestmark = pytest.mark.integration


def make_chunk(ticker: str, year: int, i: int, dim: int) -> Chunk:
    # Real Qdrant point ids must be a UUID or an unsigned int, and v1's
    # Chunk.chunk_id defaults to str(uuid.uuid4()). The first draft of this
    # test used readable ids like "AAPL-2024-0000"; the fake store in the unit
    # tests accepted them and real Qdrant rejected them outright, which is the
    # reason this file exists. uuid5 keeps them deterministic.
    return Chunk(
        chunk_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ticker}/{year}/{i}")),
        parent_id=f"{ticker}-{year}-section",
        doc_id=f"{ticker}-{year}-doc",
        text=f"{ticker} {year} chunk {i}",
        company=f"{ticker} Inc.",
        ticker=ticker,
        filing_type="10-K",
        fiscal_year=year,
        section_name="Item 7: MD&A",
        chunk_type="text",
        token_count=10,
        position=i,
    )


class FakeEmbedder:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.batches: list[list[str]] = []

    def encode_dense(self, texts):
        self.batches.append(list(texts))
        # Distinct, unit-ish vectors so Qdrant has something real to store.
        return [[(hash(t) % 100) / 100.0] + [0.1] * (self.dim - 1) for t in texts]

    def encode_sparse(self, texts):
        return [([0, 7], [1.0, 0.5]) for _ in texts]


@pytest.fixture
def qdrant_store(tmp_path, monkeypatch):
    """A QdrantVectorStore backed by a throwaway local directory.

    `retrieval.vector_store` caches one client in a module global and local mode
    takes an exclusive lock on its directory, so the global is reset around the
    test rather than left pointing at a deleted tmp_path.
    """
    import retrieval.vector_store as vs
    from ingestion.indexer import QdrantVectorStore

    monkeypatch.setattr(vs.settings, "qdrant_path", str(tmp_path / "qdrant"))
    monkeypatch.setattr(vs.settings, "qdrant_url", None, raising=False)
    monkeypatch.setattr(vs, "_client", None)
    try:
        yield QdrantVectorStore()
    finally:
        client = vs._client
        if client is not None:
            client.close()
        vs._client = None


def test_real_store_round_trip_and_resume(qdrant_store, monkeypatch):
    import retrieval.vector_store as vs
    from ingestion.indexer import index_stream

    dim = vs.settings.embedding_dim
    chunks = [make_chunk("AAPL", 2024, i, dim) for i in range(10)]

    # First pass: stop after 6 chunks, as an interrupted overnight run would.
    emb = FakeEmbedder(dim)
    first = index_stream(chunks[:6], batch_size=4, embedder=emb,
                         store=qdrant_store)
    assert first.chunks_embedded == 6
    assert [c.status for c in first.collections] == ["indexed"]
    assert vs.list_collections() == ["AAPL_2024"]
    assert vs.get_collection_stats("AAPL_2024")["points_count"] == 6

    # The ids really come back out of Qdrant — this is the claim the fake
    # store cannot make.
    assert qdrant_store.existing_point_ids("AAPL_2024") == {
        c.chunk_id for c in chunks[:6]
    }

    # Second pass: the full set. Only the 4 new chunks may be embedded.
    emb2 = FakeEmbedder(dim)
    second = index_stream(chunks, batch_size=4, embedder=emb2,
                          store=qdrant_store)
    embedded = [t for b in emb2.batches for t in b]
    assert len(embedded) == 4, f"re-embedded finished work: {embedded}"
    assert sorted(embedded) == sorted(c.text for c in chunks[6:])
    assert [c.status for c in second.collections] == ["resumed"]
    assert second.collections[0].chunks_already_present == 6
    assert vs.get_collection_stats("AAPL_2024")["points_count"] == 10

    # Third pass: nothing left to do.
    emb3 = FakeEmbedder(dim)
    third = index_stream(chunks, batch_size=4, embedder=emb3, store=qdrant_store)
    assert emb3.batches == []
    assert [c.status for c in third.collections] == ["skipped"]


def test_real_store_writes_payload_that_retrieval_can_filter(qdrant_store):
    """A streamed point must be a normal v1 point, filters and all."""
    import retrieval.vector_store as vs
    from ingestion.indexer import index_stream

    dim = vs.settings.embedding_dim
    chunks = [make_chunk("AAPL", 2024, i, dim) for i in range(4)]
    chunks[2].chunk_type = "table"
    index_stream(chunks, batch_size=2, embedder=FakeEmbedder(dim),
                 store=qdrant_store)

    hits = vs.hybrid_search(
        collection_name="AAPL_2024",
        query_dense=[0.5] + [0.1] * (dim - 1),
        query_sparse_indices=[0, 7],
        query_sparse_values=[1.0, 0.5],
        top_k=10,
        chunk_type_filter="table",
    )
    assert hits, "the table filter returned nothing from a streamed collection"
    assert {h["payload"]["chunk_type"] for h in hits} == {"table"}
    assert all(h["payload"]["ticker"] == "AAPL" for h in hits)


def test_multiple_collections_are_written_incrementally(qdrant_store):
    """Each collection must exist as soon as its own batches are done."""
    import retrieval.vector_store as vs
    from ingestion.indexer import index_stream

    dim = vs.settings.embedding_dim
    chunks = ([make_chunk("AAPL", 2024, i, dim) for i in range(3)]
              + [make_chunk("MSFT", 2024, i, dim) for i in range(5)])

    seen: list[tuple[str, list[str]]] = []

    def watch(progress):
        # list_collections() mid-run: v1's one-pass path would show none until
        # every chunk of every collection had been embedded.
        seen.append((progress.collection, vs.list_collections()))

    index_stream(chunks, batch_size=2, embedder=FakeEmbedder(dim),
                 store=qdrant_store, progress=watch)

    assert vs.list_collections() == ["AAPL_2024", "MSFT_2024"]
    # While MSFT was still being embedded, AAPL was already queryable.
    during_msft = [cols for col, cols in seen if col == "MSFT_2024"]
    assert during_msft and all("AAPL_2024" in cols for cols in during_msft)
