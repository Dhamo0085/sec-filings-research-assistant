"""Streaming indexer: embed and upsert one collection at a time (P2-00c).

    python -m ingestion.indexer --only MSFT,JPM
    python -m ingestion.indexer --batch-size 32 --report reports/phase2/index.json

**Why this replaces v1's one-pass path.** ``ingestion.embedder.index_chunks``
collects the text of every chunk across every collection, embeds the lot, and
only then upserts::

    all_texts = [c.text for _, col_chunks in needs_indexing for c in col_chunks]
    all_dense  = encode_dense(all_texts,  batch_size=batch_size)
    all_sparse = encode_sparse(all_texts, batch_size=batch_size)

That is deliberate — one large ONNX batch beats many small ones — but it holds
the texts plus a 768-float dense vector plus a sparse vector for the whole
corpus before a single point is written. For the bundled 12 (35,838 chunks) on
this 8 GB machine it reached 2.4 GB RSS, drove swap to 9.6 GB of 10 GB, and had
written nothing after 68 minutes (P1-00). Throughput then measured ~1.6 s/chunk,
which is a swapping machine, not a slow model.

This module keeps the batching but bounds what is in flight:

* **Per collection, in ascending size order.** Cheap collections land first, so
  an interruption still leaves a usable index.
* **Bounded batches.** ``batch_size`` chunks are embedded and upserted, then
  dropped, before the next batch is read. Peak memory is a function of the
  batch, not the corpus.
* **Resumable at batch granularity.** Already-stored point ids are read once per
  collection and skipped, so a run killed mid-collection does not re-embed what
  it finished. v1 could only skip whole collections.
* **Visible progress.** chunks/s and peak RSS per batch, because the only reason
  P1-00's 68-minute run looked healthy was that it printed nothing.

``embedder`` and ``store`` are injectable so T2-11 can assert the batching and
resume behaviour without loading an ONNX model or writing a Qdrant collection.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from loguru import logger

DenseVector = Sequence[float]
SparseVector = Tuple[Sequence[int], Sequence[float]]


# ── peak memory ────────────────────────────────────────────────────────────

def peak_rss_bytes() -> int:
    """Peak resident set size of this process, in bytes.

    ``ru_maxrss`` is bytes on macOS/BSD and kilobytes on Linux — the one
    platform difference that cannot be avoided, so it is converted here rather
    than left for each caller to get wrong. Peak rather than current is the
    number that matters: the question P1-00 raised is whether a run fits in
    memory at its worst moment.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw if sys.platform == "darwin" else raw * 1024


# ── injectable collaborators ───────────────────────────────────────────────

class Embedder(Protocol):
    def encode_dense(self, texts: Sequence[str]) -> Sequence[DenseVector]: ...
    def encode_sparse(self, texts: Sequence[str]) -> Sequence[SparseVector]: ...


class VectorStore(Protocol):
    def collection_exists(self, name: str) -> bool: ...
    def create_collection(self, name: str) -> None: ...
    def delete_collection(self, name: str) -> None: ...
    def existing_point_ids(self, name: str) -> set: ...
    def upsert(self, name: str, chunks: Sequence, dense: Sequence[DenseVector],
               sparse: Sequence[SparseVector]) -> None: ...


class FastEmbedEmbedder:
    """The real embedder: v1's fastembed models, with an optional length cap.

    ``max_length`` exists for the P2-00(d) throughput profile. Note what it
    changes: bge's tokenizer truncates at 512 by default, and 81% of this
    corpus' chunks are longer than that, so lowering the cap shortens what the
    vector actually represents. A collection embedded at 256 is therefore NOT
    comparable with one embedded at 512 — mixing them in one index would make
    retrieval quality depend on which day a collection was built.
    """

    def __init__(self, max_length: Optional[int] = None) -> None:
        self.max_length = max_length
        self._applied = False

    def _apply_max_length(self) -> None:
        if self.max_length is None or self._applied:
            return
        from ingestion.embedder import _get_dense
        tokenizer = _get_dense().model.tokenizer
        tokenizer.enable_truncation(max_length=self.max_length)
        logger.info(f"indexer: dense tokenizer truncation set to "
                    f"{self.max_length} tokens")
        self._applied = True

    def encode_dense(self, texts: Sequence[str]) -> Sequence[DenseVector]:
        from ingestion.embedder import encode_dense
        self._apply_max_length()
        # batch_size=len(texts): this module already decided the batch, so
        # letting the model re-split it would hide the size being measured.
        return encode_dense(list(texts), batch_size=max(len(texts), 1))

    def encode_sparse(self, texts: Sequence[str]) -> Sequence[SparseVector]:
        from ingestion.embedder import encode_sparse
        return encode_sparse(list(texts), batch_size=max(len(texts), 1))


class QdrantVectorStore:
    """The real store: ``retrieval.vector_store``, plus an id reader for resume."""

    def collection_exists(self, name: str) -> bool:
        from retrieval.vector_store import collection_exists
        return collection_exists(name)

    def create_collection(self, name: str) -> None:
        from retrieval.vector_store import create_collection
        create_collection(name)

    def delete_collection(self, name: str) -> None:
        from retrieval.vector_store import delete_collection
        delete_collection(name)

    def existing_point_ids(self, name: str) -> set:
        """Every point id already stored, so a resumed run skips them.

        Scrolled without payload or vectors: ids alone are what resume needs,
        and pulling 768 floats per point back out would cost more than the
        re-embedding it saves.
        """
        from retrieval.vector_store import get_client
        client = get_client()
        ids, offset = set(), None
        while True:
            points, offset = client.scroll(
                collection_name=name, limit=1000, offset=offset,
                with_payload=False, with_vectors=False,
            )
            ids.update(str(p.id) for p in points)
            if offset is None:
                return ids

    def upsert(self, name: str, chunks: Sequence, dense: Sequence[DenseVector],
               sparse: Sequence[SparseVector]) -> None:
        from retrieval.vector_store import upsert_chunks
        upsert_chunks(
            collection_name=name, chunks=list(chunks),
            dense_vectors=list(dense), sparse_vectors=list(sparse),
            # One write per embed batch: this module owns the bound, so a
            # second split here would make the measured batch size a fiction.
            batch_size=max(len(chunks), 1),
        )


# ── results ────────────────────────────────────────────────────────────────

@dataclass
class BatchProgress:
    collection: str
    collection_index: int
    collection_count: int
    batch_size: int
    chunks_done: int          # within this collection
    chunks_total: int         # within this collection
    seconds: float            # this batch
    chunks_per_second: float  # this batch
    peak_rss_bytes: int

    def line(self) -> str:
        return (
            f"[{self.collection_index}/{self.collection_count}] {self.collection:12s} "
            f"{self.chunks_done:5d}/{self.chunks_total:<5d} "
            f"batch={self.batch_size:<3d} "
            f"{self.chunks_per_second:6.2f} chunks/s  "
            f"peak RSS {self.peak_rss_bytes / 1e9:4.2f} GB"
        )


@dataclass
class CollectionResult:
    name: str
    chunks_total: int
    chunks_embedded: int
    chunks_already_present: int
    seconds: float
    status: str               # indexed | resumed | skipped | failed
    error: Optional[str] = None

    @property
    def chunks_per_second(self) -> Optional[float]:
        if not self.chunks_embedded or self.seconds <= 0:
            return None
        return self.chunks_embedded / self.seconds


@dataclass
class IndexRun:
    batch_size: int
    max_length: Optional[int]
    collections: List[CollectionResult] = field(default_factory=list)
    seconds: float = 0.0
    peak_rss_bytes: int = 0

    @property
    def chunks_embedded(self) -> int:
        return sum(c.chunks_embedded for c in self.collections)

    @property
    def chunks_per_second(self) -> Optional[float]:
        if not self.chunks_embedded or self.seconds <= 0:
            return None
        return self.chunks_embedded / self.seconds

    @property
    def failed(self) -> List[CollectionResult]:
        return [c for c in self.collections if c.status == "failed"]

    def to_dict(self) -> Dict:
        return {
            "batch_size": self.batch_size,
            "max_length": self.max_length,
            "seconds": round(self.seconds, 1),
            "chunks_embedded": self.chunks_embedded,
            "chunks_per_second": (round(self.chunks_per_second, 3)
                                  if self.chunks_per_second else None),
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_rss_gb": round(self.peak_rss_bytes / 1e9, 3),
            "collections": [
                {
                    "name": c.name,
                    "chunks_total": c.chunks_total,
                    "chunks_embedded": c.chunks_embedded,
                    "chunks_already_present": c.chunks_already_present,
                    "seconds": round(c.seconds, 1),
                    "chunks_per_second": (round(c.chunks_per_second, 3)
                                          if c.chunks_per_second else None),
                    "status": c.status,
                    "error": c.error,
                }
                for c in self.collections
            ],
        }


# ── the indexer ────────────────────────────────────────────────────────────

def group_by_collection(chunks: Iterable) -> Dict[str, List]:
    """Group chunks by ``{TICKER}_{fiscal_year}``, v1's collection naming."""
    from retrieval.vector_store import get_collection_name
    grouped: Dict[str, List] = defaultdict(list)
    for chunk in chunks:
        grouped[get_collection_name(chunk.ticker, chunk.fiscal_year)].append(chunk)
    return dict(grouped)


def index_stream(
    chunks: Iterable,
    *,
    batch_size: Optional[int] = None,
    force_reindex: bool = False,
    embedder: Optional[Embedder] = None,
    store: Optional[VectorStore] = None,
    max_length: Optional[int] = None,
    progress: Optional[Callable[[BatchProgress], None]] = None,
    limit_per_collection: Optional[int] = None,
) -> IndexRun:
    """Embed and upsert one collection at a time, in bounded batches.

    ``limit_per_collection`` caps how many chunks of each collection are
    embedded. It exists for the throughput profile, which measures a fixed
    number of real chunks; a real indexing run leaves it unset.
    """
    if batch_size is None:
        from config import settings
        # index_batch_size, not embedding_batch_size: see D2-00. The grid found
        # batch size worth ~9% of throughput but it does drive peak memory, and
        # memory is what stopped P1-00.
        batch_size = settings.index_batch_size
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    embedder = embedder or FastEmbedEmbedder(max_length=max_length)
    store = store or QdrantVectorStore()

    grouped = group_by_collection(chunks)
    # Ascending size: an interrupted run still leaves whole small collections
    # behind rather than one half-written large one.
    names = sorted(grouped, key=lambda n: (len(grouped[n]), n))

    run = IndexRun(batch_size=batch_size, max_length=max_length)
    started_run = time.perf_counter()

    for position, name in enumerate(names, 1):
        col_chunks = grouped[name]
        started = time.perf_counter()
        try:
            result = _index_one(
                name, col_chunks, position, len(names),
                batch_size=batch_size, force_reindex=force_reindex,
                embedder=embedder, store=store, progress=progress,
                limit_per_collection=limit_per_collection,
            )
        except Exception as exc:
            # Recorded and carried on: one bad collection must not cost the
            # hours of work the others already represent.
            result = CollectionResult(
                name=name, chunks_total=len(col_chunks), chunks_embedded=0,
                chunks_already_present=0, seconds=time.perf_counter() - started,
                status="failed", error=f"{type(exc).__name__}: {exc}",
            )
            logger.error(f"indexer: {name} failed — {result.error}")
        run.collections.append(result)
        run.peak_rss_bytes = max(run.peak_rss_bytes, peak_rss_bytes())

    run.seconds = time.perf_counter() - started_run
    run.peak_rss_bytes = max(run.peak_rss_bytes, peak_rss_bytes())
    return run


def _index_one(
    name: str,
    col_chunks: List,
    position: int,
    total_collections: int,
    *,
    batch_size: int,
    force_reindex: bool,
    embedder: Embedder,
    store: VectorStore,
    progress: Optional[Callable[[BatchProgress], None]],
    limit_per_collection: Optional[int],
) -> CollectionResult:
    started = time.perf_counter()
    existed = store.collection_exists(name)

    if existed and force_reindex:
        store.delete_collection(name)
        existed = False
    if not existed:
        store.create_collection(name)
        already: set = set()
    else:
        already = store.existing_point_ids(name)

    pending = [c for c in col_chunks if str(c.chunk_id) not in already]
    skipped = len(col_chunks) - len(pending)
    if limit_per_collection is not None:
        pending = pending[:limit_per_collection]

    if not pending:
        logger.info(f"indexer: {name} already complete ({skipped} point(s))")
        return CollectionResult(
            name=name, chunks_total=len(col_chunks), chunks_embedded=0,
            chunks_already_present=skipped,
            seconds=time.perf_counter() - started, status="skipped",
        )

    if skipped:
        logger.info(f"indexer: {name} resuming — {skipped} of {len(col_chunks)} "
                    f"point(s) already stored")

    done = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        batch_started = time.perf_counter()
        texts = [c.text for c in batch]
        dense = embedder.encode_dense(texts)
        sparse = embedder.encode_sparse(texts)
        store.upsert(name, batch, dense, sparse)
        done += len(batch)
        elapsed = time.perf_counter() - batch_started
        report = BatchProgress(
            collection=name, collection_index=position,
            collection_count=total_collections, batch_size=len(batch),
            chunks_done=done, chunks_total=len(pending), seconds=elapsed,
            chunks_per_second=(len(batch) / elapsed if elapsed > 0 else 0.0),
            peak_rss_bytes=peak_rss_bytes(),
        )
        if progress is not None:
            progress(report)

    return CollectionResult(
        name=name, chunks_total=len(col_chunks), chunks_embedded=done,
        chunks_already_present=skipped, seconds=time.perf_counter() - started,
        status="resumed" if skipped else "indexed",
    )


# ── CLI ────────────────────────────────────────────────────────────────────

def load_chunks(chunks_dir: Path, tickers: Optional[set] = None) -> List:
    """Read v1's chunk JSON files into ``Chunk`` objects."""
    from models import Chunk
    out: List = []
    for path in sorted(chunks_dir.glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            chunk = Chunk(**raw)
            if tickers and chunk.ticker.upper() not in tickers:
                continue
            out.append(chunk)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="",
                    help="comma-separated tickers (default: every chunk file)")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="chunks embedded and upserted per batch")
    ap.add_argument("--max-length", type=int, default=None,
                    help="dense tokenizer truncation; lowering it changes what "
                         "the vectors mean, so an index must not mix values")
    ap.add_argument("--limit-per-collection", type=int, default=None,
                    help="cap chunks embedded per collection (profiling only)")
    ap.add_argument("--force-reindex", action="store_true",
                    help="delete and rebuild each collection")
    ap.add_argument("--report", default="", help="write a JSON run report here")
    args = ap.parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")

    from config import settings
    chunks_dir = Path(settings.chunks_dir)
    tickers = {t.strip().upper() for t in args.only.split(",") if t.strip()}
    chunks = load_chunks(chunks_dir, tickers or None)
    if not chunks:
        print(f"no chunks in {chunks_dir}"
              + (f" for {sorted(tickers)}" if tickers else "")
              + "; run the download/parse/chunk steps first", file=sys.stderr)
        return 2

    grouped = group_by_collection(chunks)
    print(f"{len(chunks)} chunk(s) across {len(grouped)} collection(s); "
          f"streaming one collection at a time\n", flush=True)

    run = index_stream(
        chunks,
        batch_size=args.batch_size,
        force_reindex=args.force_reindex,
        max_length=args.max_length,
        limit_per_collection=args.limit_per_collection,
        progress=lambda p: print("  " + p.line(), flush=True),
    )

    print(f"\n{'COLLECTION':14s} {'TOTAL':>6s} {'NEW':>6s} {'HAD':>6s} "
          f"{'SECONDS':>8s} {'CHUNKS/S':>9s}  STATUS")
    for c in run.collections:
        rate = f"{c.chunks_per_second:.2f}" if c.chunks_per_second else "-"
        print(f"{c.name:14s} {c.chunks_total:6d} {c.chunks_embedded:6d} "
              f"{c.chunks_already_present:6d} {c.seconds:8.1f} {rate:>9s}  "
              f"{c.status}" + (f" ({c.error})" if c.error else ""))

    overall = f"{run.chunks_per_second:.2f}" if run.chunks_per_second else "-"
    print(f"\n{run.chunks_embedded} chunk(s) embedded in {run.seconds:.1f}s "
          f"({overall} chunks/s); peak RSS {run.peak_rss_bytes / 1e9:.2f} GB")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
        print(f"wrote {out}")

    return 1 if run.failed else 0


if __name__ == "__main__":
    sys.exit(main())
