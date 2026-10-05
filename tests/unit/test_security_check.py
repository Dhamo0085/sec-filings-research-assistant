"""The security pass must be able to fail (P5-01, CLAUDE.md rule 15)."""

from __future__ import annotations

from pathlib import Path

from scripts.security_check import (
    ACCEPTED_FILE,
    SECRET_LIKE,
    SECRET_SUFFIXES,
    _accepted_ids,
    check_no_tracked_secrets,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_accepted_file_parses_and_every_entry_carries_a_reason():
    accepted = _accepted_ids()
    assert accepted, "an empty accepted file would make the audit row meaningless"
    for advisory, reason in accepted.items():
        assert advisory.startswith(("PYSEC-", "GHSA-", "CVE-")), advisory
        assert len(reason) > 40, f"{advisory} is accepted without a stated reason"


def test_accepted_entries_say_what_would_end_the_acceptance():
    text = ACCEPTED_FILE.read_text(encoding="utf-8").lower()
    assert "acceptance ends" in text or "would end" in text


def test_comments_and_blank_lines_are_not_read_as_advisories():
    assert not any(a.startswith("#") for a in _accepted_ids())


def test_the_env_file_is_covered_by_the_secret_patterns():
    assert ".env" in SECRET_LIKE
    assert ".pem" in SECRET_SUFFIXES


def test_no_secret_shaped_file_is_tracked_today():
    row = check_no_tracked_secrets()
    assert row.state == "PASS", row.detail
