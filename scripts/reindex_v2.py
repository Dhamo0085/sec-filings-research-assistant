#!/usr/bin/env python3
"""P4-12: re-parse, re-chunk and re-index the corpus into a NEW store. One command.

    .venv/bin/python scripts/reindex_v2.py            # the overnight run
    .venv/bin/python scripts/reindex_v2.py --dry-run  # preflight only, seconds

Spec: PROJECT_SPEC P4-12 (D28). The parser was rewritten in P4-00, so every
chunk and every vector built before it describes a corpus the shipped parser no
longer produces. This rebuilds all three artifacts from the current parser.

**It writes only to new directories.** ``data/parsed_v2``, ``data/chunks_v2``
and ``data/qdrant_v2``. ``data/parsed``, ``data/chunks``, ``data/qdrant`` and
``data/qdrant_v1_backup`` are read (the first as the list of filings and their
source documents) and never written: preflight refuses any output path that
equals, contains or sits inside one of them, and the run fingerprints all four
before and after and fails if a fingerprint moved. Swapping the new artifacts
in is a separate, reversible step — ``scripts/activate_reindex.sh``.

**Resumable, because an interrupted 5-hour job must not restart from zero.**
Every stage decides what is left from the files on disk, not from a state file:

* parse — skipped when the output holds a readable parse with sections;
* chunk — skipped when the chunk file names the right filing AND is no older
  than that filing's parse. "The file exists" is not enough: ``doc_id`` is
  derived from the ticker and year, so it is identical before and after a
  re-parse and cannot witness one;
* index — ``ingestion.indexer`` already resumes at batch granularity by reading
  the point ids already stored. ``Chunk.chunk_id`` is a fresh ``uuid4`` on
  every chunking run, so any collection holding a point today's chunk files do
  not is dropped and rebuilt first, rather than gaining a second copy of every
  passage under new ids.

Re-running the same command after a crash, a battery failure or a Ctrl-C
therefore continues where it stopped. ``reports/phase4/reindex_run.json`` is
rewritten atomically as the run progresses, so a killed run still leaves its
evidence behind.

**It refuses to start if anything holds a Qdrant lock.** Qdrant local mode takes
an exclusive flock on its storage folder; a running ``uvicorn`` already cost
this project one evaluation run (STATE section 8). The probe is a non-blocking
flock on each store's ``.lock``, taken on a read-only descriptor and released
immediately, so probing never creates or modifies anything.

Offline: no network, no LLM calls. The only inputs are files already on disk.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Both are import-light and, crucially, neither imports `config` at module
# level: `config.settings` is a singleton built at first import, so this module
# must be able to set PARSED_DIR / CHUNKS_DIR / QDRANT_PATH in the environment
# before anything reads them.
from scripts import rechunk_corpus, reparse_corpus  # noqa: E402

#: Directories this task must never write to.
PROTECTED_DIRS = ("data/parsed", "data/chunks", "data/qdrant", "data/qdrant_v1_backup")

#: The frozen v1 index, whose point count T4-09 requires to be unchanged.
BACKUP_DIR = "data/qdrant_v1_backup"

#: Measured 2026-10-03 and recorded in docs/STATE.md.
EXPECTED_BACKUP_POINTS = 14557
EXPECTED_BACKUP_COLLECTIONS = 25

#: P4-12's scope: the 12 bundled tickers (BLK included, so BLK FY2023 is in
#: scope — it is the D24 filing under BlackRock's old CIK) plus NFLX. TSLA_2025
#: is a Phase 1 auto-ingest leftover and is deliberately out of scope.
EXTRA_TICKERS = ("NFLX",)

#: Filings that must be present, or the run is not what P4-12 asked for.
REQUIRED_FILINGS = ("BLK_2023", "NFLX_2023", "NFLX_2024", "NFLX_2025")

ALL_STAGES = ("parse", "chunk", "index", "checks")


# ── output plumbing ────────────────────────────────────────────────────────

class Tee:
    """Print to the terminal and to a log file at once.

    An overnight run's only witness is its log; `tee(1)` in the owner's shell
    would work too, but then forgetting it loses the evidence the phase report
    has to cite.
    """

    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def __call__(self, message: str = "") -> None:
        print(message, flush=True)
        if self._fh is not None:
            self._fh.write(message + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Qdrant stores, read without opening a client ───────────────────────────

def qdrant_lock_state(store_dir: Path) -> str:
    """``free``, ``held`` or ``absent`` for a Qdrant local-mode storage folder.

    Mirrors what ``QdrantLocal._load`` does — a non-blocking exclusive flock on
    ``<dir>/.lock`` — but on a read-only descriptor and released at once, so
    probing a store neither creates its lock file nor modifies it. ``absent``
    means there is no store there yet, which is the normal state of the target
    directory before the first run.
    """
    lock = store_dir / ".lock"
    if not lock.is_file():
        return "absent"
    import portalocker

    with lock.open("r") as fh:
        try:
            portalocker.lock(
                fh, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
            )
        except Exception:
            return "held"
        portalocker.unlock(fh)
    return "free"


def count_points_readonly(store_dir: Path) -> Dict:
    """Collection and point counts of a local-mode store, without a client.

    ``QdrantClient(path=...)`` takes the exclusive lock and opens every
    collection's sqlite read-write, which is exactly what "do not touch
    ``data/qdrant``" rules out. Local mode keeps one ``storage.sqlite`` per
    collection with a single ``points`` table, so the same numbers are
    available through a read-only sqlite URI. Verified against the client on
    ``data/qdrant_v1_backup``: both report 25 collections and 14,557 points,
    and AAPL_2024 = 197 either way.
    """
    meta = store_dir / "meta.json"
    if not meta.is_file():
        return {"exists": False, "collections": 0, "points": 0, "per_collection": {}}

    names = sorted(json.loads(meta.read_text(encoding="utf-8")).get("collections", {}))
    per: Dict[str, int] = {}
    for name in names:
        db = store_dir / "collection" / name / "storage.sqlite"
        if not db.is_file():
            per[name] = 0
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            per[name] = con.execute("SELECT count(*) FROM points").fetchone()[0]
        finally:
            con.close()
    return {"exists": True, "collections": len(names),
            "points": sum(per.values()), "per_collection": per}


def fingerprint_dir(directory: Path) -> Dict:
    """A cheap, exact witness that a directory was not written to.

    Name, size and mtime of every file below it, hashed. Reading a file does
    not change any of the three, so the lock probe and the read-only point
    counts above leave this untouched — while any write, rename or deletion
    moves it.
    """
    if not directory.is_dir():
        return {"exists": False, "files": 0, "bytes": 0, "sha256": ""}
    digest = sha256()
    files = 0
    total = 0
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        stat = path.stat()
        digest.update(f"{path.relative_to(directory)}|{stat.st_size}|{stat.st_mtime_ns}\n"
                      .encode())
        files += 1
        total += stat.st_size
    return {"exists": True, "files": files, "bytes": total,
            "sha256": digest.hexdigest()}


# ── path safety ────────────────────────────────────────────────────────────

def path_conflicts(outputs: Dict[str, Path], protected: Sequence[Path]) -> List[str]:
    """Every way an output path could collide with a protected one.

    Equality is the obvious case. Containment matters just as much in both
    directions: Qdrant local mode walks its storage folder, so a store nested
    inside another is read as part of it (the reason T4-07 asserts the same
    property for the v1 backup), and a parsed/chunks directory nested inside
    the live one would be swept up by every ``glob``.
    """
    problems: List[str] = []
    for label, out in outputs.items():
        out = out.resolve()
        for prot in protected:
            prot = prot.resolve()
            if out == prot:
                problems.append(f"{label} is {prot} — a protected directory")
            elif prot in out.parents:
                problems.append(f"{label} ({out}) is inside the protected {prot}")
            elif out in prot.parents:
                problems.append(f"{label} ({out}) contains the protected {prot}")
    names = list(outputs)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if outputs[a].resolve() == outputs[b].resolve():
                problems.append(f"{a} and {b} are the same directory")
    return problems


# ── scope ──────────────────────────────────────────────────────────────────

def scope_tickers(extra: Sequence[str] = EXTRA_TICKERS) -> List[str]:
    """The 12 bundled tickers plus NFLX, read from ``config.COMPANIES``."""
    import config

    return sorted({c["ticker"] for c in config.COMPANIES} | set(extra))


def filings_in_scope(parsed_dir: Path, tickers: Sequence[str]) -> List[str]:
    """``<TICKER>_<YEAR>`` stems present in ``parsed_dir`` for these tickers."""
    allowed = set(tickers)
    return sorted(p.stem for p in parsed_dir.glob("*.json")
                  if p.stem.rsplit("_", 1)[0] in allowed)


# ── the run ────────────────────────────────────────────────────────────────

@dataclass
class Run:
    """Everything the report file holds. Written atomically after every stage."""

    started_at: str
    config: Dict
    stages: Dict = field(default_factory=dict)
    integrity: Dict = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    status: str = "running"
    finished_at: Optional[str] = None
    seconds: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "task": "P4-12",
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "seconds": round(self.seconds, 1),
            "host": {"platform": platform.platform(),
                     "python": platform.python_version()},
            "config": self.config,
            "stages": self.stages,
            "integrity": self.integrity,
            "errors": self.errors,
        }


def write_report(run: Run, path: Path) -> None:
    rechunk_corpus.write_atomic(path, json.dumps(run.to_dict(), indent=2))


# ── stages ─────────────────────────────────────────────────────────────────

def stage_preflight(args, run: Run, say: Tee) -> bool:
    """Refuse to start unless every precondition holds. Returns ok."""
    say("── preflight " + "─" * 54)
    problems: List[str] = []
    checks: Dict = {}

    outputs = {"--parsed-out": args.parsed_out, "--chunks-out": args.chunks_out,
               "--qdrant-out": args.qdrant_out}
    protected = [REPO_ROOT / d for d in PROTECTED_DIRS]
    if args.protect_source:
        protected.append(args.source_parsed)
    # Deduplicated by resolved path: data/parsed is both a protected directory
    # and the usual --source-parsed, and reporting that collision twice reads
    # like two separate problems.
    protected = list({p.resolve(): p for p in protected}.values())
    conflicts = path_conflicts(outputs, protected)
    checks["path_conflicts"] = conflicts
    problems += conflicts
    say(f"  paths              {'OK' if not conflicts else 'REFUSED'} — "
        f"outputs disjoint from {', '.join(PROTECTED_DIRS)}")
    for problem in conflicts:
        say(f"    ! {problem}")

    # Locks. The target must be free to be written; data/qdrant and the backup
    # must be free because a holder means a server is up, which both competes
    # for this 8 GB machine's memory and may ingest into the live store while
    # we run.
    locks: Dict[str, str] = {}
    for label, store in (("target", args.qdrant_out),
                         ("live data/qdrant", REPO_ROOT / "data" / "qdrant"),
                         ("v1 backup", REPO_ROOT / BACKUP_DIR)):
        state = qdrant_lock_state(store)
        locks[label] = state
        if state == "held":
            problems.append(f"a Qdrant lock on {store} is held by another process "
                            f"(stop uvicorn / any eval run and retry)")
    checks["qdrant_locks"] = locks
    held = [k for k, v in locks.items() if v == "held"]
    say(f"  qdrant locks       {'OK' if not held else 'REFUSED'} — "
        + ", ".join(f"{k}={v}" for k, v in locks.items()))

    # Disk.
    free_gb = shutil.disk_usage(REPO_ROOT).free / 1e9
    checks["free_gb"] = round(free_gb, 2)
    if free_gb < args.min_free_gb:
        problems.append(f"only {free_gb:.1f} GB free, need {args.min_free_gb} GB")
    say(f"  disk               {'OK' if free_gb >= args.min_free_gb else 'REFUSED'} — "
        f"{free_gb:.1f} GB free (need {args.min_free_gb})")

    # Scope.
    if not args.source_parsed.is_dir():
        problems.append(f"{args.source_parsed} is not a directory")
        checks["scope"] = {}
    else:
        tickers = scope_tickers()
        stems = filings_in_scope(args.source_parsed, tickers)
        if args.only:
            stems = [s for s in stems if s in args.only]
        missing = [f for f in REQUIRED_FILINGS if f not in stems] if not args.only else []
        checks["scope"] = {"tickers": tickers, "filings": stems,
                           "filing_count": len(stems), "missing_required": missing}
        if not stems:
            problems.append(f"no filings in scope under {args.source_parsed}")
        if missing:
            problems.append(f"required filings missing from the source parse: {missing}")
        say(f"  scope              {'OK' if not missing and stems else 'REFUSED'} — "
            f"{len(stems)} filing(s) across {len(tickers)} ticker(s)")
        if args.only:
            say(f"    (restricted by --only to {sorted(args.only)})")

    # The "do not touch" witnesses, taken before any work.
    run.integrity["protected_before"] = {
        d: fingerprint_dir(REPO_ROOT / d) for d in PROTECTED_DIRS
    }
    run.integrity["backup_points_before"] = count_points_readonly(REPO_ROOT / BACKUP_DIR)
    before = run.integrity["backup_points_before"]
    say(f"  v1 backup          {before['collections']} collection(s), "
        f"{before['points']} point(s)")

    run.stages["preflight"] = {"ok": not problems, "checks": checks,
                               "problems": problems}
    run.errors += problems
    say(f"  → preflight {'PASSED' if not problems else 'REFUSED TO START'}")
    say()
    return not problems


def stage_parse(args, run: Run, say: Tee) -> bool:
    say("── parse " + "─" * 58)
    args.parsed_out.mkdir(parents=True, exist_ok=True)
    stems = run.stages["preflight"]["checks"]["scope"]["filings"]

    done, reused, failed = [], [], []
    started = time.monotonic()
    for stem in stems:
        target = args.parsed_out / f"{stem}.json"
        if not args.force_parse and reparse_corpus.parse_is_complete(target):
            reused.append(stem)
            say(f"  reuse   {stem}")
            continue
        doc = json.loads((args.source_parsed / f"{stem}.json").read_text(encoding="utf-8"))
        src = reparse_corpus.resolve_source(doc)
        if src is None:
            failed.append({"filing": stem, "error": "no readable source document on disk"})
            say(f"  FAIL    {stem}: no readable source document on disk")
            continue
        try:
            name, sections, seconds = reparse_corpus.reparse_one(src, doc, args.parsed_out)
        except Exception as exc:
            failed.append({"filing": stem, "error": f"{type(exc).__name__}: {exc}"})
            say(f"  FAIL    {stem}: {type(exc).__name__}: {exc}")
            continue
        done.append({"filing": name, "sections": sections, "seconds": round(seconds, 1)})
        say(f"  parsed  {name:14s} {sections:3d} sections  {seconds:6.1f}s")

    run.stages["parse"] = {
        "ok": not failed, "attempted": len(stems), "parsed": len(done),
        "reused": len(reused), "failed": len(failed),
        "seconds": round(time.monotonic() - started, 1),
        "results": done, "reused_filings": reused, "failures": failed,
    }
    run.errors += [f"parse: {f['filing']}: {f['error']}" for f in failed]
    say(f"  → {len(done)} parsed, {len(reused)} reused, {len(failed)} failed "
        f"in {time.monotonic() - started:.1f}s")
    say()
    return not failed


def stage_chunk(args, run: Run, say: Tee) -> bool:
    say("── chunk " + "─" * 58)
    stems = set(run.stages["preflight"]["checks"]["scope"]["filings"])
    started = time.monotonic()
    results = rechunk_corpus.rechunk_corpus(
        args.parsed_out, args.chunks_out, only=stems, force=args.force_chunk,
        on_result=lambda r: say(rechunk_corpus.format_result(r)),
    )
    summary = rechunk_corpus.summarize(results)
    summary["ok"] = summary["failed"] == 0 and summary["filings"] == len(stems)
    summary["wall_seconds"] = round(time.monotonic() - started, 1)
    from ingestion.indexer import peak_rss_bytes
    summary["peak_rss_gb"] = round(peak_rss_bytes() / 1e9, 2)
    run.stages["chunk"] = summary
    run.errors += [f"chunk: {r['filing']}: {r['error']}"
                   for r in summary["results"] if r["status"] == "failed"]
    if summary["filings"] != len(stems):
        run.errors.append(f"chunk: {summary['filings']} filing(s) chunked, "
                          f"{len(stems)} in scope")
    say(f"  → {summary['chunked']} chunked, {summary['reused']} reused, "
        f"{summary['failed']} failed; {summary['chunks_total']} chunks in "
        f"{summary['wall_seconds']}s; peak RSS {summary['peak_rss_gb']} GB")
    say()
    return bool(summary["ok"])


def prune_stale_collections(store, chunks, say: Tee) -> List[str]:
    """Drop any collection holding a point that today's chunk files do not.

    This is what makes resuming safe across a re-chunk. The indexer resumes by
    skipping chunk ids already stored, and ``Chunk.chunk_id`` is a fresh
    ``uuid4`` on every chunking run — so a collection built from an earlier
    parse would keep all of its old points *and* gain a full set of new ones,
    leaving the store with two copies of every passage under different ids and
    a stale ``parent_id`` on half of them. Rebuilding exactly the collections
    whose contents no longer match is cheaper than a blanket ``--force-index``
    and catches the case however it arose: a forced re-parse, a half-finished
    earlier attempt, or a leftover collection from a different corpus.
    """
    from ingestion.indexer import group_by_collection

    dropped: List[str] = []
    for name, col_chunks in sorted(group_by_collection(chunks).items()):
        if not store.collection_exists(name):
            continue
        stored = store.existing_point_ids(name)
        wanted = {str(c.chunk_id) for c in col_chunks}
        if stored - wanted:
            store.delete_collection(name)
            dropped.append(name)
            say(f"  rebuild {name}: {len(stored - wanted)} stored point(s) are not "
                f"in today's chunk file")
    return dropped


def stage_index(args, run: Run, say: Tee) -> bool:
    say("── index " + "─" * 58)
    from ingestion.indexer import QdrantVectorStore, index_stream, load_chunks, peak_rss_bytes

    chunks = load_chunks(args.chunks_out)
    if not chunks:
        run.stages["index"] = {"ok": False, "error": f"no chunks in {args.chunks_out}"}
        run.errors.append(f"index: no chunks in {args.chunks_out}")
        say(f"  ! no chunks in {args.chunks_out}")
        return False

    say(f"  {len(chunks)} chunk(s) to consider; batch_size={args.batch_size or 'settings'}")
    store = QdrantVectorStore()
    dropped = prune_stale_collections(store, chunks, say)
    started = time.monotonic()
    index_run = index_stream(
        chunks,
        batch_size=args.batch_size,
        force_reindex=args.force_index,
        store=store,
        progress=lambda p: say("  " + p.line()),
    )
    payload = index_run.to_dict()
    payload["ok"] = not index_run.failed
    payload["rebuilt_collections"] = dropped
    payload["wall_seconds"] = round(time.monotonic() - started, 1)
    payload["peak_rss_gb"] = round(peak_rss_bytes() / 1e9, 2)
    run.stages["index"] = payload
    run.errors += [f"index: {c.name}: {c.error}" for c in index_run.failed]
    say(f"  → {index_run.chunks_embedded} chunk(s) embedded in "
        f"{index_run.seconds:.1f}s; peak RSS {payload['peak_rss_gb']} GB; "
        f"{len(index_run.failed)} failed collection(s)")
    say()
    return not index_run.failed


def release_qdrant_client() -> None:
    """Drop the module-level client so its exclusive lock is released.

    ``retrieval.vector_store`` caches one client for the process. The integrity
    checks read the new store from outside that client, and the activation
    script moves the directory, so the lock must be gone before this process
    does anything else with it. Importing the module just to find there is no
    client would be pointless work, so a run that never indexed is a no-op.
    """
    if "retrieval.vector_store" not in sys.modules:
        return
    from retrieval import vector_store

    client = getattr(vector_store, "_client", None)
    if client is not None:
        client.close()
        vector_store._client = None


def stage_checks(args, run: Run, say: Tee) -> bool:
    """T4-09: re-index integrity."""
    say("── checks (T4-09) " + "─" * 49)
    release_qdrant_client()
    problems: List[str] = []

    # 1. The v1 backup still holds exactly what it held.
    after = count_points_readonly(REPO_ROOT / BACKUP_DIR)
    before = run.integrity.get("backup_points_before", after)
    run.integrity["backup_points_after"] = after
    backup_ok = (after == before
                 and after["points"] == args.expect_backup_points
                 and after["collections"] == EXPECTED_BACKUP_COLLECTIONS)
    if not backup_ok:
        problems.append(
            f"v1 backup changed or does not match the recorded baseline: "
            f"{after['collections']} collection(s)/{after['points']} point(s), "
            f"expected {EXPECTED_BACKUP_COLLECTIONS}/{args.expect_backup_points}")
    say(f"  v1 backup unchanged      {'OK' if backup_ok else 'FAIL'} — "
        f"{after['collections']} collection(s), {after['points']} point(s) "
        f"(expected {EXPECTED_BACKUP_COLLECTIONS}/{args.expect_backup_points})")

    # 2. Nothing wrote to a protected directory.
    protected_after = {d: fingerprint_dir(REPO_ROOT / d) for d in PROTECTED_DIRS}
    run.integrity["protected_after"] = protected_after
    protected_before = run.integrity.get("protected_before", protected_after)
    moved = [d for d in PROTECTED_DIRS if protected_after[d] != protected_before.get(d)]
    run.integrity["protected_unchanged"] = not moved
    if moved:
        problems.append(f"protected directories were modified: {moved}")
    say(f"  protected dirs untouched {'OK' if not moved else 'FAIL'} — "
        + ", ".join(f"{d.split('/')[-1]}={protected_after[d]['files']}f" for d in PROTECTED_DIRS))

    # 3. No store shares a path with another.
    stores = {"new": args.qdrant_out, "live": REPO_ROOT / "data" / "qdrant",
              "v1_backup": REPO_ROOT / BACKUP_DIR}
    shared = path_conflicts({"new store": args.qdrant_out},
                            [stores["live"], stores["v1_backup"]])
    run.integrity["no_shared_paths"] = {"ok": not shared, "problems": shared,
                                        "paths": {k: str(v.resolve()) for k, v in stores.items()}}
    problems += shared
    say(f"  stores disjoint          {'OK' if not shared else 'FAIL'} — "
        f"{len(stores)} distinct path(s)")

    # 4. The new store holds what the chunk files hold.
    new_store = count_points_readonly(args.qdrant_out)
    run.integrity["new_store"] = new_store
    expected_chunks = run.stages.get("chunk", {}).get("chunks_total")
    expected_collections = len(run.stages["preflight"]["checks"]["scope"]["filings"])
    counts_ok = True
    if expected_chunks is not None and new_store["points"] != expected_chunks:
        counts_ok = False
        problems.append(f"new store holds {new_store['points']} point(s) but "
                        f"{expected_chunks} chunk(s) were written")
    if new_store["collections"] != expected_collections:
        counts_ok = False
        problems.append(f"new store holds {new_store['collections']} collection(s), "
                        f"expected {expected_collections}")
    say(f"  new store complete       {'OK' if counts_ok else 'FAIL'} — "
        f"{new_store['collections']} collection(s), {new_store['points']} point(s) "
        f"(chunks on disk: {expected_chunks})")

    # 5. Catalog: every new collection has a catalog row; no orphans.
    #    DRY RUN ON PURPOSE. Writing collection_name now would point the live
    #    system's text scope at collections that only exist in the new store,
    #    and the new store is not live until the activation script runs. The
    #    real link is activation's last step.
    catalog_report = {"ran": False}
    try:
        from catalog.store import CatalogStore
        from config import settings
        from ingestion.catalog_ingest import link_collections

        names = sorted(new_store["per_collection"])
        catalog = CatalogStore(settings.catalog_path)
        link = link_collections(catalog, names, dry_run=True)
        catalog_report = {
            "ran": True, "dry_run": True, "collections": len(names),
            "orphan_collections": list(link.orphan_collections),
            "would_link": len(link.linked), "already_linked": len(link.already),
            "unindexed_filings": len(link.unindexed_filings),
            "ok": link.ok,
        }
        if not link.ok:
            problems.append(f"catalog orphan collections: {link.orphan_collections}")
    except Exception as exc:
        catalog_report = {"ran": False, "error": f"{type(exc).__name__}: {exc}"}
        problems.append(f"catalog check could not run: {type(exc).__name__}: {exc}")
    run.integrity["catalog"] = catalog_report
    say(f"  catalog rows (dry run)   "
        f"{'OK' if catalog_report.get('ok') else 'FAIL'} — "
        f"orphans={len(catalog_report.get('orphan_collections', []))}, "
        f"would link {catalog_report.get('would_link', '-')}")

    run.integrity["ok"] = not problems
    run.integrity["problems"] = problems
    run.errors += [f"T4-09: {p}" for p in problems]
    say(f"  → T4-09 {'PASSED' if not problems else 'FAILED'}")
    say()
    return not problems


# ── CLI ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Re-run the same command to resume. It never writes to "
               + ", ".join(PROTECTED_DIRS) + ".",
    )
    ap.add_argument("--source-parsed", type=Path, default=REPO_ROOT / "data" / "parsed",
                    help="the existing parses, read for the filing list and source paths")
    ap.add_argument("--parsed-out", type=Path, default=REPO_ROOT / "data" / "parsed_v2")
    ap.add_argument("--chunks-out", type=Path, default=REPO_ROOT / "data" / "chunks_v2")
    ap.add_argument("--qdrant-out", type=Path, default=REPO_ROOT / "data" / "qdrant_v2")
    ap.add_argument("--report", type=Path,
                    default=REPO_ROOT / "reports" / "phase4" / "reindex_run.json")
    ap.add_argument("--log", type=Path,
                    default=REPO_ROOT / "reports" / "phase4" / "reindex_run.log")
    ap.add_argument("--only", default="",
                    help="comma-separated filing stems; for the subset rehearsal only")
    ap.add_argument("--stages", default=",".join(ALL_STAGES),
                    help=f"comma-separated subset of {','.join(ALL_STAGES)}")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="indexer batch size (default: settings.index_batch_size)")
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    ap.add_argument("--expect-backup-points", type=int, default=EXPECTED_BACKUP_POINTS)
    ap.add_argument("--dry-run", action="store_true",
                    help="run preflight only and write the report")
    ap.add_argument("--force-parse", action="store_true")
    ap.add_argument("--force-chunk", action="store_true",
                    help="re-chunk even when the chunk file matches today's parse; "
                         "affected collections are rebuilt automatically")
    ap.add_argument("--force-index", action="store_true")
    ap.add_argument("--no-protect-source", dest="protect_source", action="store_false",
                    help="allow writing into --source-parsed (never for P4-12)")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.only = {s.strip() for s in args.only.split(",") if s.strip()}
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    unknown = [s for s in stages if s not in ALL_STAGES]
    if unknown:
        print(f"error: unknown stage(s) {unknown}; pick from {ALL_STAGES}", file=sys.stderr)
        return 2
    # Before any repo import that could construct config.Settings. The whole
    # re-index is a configuration change (STATE 1d) and this is where it is made.
    os.environ["PARSED_DIR"] = str(args.parsed_out)
    os.environ["CHUNKS_DIR"] = str(args.chunks_out)
    os.environ["QDRANT_PATH"] = str(args.qdrant_out)
    import config
    mismatched = {
        "parsed_dir": (str(config.settings.parsed_dir), str(args.parsed_out)),
        "chunks_dir": (str(config.settings.chunks_dir), str(args.chunks_out)),
        "qdrant_path": (str(config.settings.qdrant_path), str(args.qdrant_out)),
    }
    bad = {k: v for k, v in mismatched.items() if Path(v[0]).resolve() != Path(v[1]).resolve()}
    if bad:
        # Fail loud rather than silently index into data/qdrant: this is the
        # single most destructive way this script could go wrong.
        print(f"error: settings did not pick up the output paths: {bad}", file=sys.stderr)
        return 2

    say = Tee(args.log)
    run = Run(started_at=now_iso(), config={
        "source_parsed_dir": str(args.source_parsed),
        "parsed_dir": str(args.parsed_out),
        "chunks_dir": str(args.chunks_out),
        "qdrant_path": str(args.qdrant_out),
        "report": str(args.report),
        "stages": stages,
        "only": sorted(args.only),
        "batch_size": args.batch_size,
        "dry_run": bool(args.dry_run),
        "forced": {"parse": args.force_parse, "chunk": args.force_chunk,
                   "index": args.force_index},
    })
    started = time.monotonic()
    say("")
    say(f"P4-12 re-index — {run.started_at}")
    say(f"  source parses  {args.source_parsed}")
    say(f"  parsed         {args.parsed_out}")
    say(f"  chunks         {args.chunks_out}")
    say(f"  qdrant         {args.qdrant_out}")
    say("")

    try:
        ok = stage_preflight(args, run, say)
        write_report(run, args.report)
        if not ok:
            run.status = "refused"
        elif args.dry_run:
            run.status = "dry-run-ok"
            say("  dry run: stopping after preflight.")
        else:
            for name, fn in (("parse", stage_parse), ("chunk", stage_chunk),
                             ("index", stage_index), ("checks", stage_checks)):
                if name not in stages:
                    say(f"── {name}: skipped (not in --stages)\n")
                    continue
                ok = fn(args, run, say)
                run.seconds = time.monotonic() - started
                write_report(run, args.report)
                if not ok:
                    run.status = "failed"
                    break
            else:
                run.status = "ok"
    except KeyboardInterrupt:
        run.status = "interrupted"
        run.errors.append("interrupted by the operator; re-run the same command to resume")
        say("\n  interrupted — re-run the same command to resume.")
    finally:
        run.seconds = time.monotonic() - started
        run.finished_at = now_iso()
        release_qdrant_client()
        write_report(run, args.report)
        say(f"status={run.status}  {run.seconds:.1f}s  report={args.report}")
        for err in run.errors:
            say(f"  ! {err}")
        say("")
        say.close()

    return 0 if run.status in ("ok", "dry-run-ok") else 1


if __name__ == "__main__":
    sys.exit(main())
