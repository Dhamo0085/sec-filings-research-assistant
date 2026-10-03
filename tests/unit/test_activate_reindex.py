"""The P4-12 activation swap: all three directories, nothing deleted, undoable.

``data/parsed``, ``data/chunks`` and ``data/qdrant`` are one artifact split
across three directories — a chunk's ``parent_id`` names a section in the
parsed document, and a point in the index is a chunk. P4-00 rewrote the section
boundaries, so a v2 index beside a v1 parse would cite passages whose parent
sections hold something else. These tests pin the three properties that keeps
safe: the swap is all-or-nothing in effect, the old artifacts survive it, and
an undo puts every directory back — including after a crash mid-swap.

``relink_catalog`` is replaced throughout: it opens the real catalog and the
real store, and what is being tested here is the moves.
"""

from __future__ import annotations

import json

import pytest

from scripts import activate_reindex as ar

pytestmark = pytest.mark.unit

LIVE = ("parsed", "chunks", "qdrant")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A data/ directory with v1 artifacts live, v2 staged, and a clean report."""
    data = tmp_path / "data"
    for name in LIVE:
        (data / name).mkdir(parents=True)
        (data / name / "marker.txt").write_text(f"v1 {name}", encoding="utf-8")
        (data / f"{name}_v2").mkdir(parents=True)
        (data / f"{name}_v2" / "marker.txt").write_text(f"v2 {name}", encoding="utf-8")
    (data / ar.UNTOUCHABLE).mkdir()
    (data / ar.UNTOUCHABLE / "frozen.txt").write_text("v1 backup", encoding="utf-8")

    report = tmp_path / "reports" / "phase4" / "reindex_run.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "status": "ok",
        "integrity": {"ok": True, "new_store": {"collections": 39, "points": 37000}},
    }), encoding="utf-8")

    monkeypatch.setattr(ar, "DATA", data)
    monkeypatch.setattr(ar, "JOURNAL_DIR", data / "activation")
    monkeypatch.setattr(ar, "REPORT", report)
    monkeypatch.setattr(ar, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ar, "relink_catalog", lambda: 0)
    return data


def marker(data, name):
    return (data / name / "marker.txt").read_text(encoding="utf-8")


def retired(data, name):
    return sorted(data.glob(f"{name}_retired_*"))


# ── the swap ───────────────────────────────────────────────────────────────

def test_without_yes_nothing_moves(world, capsys):
    assert ar.main([]) == 0
    assert capsys.readouterr().out.count("mv  ") == 6
    for name in LIVE:
        assert marker(world, name) == f"v1 {name}"
        assert (world / f"{name}_v2").is_dir()


def test_all_three_directories_swap_together(world):
    assert ar.main(["--yes"]) == 0
    for name in LIVE:
        assert marker(world, name) == f"v2 {name}", f"{name} did not become v2"
        assert not (world / f"{name}_v2").exists()


def test_the_old_artifacts_are_renamed_not_deleted(world):
    ar.main(["--yes"])
    for name in LIVE:
        kept = retired(world, name)
        assert len(kept) == 1, f"{name} was not retired exactly once"
        assert (kept[0] / "marker.txt").read_text(encoding="utf-8") == f"v1 {name}"


def test_the_v1_backup_is_never_touched(world):
    from scripts.reindex_v2 import fingerprint_dir

    before = fingerprint_dir(world / ar.UNTOUCHABLE)
    ar.main(["--yes"])
    assert fingerprint_dir(world / ar.UNTOUCHABLE) == before


def test_undo_restores_every_directory(world):
    ar.main(["--yes"])
    assert ar.main(["--undo"]) == 0
    for name in LIVE:
        assert marker(world, name) == f"v1 {name}"
        assert (world / f"{name}_v2" / "marker.txt").read_text(encoding="utf-8") == \
            f"v2 {name}"
        assert retired(world, name) == []


def test_undo_is_recorded_as_its_own_journal(world):
    ar.main(["--yes"])
    ar.main(["--undo"])
    journals = sorted((world / "activation").glob("activation_*.json"))
    directions = [json.loads(p.read_text(encoding="utf-8"))["direction"] for p in journals]
    assert sorted(directions) == ["activate", "undo"]


# ── crash in the middle ────────────────────────────────────────────────────

def test_a_crash_mid_swap_is_undone_from_the_journal(world, monkeypatch):
    """The case the journal exists for.

    Six renames are not one atomic operation. Stopping after four leaves the
    three directories describing two different corpora, and without a record of
    which moves happened there is no safe way back.
    """
    real_rename = ar.os.rename
    calls = {"n": 0}

    def exploding_rename(src, dst):
        if calls["n"] >= 4:
            raise OSError("simulated power failure")
        calls["n"] += 1
        return real_rename(src, dst)

    monkeypatch.setattr(ar.os, "rename", exploding_rename)
    with pytest.raises(OSError):
        ar.main(["--yes"])
    monkeypatch.setattr(ar.os, "rename", real_rename)

    journal = json.loads(ar.latest_journal().read_text(encoding="utf-8"))
    assert journal["finished"] is False
    assert len(journal["completed"]) == 4

    assert ar.main(["--undo"]) == 0
    for name in LIVE:
        assert marker(world, name) == f"v1 {name}"
        assert marker(world, f"{name}_v2") == f"v2 {name}"
        assert retired(world, name) == []


def test_undo_refuses_when_a_recorded_path_has_been_moved_by_hand(world, capsys):
    ar.main(["--yes"])
    moved = retired(world, "parsed")[0]
    moved.rename(world / "somewhere_else")

    assert ar.main(["--undo"]) == 1
    assert "not where the journal says" in capsys.readouterr().out
    # Refused means refused: nothing else was moved either.
    assert marker(world, "chunks") == "v2 chunks"


def test_undo_without_a_journal_is_an_error(world):
    assert ar.main(["--undo"]) == 2


# ── refusals ───────────────────────────────────────────────────────────────

def test_activation_is_refused_when_the_staged_artifacts_are_missing(world, capsys):
    import shutil

    shutil.rmtree(world / "qdrant_v2")
    assert ar.main(["--yes"]) == 1
    assert "data/qdrant_v2 is missing or empty" in capsys.readouterr().out
    assert marker(world, "qdrant") == "v1 qdrant"


def test_activation_is_refused_when_the_report_says_the_run_failed(world, capsys):
    ar.REPORT.write_text(json.dumps({"status": "interrupted",
                                     "integrity": {"ok": True}}), encoding="utf-8")
    assert ar.main(["--yes"]) == 1
    assert "status='interrupted'" in capsys.readouterr().out
    assert marker(world, "parsed") == "v1 parsed"


def test_activation_is_refused_when_t4_09_did_not_pass(world, capsys):
    ar.REPORT.write_text(json.dumps({"status": "ok", "integrity": {"ok": False}}),
                         encoding="utf-8")
    assert ar.main(["--yes"]) == 1
    assert "integrity checks did not pass" in capsys.readouterr().out


def test_activation_is_refused_when_the_report_is_missing(world, capsys):
    ar.REPORT.unlink()
    assert ar.main(["--yes"]) == 1
    assert "has not reported success" in capsys.readouterr().out


def test_activation_is_refused_while_a_qdrant_lock_is_held(world, capsys):
    """Negative control for the guard that already cost this project a run."""
    import portalocker

    lock = world / "qdrant" / ".lock"
    lock.write_text("tmp lock file", encoding="utf-8")
    with lock.open("r+") as holder:
        portalocker.lock(
            holder, portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING
        )
        assert ar.main(["--yes"]) == 1
        assert "lock on data/qdrant is held" in capsys.readouterr().out
        portalocker.unlock(holder)
    assert marker(world, "qdrant") == "v1 qdrant"


def test_a_clean_world_is_accepted(world):
    """The negative control for every refusal above: unchanged, it goes."""
    problems, _ = ar.check_ready("20261004T000000Z")
    assert problems == []
