#!/usr/bin/env python3
"""Chunk an already-parsed corpus into a chunk directory, offline (P4-12).

**Why this exists.** ``ingestion.chunker.chunk_all_documents()`` is a library
function with no ``__main__``, and the only entry point that reaches it is
``run_ingestion.py``, which drives everything from ``data/raw/manifest.json``.
That manifest holds 39 entries and is missing BLK FY2023 — the filing D24
exists to cover — so there was no way to chunk a re-parsed corpus without going
through a manifest that does not cover it. ``scripts/reparse_corpus.py``
already fills the same hole for the parse step; this is its chunk counterpart.

**The resume rule, and why it is not "the file exists".**
A chunk file is reused only when all three hold: it parses as a non-empty list,
every chunk in it carries the ``doc_id`` of the parsed document next to it, and
it is no older than that parsed file.

The mtime half is the part that matters, and it is there because the ids are a
mix. ``ParsedDocument.doc_id`` (``AAPL_2025``) and ``ParsedSection.section_id``
(``item_1_business``) are *deterministic*, so they are the same before and
after a re-parse and cannot witness one — a stale chunk file would sail through
a doc_id check while holding text the current parser no longer produces, which
is exactly what P4-00's section-boundary rewrite changes. ``Chunk.chunk_id`` is
the opposite: a fresh ``uuid4`` every time, so re-chunking a filing renames
every point in its collection. The driver's index stage handles that by
rebuilding any collection holding a point today's chunk files do not.

The doc_id half still earns its place: it catches a file written for a
different filing, or truncated to a fragment of one.

Writes are atomic (temp file in the same directory, then ``os.replace``), so a
run killed mid-write leaves either the old file or the new one, never half of
one that the next run would happily reuse.

Offline: reads only files already on disk and never contacts SEC or an LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ── atomic writes ──────────────────────────────────────────────────────────

def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` so no reader ever sees a partial file.

    The temp file is created in the destination directory on purpose:
    ``os.replace`` is only atomic within one filesystem, and the scratch
    directory used in testing is not always on the same one as ``data/``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── the parsed corpus on disk ──────────────────────────────────────────────

def parsed_files(parsed_dir: Path, only: Optional[Set[str]] = None) -> List[Path]:
    """The parsed documents to chunk, as ``<TICKER>_<YEAR>.json`` paths."""
    files = sorted(p for p in parsed_dir.glob("*.json") if not p.name.endswith(".partial"))
    if only:
        files = [p for p in files if p.stem in only]
    return files


def load_parsed(path: Path):
    """Read one parsed document. Imported lazily so ``--help`` costs nothing."""
    from models import ParsedDocument

    return ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))


def chunk_path_for(out_dir: Path, ticker: str, fiscal_year: int) -> Path:
    """v1's chunk file naming, which ``ingestion.chunker`` also writes."""
    return out_dir / f"{ticker}_{fiscal_year}_chunks.json"


def chunk_file_is_current(path: Path, doc_id: str,
                          parsed_path: Optional[Path] = None) -> bool:
    """True when ``path`` holds chunks of the parse sitting next to it today.

    False — so the filing is chunked again — for a missing, empty, unreadable,
    mismatched or out-of-date file. Being conservative here is cheap (chunking
    one filing takes under a second); being wrong the other way indexes text
    the current parser does not produce, which is the entire thing P4-12 exists
    to replace.

    ``parsed_path`` supplies the freshness half of the rule: a chunk file older
    than the parse it claims to describe is stale. ``doc_id`` alone cannot say
    so, because it is derived from the ticker and fiscal year and is therefore
    identical across parses.
    """
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    if not isinstance(raw, list) or not raw:
        return False
    if not all(isinstance(c, dict) and c.get("doc_id") == doc_id for c in raw):
        return False
    if parsed_path is None or not parsed_path.is_file():
        return True
    return path.stat().st_mtime_ns >= parsed_path.stat().st_mtime_ns


# ── results ────────────────────────────────────────────────────────────────

@dataclass
class ChunkResult:
    filing: str
    chunks: int
    text_chunks: int
    table_chunks: int
    footnote_chunks: int
    tokens: int
    seconds: float
    status: str                      # chunked | reused | failed
    error: Optional[str] = None

    @property
    def chunks_per_second(self) -> Optional[float]:
        if not self.chunks or self.seconds <= 0 or self.status != "chunked":
            return None
        return self.chunks / self.seconds


def rechunk_one(doc, out_dir: Path) -> ChunkResult:
    """Chunk one parsed document and write its chunk file atomically."""
    from ingestion.chunker import chunk_document

    started = time.monotonic()
    chunks = chunk_document(doc)
    elapsed = time.monotonic() - started
    if not chunks:
        # A 10-K that chunks to nothing is a parser defect, not a valid
        # result: writing an empty file would make every later run re-chunk it
        # for ever, because an empty file can carry no doc_id to match.
        raise RuntimeError("produced 0 chunks")

    path = chunk_path_for(out_dir, doc.ticker, doc.fiscal_year)
    write_atomic(path, json.dumps([c.model_dump() for c in chunks], indent=2))

    kinds: Dict[str, int] = {"text": 0, "table": 0, "footnote": 0}
    for c in chunks:
        kinds[c.chunk_type] = kinds.get(c.chunk_type, 0) + 1
    return ChunkResult(
        filing=f"{doc.ticker}_{doc.fiscal_year}",
        chunks=len(chunks),
        text_chunks=kinds.get("text", 0),
        table_chunks=kinds.get("table", 0),
        footnote_chunks=kinds.get("footnote", 0),
        tokens=sum(c.token_count for c in chunks),
        seconds=elapsed,
        status="chunked",
    )


def rechunk_corpus(
    parsed_dir: Path,
    out_dir: Path,
    *,
    only: Optional[Set[str]] = None,
    force: bool = False,
    on_result=None,
) -> List[ChunkResult]:
    """Chunk every parsed document in ``parsed_dir`` into ``out_dir``.

    Resumable: a filing whose chunk file already matches today's parse is
    reported ``reused`` and not chunked again, unless ``force``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results: List[ChunkResult] = []

    for path in parsed_files(parsed_dir, only):
        try:
            doc = load_parsed(path)
        except Exception as exc:        # reported per filing, never swallowed
            result = ChunkResult(path.stem, 0, 0, 0, 0, 0, 0.0, "failed",
                                 f"unreadable parse: {type(exc).__name__}: {exc}")
            results.append(result)
            if on_result:
                on_result(result)
            continue

        target = chunk_path_for(out_dir, doc.ticker, doc.fiscal_year)
        if not force and chunk_file_is_current(target, doc.doc_id, path):
            existing = json.loads(target.read_text(encoding="utf-8"))
            result = ChunkResult(
                filing=path.stem, chunks=len(existing),
                text_chunks=sum(1 for c in existing if c.get("chunk_type") == "text"),
                table_chunks=sum(1 for c in existing if c.get("chunk_type") == "table"),
                footnote_chunks=sum(1 for c in existing if c.get("chunk_type") == "footnote"),
                tokens=sum(int(c.get("token_count") or 0) for c in existing),
                seconds=0.0, status="reused",
            )
        else:
            try:
                result = rechunk_one(doc, out_dir)
            except Exception as exc:
                result = ChunkResult(path.stem, 0, 0, 0, 0, 0, 0.0, "failed",
                                     f"{type(exc).__name__}: {exc}")
        results.append(result)
        if on_result:
            on_result(result)

    return results


def summarize(results: Sequence[ChunkResult]) -> Dict:
    """The shape the P4-12 report embeds."""
    return {
        "filings": len(results),
        "chunked": sum(1 for r in results if r.status == "chunked"),
        "reused": sum(1 for r in results if r.status == "reused"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "chunks_total": sum(r.chunks for r in results),
        "tokens_total": sum(r.tokens for r in results),
        "seconds": round(sum(r.seconds for r in results), 1),
        "results": [asdict(r) | {"chunks_per_second": (round(r.chunks_per_second, 2)
                                                       if r.chunks_per_second else None)}
                    for r in results],
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def format_result(r: ChunkResult) -> str:
    if r.status == "failed":
        return f"  FAIL {r.filing:14s} {r.error}"
    rate = f"{r.chunks_per_second:6.1f} chunks/s" if r.chunks_per_second else " " * 15
    return (f"  {r.status:7s} {r.filing:14s} {r.chunks:5d} chunks "
            f"(text={r.text_chunks:4d} table={r.table_chunks:4d} "
            f"foot={r.footnote_chunks:3d}) {r.seconds:6.1f}s {rate}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--parsed-dir", type=Path, required=True,
                    help="the parsed documents to chunk")
    ap.add_argument("--out-dir", type=Path, required=True,
                    help="where the chunk files go (never data/chunks for P4-12)")
    ap.add_argument("--only", default="",
                    help="comma-separated filing stems, e.g. JPM_2024,GS_2024")
    ap.add_argument("--force", action="store_true",
                    help="re-chunk even when the existing file matches today's parse")
    ap.add_argument("--report", default="", help="write a JSON summary here")
    args = ap.parse_args(argv)

    if not args.parsed_dir.is_dir():
        print(f"error: {args.parsed_dir} is not a directory", file=sys.stderr)
        return 2
    if args.parsed_dir.resolve() == args.out_dir.resolve():
        print("error: --out-dir must differ from --parsed-dir", file=sys.stderr)
        return 2

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    started = time.monotonic()
    results = rechunk_corpus(args.parsed_dir, args.out_dir, only=only or None,
                             force=args.force,
                             on_result=lambda r: print(format_result(r), flush=True))
    elapsed = time.monotonic() - started

    summary = summarize(results)
    from ingestion.indexer import peak_rss_bytes
    print(f"\n{summary['chunked']} chunked, {summary['reused']} reused, "
          f"{summary['failed']} failed; {summary['chunks_total']} chunks "
          f"({summary['tokens_total']} tokens) in {elapsed:.1f}s; "
          f"peak RSS {peak_rss_bytes() / 1e9:.2f} GB")

    if args.report:
        write_atomic(Path(args.report), json.dumps(summary, indent=2))
        print(f"wrote {args.report}")

    return 1 if summary["failed"] or not results else 0


if __name__ == "__main__":
    sys.exit(main())
