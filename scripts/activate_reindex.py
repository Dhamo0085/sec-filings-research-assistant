#!/usr/bin/env python3
"""Swap the P4-12 re-indexed artifacts in — or back out again. Renames only.

    .venv/bin/python scripts/activate_reindex.py            # show the plan
    .venv/bin/python scripts/activate_reindex.py --yes      # do it
    .venv/bin/python scripts/activate_reindex.py --undo     # put it all back

**Why all three move together.** ``data/parsed``, ``data/chunks`` and
``data/qdrant`` are one artifact in three directories. A chunk's ``parent_id``
names a section in the parsed document, and retrieval fetches that parent to
build the context a narrative answer cites; a point in the index *is* a chunk.
P4-00 rewrote the section boundaries, so a v2 index beside a v1 parse would
retrieve passages whose parent sections have different contents, or none —
wrong answers with real citations, which is the worst failure this system has.
So the swap is all six renames or none of them.

**Nothing is deleted, ever.** The current artifacts are renamed aside to
``data/<name>_retired_<stamp>`` and left there. ``_retired_`` rather than
``_v1_`` on purpose: ``data/qdrant_v1_backup`` is the frozen store T4-07 pins,
and a retired directory called ``data/qdrant_v1_<stamp>`` would put it inside
the blast radius of the obvious ``rm -rf data/qdrant_v1_*`` cleanup later.
Reclaiming the space is a separate, deliberate ``rm`` by the owner, after the
post-swap evaluation has been run and believed. ``data/qdrant_v1_backup`` is not
involved and is never touched: it is the frozen store the V0 arm and D25 read,
and T4-07 pins it.

**Undo is one command.** Each run writes a journal under ``data/activation/``
before it moves anything and appends to it after every single rename, so
``--undo`` reverses exactly the moves that completed — including after a crash
halfway through, which would otherwise leave the three directories describing
two different corpora with no record of which way round they were.

Offline: renames and one catalog relink. No network, no LLM, no embedding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.reindex_v2 import qdrant_lock_state  # noqa: E402

DATA = REPO_ROOT / "data"
JOURNAL_DIR = DATA / "activation"

#: (live name, staged name) for each of the three directories that move together.
PAIRS: Tuple[Tuple[str, str], ...] = (
    ("parsed", "parsed_v2"),
    ("chunks", "chunks_v2"),
    ("qdrant", "qdrant_v2"),
)

#: Never moved, never read here. The V0 arm and D25 depend on it (T4-07).
UNTOUCHABLE = "qdrant_v1_backup"

REPORT = REPO_ROOT / "reports" / "phase4" / "reindex_run.json"


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ── journal ────────────────────────────────────────────────────────────────

def journal_path(stamp: str) -> Path:
    return JOURNAL_DIR / f"activation_{stamp}.json"


def latest_journal() -> Optional[Path]:
    if not JOURNAL_DIR.is_dir():
        return None
    journals = sorted(JOURNAL_DIR.glob("activation_*.json"))
    return journals[-1] if journals else None


def write_journal(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def move(src: Path, dst: Path, journal: Path, payload: Dict) -> None:
    """One rename, recorded the moment it succeeds.

    Recorded *after* rather than before: a move written down but never made
    would send --undo looking for a directory that is still where it started,
    and a move made but not written down is the one case the undo has to catch.
    ``os.rename`` is atomic within a filesystem, so there is no third state.
    """
    os.rename(src, dst)
    payload["completed"].append({"from": str(src), "to": str(dst)})
    write_journal(journal, payload)


# ── preflight ──────────────────────────────────────────────────────────────

def check_ready(stamp: str) -> Tuple[List[str], List[str]]:
    """(problems, notes) for an activation. Empty problems means go."""
    problems: List[str] = []
    notes: List[str] = []

    for live, staged in PAIRS:
        staged_dir = DATA / staged
        if not staged_dir.is_dir() or not any(staged_dir.iterdir()):
            problems.append(f"data/{staged} is missing or empty — run the re-index first")
        if not (DATA / live).is_dir():
            notes.append(f"data/{live} does not exist; nothing to retire for it")
        retired = DATA / f"{live}_retired_{stamp}"
        if retired.exists():
            problems.append(f"{retired} already exists; refusing to overwrite it")

    for label, store in (("data/qdrant", DATA / "qdrant"),
                         ("data/qdrant_v2", DATA / "qdrant_v2")):
        state = qdrant_lock_state(store)
        if state == "held":
            problems.append(f"a Qdrant lock on {label} is held by another process "
                            f"(stop uvicorn and any eval run, then retry)")

    if not REPORT.is_file():
        problems.append(f"{REPORT.relative_to(REPO_ROOT)} is missing — "
                        f"the re-index has not reported success")
    else:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        if report.get("status") != "ok":
            problems.append(f"the re-index report says status={report.get('status')!r}, "
                            f"not 'ok'")
        if not report.get("integrity", {}).get("ok"):
            problems.append("the re-index report's T4-09 integrity checks did not pass")
        else:
            new_store = report["integrity"].get("new_store", {})
            notes.append(f"new store: {new_store.get('collections')} collection(s), "
                         f"{new_store.get('points')} point(s)")

    return problems, notes


# ── actions ────────────────────────────────────────────────────────────────

def plan(stamp: str) -> List[Tuple[Path, Path]]:
    """The six renames, retire-then-promote so no name is ever occupied twice."""
    moves: List[Tuple[Path, Path]] = []
    for live, _ in PAIRS:
        if (DATA / live).is_dir():
            moves.append((DATA / live, DATA / f"{live}_retired_{stamp}"))
    for live, staged in PAIRS:
        moves.append((DATA / staged, DATA / live))
    return moves


def activate(args) -> int:
    stamp = now_stamp()
    problems, notes = check_ready(stamp)
    moves = plan(stamp)

    print(f"\nP4-12 activation plan ({stamp})")
    print(f"  {UNTOUCHABLE} is not involved and will not be touched.\n")
    for src, dst in moves:
        print(f"  mv  {src.relative_to(REPO_ROOT)}  →  {dst.relative_to(REPO_ROOT)}")
    for note in notes:
        print(f"\n  note: {note}")
    if problems:
        print("\nREFUSED:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1
    if not args.yes:
        print("\n  Nothing moved. Re-run with --yes to perform the swap.")
        return 0

    journal = journal_path(stamp)
    payload = {
        "stamp": stamp, "direction": "activate", "started_at": now_stamp(),
        "planned": [{"from": str(s), "to": str(d)} for s, d in moves],
        "completed": [], "finished": False,
    }
    write_journal(journal, payload)

    print()
    for src, dst in moves:
        move(src, dst, journal, payload)
        print(f"  moved {src.name} → {dst.name}")
    payload["finished"] = True
    write_journal(journal, payload)

    rc = relink_catalog()
    print(f"\nActivated. Journal: {journal.relative_to(REPO_ROOT)}")
    print("Undo with one command:\n")
    print("    .venv/bin/python scripts/activate_reindex.py --undo\n")
    print("The retired artifacts are kept, not deleted:")
    for live, _ in PAIRS:
        retired = DATA / f"{live}_retired_{stamp}"
        if retired.exists():
            print(f"    {retired.relative_to(REPO_ROOT)}")
    return rc


def undo(args) -> int:
    path = Path(args.journal) if args.journal else latest_journal()
    if path is None or not path.is_file():
        print("error: no activation journal found under data/activation/", file=sys.stderr)
        return 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("direction") != "activate":
        print(f"error: {path} is not an activation journal", file=sys.stderr)
        return 2

    completed = payload.get("completed", [])
    if not completed:
        print(f"{path.name} records no completed moves; nothing to undo.")
        return 0

    # Reverse order: the forward plan retires the live names before promoting
    # the staged ones onto them, so undoing must free each name in the opposite
    # sequence or a rename lands on an occupied path.
    reverse = [(Path(m["to"]), Path(m["from"])) for m in reversed(completed)]

    print(f"\nUndo plan (from {path.name})")
    for src, dst in reverse:
        print(f"  mv  {src.relative_to(REPO_ROOT)}  →  {dst.relative_to(REPO_ROOT)}")

    blocked = [str(s) for s, _ in reverse if not s.exists()]
    if blocked:
        print("\nREFUSED: these paths are not where the journal says they are:")
        for b in blocked:
            print(f"  ! {b}")
        print("  Someone moved them by hand. Fix that first; nothing was changed.")
        return 1
    for label, store in (("data/qdrant", DATA / "qdrant"),
                         ("data/qdrant_v2", DATA / "qdrant_v2")):
        if qdrant_lock_state(store) == "held":
            print(f"\nREFUSED: a Qdrant lock on {label} is held by another process.")
            return 1

    undo_journal = journal_path(payload["stamp"] + "-undo")
    undo_payload = {
        "stamp": payload["stamp"] + "-undo", "direction": "undo",
        "started_at": now_stamp(), "of_journal": str(path),
        "planned": [{"from": str(s), "to": str(d)} for s, d in reverse],
        "completed": [], "finished": False,
    }
    write_journal(undo_journal, undo_payload)

    print()
    for src, dst in reverse:
        move(src, dst, undo_journal, undo_payload)
        print(f"  moved {src.name} → {dst.name}")
    undo_payload["finished"] = True
    write_journal(undo_journal, undo_payload)

    rc = relink_catalog()
    print(f"\nReverted. The previous artifacts are live again. "
          f"Journal: {undo_journal.relative_to(REPO_ROOT)}")
    return rc


def relink_catalog() -> int:
    """Re-join catalog.filings.collection_name to whatever is now in the store.

    Deliberately *after* the swap, never before. ``collection_name`` is what the
    text path's ``as_of`` scope reads, so writing it while the new store is
    still staged would point every scoped query at collections the live store
    does not hold (P3-06 found that failure the other way round: all 483 rows
    were null and the scope came back empty on real data).
    """
    print("\nRelinking the catalog to the live store …")
    from catalog.store import CatalogStore
    from config import settings
    from ingestion.catalog_ingest import link_collections
    from retrieval.vector_store import get_client, list_collections

    names = sorted(list_collections())
    catalog = CatalogStore(settings.catalog_path)
    report = link_collections(catalog, names, dry_run=False)
    get_client().close()
    print(f"  {len(names)} collection(s): linked {len(report.linked)}, "
          f"already correct {len(report.already)}, "
          f"cleared {len(report.cleared)}, "
          f"orphans {len(report.orphan_collections)}, "
          f"unindexed catalog filings {len(report.unindexed_filings)}")
    for name in report.cleared:
        print(f"  - cleared a link to {name}: the live store no longer holds it")
    for name in report.orphan_collections:
        print(f"  ! orphan collection with no catalog filing: {name}")
    return 0 if report.ok else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Nothing is ever deleted: the current artifacts are renamed aside.",
    )
    ap.add_argument("--yes", action="store_true",
                    help="perform the swap (without it, the plan is printed and "
                         "nothing moves)")
    ap.add_argument("--undo", action="store_true",
                    help="reverse the most recent activation")
    ap.add_argument("--journal", default="",
                    help="undo this specific journal instead of the most recent")
    args = ap.parse_args(argv)

    if args.undo:
        return undo(args)
    return activate(args)


if __name__ == "__main__":
    sys.exit(main())
