"""T1-12 and T1-13 — repo hygiene, and proof the hygiene checks can fail.

T1-12 asserts the real tracked tree is clean. T1-13 asserts each check
actually detects a planted violation, so a silent "no matches" cannot be a
false pass (CLAUDE.md rule 15, written after two Step 0 shell checks reported
false results).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_repo_hygiene as H  # noqa: E402

pytestmark = pytest.mark.unit


def _is_git_repo() -> bool:
    p = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                       cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return p.returncode == 0


requires_git = pytest.mark.skipif(not _is_git_repo(), reason="not a git checkout")


# ── T1-13: the checks can fail ──────────────────────────────────────────────

def test_self_test_passes():
    """Every check is exercised against a planted violation."""
    assert H.self_test() == 0


def test_key_pattern_detects_a_planted_secret(tmp_path):
    f = tmp_path / "leak.py"
    f.write_text('KEY = "gsk_' + "A" * 32 + '"\n', encoding="utf-8")
    hits = H.check_keys(tmp_path, ["leak.py"])
    assert len(hits) == 1
    assert hits[0].location == "leak.py:1"
    # the finding must never carry the matched content
    assert "gsk_" not in hits[0].detail and "gsk_" not in hits[0].location


def test_large_file_check_detects_oversize(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"\x00" * (H.MAX_FILE_BYTES + 1))
    assert H.check_large_files(tmp_path, ["big.bin"])
    (tmp_path / "small.bin").write_bytes(b"\x00" * 10)
    assert not H.check_large_files(tmp_path, ["small.bin"])


def test_reference_check_detects_a_deployment_url(tmp_path):
    (tmp_path / "doc.md").write_text("https://foo.up.railway.app/health\n", encoding="utf-8")
    assert H.check_references(tmp_path, ["doc.md"])


def test_reference_check_exempts_historical_records(tmp_path):
    rel = "reports/phase0/REPORT.md"
    (tmp_path / "reports" / "phase0").mkdir(parents=True)
    (tmp_path / rel).write_text("https://foo.up.railway.app/\n", encoding="utf-8")
    assert not H.check_references(tmp_path, [rel]), (
        "Phase 0 records deliberately document the old deployment"
    )


def test_project_own_name_is_not_flagged(tmp_path):
    """The project's own working name must not be a finding."""
    (tmp_path / "x.md").write_text("# Financial_RAG v2 — Project Specification\n",
                                   encoding="utf-8")
    assert not H.check_references(tmp_path, ["x.md"])


# ── T1-12: the real tree is clean ───────────────────────────────────────────

@requires_git
def test_tracked_tree_is_clean():
    findings = H.run(REPO_ROOT)
    assert not findings, "\n".join(f"{f.check} {f.location} [{f.detail}]" for f in findings)


@requires_git
def test_license_and_notice_are_tracked():
    tracked = set(H.tracked_files(REPO_ROOT))
    assert "LICENSE" in tracked
    assert "NOTICE" in tracked


@requires_git
def test_env_is_not_tracked():
    assert ".env" not in set(H.tracked_files(REPO_ROOT))


@requires_git
def test_env_is_ignored():
    p = subprocess.run(["git", "check-ignore", "-q", ".env"],
                       cwd=REPO_ROOT, check=False)
    assert p.returncode == 0, ".env must be covered by .gitignore"


# ── T1-10: chat history untracked and ignored ───────────────────────────────

@requires_git
def test_chat_db_is_not_tracked():
    tracked = H.tracked_files(REPO_ROOT)
    offenders = [f for f in tracked if f.startswith("data/chat_history.db")]
    assert not offenders, f"chat history must not be tracked: {offenders}"


@requires_git
@pytest.mark.parametrize("name", [
    "data/chat_history.db",
    "data/chat_history.db-wal",
    "data/chat_history.db-shm",
])
def test_chat_db_and_sidecars_are_ignored(name):
    """T1-10. SQLite recreates -wal/-shm at runtime, so all three must match."""
    p = subprocess.run(["git", "check-ignore", "-q", name], cwd=REPO_ROOT, check=False)
    assert p.returncode == 0, f"{name} must be covered by .gitignore"


@requires_git
def test_negative_control_check_ignore_can_fail():
    """rule 15: prove check-ignore distinguishes ignored from tracked paths."""
    p = subprocess.run(["git", "check-ignore", "-q", "README.md"],
                       cwd=REPO_ROOT, check=False)
    assert p.returncode != 0, "README.md is tracked; check-ignore must not match it"
