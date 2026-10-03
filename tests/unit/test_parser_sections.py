"""Section-boundary behaviour and the defects P3-00 measured (spec D23, T3-12).

``scripts/audit_sections.py`` measured v1's parsed sections over the 39-filing
corpus: 238 of 429 (filing, audited section) pairs are usable, 122 sections are
missing outright, 24 are heading-only and 45 are oversized. The two ``xfail``
tests below reproduce the two defect classes behind most of that, using a
synthetic 10-K small enough to live in this file.

Phase 3 left the parser unchanged (D3-00): three local fixes were implemented
and ablated over 13 filings, moved the audit by +2 usable pairs out of 143, and
were rejected as inside the noise of a change that forces a re-index. The two
defect classes were pinned here as strict ``xfail`` tests, so that fixing the
parser would fail the suite and force the record to be revisited.

**P4-00 (D25) rewrote boundary selection and both now pass**, so they are
ordinary tests again. Over the 40-filing corpus the audit went from 245 to 298
usable pairs, and Item 1 and Item 1A are usable in 40 of 40 (from 33 and 20).
The measurement and the four remaining regressions are in
``reports/phase4/REPORT.md``; the decision is D4-00 in ``docs/DECISIONS.md``.

The rest of this file is live regression guards: behaviour that held before the
rewrite and must keep holding.
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


# ── defect 1 (fixed in P4-00): a real heading inside the old 15 % TOC zone ──

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


# ── defect 2 (fixed in P4-00): a heading destroyed by table decomposition ───

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


# ── T4-06: the global assignment itself ──────────────────────────────────────

def _greedy_monotonic(candidates):
    """The selection rule P4-00 replaced, for the negative control below.

    Keep the first occurrence of each section id, then walk them in line order
    and drop anything whose Item priority is not higher than the highest seen
    so far. This is what ``_select_and_validate`` did before the rewrite.
    """
    from ingestion.parser import _SECTION_PRIORITY

    first: dict = {}
    for c in sorted(candidates, key=lambda c: c.line):
        first.setdefault(c.section_id, c)

    kept, watermark = [], -1
    for c in sorted(first.values(), key=lambda c: c.line):
        priority = _SECTION_PRIORITY.get(c.section_id, 500)
        if priority > watermark:
            kept.append(c)
            watermark = priority
    return kept


def _candidates_for(body: str, tmp_path: Path):
    """The scored candidates parse_filing would work from, for one body."""
    import re as _re

    from bs4 import BeautifulSoup

    from ingestion.parser import (
        _annotate_fs_header_tables,
        _collect_candidates,
        _extract_tables,
        _strip_ixbrl,
    )

    path = _write_filing(tmp_path, body)
    soup = BeautifulSoup(_strip_ixbrl(path.read_text(encoding="utf-8")), "lxml")
    _annotate_fs_header_tables(soup)
    _extract_tables(soup, start_idx=0)
    text = _re.sub(r"\n{4,}", "\n\n\n", soup.get_text(separator="\n"))
    lines = text.split("\n")
    return lines, _collect_candidates(lines, int(len(lines) * 0.97))


# A filing whose first "Items 10-14" match arrives before the real Item 7, which
# is what TROW FY2024 does: the greedy rule raised the watermark to 100 at that
# line and then discarded Items 5, 7, 7A and 8. IVZ FY2024 lost Items 3, 4, 5, 7
# and 7A the same way to an early Item 8.
_EARLY_HIGH_PRIORITY_BODY_PARTS = (
    "<p>Part I</p>",
    "<p>Item 1. Business</p>",
    "<p>Item 1A. Risk Factors</p>",
    "<p>Item 10. Directors, Executive Officers and Corporate Governance</p>",
    "<p>Item 7. Management's Discussion and Analysis</p>",
    "<p>Item 8. Financial Statements and Supplementary Data</p>",
)


def _early_high_priority_body():
    """The planted shape, with real prose behind every heading."""
    part_i, item_1, item_1a, item_10, item_7, item_8 = _EARLY_HIGH_PRIORITY_BODY_PARTS
    return _html(
        part_i,
        item_1, _filler(60),
        item_1a, _filler(60),
        item_10, _filler(60),
        item_7, _filler(300),
        item_8, _filler(60),
    )


def test_global_assignment_keeps_sections_the_greedy_rule_dropped(tmp_path):
    """Negative control: the rule P4-00 replaced loses a section here, the new one does not.

    CLAUDE.md rule 15 — a check that has never failed is not evidence. This
    plants the exact shape that cost TROW and IVZ four and five sections, runs
    BOTH selection rules over the same scored candidates, and asserts they
    disagree in the direction the rewrite claims.
    """
    from ingestion.parser import _assign_sections

    _lines, candidates = _candidates_for(_early_high_priority_body(), tmp_path)

    greedy = {c.section_id for c in _greedy_monotonic(candidates)}
    assigned = {c.section_id for c in _assign_sections(candidates)}

    assert not {"item_7_mda", "item_8_financials"} & greedy, (
        "the negative control no longer reproduces the defect it controls for: "
        f"the greedy rule kept {sorted(greedy)}"
    )
    assert {
        "item_1_business", "item_1a_risk_factors", "item_7_mda", "item_8_financials",
    } <= assigned
    assert len(assigned) > len(greedy)


def test_the_same_candidates_always_assign_the_same_way(tmp_path):
    """Determinism: the assignment is a function of its input, not of iteration order."""
    from ingestion.parser import _assign_sections

    _lines, candidates = _candidates_for(_early_high_priority_body(), tmp_path)

    expected = _assign_sections(candidates)
    for shuffled in (list(reversed(candidates)), sorted(candidates, key=lambda c: c.title)):
        assert _assign_sections(shuffled) == expected


def test_a_section_with_no_content_does_not_outrank_a_real_one(tmp_path):
    """The bank cross-reference index: Item headings whose body is one pointer line.

    JPM, WFC, GS and BAC satisfy Items 3, 5, 7, 7A and 8 by pointing at their
    annual report, printing a run of well-formed Item headings each followed by
    a single "Refer to pages ..." line. Those entries are anchored, explicit and
    heading-shaped, so on score alone a run of them outranks the real sections
    elsewhere in the document. _assign_sections weights them at 1 so they
    cannot.
    """
    from ingestion.parser import _assign_sections

    body = _html(
        "<p>Part I</p>", "<p>Item 1. Business</p>", _filler(200),
        # The pointer index: two well-formed headings, no disclosure behind them.
        "<p>Item 7. Management's Discussion and Analysis</p>",
        "<p>Refer to pages 44-115 of the 2024 Annual Report.</p>",
        "<p>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</p>",
        "<p>Refer to pages 141-149 of the 2024 Annual Report.</p>",
        # The disclosure itself, under the filer's own heading.
        "<p>Management's Discussion and Analysis</p>", _filler(400),
    )
    _lines, candidates = _candidates_for(body, tmp_path)

    stubs = [c for c in candidates if c.section_id == "item_7_mda" and not c.has_prose]
    assert stubs, "the planted pointer entries are not being scored as content-free"

    chosen = {c.section_id: c for c in _assign_sections(candidates)}
    assert chosen["item_7_mda"].has_prose, (
        "the content-free pointer entry was chosen as Item 7 over the real heading"
    )
