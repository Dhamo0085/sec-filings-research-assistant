"""T4-06: the before/after comparison is itself checked (scripts/compare_section_audits.py).

The P4-00 numbers in reports/phase4/REPORT.md and in D4-00 are produced by this
script, so a bug in it would misreport the result of the change it exists to
measure. The script carries its own negative control; these tests run it, and
pin the two behaviours the report depends on: a regression is never summarised
away, and pairs missing from one side are excluded rather than counted as
changes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "compare_section_audits.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=REPO_ROOT, check=False,
    )


def test_self_test_passes():
    """The script's own negative control, run the way CI would."""
    result = _run("--self-test")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "self-test passed" in result.stdout


def _audit(path: Path, rows) -> Path:
    path.write_text(json.dumps({"rows": [
        {"filing": f, "section_id": s, "verdict": v, "chars": c} for f, s, v, c in rows
    ]}), encoding="utf-8")
    return path


def test_a_regression_is_listed_and_sets_a_failing_exit_code(tmp_path):
    """A lost section must be named and must make the command fail.

    Net totals can hide a change that gains as much as it loses, and P4-00's
    acceptance criterion is per filing ("no filing loses a section that was ok
    before, except cases listed and explained one by one"). So the exit code
    tracks losses, not the total.
    """
    before = _audit(tmp_path / "before.json", [
        ("X_2024", "item_1_business", "missing", 0),
        ("X_2024", "item_7_mda", "ok", 40_000),
    ])
    after = _audit(tmp_path / "after.json", [
        ("X_2024", "item_1_business", "ok", 40_000),
        ("X_2024", "item_7_mda", "too_large", 900_000),
    ])

    result = _run(str(before), str(after))
    assert result.returncode == 1, "a lost section did not fail the command"
    assert "usable: 1 -> 1" in result.stdout, "the net-zero total is reported as such"
    assert "X_2024" in result.stdout and "item_7_mda" in result.stdout
    assert "ok -> too_large" in result.stdout


def test_pairs_only_one_run_has_are_excluded_and_announced(tmp_path):
    """A different corpus must not shrink the denominator silently."""
    before = _audit(tmp_path / "before.json", [("X_2024", "item_1_business", "ok", 40_000)])
    after = _audit(tmp_path / "after.json", [
        ("X_2024", "item_1_business", "ok", 40_000),
        ("Y_2024", "item_1_business", "ok", 40_000),
    ])

    result = _run(str(before), str(after))
    assert result.returncode == 0
    assert "compared 1 pairs over 1 filings" in result.stdout
    assert "1 pairs only in after" in result.stdout
