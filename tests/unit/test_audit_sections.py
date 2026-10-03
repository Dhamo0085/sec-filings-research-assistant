"""The section audit's own negative controls, run by `make test` (T3-12, P3-00a).

``scripts/audit_sections.py --self-test`` plants the defects and asserts each
is caught. That was only ever invoked by hand, which means the check that is
supposed to prove the audit can fail was not itself enforced by the suite —
exactly the gap CLAUDE.md rule 15 is about. These tests call the same code
in-process so a regression fails `make test`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_sections import (
    AUDITED_SECTIONS,
    VERDICT_EMPTY,
    VERDICT_MISSING,
    VERDICT_OK,
    VERDICT_TOO_LARGE,
    VERDICT_TOO_SMALL,
    audit_corpus,
    load_corpus,
    self_test,
    summarize,
    write_outputs,
)
from scripts.audit_sections import _fixture_doc as fixture_doc

pytestmark = pytest.mark.unit


def test_the_self_test_passes(tmp_path):
    """T3-12: planted empty, tiny and oversized sections are each flagged, a
    clean fixture passes, and two runs give identical output."""
    assert self_test(tmp_root=tmp_path) == 0


def write_doc(parsed: Path, ticker: str, sections) -> None:
    parsed.mkdir(parents=True, exist_ok=True)
    (parsed / f"{ticker}_2024.json").write_text(
        json.dumps(fixture_doc(ticker, 2024, sections)), encoding="utf-8")


def audit(parsed: Path):
    return {(r.filing, r.section_id): r for r in audit_corpus(load_corpus(parsed))}


def test_each_verdict_is_reachable(tmp_path):
    parsed = tmp_path / "parsed"
    spec = {s.section_id: s for s in AUDITED_SECTIONS}
    sections = []
    for s in AUDITED_SECTIONS:
        if s.section_id == "item_3_legal":
            continue                                   # missing
        if s.section_id == "item_1a_risk_factors":
            sections.append((s.section_id, "t", ""))    # empty
        elif s.section_id == "item_1c_cyber":
            sections.append((s.section_id, "t", "x" * 10))   # too small
        elif s.section_id == "fs_income_stmt":
            sections.append((s.section_id, "t", "y" * (s.max_chars + 1)))  # too large
        else:
            sections.append((s.section_id, "t", "w" * (s.min_chars + 100)))
    write_doc(parsed, "T", sections)

    rows = audit(parsed)
    assert rows[("T_2024", "item_3_legal")].verdict == VERDICT_MISSING
    assert rows[("T_2024", "item_1a_risk_factors")].verdict == VERDICT_EMPTY
    assert rows[("T_2024", "item_1c_cyber")].verdict == VERDICT_TOO_SMALL
    assert rows[("T_2024", "fs_income_stmt")].verdict == VERDICT_TOO_LARGE
    assert rows[("T_2024", "fs_notes")].verdict == VERDICT_OK
    assert spec["fs_income_stmt"].kind == "statement"


def test_a_section_exactly_on_a_threshold_is_ok(tmp_path):
    """The bands are inclusive, so a borderline section is not a finding."""
    parsed = tmp_path / "parsed"
    spec = next(s for s in AUDITED_SECTIONS if s.section_id == "fs_notes")
    write_doc(parsed, "EDGE", [("fs_notes", "t", "w" * spec.min_chars)])
    assert audit(parsed)[("EDGE_2024", "fs_notes")].verdict == VERDICT_OK

    write_doc(parsed, "EDGE", [("fs_notes", "t", "w" * spec.max_chars)])
    assert audit(parsed)[("EDGE_2024", "fs_notes")].verdict == VERDICT_OK


def test_boundary_evidence_only_counts_a_heading_on_its_own_line(tmp_path):
    """A cross-reference inside a sentence is not a swallowed heading, and
    counting it would manufacture evidence."""
    parsed = tmp_path / "parsed"
    body = "w " * 2000
    inline = body + " see the Consolidated Balance Sheets on page 62 for detail " + body
    write_doc(parsed, "INLINE", [("item_1_business", "t", inline)])
    assert audit(parsed)[("INLINE_2024", "item_1_business")].absorbs == ""

    standalone = body + "\n\nConsolidated Balance Sheets\n\n" + body
    write_doc(parsed, "STANDALONE", [("item_1_business", "t", standalone)])
    assert "fs_balance_sheet" in audit(parsed)[("STANDALONE_2024", "item_1_business")].absorbs


def test_a_section_does_not_report_absorbing_one_that_is_present(tmp_path):
    parsed = tmp_path / "parsed"
    body = "w " * 2000
    write_doc(parsed, "BOTH", [
        ("item_1_business", "t", body + "\n\nConsolidated Balance Sheets\n\n" + body),
        ("fs_balance_sheet", "t", "w " * 2000),
    ])
    assert audit(parsed)[("BOTH_2024", "item_1_business")].absorbs == ""


def test_the_summary_counts_every_pair_once(tmp_path):
    parsed = tmp_path / "parsed"
    write_doc(parsed, "A", [("fs_notes", "t", "w" * 20000)])
    write_doc(parsed, "B", [("fs_notes", "t", "w" * 20000)])

    rows = audit_corpus(load_corpus(parsed))
    summary = summarize(rows)
    assert summary["filings"] == 2
    assert summary["pairs"] == 2 * len(AUDITED_SECTIONS)
    assert sum(summary["per_verdict"].values()) == summary["pairs"]
    assert summary["thresholds"]["fs_notes"]["kind"] == "notes"


def test_the_written_artifacts_are_deterministic(tmp_path):
    parsed = tmp_path / "parsed"
    write_doc(parsed, "A", [("fs_notes", "t", "w" * 20000)])
    rows = audit_corpus(load_corpus(parsed))
    summary = summarize(rows)

    first = tmp_path / "one"
    second = tmp_path / "two"
    write_outputs(rows, summary, first)
    write_outputs(audit_corpus(load_corpus(parsed)), summary, second)

    for name in ("section_audit.json", "section_audit.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
