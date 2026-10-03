"""Section-boundary behaviour and the defects P3-00 measured (spec D23, T3-12).

``scripts/audit_sections.py`` measured v1's parsed sections over the 39-filing
corpus: 238 of 429 (filing, audited section) pairs are usable, 122 sections are
missing outright, 24 are heading-only and 45 are oversized. The two ``xfail``
tests below reproduce the two defect classes behind most of that, using a
synthetic 10-K small enough to live in this file.

**The parser is deliberately unchanged in Phase 3** (decision D3-00). Three
targeted fixes were implemented and ablated over 13 filings; they moved the
audit by +2 usable pairs out of 143 — inside the noise of a change that would
require re-parsing, re-chunking and re-indexing the whole corpus. The numbers
are in ``reports/phase3/REPORT.md`` section 4 and the decision is in
``docs/DECISIONS.md``. These tests therefore record the defects rather than
assert they are fixed: ``strict=True`` means that if someone does fix the
parser, the unexpected pass fails the suite and forces the record to be
updated, so a silent divergence between the code and D3-00 is not possible.

The non-xfail tests are live regression guards for behaviour that does hold
today and that any future boundary-selection rewrite must preserve.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from ingestion.parser import parse_filing

pytestmark = pytest.mark.unit

# A paragraph long enough to read as prose rather than a heading or a table row.
PROSE = (
    "The following discussion sets forth the material risk factors that could "
    "affect the Firm's financial condition and results of operations, and "
    "should be read together with the consolidated financial statements and "
    "the related notes appearing elsewhere in this Annual Report on Form 10-K. "
)


def _filler(n: int, text: str = PROSE) -> List[str]:
    return [f"<p>{text}</p>" for _ in range(n)]


def _html(*parts) -> str:
    """Join heading strings and filler blocks into one document body."""
    out: List[str] = []
    for part in parts:
        out.extend([part] if isinstance(part, str) else part)
    return "".join(out)


def _write_filing(tmp_path: Path, body: str, name: str = "primary.html") -> Path:
    path = tmp_path / name
    path.write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    return path


def _parse(path: Path, ticker: str = "TEST"):
    return parse_filing(
        file_path=path,
        company=f"{ticker} Inc.",
        ticker=ticker,
        fiscal_year=2024,
        accession_number="0000000000-24-000001",
        filing_date="2024-02-15",
    )


def _section_ids(doc) -> set:
    # parse_filing suffixes a repeated id ("__2"); the base id is what
    # retrieval/retriever.py scrolls by, so compare on that.
    return {s.section_id.split("__")[0] for s in doc.sections}


def _chars(doc, section_id: str) -> int:
    for s in doc.sections:
        if s.section_id.split("__")[0] == section_id:
            return len(s.full_text())
    return 0


# ── defect 1 (open): the 15% TOC skip zone swallows a real early heading ─────

@pytest.mark.xfail(
    strict=True,
    reason="D3-00: open defect. parse_filing skips the first 15% of a "
           "document's lines to avoid table-of-contents matches. The filers "
           "whose primary document IS the whole annual report start Part I "
           "well inside that zone, so the real heading is never a candidate: "
           "JPM FY2024's 'Item 1A. Risk Factors.' is at line 229 of 6,897 "
           "(3.3%), GS FY2024's at 490 of 5,370 (9.1%), STT FY2024's at 405 "
           "of 3,715 (10.9%). Item 1A is missing from 17 of 39 parsed "
           "filings and its text is absorbed by the preceding section "
           "(GS FY2024's Item 1 is 295,735 characters; JPM FY2024's "
           "fs_income_stmt is 1,525,489). Capping the zone in absolute lines "
           "recovers GS's 74,825-character Risk Factors section but costs "
           "item_1c and item_3_legal on STT, netting out inside the noise "
           "(reports/phase3/REPORT.md section 4).",
)
def test_real_item_1a_heading_before_the_toc_zone_is_detected(tmp_path):
    """The real Item 1A heading sits at ~10% of the lines, as it does for JPM/GS/STT."""
    body = _html(
        # A short cover page and a genuine table-of-contents list, which is
        # what the skip zone exists to ignore.
        "<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p>",
        "<p>Item 1. Business 3</p>",
        "<p>Item 1A. Risk Factors 10</p>",
        "<p>Item 7. Management's Discussion and Analysis 60</p>",
        "<p>Part I</p>",
        "<p>Item 1. Business</p>",
        _filler(100),
        "<p>Item 1A. Risk Factors</p>",
        _filler(400),
        "<p>Item 7. Management's Discussion and Analysis of Financial "
        "Condition and Results of Operations</p>",
        _filler(390),
    )
    # Every section here spans fewer than 1,000 lines on purpose: that is the
    # threshold above which _select_and_validate calls the LLM fs-heading
    # recovery pass, and an offline unit test must not reach for the network
    # (or, with a key present, spend free-tier quota).
    doc = _parse(_write_filing(tmp_path, body))

    assert "item_1a_risk_factors" in _section_ids(doc), (
        "the real Item 1A heading at 10% of the document was not detected; "
        f"sections found: {sorted(_section_ids(doc))}"
    )
    assert _chars(doc, "item_1a_risk_factors") > 10_000


# ── defect 2 (open): a heading destroyed by table decomposition ───────────────

@pytest.mark.xfail(
    strict=True,
    reason="D3-00: open defect. Amazon renders each item heading as a table "
           "row with 'Item 1A.' in one <td> and 'Risk Factors' in the next. "
           "_extract_tables decomposes the table, so unlike every other "
           "defect class the heading text never reaches the plain-text stream "
           "— the audit found zero occurrences of it anywhere in AMZN's "
           "parsed output, so no later line scan can recover it. "
           "_annotate_fs_header_tables already joins a row's cell texts; "
           "_ITEM_RECOVERY_PATTERNS lists only 'Item 1. Business'. Adding the "
           "other item headings to that list recovers AMZN's Item 1C but "
           "costs AMZN's Item 1 (the new sentinel outranks the plain-text "
           "heading), again netting out inside the noise.",
)
def test_item_heading_split_across_table_cells_is_recovered(tmp_path):
    """AMZN class: <td>Item 1A.</td><td>Risk Factors</td> in one row."""
    def heading_table(num: str, title: str) -> str:
        return (
            "<table><tr>"
            f"<td><span>Item {num}</span></td>"
            f"<td><span>{title}</span></td>"
            "</tr></table>"
        )

    body = _html(
        "<p>Part I</p>", heading_table("1.", "Business"),
        _filler(300),
        heading_table("1A.", "Risk Factors"),
        _filler(600),
    )
    doc = _parse(_write_filing(tmp_path, body))

    assert "item_1a_risk_factors" in _section_ids(doc), (
        "an Item 1A heading rendered as two adjacent table cells was lost; "
        f"sections found: {sorted(_section_ids(doc))}"
    )
    assert _chars(doc, "item_1a_risk_factors") > 10_000


# ── live regression guards ────────────────────────────────────────────────────

def test_cross_reference_to_item_1a_does_not_become_its_section(tmp_path):
    """"Refer to Part I, Item 1A: Risk Factors on pages 10-37" is real JPM text.

    The Item-N patterns are applied with ``re.search``, so this line does match
    ``item_1a_risk_factors``; what keeps it out today is the monotonic
    Item-priority filter, since MD&A (priority 70) was already selected before
    it. That is incidental protection, not a rule about headings, so it is
    pinned here: any boundary-selection rewrite must still refuse this line.
    """
    body = _html(
        "<p>Part I</p>", "<p>Item 1. Business</p>",
        _filler(400),
        "<p>Item 7. Management's Discussion and Analysis</p>",
        _filler(200),
        "<p>Refer to Part I, Item 1A: Risk Factors on pages 10-37 "
        "of this 2024 Form 10-K for further information.</p>",
        _filler(400),
    )
    doc = _parse(_write_filing(tmp_path, body))

    assert "item_1a_risk_factors" not in _section_ids(doc), (
        "a cross-reference inside MD&A was selected as the Item 1A heading"
    )


def test_mid_sentence_item_mention_does_not_become_a_section(tmp_path):
    """Amazon's own Item 1A prose says "elsewhere in this Item 1A"."""
    body = _html(
        "<p>Part I</p>", "<p>Item 1. Business</p>",
        _filler(300),
        "<p>In addition to risks described elsewhere in this Item 1A "
        "relating to our business, the following are additional risks.</p>",
        _filler(300),
    )
    doc = _parse(_write_filing(tmp_path, body))
    assert "item_1a_risk_factors" not in _section_ids(doc)


@pytest.mark.parametrize(
    "heading",
    [
        "ITEM 1A. RISK FACTORS",
        "Item 1A.\u00a0\u00a0\u00a0\u00a0Risk Factors",
        "Item 1A — Risk Factors",
        "PART I - ITEM 1A. RISK FACTORS",
        "Part II, Item 1A. Risk Factors",
    ],
)
def test_legitimate_heading_spellings_are_detected(tmp_path, heading):
    """The heading spellings the corpus actually contains, past the TOC zone.

    A future fix that anchors the Item-N patterns at the start of the line must
    keep the "Part N" prefixed spellings, which filers do use.
    """
    body = _html(
        "<p>Part I</p>", "<p>Item 1. Business</p>",
        _filler(300),
        f"<p>{heading}</p>",
        _filler(600),
    )
    doc = _parse(_write_filing(tmp_path, body))
    assert "item_1a_risk_factors" in _section_ids(doc), f"{heading!r} no longer matches"
