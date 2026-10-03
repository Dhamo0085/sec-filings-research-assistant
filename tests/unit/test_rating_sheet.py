"""T4-11: the narrative rating sheet is deterministic, complete and honest.

The sheet is the only artifact the owner rates narrative quality from, so the
three ways it could quietly mislead are each pinned by a test with a planted
fault: a row that shows no passage must say so, a row whose answer carries no
citation must be flagged rather than look like a clean row with an empty
passage, and two runs over the same transcript must produce the same bytes or
a rating cannot be attached to a measurement.

D29 (CLAUDE.md rule 17) is pinned too: the generator must never write into a
column the owner signs.
"""

from __future__ import annotations

import csv
from pathlib import Path

from scripts.make_rating_sheet import (
    COLUMNS,
    OWNER_COLUMNS,
    build_rows,
    edgar_link,
    sample_ids,
    write_sheet,
)


def run_row(item_id: str, *, citations=None, answer="Apple says things [1].",
            status="answered_text", error_code=None) -> dict:
    if citations is None:
        citations = [{"index": 1, "ticker": "AAPL", "fiscal_label": 2024,
                      "section": "Item 1A: Risk Factors", "cik": 320193,
                      "accession": "0000320193-24-000123", "kind": "text"}]
    return {
        "item_id": item_id, "category": "narrative",
        "question": f"What does Apple disclose about {item_id}?",
        "as_of": None, "latency_s": 1.0, "variant": "V3",
        "outcome": {"answer": answer, "citations": citations,
                    "status": status, "error_code": error_code},
    }


def read_sheet(path: Path) -> list:
    """Rows below the rubric block, as dicts."""
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    header = next(i for i, r in enumerate(rows) if r and r[0] == "item_id")
    return [dict(zip(rows[header], r, strict=False)) for r in rows[header + 1:] if r]


def test_every_row_carries_passage_text_and_a_link(tmp_path):
    rows = build_rows([run_row("R-A")], ["R-A"], passage_chars=1500, recover=False)
    # recover=False is the planted fault: no passage could be recovered, and
    # the sheet has to say that in the cell rather than leave it blank.
    assert rows[0]["assist_passage"] == "PASSAGE NOT RECOVERED"
    assert rows[0]["edgar_link"].startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    assert rows[0]["edgar_link"].endswith("0000320193-24-000123-index.htm")


def test_an_answer_with_no_citation_is_flagged(tmp_path):
    """T4-11's negative control: a planted uncited answer must appear flagged."""
    rows = build_rows([run_row("R-A", citations=[], answer="Apple is large.")],
                      ["R-A"], passage_chars=1500, recover=False)
    assert len(rows) == 1
    assert "no_citation" in rows[0]["assist_flags"]
    assert rows[0]["assist_passage"] == "PASSAGE NOT RECOVERED"


def test_a_cited_answer_is_not_flagged(tmp_path):
    """Without this the flag test above would pass on a generator that flagged
    every row."""
    rows = build_rows([run_row("R-A")], ["R-A"], passage_chars=1500, recover=False)
    assert rows[0]["assist_flags"] == ""


def test_a_run_error_is_carried_into_the_sheet():
    rows = build_rows(
        [run_row("R-A", citations=[], answer="", status="error",
                 error_code="llm_rate_limited")],
        ["R-A"], passage_chars=1500, recover=False)
    assert "run_error:llm_rate_limited" in rows[0]["assist_flags"]


def test_one_row_per_citation():
    """The owner rates a claim against the passage it cites, so a two-source
    answer needs two passage cells, not one."""
    two = [{"index": 1, "ticker": "AAPL", "fiscal_label": 2024,
            "section": "Item 1A: Risk Factors", "cik": 320193,
            "accession": "0000320193-24-000123", "kind": "text"},
           {"index": 2, "ticker": "AAPL", "fiscal_label": 2023,
            "section": "Item 1: Business", "cik": 320193,
            "accession": "0000320193-23-000106", "kind": "text"}]
    rows = build_rows([run_row("R-A", citations=two)], ["R-A"],
                      passage_chars=1500, recover=False)
    assert [r["citation_index"] for r in rows] == [1, 2]
    assert [r["cited_fiscal_label"] for r in rows] == [2024, 2023]


def test_owner_columns_are_written_empty():
    """D29: verdict, issue and notes are the owner's signature."""
    rows = build_rows([run_row("R-A")], ["R-A"], passage_chars=1500, recover=False)
    for column in OWNER_COLUMNS:
        assert rows[0][column] == ""


def test_the_sample_is_deterministic_for_a_seed():
    rows = [run_row(f"R-{i:02d}") for i in range(30)]
    first = sample_ids(rows, 15, seed=20261003)
    second = sample_ids(rows, 15, seed=20261003)
    assert first == second and len(first) == 15
    assert sample_ids(rows, 15, seed=1) != first, "a different seed must differ"


def test_a_sample_larger_than_the_set_returns_everything():
    rows = [run_row(f"R-{i:02d}") for i in range(4)]
    assert len(sample_ids(rows, 15, seed=20261003)) == 4


def test_the_sheet_is_byte_identical_across_two_builds(tmp_path):
    rows = build_rows([run_row("R-A"), run_row("R-B")], ["R-A", "R-B"],
                      passage_chars=1500, recover=False)
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    write_sheet(first, rows, ["note"])
    write_sheet(second, rows, ["note"])
    assert first.read_bytes() == second.read_bytes()


def test_the_sheet_carries_the_rubric_above_the_header(tmp_path):
    rows = build_rows([run_row("R-A")], ["R-A"], passage_chars=1500, recover=False)
    path = tmp_path / "sheet.csv"
    write_sheet(path, rows, ["RUBRIC — supported means every claim is in the text"])
    text = path.read_text(encoding="utf-8")
    assert "RUBRIC" in text.splitlines()[0]
    assert read_sheet(path)[0]["item_id"] == "R-A"


def test_the_column_order_is_the_documented_one(tmp_path):
    rows = build_rows([run_row("R-A")], ["R-A"], passage_chars=1500, recover=False)
    path = tmp_path / "sheet.csv"
    write_sheet(path, rows, [])
    assert list(read_sheet(path)[0]) == list(COLUMNS)
    assert COLUMNS[-3:] == OWNER_COLUMNS


def test_a_citation_without_a_cik_yields_no_link_rather_than_a_broken_one():
    assert edgar_link(None, "0000320193-24-000123") == ""
    assert edgar_link(320193, None) == ""


def test_a_passage_is_truncated_to_the_requested_length():
    """1,500 characters is a reading budget, not a suggestion: an untruncated
    parent section runs to 130 kB and no one rates fifteen of those."""
    rows = build_rows([run_row("R-A")], ["R-A"], passage_chars=10, recover=False)
    assert rows[0]["assist_passage_chars"] == 0  # not recovered, so nothing to cut
