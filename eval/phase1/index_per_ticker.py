"""Drive v1's own index_chunks() one ticker at a time (P1-00).

Why this exists: ``ingestion/embedder.index_chunks`` deliberately embeds every
chunk across every collection in a single pass --

    # Embed ALL chunks across every collection in a single pass so the ONNX
    # model processes one large batch instead of many small per-collection ones.
    all_texts = [c.text for _, col_chunks in needs_indexing for c in col_chunks]

-- which accumulates the texts plus the dense and sparse vectors for all of
them before a single point is upserted. For the bundled 12 + NFLX that is
35,838 chunks, and on this 8 GB machine the process reached 2.4 GB RSS and
drove swap to 9.6 GB of 10 GB with ~200 MB of RAM free, after 68 minutes with
no collection written. It was stopped rather than left to be OOM-killed.

This driver changes no v1 code. It calls the same ``index_chunks`` repeatedly
with one ticker's chunks at a time, which:

  * bounds peak memory to the largest single ticker (BAC, ~8.2k chunks);
  * writes collections incrementally, so progress survives an interruption;
  * is exactly what v1's own on-demand path does in production
    (``ingestion/auto_ingest.ensure_ticker_indexed`` indexes one filer), so the
    resulting index is the same content v1 would produce.

``index_chunks`` already skips collections that exist, so re-running is cheap
and the whole thing is resumable.

Run from the v1-baseline worktree so the v1 modules are the ones imported:

    cd <worktree> && .venv/bin/python <repo>/eval/phase1/index_per_ticker.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default="", help="comma-separated tickers to index")
    ap.add_argument("--report", default="", help="write a JSON timing report here")
    args = ap.parse_args(argv)

    # Imported here so the error is legible if run from the wrong directory.
    from config import settings
    from ingestion.embedder import index_chunks
    from models import Chunk
    from retrieval.vector_store import list_collections

    chunks_dir = Path(settings.chunks_dir)
    files = sorted(chunks_dir.glob("*.json"))
    if not files:
        print(f"no chunk files in {chunks_dir}; run the parse/chunk steps first",
              file=sys.stderr)
        return 2

    wanted = {t.strip().upper() for t in args.only.split(",") if t.strip()}

    by_ticker: Dict[str, List[Chunk]] = defaultdict(list)
    for path in files:
        for raw in json.loads(path.read_text(encoding="utf-8")):
            chunk = Chunk(**raw)
            if wanted and chunk.ticker.upper() not in wanted:
                continue
            by_ticker[chunk.ticker.upper()].append(chunk)

    if not by_ticker:
        print("no chunks matched", file=sys.stderr)
        return 2

    total = sum(len(v) for v in by_ticker.values())
    print(f"{total} chunks across {len(by_ticker)} ticker(s); indexing one ticker "
          f"at a time to bound peak memory\n")

    timings = []
    # Smallest first: cheap tickers land early, so an interruption still leaves
    # a usable index.
    for ticker in sorted(by_ticker, key=lambda t: len(by_ticker[t])):
        chunks = by_ticker[ticker]
        print(f"--- {ticker}: {len(chunks)} chunks ---", flush=True)
        started = time.perf_counter()
        try:
            index_chunks(chunks)
            ok, err = True, None
        except Exception as exc:          # recorded, then continue
            ok, err = False, f"{type(exc).__name__}: {exc}"
            print(f"    FAILED: {err}", flush=True)
        elapsed = time.perf_counter() - started
        timings.append({"ticker": ticker, "chunks": len(chunks),
                        "seconds": round(elapsed, 1), "ok": ok, "error": err})
        print(f"    {'done' if ok else 'failed'} in {elapsed:.1f}s", flush=True)

    try:
        collections = sorted(list_collections())
    except Exception as exc:
        collections = []
        print(f"could not list collections: {exc}")

    print(f"\ncollections now indexed: {len(collections)}")
    for name in collections:
        print(f"  {name}")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "total_chunks": total,
            "per_ticker": timings,
            "collections": collections,
            "note": (
                "Indexed per ticker because v1's index_chunks embeds every chunk "
                "across all collections before upserting any, which exhausted "
                "swap on an 8 GB machine for the full 35,838-chunk set."
            ),
        }, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")

    failed = [t for t in timings if not t["ok"]]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
