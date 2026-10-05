"""T4-09: re-index integrity, and the guards that let P4-12 run unattended.

The P4-12 driver is a long unattended job that writes next to irreplaceable
data: ``data/qdrant`` (the live index), ``data/qdrant_v1_backup`` (the frozen
v1 index the whole D25 comparison rests on), ``data/parsed`` and
``data/chunks``. Two classes of defect matter, and neither shows up in a run
that goes well:

* a guard that cannot fire — a "no conflict" result that would be printed just
  the same if the output path *were* ``data/qdrant``;
* a resume rule that reuses work it should have redone, which silently indexes
  text the current parser no longer produces.

So every test below that asserts a check passes has a partner that plants the
fault and asserts the same check fails (CLAUDE.md rule 15).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import rechunk_corpus as rc
from scripts import reindex_v2 as rx
from scripts import reparse_corpus as rp

pytestmark = pytest.mark.unit


# ── path safety ────────────────────────────────────────────────────────────

def test_disjoint_output_paths_raise_no_conflict(tmp_path):
    outputs = {"--parsed-out": tmp_path / "parsed_v2",
               "--chunks-out": tmp_path / "chunks_v2",
               "--qdrant-out": tmp_path / "qdrant_v2"}
    protected = [tmp_path / "parsed", tmp_path / "chunks", tmp_path / "qdrant"]
    assert rx.path_conflicts(outputs, protected) == []


def test_an_output_that_is_a_protected_directory_is_refused(tmp_path):
    """The negative control for the test above: the same call, one path moved."""
    outputs = {"--qdrant-out": tmp_path / "qdrant"}
    problems = rx.path_conflicts(outputs, [tmp_path / "qdrant"])
    assert len(problems) == 1
    assert "protected" in problems[0]


def test_an_output_nested_inside_a_protected_directory_is_refused(tmp_path):
    outputs = {"--chunks-out": tmp_path / "parsed" / "sub"}
    problems = rx.path_conflicts(outputs, [tmp_path / "parsed"])
    assert problems and "is inside the protected" in problems[0]


def test_an_output_that_contains_a_protected_directory_is_refused(tmp_path):
    """Qdrant local mode walks its storage folder, so a store nested in the
    output directory would be read — and rewritten — as part of it."""
    outputs = {"--qdrant-out": tmp_path}
    problems = rx.path_conflicts(outputs, [tmp_path / "qdrant_v1_backup"])
    assert problems and "contains the protected" in problems[0]


def test_two_outputs_sharing_one_directory_are_refused(tmp_path):
    outputs = {"--parsed-out": tmp_path / "x", "--chunks-out": tmp_path / "x"}
    problems = rx.path_conflicts(outputs, [])
    assert problems and "same directory" in problems[0]


# ── the Qdrant lock probe ──────────────────────────────────────────────────

def test_a_directory_with_no_store_reports_absent(tmp_path):
    assert rx.qdrant_lock_state(tmp_path / "nothing-here") == "absent"


def test_an_unheld_lock_reports_free(tmp_path):
    (tmp_path / ".lock").write_text("tmp lock file", encoding="utf-8")
    assert rx.qdrant_lock_state(tmp_path) == "free"


def test_a_held_lock_reports_held(tmp_path):
    """The negative control that matters most.

    A running ``uvicorn`` holding the storage lock already cost this project
    one evaluation run (STATE section 8), and a probe that always answered
    "free" would look exactly like the passing case. ``flock`` is per open file
    description, so a second ``open()`` here conflicts just as another process
    would.
    """
    import portalocker

    lock = tmp_path / ".lock"
    lock.write_text("tmp lock file", encoding="utf-8")
    with lock.open("r+") as holder:
        portalocker.lock(
            holder, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
        )
        assert rx.qdrant_lock_state(tmp_path) == "held"
        portalocker.unlock(holder)
    assert rx.qdrant_lock_state(tmp_path) == "free"


def test_probing_does_not_create_or_modify_the_lock_file(tmp_path):
    """Probing a protected store must leave no trace in its fingerprint."""
    (tmp_path / ".lock").write_text("tmp lock file", encoding="utf-8")
    (tmp_path / "meta.json").write_text('{"collections": {}, "aliases": {}}',
                                        encoding="utf-8")
    before = rx.fingerprint_dir(tmp_path)
    assert rx.qdrant_lock_state(tmp_path) == "free"
    assert rx.fingerprint_dir(tmp_path) == before

    empty = tmp_path / "no-store"
    assert rx.qdrant_lock_state(empty) == "absent"
    assert not empty.exists()


# ── reading a store without opening it ─────────────────────────────────────

def _tiny_store(path: Path, collections: dict) -> None:
    """A real local-mode store, built by the real client, then closed."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = QdrantClient(path=str(path))
    try:
        for name, count in collections.items():
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=2, distance=Distance.COSINE),
            )
            client.upsert(collection_name=name, wait=True, points=[
                PointStruct(id=i + 1, vector=[0.1 * (i + 1), 0.2], payload={"i": i})
                for i in range(count)
            ])
    finally:
        client.close()


def test_point_counts_are_read_without_taking_the_lock(tmp_path):
    """``count_points_readonly`` must agree with the client it replaces.

    It exists because ``QdrantClient(path=...)`` takes the exclusive lock and
    opens every collection read-write, which is exactly what "never touch
    ``data/qdrant``" rules out.
    """
    store = tmp_path / "store"
    _tiny_store(store, {"AAPL_2025": 3, "MSFT_2026": 5})

    result = rx.count_points_readonly(store)
    assert result["exists"] is True
    assert result["collections"] == 2
    assert result["points"] == 8
    assert result["per_collection"] == {"AAPL_2025": 3, "MSFT_2026": 5}

    # The probe did not take the lock: it is still free afterwards.
    assert rx.qdrant_lock_state(store) == "free"


def test_a_missing_store_counts_as_empty(tmp_path):
    result = rx.count_points_readonly(tmp_path / "not-a-store")
    assert result == {"exists": False, "collections": 0, "points": 0,
                      "per_collection": {}}


def test_the_point_count_notices_a_changed_store(tmp_path):
    """Negative control for the T4-09 "backup unchanged" assertion."""
    store = tmp_path / "store"
    _tiny_store(store, {"AAPL_2025": 3})
    before = rx.count_points_readonly(store)

    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    client = QdrantClient(path=str(store))
    client.upsert(collection_name="AAPL_2025", wait=True,
                  points=[PointStruct(id=99, vector=[0.3, 0.4], payload={})])
    client.close()

    after = rx.count_points_readonly(store)
    assert after != before
    assert after["points"] == before["points"] + 1


# ── the "was it touched" witness ───────────────────────────────────────────

def test_reading_a_directory_leaves_its_fingerprint_alone(tmp_path):
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.json").write_text("{}", encoding="utf-8")

    before = rx.fingerprint_dir(tmp_path)
    assert (tmp_path / "a.json").read_text(encoding="utf-8") == "[]"
    assert rx.fingerprint_dir(tmp_path) == before
    assert before["files"] == 2


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda d: (d / "c.json").write_text("1", encoding="utf-8"), id="added"),
    pytest.param(lambda d: (d / "a.json").write_text("[1]", encoding="utf-8"), id="rewritten"),
    pytest.param(lambda d: (d / "a.json").unlink(), id="deleted"),
])
def test_any_write_moves_the_fingerprint(tmp_path, mutate):
    (tmp_path / "a.json").write_text("[]", encoding="utf-8")
    before = rx.fingerprint_dir(tmp_path)
    mutate(tmp_path)
    assert rx.fingerprint_dir(tmp_path) != before


# ── resume rules ───────────────────────────────────────────────────────────

def _write_parse(path: Path, doc_id: str = "AAPL_2025") -> None:
    path.write_text(json.dumps({"doc_id": doc_id, "ticker": "AAPL",
                                "fiscal_year": 2025,
                                "sections": [{"section_id": "item_1_business"}]}),
                    encoding="utf-8")


def _write_chunks(path: Path, doc_id: str = "AAPL_2025", n: int = 2) -> None:
    path.write_text(json.dumps([{"chunk_id": f"id-{i}", "doc_id": doc_id,
                                 "chunk_type": "text", "token_count": 10}
                                for i in range(n)]), encoding="utf-8")


def test_a_complete_parse_is_reused(tmp_path):
    parse = tmp_path / "AAPL_2025.json"
    _write_parse(parse)
    assert rp.parse_is_complete(parse) is True


@pytest.mark.parametrize("content, why", [
    ("", "empty file"),
    ("{ truncated", "half-written JSON"),
    ('{"doc_id": "AAPL_2025", "sections": []}', "parsed to no sections"),
    ('{"sections": [{"section_id": "x"}]}', "no doc_id"),
])
def test_an_incomplete_parse_is_redone(tmp_path, content, why):
    """Negative controls: a run killed mid-write must not look finished."""
    parse = tmp_path / "AAPL_2025.json"
    parse.write_text(content, encoding="utf-8")
    assert rp.parse_is_complete(parse) is False, why


def test_a_chunk_file_matching_todays_parse_is_reused(tmp_path):
    parse = tmp_path / "AAPL_2025.json"
    chunks = tmp_path / "AAPL_2025_chunks.json"
    _write_parse(parse)
    _write_chunks(chunks)
    assert rc.chunk_file_is_current(chunks, "AAPL_2025", parse) is True


def test_a_chunk_file_older_than_its_parse_is_stale(tmp_path):
    """The rule doc_id alone cannot express.

    ``doc_id`` is ``f"{ticker}_{fiscal_year}"`` — identical before and after a
    re-parse — so a chunk file written from the *previous* parser passes every
    id check while holding text P4-00's rewrite replaced.
    """
    parse = tmp_path / "AAPL_2025.json"
    chunks = tmp_path / "AAPL_2025_chunks.json"
    _write_chunks(chunks)
    _write_parse(parse)
    import os
    os.utime(chunks, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    assert rc.chunk_file_is_current(chunks, "AAPL_2025", parse) is False


@pytest.mark.parametrize("content, why", [
    ("[]", "no chunks at all"),
    ("[{", "half-written JSON"),
    ('[{"chunk_id": "a", "doc_id": "MSFT_2026"}]', "chunks of another filing"),
])
def test_a_bad_chunk_file_is_rechunked(tmp_path, content, why):
    parse = tmp_path / "AAPL_2025.json"
    chunks = tmp_path / "AAPL_2025_chunks.json"
    _write_parse(parse)
    chunks.write_text(content, encoding="utf-8")
    assert rc.chunk_file_is_current(chunks, "AAPL_2025", parse) is False, why


def test_a_missing_chunk_file_is_rechunked(tmp_path):
    assert rc.chunk_file_is_current(tmp_path / "nope.json", "AAPL_2025") is False


def test_an_atomic_write_leaves_no_partial_file(tmp_path):
    target = tmp_path / "out.json"
    rc.write_atomic(target, '{"a": 1}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert list(tmp_path.glob("*.partial")) == []


# ── rebuilding a collection whose points no longer match the chunks ────────

class _FakeStore:
    """A VectorStore that records what the driver asked it to delete."""

    def __init__(self, stored):
        self.stored = {k: set(v) for k, v in stored.items()}
        self.deleted = []

    def collection_exists(self, name): return name in self.stored
    def existing_point_ids(self, name): return set(self.stored[name])
    def delete_collection(self, name):
        self.deleted.append(name)
        self.stored.pop(name, None)


class _FakeChunk:
    def __init__(self, chunk_id, ticker, fiscal_year):
        self.chunk_id = chunk_id
        self.ticker = ticker
        self.fiscal_year = fiscal_year


def _say(_message=""):
    return None


def test_a_collection_whose_points_match_is_left_alone(tmp_path):
    chunks = [_FakeChunk("a", "AAPL", 2025), _FakeChunk("b", "AAPL", 2025)]
    store = _FakeStore({"AAPL_2025": {"a", "b"}})
    assert rx.prune_stale_collections(store, chunks, _say) == []
    assert store.deleted == []


def test_a_partly_indexed_collection_is_resumed_not_rebuilt(tmp_path):
    """A crash mid-collection leaves a subset. That must survive, or every
    interrupted overnight run restarts from zero."""
    chunks = [_FakeChunk("a", "AAPL", 2025), _FakeChunk("b", "AAPL", 2025)]
    store = _FakeStore({"AAPL_2025": {"a"}})
    assert rx.prune_stale_collections(store, chunks, _say) == []
    assert store.deleted == []


def test_a_collection_holding_points_no_chunk_file_claims_is_rebuilt(tmp_path):
    """``Chunk.chunk_id`` is a fresh uuid4 per chunking run, so a re-chunk
    renames every point. Without this the store would keep both copies."""
    chunks = [_FakeChunk("new-a", "AAPL", 2025)]
    store = _FakeStore({"AAPL_2025": {"old-a", "old-b"}})
    assert rx.prune_stale_collections(store, chunks, _say) == ["AAPL_2025"]
    assert store.deleted == ["AAPL_2025"]


def test_only_the_stale_collection_is_rebuilt(tmp_path):
    chunks = [_FakeChunk("a", "AAPL", 2025), _FakeChunk("new", "MSFT", 2026)]
    store = _FakeStore({"AAPL_2025": {"a"}, "MSFT_2026": {"old"}})
    assert rx.prune_stale_collections(store, chunks, _say) == ["MSFT_2026"]


# ── scope ──────────────────────────────────────────────────────────────────

def test_the_scope_is_the_twelve_bundled_tickers_plus_nflx():
    tickers = rx.scope_tickers()
    assert len(tickers) == 13
    assert "NFLX" in tickers
    # BLK carries FY2023 under BlackRock's old CIK (D24); it is in scope
    # because it is one of the twelve, not as a special case.
    assert "BLK" in tickers
    assert "TSLA" not in tickers


def test_filings_out_of_scope_are_excluded(tmp_path):
    for stem in ("AAPL_2025", "NFLX_2023", "BLK_2023", "TSLA_2025"):
        (tmp_path / f"{stem}.json").write_text("{}", encoding="utf-8")
    found = rx.filings_in_scope(tmp_path, rx.scope_tickers())
    assert found == ["AAPL_2025", "BLK_2023", "NFLX_2023"]
    assert "TSLA_2025" not in found


def test_the_required_filings_are_named_so_a_short_corpus_is_refused():
    """P4-12 names BLK FY2023 and NFLX explicitly; a source parse missing them
    would otherwise produce a perfectly clean run of the wrong job."""
    assert set(rx.REQUIRED_FILINGS) == {"BLK_2023", "NFLX_2023", "NFLX_2024",
                                        "NFLX_2025"}
