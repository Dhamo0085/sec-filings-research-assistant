"""T2-11 — the streaming indexer embeds and upserts one collection at a time.

The three claims the spec asks to be proved, with a fake embedder:

1. collections are embedded and upserted one at a time (not pooled, as v1 did);
2. no batch exceeds the configured size;
3. an interrupted run resumes without re-embedding what it finished.

Claim 3 is the one that matters most in practice. v1 could only skip a whole
collection, so a run killed inside BAC's 8,202 chunks lost all of them. These
tests assert resume at batch granularity, which is what makes an overnight run
survivable.
"""
from __future__ import annotations

from itertools import pairwise

import pytest

from ingestion.indexer import (
    BatchProgress,
    IndexRun,
    group_by_collection,
    index_stream,
    peak_rss_bytes,
)
from models import Chunk

pytestmark = pytest.mark.unit


def make_chunk(ticker: str, year: int, i: int) -> Chunk:
    return Chunk(
        chunk_id=f"{ticker}-{year}-{i}",
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
    """Records every batch it was asked to embed, in order."""

    def __init__(self) -> None:
        self.dense_batches: list[list[str]] = []
        self.sparse_batches: list[list[str]] = []

    def encode_dense(self, texts):
        self.dense_batches.append(list(texts))
        return [[float(len(t)), 0.5] for t in texts]

    def encode_sparse(self, texts):
        self.sparse_batches.append(list(texts))
        return [([0, 1], [1.0, 2.0]) for _ in texts]

    @property
    def texts_embedded(self) -> list[str]:
        return [t for batch in self.dense_batches for t in batch]


class FakeStore:
    """An in-memory stand-in for Qdrant that records the call order.

    It deliberately does NOT validate point ids. Real Qdrant requires a UUID
    or an unsigned int, and this fake accepting "AAPL-2024-0000" is what hid
    that from the first draft of the integration test. Readable ids keep the
    assertions below legible; the constraint belongs to the store, and
    tests/integration/test_stream_indexer_qdrant.py exercises the real one.
    """

    def __init__(self, existing: dict | None = None) -> None:
        self.collections: dict[str, set] = {k: set(v) for k, v in (existing or {}).items()}
        self.calls: list[tuple[str, str, int]] = []   # (op, collection, size)
        self.fail_on_upsert: tuple[str, int] | None = None   # (collection, nth call)
        self._upserts_seen: dict[str, int] = {}

    def collection_exists(self, name):
        self.calls.append(("exists", name, 0))
        return name in self.collections

    def create_collection(self, name):
        self.calls.append(("create", name, 0))
        self.collections[name] = set()

    def delete_collection(self, name):
        self.calls.append(("delete", name, 0))
        self.collections.pop(name, None)

    def existing_point_ids(self, name):
        self.calls.append(("read-ids", name, 0))
        return set(self.collections.get(name, set()))

    def upsert(self, name, chunks, dense, sparse):
        assert len(chunks) == len(dense) == len(sparse)
        n = self._upserts_seen[name] = self._upserts_seen.get(name, 0) + 1
        if self.fail_on_upsert == (name, n):
            raise RuntimeError("simulated interruption")
        self.calls.append(("upsert", name, len(chunks)))
        self.collections[name].update(str(c.chunk_id) for c in chunks)


# ── claim 1: one collection at a time ──────────────────────────────────────

def test_collections_are_embedded_and_upserted_one_at_a_time():
    chunks = ([make_chunk("AAPL", 2024, i) for i in range(5)]
              + [make_chunk("MSFT", 2024, i) for i in range(7)])
    emb, store = FakeEmbedder(), FakeStore()

    run = index_stream(chunks, batch_size=4, embedder=emb, store=store)

    # Every text embedded for a collection must be contiguous: v1's defect was
    # pooling all collections into one embed call.
    owners = [t.split()[0] for t in emb.texts_embedded]
    transitions = sum(1 for a, b in pairwise(owners) if a != b)
    assert transitions == 1, f"collections were interleaved: {owners}"

    # ...and no embed batch may mix two collections.
    for batch in emb.dense_batches:
        assert len({t.split()[0] for t in batch}) == 1

    # The upserts for a collection must come before the next one is created,
    # i.e. points are written as we go rather than at the end.
    upsert_cols = [name for op, name, _ in store.calls if op == "upsert"]
    assert upsert_cols == ["AAPL_2024"] * 2 + ["MSFT_2024"] * 2

    assert run.chunks_embedded == 12
    assert {c.name for c in run.collections} == {"AAPL_2024", "MSFT_2024"}
    assert all(c.status == "indexed" for c in run.collections)


def test_smallest_collection_is_processed_first():
    """An interruption should leave whole small collections behind."""
    chunks = ([make_chunk("BAC", 2024, i) for i in range(9)]
              + [make_chunk("AAPL", 2024, i) for i in range(2)]
              + [make_chunk("MSFT", 2024, i) for i in range(5)])
    emb, store = FakeEmbedder(), FakeStore()
    run = index_stream(chunks, batch_size=16, embedder=emb, store=store)
    assert [c.name for c in run.collections] == ["AAPL_2024", "MSFT_2024", "BAC_2024"]


# ── claim 2: bounded batches ───────────────────────────────────────────────

@pytest.mark.parametrize("batch_size", [1, 3, 8, 64])
def test_no_batch_exceeds_the_configured_size(batch_size):
    chunks = [make_chunk("AAPL", 2024, i) for i in range(17)]
    emb, store = FakeEmbedder(), FakeStore()
    index_stream(chunks, batch_size=batch_size, embedder=emb, store=store)

    assert emb.dense_batches, "nothing was embedded"
    for batch in emb.dense_batches:
        assert 0 < len(batch) <= batch_size
    for op, _name, size in store.calls:
        if op == "upsert":
            assert 0 < size <= batch_size
    assert sum(len(b) for b in emb.dense_batches) == 17


def test_dense_and_sparse_see_the_same_batches():
    """A mismatch would pair a chunk with another chunk's sparse vector."""
    chunks = [make_chunk("AAPL", 2024, i) for i in range(10)]
    emb, store = FakeEmbedder(), FakeStore()
    index_stream(chunks, batch_size=3, embedder=emb, store=store)
    assert emb.dense_batches == emb.sparse_batches


def test_batch_size_below_one_is_rejected():
    with pytest.raises(ValueError):
        index_stream([make_chunk("AAPL", 2024, 0)], batch_size=0,
                     embedder=FakeEmbedder(), store=FakeStore())


# ── claim 3: resume ────────────────────────────────────────────────────────

def test_finished_collection_is_not_re_embedded():
    chunks = [make_chunk("AAPL", 2024, i) for i in range(5)]
    store = FakeStore({"AAPL_2024": {c.chunk_id for c in chunks}})
    emb = FakeEmbedder()

    run = index_stream(chunks, batch_size=2, embedder=emb, store=store)

    assert emb.dense_batches == [], "a complete collection was re-embedded"
    assert run.chunks_embedded == 0
    assert [c.status for c in run.collections] == ["skipped"]
    assert run.collections[0].chunks_already_present == 5


def test_interrupted_collection_resumes_without_re_embedding_finished_batches():
    chunks = [make_chunk("AAPL", 2024, i) for i in range(10)]

    # First run: fail on the third upsert, so batches 1-2 (4 chunks) survive.
    store = FakeStore()
    store.fail_on_upsert = ("AAPL_2024", 3)
    first = index_stream(chunks, batch_size=2, embedder=FakeEmbedder(), store=store)
    assert [c.status for c in first.collections] == ["failed"]
    assert "simulated interruption" in (first.collections[0].error or "")
    assert len(store.collections["AAPL_2024"]) == 4

    # Second run: the same inputs, nothing forced.
    store.fail_on_upsert = None
    emb = FakeEmbedder()
    calls_before = len(store.calls)
    second = index_stream(chunks, batch_size=2, embedder=emb, store=store)

    assert second.chunks_embedded == 6, "resume re-embedded finished work"
    assert sorted(emb.texts_embedded) == sorted(
        c.text for c in chunks[4:]
    ), "the wrong chunks were resumed"
    assert [c.status for c in second.collections] == ["resumed"]
    assert second.collections[0].chunks_already_present == 4
    # The collection already existed, so it must not be recreated.
    second_run_calls = store.calls[calls_before:]
    assert ("create", "AAPL_2024", 0) not in second_run_calls, second_run_calls
    assert len(store.collections["AAPL_2024"]) == 10


def test_force_reindex_deletes_and_rebuilds():
    chunks = [make_chunk("AAPL", 2024, i) for i in range(4)]
    store = FakeStore({"AAPL_2024": {c.chunk_id for c in chunks}})
    emb = FakeEmbedder()
    run = index_stream(chunks, batch_size=2, embedder=emb, store=store,
                       force_reindex=True)
    ops = [op for op, name, _ in store.calls if name == "AAPL_2024"]
    assert "delete" in ops and "create" in ops
    assert run.chunks_embedded == 4
    assert len(emb.texts_embedded) == 4


# ── failure isolation ──────────────────────────────────────────────────────

def test_one_failing_collection_does_not_abort_the_others():
    chunks = ([make_chunk("AAPL", 2024, i) for i in range(2)]
              + [make_chunk("MSFT", 2024, i) for i in range(4)])
    store = FakeStore()
    store.fail_on_upsert = ("AAPL_2024", 1)
    run = index_stream(chunks, batch_size=2, embedder=FakeEmbedder(), store=store)

    by_name = {c.name: c for c in run.collections}
    assert by_name["AAPL_2024"].status == "failed"
    assert by_name["MSFT_2024"].status == "indexed"
    assert by_name["MSFT_2024"].chunks_embedded == 4
    assert [c.name for c in run.failed] == ["AAPL_2024"]


# ── bookkeeping ────────────────────────────────────────────────────────────

def test_progress_reports_rate_and_peak_rss():
    chunks = [make_chunk("AAPL", 2024, i) for i in range(6)]
    seen: list[BatchProgress] = []
    index_stream(chunks, batch_size=2, embedder=FakeEmbedder(), store=FakeStore(),
                 progress=seen.append)
    assert len(seen) == 3
    assert [p.chunks_done for p in seen] == [2, 4, 6]
    assert all(p.chunks_total == 6 for p in seen)
    assert all(p.peak_rss_bytes > 0 for p in seen)
    assert all(p.chunks_per_second > 0 for p in seen)
    assert "chunks/s" in seen[0].line()


def test_limit_per_collection_caps_profiling_runs():
    chunks = [make_chunk("AAPL", 2024, i) for i in range(50)]
    emb, store = FakeEmbedder(), FakeStore()
    run = index_stream(chunks, batch_size=8, embedder=emb, store=store,
                       limit_per_collection=20)
    assert run.chunks_embedded == 20
    assert len(emb.texts_embedded) == 20


def test_group_by_collection_uses_v1_naming():
    grouped = group_by_collection([make_chunk("AAPL", 2024, 0),
                                   make_chunk("AAPL", 2023, 0),
                                   make_chunk("MSFT", 2024, 0)])
    assert sorted(grouped) == ["AAPL_2023", "AAPL_2024", "MSFT_2024"]


def test_run_report_is_json_serialisable():
    import json
    chunks = [make_chunk("AAPL", 2024, i) for i in range(3)]
    run = index_stream(chunks, batch_size=2, embedder=FakeEmbedder(), store=FakeStore())
    payload = json.loads(json.dumps(run.to_dict()))
    assert payload["batch_size"] == 2
    assert payload["chunks_embedded"] == 3
    assert payload["collections"][0]["name"] == "AAPL_2024"


def test_peak_rss_is_positive_and_plausible():
    rss = peak_rss_bytes()
    assert 1e6 < rss < 1e11, f"implausible peak RSS: {rss}"


def test_empty_input_is_a_no_op():
    run = index_stream([], batch_size=4, embedder=FakeEmbedder(), store=FakeStore())
    assert isinstance(run, IndexRun)
    assert run.collections == []
    assert run.chunks_embedded == 0
