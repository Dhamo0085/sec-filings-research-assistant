"""The assisted verification sheet (P4-02b, T4-12).

Two kinds of property are tested here, and they fail for different reasons.

The first is D29's rule: the script must never write ``verdict`` or
``owner_note``, never touch an existing column, and never overwrite the sheet
the owner signs. A breach here does not produce a wrong number — it produces a
sheet that *looks* signed when nobody signed it, which is worse.

The second is whether the located line is really the line: a value planted in a
fixture filing is found with its row label, column header and units note, and a
value that is not in the fixture is reported ``not_found`` rather than matched
to something nearby. The ``not_found`` case is the negative control — every row
of the real sheet currently matches, so without a planted miss the failure path
would never be exercised.

No network, no derived stores: the fixtures are small HTML documents written in
the test itself.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from scripts.make_gold_verification_assist import (
    ASSIST_COLUMNS,
    OWNER_ONLY_COLUMNS,
    AssistError,
    assert_unchanged,
    candidate_forms,
    choose_spotcheck,
    main,
    normalize_numeral,
    read_sheet,
    search_document,
    statement_score,
    units_header,
    write_sheet,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

# A filing in miniature: a units note, a dated column header, an MD&A table
# that repeats the figure, and the real statement below it.
FIXTURE_HTML = """
<html><body>
<p>Item 7. Management's Discussion and Analysis</p>
<p>Table 1 Selected Income Statement Data (Dollars in millions)</p>
<table>
  <tr><td></td><td>2024</td><td>2023</td></tr>
  <tr><td>Total revenue</td><td>12,345</td><td>11,000</td></tr>
</table>
<p>Item 8. Financial Statements</p>
<p>Consolidated Statement of Income</p>
<table>
  <tr><td></td><td colspan="2">December 31, 2024</td><td colspan="2">December 31, 2023</td></tr>
  <tr><td>Total revenue</td><td>$</td><td>12,345</td><td>$</td><td>11,000</td></tr>
  <tr><td>Purchases of property and equipment</td><td></td><td>(2,500)</td><td></td><td>(2,100)</td></tr>
</table>
</body></html>
"""

INCOME = "Consolidated Statements of Operations / of Income"


# ── numerals ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("12,345", "12345"),
        ("$ 12,345", "12345"),
        ("( 2,500 )", "-2500"),
        ("(2,500)", "-2500"),
        ("7,093.60", "7093.6"),
        ("−986.5", "-986.5"),
        ("Total revenue", None),
        ("", None),
        ("—", None),
    ],
)
def test_normalize_numeral(text, expected):
    assert normalize_numeral(text) == expected


def test_candidate_forms_prefer_the_declared_scale():
    forms = candidate_forms(Decimal("17859000000"), "6")
    assert forms["17859"] == ("found_exact", 6)
    assert forms["17859000000"][0] == "found_scaled"
    assert forms["17859000"][0] == "found_scaled"


def test_units_header_prefers_the_filers_own_parenthesis():
    """Without the bracket the match has no end and drags in the next row."""
    table_text = "(In millions, except per share amounts) 2024 2023 Revenue $ 12,345"
    assert units_header(table_text, "") == "In millions, except per share amounts"


def test_statement_score_sees_through_letter_spacing():
    """Microsoft's headings reach the text layer as 'INC OME STATE MENTS'."""
    assert statement_score("ITEM 8. INC OME STATE MENTS", INCOME) == 3
    assert statement_score("ITEM 8. INCOME STATEMENTS", INCOME) == 3
    assert statement_score("a discussion of our segments", INCOME) == 0


# ── locating a value ──────────────────────────────────────────────────────


def test_a_planted_value_is_found_with_its_label_header_and_units():
    hit = search_document(
        FIXTURE_HTML, Decimal("12345000000"), "6",
        statement_to_check=INCOME, metric_label="Total net revenue",
    )
    assert hit is not None
    assert hit.kind == "found_exact"
    assert hit.printed == "12,345"
    assert hit.label == "Total revenue"
    assert hit.header == "December 31, 2024"
    assert hit.units == "Dollars in millions"
    assert "11,000" in hit.line


def test_the_statement_beats_the_md_and_a_table_that_repeats_the_figure():
    """Negative control: the MD&A table comes first in the document.

    Both tables carry 12,345 under a 2024 heading. Only the column header
    tells them apart, so a search without statement scoring returns the MD&A
    row — which is a real printed line, but not the one `statement_to_check`
    sends the owner to.
    """
    hit = search_document(
        FIXTURE_HTML, Decimal("12345000000"), "6",
        statement_to_check=INCOME, metric_label="Total net revenue",
    )
    assert hit.header == "December 31, 2024", "matched the MD&A table, not the statement"

    unguided = search_document(FIXTURE_HTML, Decimal("12345000000"), "6")
    assert unguided.header == "2024", "the fixture no longer distinguishes the two tables"


def test_a_value_printed_in_parentheses_is_found_by_magnitude():
    """Capital expenditure is held positive and printed as an outflow."""
    hit = search_document(FIXTURE_HTML, Decimal("2500000000"), "6")
    assert hit is not None
    assert hit.label == "Purchases of property and equipment"
    assert hit.printed == "(2,500)", "the sign must survive into the sheet verbatim"


def test_a_value_at_another_scale_is_found_scaled():
    hit = search_document(FIXTURE_HTML, Decimal("12345000000"), "3")
    assert hit is not None
    assert hit.kind == "found_scaled"


def test_a_value_absent_from_the_filing_is_not_found():
    """The negative control: nothing nearby is offered as a match."""
    assert search_document(FIXTURE_HTML, Decimal("99999000000"), "6") is None


def test_the_search_is_deterministic():
    first = search_document(FIXTURE_HTML, Decimal("12345000000"), "6",
                            statement_to_check=INCOME)
    second = search_document(FIXTURE_HTML, Decimal("12345000000"), "6",
                             statement_to_check=INCOME)
    assert (first.line, first.label, first.header) == (second.line, second.label,
                                                       second.header)


# ── D29: the owner's columns ──────────────────────────────────────────────


def _sheet_rows():
    return [
        {"gold_id": "A", "verdict": "OK", "owner_note": "checked", "priority": "core"},
        {"gold_id": "B", "verdict": "", "owner_note": "", "priority": "extra"},
    ]


def test_owner_only_columns_are_never_assist_columns():
    assert not set(OWNER_ONLY_COLUMNS) & set(ASSIST_COLUMNS)


def test_assert_unchanged_passes_when_only_assist_columns_were_added():
    original = _sheet_rows()
    rows = [dict(r, assist_match="found_exact") for r in original]
    assert_unchanged(rows, original, ["gold_id", "verdict", "owner_note", "priority"])


def test_assert_unchanged_catches_a_filled_in_verdict():
    """Negative control for D29: the guard must fire, not just exist."""
    original = _sheet_rows()
    rows = [dict(r) for r in original]
    rows[1]["verdict"] = "OK"
    with pytest.raises(AssistError, match="verdict"):
        assert_unchanged(rows, original, ["gold_id", "verdict", "owner_note", "priority"])


def test_assert_unchanged_catches_a_dropped_row():
    original = _sheet_rows()
    with pytest.raises(AssistError, match="row set"):
        assert_unchanged(original[:1], original, ["gold_id"])


def test_the_script_refuses_to_write_over_its_input(tmp_path, capsys):
    sheet = tmp_path / "gold_verification.csv"
    sheet.write_text("gold_id,verdict\nA,\n", encoding="utf-8")
    assert main(["--in", str(sheet), "--out", str(sheet)]) == 2
    assert "D29" in capsys.readouterr().err


# ── the written sheet ─────────────────────────────────────────────────────


def test_write_sheet_appends_assist_columns_after_the_originals(tmp_path):
    original = _sheet_rows()
    fieldnames = ["gold_id", "verdict", "owner_note", "priority"]
    rows = [dict(r, **dict.fromkeys(ASSIST_COLUMNS, "")) for r in original]
    rows[0]["assist_match"] = "found_exact"
    out = tmp_path / "assisted.csv"
    write_sheet(rows, out, fieldnames)

    written, written_fields = read_sheet(out)
    assert written_fields[: len(fieldnames)] == fieldnames
    assert written_fields[len(fieldnames):] == list(ASSIST_COLUMNS)
    assert written[0]["verdict"] == "OK"
    assert written[0]["assist_match"] == "found_exact"


def test_the_spotcheck_sample_is_seeded_and_prefers_located_rows():
    rows = [{"gold_id": f"G{i}", "assist_match": "found_exact"} for i in range(20)]
    rows += [{"gold_id": "MISS", "assist_match": "not_found"}]

    first = choose_spotcheck(rows, seed=20261003)
    assert first == choose_spotcheck(rows, seed=20261003)
    assert first != choose_spotcheck(rows, seed=1)
    assert len(first) == 5
    assert "MISS" not in first


def test_the_spotcheck_sample_is_topped_up_when_little_was_located():
    rows = [{"gold_id": "FOUND", "assist_match": "found_exact"}]
    rows += [{"gold_id": f"M{i}", "assist_match": "not_found"} for i in range(9)]
    chosen = choose_spotcheck(rows, seed=7)
    assert len(chosen) == 5
    assert "FOUND" in chosen


# ── the committed sheet ───────────────────────────────────────────────────

ASSISTED = REPO_ROOT / "reports" / "phase4" / "gold_verification_assisted.csv"
SOURCE = REPO_ROOT / "reports" / "phase4" / "gold_verification.csv"


@pytest.mark.skipif(not ASSISTED.is_file(), reason="assisted sheet not generated yet")
def test_the_committed_assisted_sheet_preserves_every_original_value():
    source, source_fields = read_sheet(SOURCE)
    assisted, assisted_fields = read_sheet(ASSISTED)
    assert assisted_fields[: len(source_fields)] == source_fields
    assert_unchanged(assisted, source, source_fields)


@pytest.mark.skipif(not ASSISTED.is_file(), reason="assisted sheet not generated yet")
def test_the_committed_assisted_sheet_leaves_the_owners_columns_empty():
    assisted, _ = read_sheet(ASSISTED)
    for row in assisted:
        for column in OWNER_ONLY_COLUMNS:
            assert row.get(column, "") == "", f"{row['gold_id']} has a written {column}"
        assert row["verified_by"] != "owner"


@pytest.mark.skipif(not ASSISTED.is_file(), reason="assisted sheet not generated yet")
def test_the_committed_assisted_sheet_sorts_not_found_first_and_is_usable():
    assisted, _ = read_sheet(ASSISTED)
    seen_located = False
    for row in assisted:
        if row["assist_match"] == "not_found":
            assert not seen_located, "a not_found row sorts after a located row"
        else:
            seen_located = True
            assert row["assist_printed_line"], f"{row['gold_id']} has no located line"
            assert row["assist_link"].startswith("https://www.sec.gov/")
    spotchecks = [r for r in assisted if r["assist_spotcheck"]]
    assert len(spotchecks) >= 5
    assert all("seed" in r["assist_spotcheck"] for r in spotchecks), "the seed is not recorded"


def _csv_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.skipif(not SOURCE.is_file(), reason="source sheet missing")
def test_reading_the_source_sheet_does_not_rewrite_it(tmp_path):
    before = _csv_text(SOURCE)
    read_sheet(SOURCE)
    assert _csv_text(SOURCE) == before


def test_csv_round_trip_is_byte_stable(tmp_path):
    """A row read and written unchanged comes back byte-identical.

    This is what lets the committed-sheet test above compare values rather
    than bytes: quoting, line endings and column order are stable, so the only
    way a value can differ is if something wrote it.
    """
    source = tmp_path / "in.csv"
    source.write_text(
        'gold_id,question,verdict\nA,"What was Apple\'s revenue, in 2024?",\n',
        encoding="utf-8",
    )
    rows, fields = read_sheet(source)
    out = tmp_path / "out.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    assert _csv_text(out) == _csv_text(source)
