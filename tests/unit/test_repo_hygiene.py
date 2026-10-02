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


# Assembled at runtime rather than written literally: a literal would make
# this file itself a finding for the very check it tests.
_PLANTED_URL = "https://foo.up." + "rail" + "way.app/health"


def test_reference_check_detects_a_deployment_url(tmp_path):
    (tmp_path / "doc.md").write_text(_PLANTED_URL + "\n", encoding="utf-8")
    assert H.check_references(tmp_path, ["doc.md"])


def test_reference_check_exempts_historical_records(tmp_path):
    rel = "reports/phase0/REPORT.md"
    (tmp_path / "reports" / "phase0").mkdir(parents=True)
    (tmp_path / rel).write_text(_PLANTED_URL + "\n", encoding="utf-8")
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


# ── T2-12: owner-supplied patterns from a gitignored .hygiene_local ─────────

def test_local_patterns_loader_parses_literals_regexes_and_comments(tmp_path):
    (tmp_path / H.LOCAL_PATTERNS_FILE).write_text(
        "# a comment\n"
        "\n"
        "Some-Old-Handle\n"
        r"re:old[0-9]+\.example\.net" "\n",
        encoding="utf-8",
    )
    loaded = H.load_local_patterns(tmp_path)
    assert [lp.label for lp in loaded] == ["local#1", "local#2"]


def test_local_patterns_loader_is_empty_without_the_file(tmp_path):
    assert H.load_local_patterns(tmp_path) == []


def test_local_reference_detects_a_planted_pattern(tmp_path):
    (tmp_path / H.LOCAL_PATTERNS_FILE).write_text("Some-Old-Handle\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("forked from Some-Old-Handle/thing\n",
                                        encoding="utf-8")
    (tmp_path / "clean.md").write_text("nothing to see\n", encoding="utf-8")
    hits = H.check_local_references(tmp_path, ["readme.md", "clean.md"])
    assert [h.location for h in hits] == ["readme.md:1"]
    # the finding carries an index, never the owner's pattern
    assert hits[0].detail == "pattern=local#1"
    assert "Old-Handle" not in hits[0].detail


def test_local_reference_is_case_insensitive_and_matches_literally(tmp_path):
    """A literal line is escaped, so regex metacharacters are not special."""
    (tmp_path / H.LOCAL_PATTERNS_FILE).write_text("a.b-project\n", encoding="utf-8")
    (tmp_path / "upper.md").write_text("A.B-PROJECT\n", encoding="utf-8")
    (tmp_path / "dotted.md").write_text("axb-project\n", encoding="utf-8")
    assert H.check_local_references(tmp_path, ["upper.md"])
    assert not H.check_local_references(tmp_path, ["dotted.md"]), (
        "'.' in a literal pattern must not act as a regex wildcard"
    )


def test_local_reference_regex_form_works(tmp_path):
    (tmp_path / H.LOCAL_PATTERNS_FILE).write_text(
        r"re:old[0-9]{2}\.example\.net" "\n", encoding="utf-8")
    (tmp_path / "host.md").write_text("talks to old42.example.net\n", encoding="utf-8")
    assert H.check_local_references(tmp_path, ["host.md"])


def test_local_reference_has_no_historical_exemption(tmp_path):
    """Unlike check_references: P2-00(b) carves out nothing (see its docstring)."""
    rel = "reports/phase0/REPORT.md"
    (tmp_path / "reports" / "phase0").mkdir(parents=True)
    (tmp_path / rel).write_text("Some-Old-Handle\n", encoding="utf-8")
    (tmp_path / H.LOCAL_PATTERNS_FILE).write_text("Some-Old-Handle\n", encoding="utf-8")
    assert H.check_local_references(tmp_path, [rel])


def test_local_patterns_file_is_never_scanned_as_a_tracked_file(tmp_path):
    """It holds the patterns, so scanning it would always self-match."""
    (tmp_path / H.LOCAL_PATTERNS_FILE).write_text("Some-Old-Handle\n", encoding="utf-8")
    assert not H.check_local_references(tmp_path, [H.LOCAL_PATTERNS_FILE])


def test_invalid_local_pattern_fails_loudly_without_echoing_it(tmp_path):
    (tmp_path / H.LOCAL_PATTERNS_FILE).write_text("re:[unclosed\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as exc:
        H.load_local_patterns(tmp_path)
    assert "#1" in str(exc.value)
    assert "unclosed" not in str(exc.value)


def test_tracking_the_local_patterns_file_is_a_finding(tmp_path):
    hits = H.check_untracked_runtime_files(tmp_path, [H.LOCAL_PATTERNS_FILE])
    assert [h.check for h in hits] == ["must-not-track"]


@requires_git
def test_local_patterns_file_is_gitignored():
    """T2-12: committing it would put the hunted strings into the repository."""
    p = subprocess.run(["git", "check-ignore", "-q", H.LOCAL_PATTERNS_FILE],
                       cwd=REPO_ROOT, check=False)
    assert p.returncode == 0, f"{H.LOCAL_PATTERNS_FILE} must be covered by .gitignore"


@requires_git
def test_local_patterns_file_is_not_tracked():
    assert H.LOCAL_PATTERNS_FILE not in set(H.tracked_files(REPO_ROOT))


@requires_git
def test_tracked_tree_has_no_old_project_names():
    """T2-12, third clause. Honest about the case where nothing was supplied.

    `test_tracked_tree_is_clean` already runs this check as part of `H.run`,
    but with an empty `.hygiene_local` it passes without looking for anything.
    This test names that condition instead of hiding it: a skip says "not
    checked", a pass says "checked and clean".
    """
    patterns = H.load_local_patterns(REPO_ROOT)
    if not patterns:
        pytest.skip(
            f"{H.LOCAL_PATTERNS_FILE} is absent or empty, so the old project's "
            f"names were not scanned for; the owner supplies them (P2-00b)"
        )
    hits = H.check_local_references(REPO_ROOT, H.tracked_files(REPO_ROOT))
    assert not hits, "\n".join(f"{h.location} [{h.detail}]" for h in hits)
