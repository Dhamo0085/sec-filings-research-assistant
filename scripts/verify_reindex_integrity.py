#!/usr/bin/env python3
"""Check the T4-09 claims in ``reports/phase4/reindex_run.json`` against the disk.

    .venv/bin/python scripts/verify_reindex_integrity.py
    .venv/bin/python scripts/verify_reindex_integrity.py --store data/qdrant   # after activation

``scripts/reindex_v2.py`` writes its own integrity block at the end of a run.
That block is a claim the runner makes about itself, so this script re-derives
every number from the artifacts instead, importing nothing from the runner: a
bug in the runner's counting or fingerprinting cannot then hide in both places.
Point counts come from each collection's ``storage.sqlite`` over a read-only,
immutable URI, so the live store and the frozen v1 backup are read without a
client and without taking Qdrant's exclusive lock.

Exit status is 0 only if every check passes. ``--self-test`` plants a fault and
asserts the checks notice it (CLAUDE.md rule 15).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


def count_store(store: Path) -> Dict[str, int]:
    """Points per collection, read from sqlite without opening a Qdrant client.

    ``immutable=1`` promises sqlite the file will not change under it, which is
    what lets this run against the live store while something else holds the
    Qdrant lock. It is a read; it does not alter size or mtime, so it leaves
    :func:`fingerprint_dir` untouched.
    """
    out: Dict[str, int] = {}
    collection_dir = store / "collection"
    if not collection_dir.is_dir():
        return out
    for sub in sorted(p for p in collection_dir.iterdir() if p.is_dir()):
        db = sub / "storage.sqlite"
        if not db.is_file():
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "points" not in tables:
                raise SystemExit(f"no 'points' table in {db}; tables={sorted(tables)}")
            out[sub.name] = con.execute("SELECT COUNT(*) FROM points").fetchone()[0]
        finally:
            con.close()
    return out


def fingerprint_dir(directory: Path) -> dict:
    """Name, size and mtime of every file below ``directory``, hashed.

    The same recipe ``reindex_v2.fingerprint_dir`` records, reimplemented here
    rather than imported so that the comparison is worth making.
    """
    if not directory.is_dir():
        return {"exists": False, "files": 0, "bytes": 0, "sha256": ""}
    digest = hashlib.sha256()
    files = 0
    total = 0
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        stat = path.stat()
        digest.update(
            f"{path.relative_to(directory)}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
        files += 1
        total += stat.st_size
    return {"exists": True, "files": files, "bytes": total, "sha256": digest.hexdigest()}


def catalog_rows(catalog_db: Path) -> List[Tuple[str, int, Optional[str]]]:
    con = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    try:
        return [(t, int(fl), cn) for t, fl, cn in con.execute(
            "SELECT ticker, fiscal_label, collection_name FROM filings")]
    finally:
        con.close()


def run_checks(root: Path, store_name: str) -> Tuple[List[Tuple[str, bool, str]], dict]:
    report = json.loads((root / "reports/phase4/reindex_run.json").read_text())
    integrity = report["integrity"]
    expected = integrity["new_store"]["per_collection"]

    new = count_store(root / "data" / store_name)
    backup = count_store(root / "data/qdrant_v1_backup")

    results: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), "" if ok else detail))

    # T4-09 (a) the new store holds what the run says it holds
    check("run status is ok with no errors",
          report["status"] == "ok" and not report["errors"],
          f"status={report['status']} errors={report['errors']}")
    check(f"{store_name}: collection count == {len(expected)}",
          len(new) == len(expected), f"got {len(new)}")
    check(f"{store_name}: per-collection point counts == the run's record",
          new == expected,
          str({k: (new.get(k), expected.get(k)) for k in set(new) | set(expected)
               if new.get(k) != expected.get(k)}))
    check(f"{store_name}: every collection holds at least one point",
          all(v > 0 for v in new.values()),
          str([k for k, v in new.items() if v == 0]))

    # T4-09 (b) the v1 backup's point counts are unchanged
    check("v1 backup: 25 collections / 14,557 points",
          len(backup) == 25 and sum(backup.values()) == 14557,
          f"got {len(backup)} / {sum(backup.values())}")
    check("v1 backup: per-collection counts == the pre-run record",
          backup == integrity["backup_points_before"]["per_collection"], "mismatch")
    check("v1 backup: per-collection counts == the post-run record",
          backup == integrity["backup_points_after"]["per_collection"], "mismatch")

    # T4-09 (c) the stores never share a path
    paths = {"new": (root / "data" / store_name).resolve(),
             "v1_backup": (root / "data/qdrant_v1_backup").resolve()}
    live = (root / "data/qdrant").resolve()
    if live != paths["new"]:
        paths["live"] = live
    shared: List[str] = []
    for a in paths:
        for b in paths:
            if a < b and (paths[a] == paths[b]
                          or paths[a] in paths[b].parents
                          or paths[b] in paths[a].parents):
                shared.append(f"{a}~{b}")
    check("the stores share no path (equal, or one inside the other)",
          not shared, str(shared))

    # T4-09 (d) catalog: no orphan collection, no dangling link
    rows = catalog_rows(root / "data/derived/catalog.sqlite")
    keys = {f"{t}_{fl}" for t, fl, _ in rows}
    orphans = sorted(set(new) - keys)
    check("catalog orphan count is 0 (every collection has a catalog row)",
          not orphans, str(orphans))
    linked = {cn for _, _, cn in rows if cn}
    dangling = sorted(linked - set(new))
    check("no catalog row links to a collection the store does not hold",
          not dangling, str(dangling))

    # the chunk files on disk back the store point for point
    chunks_name = "chunks" if store_name == "qdrant" else "chunks_v2"
    parsed_name = "parsed" if store_name == "qdrant" else "parsed_v2"
    chunk_files = sorted((root / "data" / chunks_name).glob("*.json"))
    chunk_total = 0
    for path in chunk_files:
        payload = json.loads(path.read_text())
        items = payload if isinstance(payload, list) else payload.get("chunks", payload)
        chunk_total += len(items)
    check(f"data/{chunks_name}: one file per collection ({len(expected)})",
          len(chunk_files) == len(expected), f"got {len(chunk_files)}")
    check(f"data/{chunks_name}: total chunks == points in the store",
          chunk_total == sum(new.values()),
          f"chunks={chunk_total} points={sum(new.values())}")
    check(f"data/{parsed_name}: one parsed filing per collection ({len(expected)})",
          len(list((root / "data" / parsed_name).glob("*.json"))) == len(expected),
          f"got {len(list((root / 'data' / parsed_name).glob('*.json')))}")

    summary = {"store": store_name, "collections": len(new), "points": sum(new.values()),
               "backup_collections": len(backup), "backup_points": sum(backup.values()),
               "chunk_files": len(chunk_files), "chunks": chunk_total}
    return results, summary


def fingerprint_checks(root: Path, store_name: str) -> List[Tuple[str, bool, str]]:
    """Protected directories must still match what the run recorded.

    Only meaningful before activation: activation renames three of the four by
    design, so this is skipped once the swap has happened.
    """
    report = json.loads((root / "reports/phase4/reindex_run.json").read_text())
    recorded = report["integrity"]["protected_after"]
    out: List[Tuple[str, bool, str]] = []
    for rel, want in recorded.items():
        got = fingerprint_dir(root / rel)
        ok = got == want
        out.append((f"{rel}: unchanged since the run finished", ok,
                    "" if ok else f"now={got} recorded={want}"))
    return out


def self_test(root: Path) -> int:
    """Plant a fault and prove the checks catch it."""
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp)
        (fake / "data/derived").mkdir(parents=True)
        (fake / "reports/phase4").mkdir(parents=True)
        shutil.copy2(root / "reports/phase4/reindex_run.json",
                     fake / "reports/phase4/reindex_run.json")
        shutil.copy2(root / "data/derived/catalog.sqlite",
                     fake / "data/derived/catalog.sqlite")
        # copy only two collections of the new store: the count check must fail
        src = root / "data/qdrant_v2"
        src = src if src.is_dir() else root / "data/qdrant"
        for name in sorted(p.name for p in (src / "collection").iterdir())[:2]:
            shutil.copytree(src / "collection" / name,
                            fake / "data/planted/collection" / name)
        (fake / "data/qdrant_v1_backup").mkdir(parents=True)
        (fake / "data/chunks_v2").mkdir(parents=True)
        (fake / "data/parsed_v2").mkdir(parents=True)
        results, _ = run_checks(fake, "planted")
        failed = [name for name, ok, _ in results if not ok]
        expected_to_fail = {
            "planted: collection count == 39",
            "planted: per-collection point counts == the run's record",
            "v1 backup: 25 collections / 14,557 points",
        }
        missing = expected_to_fail - set(failed)
        if missing:
            print(f"SELF-TEST FAILED: these planted faults went unnoticed: {sorted(missing)}")
            return 1
        print(f"SELF-TEST PASSED: the planted corpus failed {len(failed)} check(s), "
              f"including every one it had to.")
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--store", default="data/qdrant_v2",
                    help="the re-indexed store to check (data/qdrant after activation)")
    ap.add_argument("--skip-fingerprints", action="store_true",
                    help="skip the protected-directory fingerprints (they move at activation)")
    ap.add_argument("--self-test", action="store_true",
                    help="plant a fault and assert the checks notice")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test(args.root)

    store_name = Path(args.store).name
    results, summary = run_checks(args.root, store_name)
    if not args.skip_fingerprints:
        results += fingerprint_checks(args.root, store_name)

    width = max(len(name) for name, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL':5} {name:{width}}  {detail}".rstrip())
    print()
    print(f"  {summary['store']}: {summary['collections']} collections, "
          f"{summary['points']} points")
    print(f"  v1 backup: {summary['backup_collections']} collections, "
          f"{summary['backup_points']} points")
    print(f"  chunk files: {summary['chunk_files']}, chunks: {summary['chunks']}")
    failed = sum(1 for _, ok, _ in results if not ok)
    print()
    print(f"ALL {len(results)} CHECKS PASS" if not failed
          else f"{failed} of {len(results)} CHECKS FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
