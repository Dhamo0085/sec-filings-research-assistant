#!/usr/bin/env python3
"""Indexing throughput profile (P2-00d): batch size x sequence length.

    python scripts/profile_indexing.py
    python scripts/profile_indexing.py --chunks 200 --out reports/phase2/throughput.json

P1-00 measured ~1.6 s/chunk and concluded nothing about *why*: the machine was
swapping 9.6 GB of 10 GB at the time, so the number described a thrashing host,
not bge-base. This grid separates the two. The decision it feeds (spec P2-00d):
keep v1's dense model if a tuned setting reaches >= 4 chunks/s, else embed only
text and footnote chunks for non-core tickers, else change the model (D21).

Two measurement details that are easy to get wrong:

* **Each configuration runs in its own subprocess.** ``ru_maxrss`` is a
  high-water mark for the life of a process, so a second configuration measured
  in the same process would inherit the first one's peak and every row after the
  worst one would be a lie. The child reports its own peak as JSON.
* **The sample is stratified.** The corpus is 77% table chunks and its tickers
  differ by an order of magnitude in size, so the first 200 chunks of one
  filing would not predict the full run. Chunks are drawn round-robin across
  (ticker, chunk_type) with a fixed seed.

``--max-length`` below 512 is measured but is not a free win: 81% of chunks are
longer than 512 tokens already, so lowering the cap shortens what each vector
represents, and a collection built at 256 is not comparable with one built at
512. Adopting it means re-indexing what already exists.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BATCH_SIZES = (8, 16, 32, 64)
DEFAULT_MAX_LENGTHS = (512, 256)
DEFAULT_CHUNKS = 200
TARGET_CHUNKS_PER_SECOND = 4.0        # spec P2-00(d)


# ── machine state, so a measurement can be interpreted later ───────────────

def _swap_usage() -> Optional[Dict[str, float]]:
    """Swap in MB on macOS; None elsewhere (the profile still runs)."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"],
                             capture_output=True, text=True, timeout=10,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    nums = re.findall(r"(\w+) = ([\d.]+)M", out)
    return {k: float(v) for k, v in nums} or None


def machine_state() -> Dict:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "swap_mb": _swap_usage(),
    }


# ── sampling ───────────────────────────────────────────────────────────────

def stratified_sample(chunks: List, n: int, seed: int = 20261002) -> List:
    """Draw ``n`` chunks round-robin across (ticker, chunk_type) strata.

    Embedding cost tracks text length, and the corpus is 77% table chunks with
    a median of 986 tokens, so a sample that over-represents one ticker or one
    chunk type would mis-predict the full run in either direction.
    """
    # S311: a fixed-seed PRNG is the point — the sample must be the same
    # one next time the profile is re-run. Nothing here is a secret.
    rng = random.Random(seed)  # noqa: S311
    strata: Dict[tuple, List] = {}
    for c in chunks:
        strata.setdefault((c.ticker, c.chunk_type), []).append(c)
    for group in strata.values():
        rng.shuffle(group)

    out: List = []
    keys = sorted(strata)
    while len(out) < n:
        took = False
        for key in keys:
            if strata[key]:
                out.append(strata[key].pop())
                took = True
                if len(out) == n:
                    break
        if not took:               # corpus smaller than n
            break
    return out


# ── the child: one configuration ───────────────────────────────────────────

def run_one(batch_size: int, max_length: Optional[int], n_chunks: int,
            seed: int) -> Dict:
    """Measure one (batch_size, max_length) pair in THIS process."""
    from config import settings
    from ingestion.indexer import (
        FastEmbedEmbedder,
        QdrantVectorStore,
        index_stream,
        peak_rss_bytes,
    )
    from ingestion.indexer import load_chunks as _load

    corpus = _load(Path(settings.chunks_dir))
    sample = stratified_sample(corpus, n_chunks, seed=seed)
    if not sample:
        raise RuntimeError(f"no chunks found in {settings.chunks_dir}")

    rss_before = peak_rss_bytes()
    swap_before = _swap_usage()

    # Warm the models before the clock starts: a first-call model load would
    # otherwise be charged to batch 1 of whichever configuration ran first.
    embedder = FastEmbedEmbedder(max_length=max_length)
    embedder.encode_dense(["warm up"])
    embedder.encode_sparse(["warm up"])

    started = time.perf_counter()
    run = index_stream(
        sample,
        batch_size=batch_size,
        embedder=embedder,
        store=QdrantVectorStore(),
        max_length=max_length,
        force_reindex=True,
    )
    elapsed = time.perf_counter() - started

    batch_rates = [c.chunks_per_second for c in run.collections
                   if c.chunks_per_second]
    return {
        "batch_size": batch_size,
        "max_length": max_length,
        "chunks": run.chunks_embedded,
        "collections": len(run.collections),
        "seconds": round(elapsed, 2),
        "chunks_per_second": round(run.chunks_embedded / elapsed, 3) if elapsed else None,
        "seconds_per_chunk": round(elapsed / run.chunks_embedded, 4) if run.chunks_embedded else None,
        "peak_rss_bytes": peak_rss_bytes(),
        "peak_rss_gb": round(peak_rss_bytes() / 1e9, 3),
        "rss_before_gb": round(rss_before / 1e9, 3),
        "swap_mb_before": swap_before,
        "swap_mb_after": _swap_usage(),
        "slowest_collection_chunks_per_second": round(min(batch_rates), 3) if batch_rates else None,
        "failed_collections": [c.name for c in run.failed],
    }


# ── the parent: the grid ───────────────────────────────────────────────────

def _child(batch_size: int, max_length: Optional[int], n_chunks: int, seed: int,
           timeout: int) -> Dict:
    """Run one configuration in a fresh process and read back its JSON."""
    qdrant_dir = Path(tempfile.mkdtemp(prefix="profile-qdrant-"))
    env = {
        **os.environ,
        "QDRANT_PATH": str(qdrant_dir),
        "PYTHONPATH": str(REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker",
           "--batch-size", str(batch_size), "--chunks", str(n_chunks),
           "--seed", str(seed)]
    if max_length is not None:
        cmd += ["--max-length", str(max_length)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"batch_size": batch_size, "max_length": max_length,
                "error": f"timed out after {timeout}s"}
    finally:
        shutil.rmtree(qdrant_dir, ignore_errors=True)

    marker = "RESULT_JSON:"
    for line in proc.stdout.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    return {
        "batch_size": batch_size, "max_length": max_length,
        "error": f"worker exit {proc.returncode}; stderr tail: "
                 f"{proc.stderr.strip()[-400:]}",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chunks", type=int, default=DEFAULT_CHUNKS)
    ap.add_argument("--batch-sizes", default=",".join(map(str, DEFAULT_BATCH_SIZES)))
    ap.add_argument("--max-lengths", default=",".join(map(str, DEFAULT_MAX_LENGTHS)))
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--timeout", type=int, default=3600,
                    help="per-configuration ceiling, seconds")
    ap.add_argument("--out", default="reports/phase2/throughput_profile.json")
    # worker mode
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--batch-size", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--max-length", type=int, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.worker:
        result = run_one(args.batch_size, args.max_length, args.chunks, args.seed)
        print("RESULT_JSON:" + json.dumps(result))
        return 0

    batch_sizes = [int(x) for x in args.batch_sizes.split(",") if x.strip()]
    max_lengths = [int(x) for x in args.max_lengths.split(",") if x.strip()]

    state = machine_state()
    print(f"machine : {state['platform']} ({state['cpu_count']} CPUs)")
    if state["swap_mb"]:
        print(f"swap    : {state['swap_mb'].get('used', 0):.0f} MB used of "
              f"{state['swap_mb'].get('total', 0):.0f} MB before the first run")
    print(f"sample  : {args.chunks} real chunks, stratified by (ticker, chunk_type), "
          f"seed {args.seed}")
    print(f"grid    : batch {batch_sizes} x max_length {max_lengths}  "
          f"({len(batch_sizes) * len(max_lengths)} runs, each in its own process)\n")

    print(f"{'BATCH':>5s} {'MAXLEN':>6s} {'CHUNKS':>6s} {'SECONDS':>8s} "
          f"{'CHUNKS/S':>9s} {'PEAK RSS':>9s}  NOTE")
    results: List[Dict] = []
    for max_length in max_lengths:
        for batch_size in batch_sizes:
            r = _child(batch_size, max_length, args.chunks, args.seed, args.timeout)
            results.append(r)
            if "error" in r:
                print(f"{batch_size:5d} {max_length:6d} {'-':>6s} {'-':>8s} "
                      f"{'-':>9s} {'-':>9s}  {r['error'][:60]}")
                continue
            print(f"{batch_size:5d} {max_length:6d} {r['chunks']:6d} "
                  f"{r['seconds']:8.1f} {r['chunks_per_second']:9.2f} "
                  f"{r['peak_rss_gb']:7.2f} GB  "
                  f"{'OK' if r['chunks_per_second'] >= TARGET_CHUNKS_PER_SECOND else 'below target'}")

    ok = [r for r in results if "error" not in r and r.get("chunks_per_second")]
    best = max(ok, key=lambda r: r["chunks_per_second"]) if ok else None
    best_512 = max((r for r in ok if r["max_length"] == 512),
                   key=lambda r: r["chunks_per_second"], default=None)

    payload = {
        "profiled_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": state,
        "sample_chunks": args.chunks,
        "seed": args.seed,
        "target_chunks_per_second": TARGET_CHUNKS_PER_SECOND,
        "results": results,
        "best": best,
        "best_at_full_512_length": best_512,
        "note": (
            "Each configuration ran in its own subprocess because ru_maxrss is a "
            "per-process high-water mark. max_length below 512 truncates the "
            "vector's content (81% of chunks exceed 512 tokens), so it is not a "
            "like-for-like comparison and adopting it means re-indexing."
        ),
    }
    if best_512:
        total = 35838
        payload["projected_full_corpus_hours_at_512"] = round(
            total / best_512["chunks_per_second"] / 3600, 2)

    out = REPO_ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if best_512:
        print(f"\nbest at 512 tokens : batch {best_512['batch_size']}, "
              f"{best_512['chunks_per_second']:.2f} chunks/s  "
              f"-> {payload['projected_full_corpus_hours_at_512']:.1f} h for "
              f"all 35,838 chunks")
        verdict = ("KEEP bge-base" if best_512["chunks_per_second"] >= TARGET_CHUNKS_PER_SECOND
                   else "BELOW the >= 4 chunks/s bar — see the P2-00(d) fallbacks")
        print(f"verdict            : {verdict}")
    print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
