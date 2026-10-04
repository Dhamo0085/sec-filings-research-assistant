"""T4-15: the blind reading view shows the evidence and nothing else (P4-14b).

The page exists so the owner can rate from question, answer and cited passage.
Two properties make it worth building rather than reading the CSV, and both
are failure-prone enough to pin:

* it must stay **blind** — no automated verdict, score or scorer output, and no
  `verdict`/`issue`/`notes` even once the owner starts filling them in. P4-15
  compares the owner's ratings against the scorer's; a rating anchored on the
  scorer is not independent evidence and the agreement number would be
  meaningless.
* the number highlighting must only ever mark text that is really in the
  passage. A highlight is a claim about the document.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from scripts.make_rating_view import (
    OWNER_COLUMNS,
    build,
    card_html,
    highlight,
    normalise,
    numbers_in,
    read_sheet,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHEET = REPO_ROOT / "reports/phase4/narrative_rating_sheet.csv"
VIEW = REPO_ROOT / "reports/phase4/narrative_rating_view.html"


def row(**over) -> dict:
    base = {
        "item_id": "R-AAPL-SUPPLY-2024", "question": "What does Apple disclose?",
        "as_of": "", "answer": "Revenue was $177,556 million [1].",
        "citation_index": "1", "cited_ticker": "AAPL", "cited_fiscal_label": "2024",
        "cited_section": "Item 1A: Risk Factors",
        "cited_accession": "0000320193-24-000123",
        "edgar_link": "https://www.sec.gov/Archives/edgar/data/320193/x-index.htm",
        "assist_passage": "Total net revenue of 177,556 for the year.",
        "assist_passage_chars": "41", "assist_flags": "",
        "verdict": "", "issue": "", "notes": "",
    }
    base.update(over)
    return base


# ── blindness ────────────────────────────────────────────────────────────────

def test_owner_columns_never_reach_the_page_even_when_filled():
    """The planted case: a half-rated sheet must not anchor the rest."""
    rated = row(verdict="unsupported", issue="hallucination",
                notes="the figure is nowhere in the cited text")
    page = build(["Rubric line"], [rated], source="sheet.csv")
    assert "unsupported" not in page
    assert "hallucination" not in page
    assert "nowhere in the cited text" not in page


def test_the_page_carries_no_scorer_output():
    page = build(["Rubric line"], [row()], source="sheet.csv")
    for token in ("correct_text_only", "wrong_value", "abstained_wrongly",
                  "scale_error", "partial_multi", "correct_abstain"):
        assert token not in page, f"scorer verdict {token!r} leaked into the view"


def test_the_page_has_no_form_fields_and_no_script():
    page = build(["Rubric line"], [row()], source="sheet.csv")
    for tag in ("<input", "<textarea", "<select", "<form", "<script", "<button"):
        assert tag not in page.lower(), f"{tag} present — the page must be read-only"


# ── one card per row, in sheet order ─────────────────────────────────────────

def test_every_row_appears_exactly_once_and_in_order():
    rows = [row(item_id="R-A", citation_index="1"),
            row(item_id="R-A", citation_index="2"),
            row(item_id="R-B", citation_index="1")]
    page = build([], rows, source="s.csv")
    assert [int(n) for n in re.findall(r'id="row-(\d+)"', page)] == [1, 2, 3]
    assert page.count('id="row-1"') == 1
    assert page.index("R-B") > page.index('id="row-2"')


# ── highlighting marks only what is in the passage ───────────────────────────

def test_a_highlight_only_ever_marks_a_substring_of_the_passage():
    passage = "Revenue of 177,556 and expenses of 39,000."
    marked = highlight(passage, ["177,556", "99,999"])
    for found in re.findall(r"<mark>(.*?)</mark>", marked):
        assert found in passage, f"{found!r} is not in the passage"
    assert "177,556" in re.findall(r"<mark>(.*?)</mark>", marked)
    assert "99,999" not in marked, "a number absent from the passage was marked"


def test_a_number_written_differently_is_matched_on_its_digits():
    """The answer may print 177,556 where the filing printed 177556."""
    marked = highlight("Total net revenue 177556 for the period.", ["177,556"])
    assert "<mark>177556</mark>" in marked


def test_nothing_is_marked_when_the_answer_has_no_numbers():
    passage = "Revenue of 177,556 for the year."
    assert "<mark>" not in highlight(passage, [])


def test_the_passage_is_escaped():
    marked = highlight("5 < 6 & <b>bold</b> 177,556", ["177,556"])
    assert "&lt;b&gt;" in marked and "<b>" not in marked
    assert "&amp;" in marked


# ── the "not found in passage" list, and its negative control ────────────────

def test_a_planted_answer_number_absent_from_the_passage_is_listed():
    """T4-15's negative control, in the direction that matters."""
    card = card_html(1, row(answer="Revenue was $404,404 million [1].",
                            assist_passage="Total net revenue of 177,556."),
                     {"R-AAPL-SUPPLY-2024": []})
    assert "not found in passage" in card
    assert "404,404" in card


def test_a_number_present_in_the_passage_is_not_listed():
    """The control for the control: without it, a card that listed every number
    would pass the test above."""
    card = card_html(1, row(answer="Revenue was 177,556 [1].",
                            assist_passage="Total net revenue of 177,556."),
                     {"R-AAPL-SUPPLY-2024": []})
    assert "not found in passage" not in card


def test_a_citation_marker_is_not_treated_as_a_figure():
    """`[1]` is a reference. Listing it as a missing number would put a
    distracting false positive on every single card."""
    card = card_html(1, row(answer="Apple discloses supplier concentration [1] [2].",
                            assist_passage="Supplier concentration is discussed."),
                     {"R-AAPL-SUPPLY-2024": []})
    assert "not found in passage" not in card


def test_a_number_found_in_a_sibling_citation_is_annotated_not_just_flagged():
    """Rows share an answer, so a figure cited from source 2 would otherwise be
    listed as missing on source 1's card and read as an accusation."""
    first = row(citation_index="1", answer="Revenue 177,556 and assets 99,999 [1][2].",
                assist_passage="Revenue of 177,556.")
    second = row(citation_index="2", answer=first["answer"],
                 assist_passage="Total assets of 99,999.")
    card = card_html(1, first, {"R-AAPL-SUPPLY-2024": [first, second]})
    assert "not found in passage" in card
    assert "appears in another cited passage" in card


def test_an_unrecovered_passage_says_so_rather_than_listing_everything():
    card = card_html(1, row(assist_passage="PASSAGE NOT RECOVERED"),
                     {"R-AAPL-SUPPLY-2024": []})
    assert "PASSAGE NOT RECOVERED" in card


# ── determinism and the committed artifact ───────────────────────────────────

def test_the_page_is_byte_identical_across_two_builds():
    rows = [row(), row(citation_index="2")]
    assert build(["R"], rows, source="s.csv") == build(["R"], rows, source="s.csv")


def test_the_committed_view_matches_the_committed_sheet():
    if not (SHEET.is_file() and VIEW.is_file()):
        pytest.skip("the sheet or the view has not been generated")
    rubric, rows = read_sheet(SHEET)
    assert build(rubric, rows, source="reports/phase4/narrative_rating_sheet.csv") \
        == VIEW.read_text(encoding="utf-8"), "the view is stale; regenerate it"


def test_every_committed_row_id_appears_exactly_once_in_the_view():
    if not (SHEET.is_file() and VIEW.is_file()):
        pytest.skip("the sheet or the view has not been generated")
    _rubric, rows = read_sheet(SHEET)
    page = VIEW.read_text(encoding="utf-8")
    ids = [int(n) for n in re.findall(r'id="row-(\d+)"', page)]
    assert ids == list(range(1, len(rows) + 1))


def test_no_owner_value_appears_in_the_committed_view():
    if not (SHEET.is_file() and VIEW.is_file()):
        pytest.skip("the sheet or the view has not been generated")
    with SHEET.open(encoding="utf-8") as handle:
        raw = list(csv.reader(handle))
    header = next(i for i, r in enumerate(raw) if r and r[0] == "item_id")
    rows = [dict(zip(raw[header], r, strict=False)) for r in raw[header + 1:] if r]
    page = VIEW.read_text(encoding="utf-8")
    for column in OWNER_COLUMNS:
        for item in rows:
            value = (item.get(column) or "").strip()
            if value:
                assert value not in page, f"{column}={value!r} leaked into the view"


def test_every_highlight_in_the_committed_view_exists_in_its_card_passage():
    """The strongest form of T4-15's highlight rule, over the real artifact."""
    if not VIEW.is_file():
        pytest.skip("the view has not been generated")
    page = VIEW.read_text(encoding="utf-8")
    cards = page.split('<article class="card"')[1:]
    checked = 0
    for card in cards:
        match = re.search(r'<div class="passage">(.*?)</div>', card, re.S)
        if not match:
            continue
        passage = re.sub(r"</?mark>", "", match.group(1))
        for marked in re.findall(r"<mark>(.*?)</mark>", card):
            checked += 1
            assert marked in passage, f"{marked!r} is not in its card's passage"
    assert checked > 0, "no highlights were checked"


def test_normalise_does_not_equate_different_scales():
    """It must not decide that $39.0 billion and 39,000,966 are one claim —
    that is the judgement the owner is making, not the page's to pre-empt."""
    assert normalise("39.0") != normalise("39000966")
    assert normalise("177,556") == normalise("177556")
    assert normalise("4.50") == normalise("4.5")


def test_numbers_in_finds_spans_that_index_back_into_the_text():
    text = "Revenue 177,556 and 39,000."
    for start, end, raw in numbers_in(text):
        assert text[start:end] == raw


# ── keeping the "not found" list signal, not noise ──────────────────────────

def test_a_parenthesised_enumerator_is_not_treated_as_a_figure():
    """Measured on the real sheet: every one of the four bare integers listed
    as missing was a list enumerator — "(7) consumer electronics; (8) grocery
    sellers" — and none was a claim. `(7)` is a reference exactly as `[7]` is,
    and a list of six false positives teaches the reader to ignore the list.
    """
    card = card_html(1, row(answer="Competitors include (7) retailers and "
                                   "(8) advertisers and (10) others [1].",
                            assist_passage="Competition is discussed at length."),
                     {"R-AAPL-SUPPLY-2024": []})
    assert "not found in passage" not in card


def test_a_year_is_listed_but_kept_separate_from_the_figures():
    """Years are NOT dropped. This project's whole point is period correctness,
    so an answer naming a year its cited passage does not is worth the owner's
    eye — but it is a period label, and it is shown as one."""
    card = card_html(1, row(answer="Revenue grew in fiscal 2025 [1].",
                            assist_passage="Results for fiscal 2024 were strong."),
                     {"R-AAPL-SUPPLY-2024": []})
    assert "Year references in the answer not in this passage" in card
    assert "2025" in card
    assert "Numbers in the answer, not found in passage" not in card


def test_a_real_figure_is_listed_without_the_year_annotation():
    """The control: the annotation must distinguish, not decorate everything."""
    card = card_html(1, row(answer="Revenue was $404,404 million [1].",
                            assist_passage="Total net revenue of 177,556."),
                     {"R-AAPL-SUPPLY-2024": []})
    assert "not found in passage" in card and "404,404" in card
    assert "year reference" not in card


def test_an_enumerator_inside_the_passage_can_still_be_highlighted():
    """Excluding `(N)` from the ANSWER's claims must not stop the passage being
    marked where a genuine figure happens to sit in brackets."""
    marked = highlight("Revenue (177,556) for the year.", ["177,556"])
    assert "<mark>177,556</mark>" in marked


def test_a_number_in_a_comma_separated_list_is_captured_without_the_comma():
    """`2023, 2024 and 2025` produced "2023," and "2024," — matching still
    worked because normalise strips commas, but the owner was shown a figure
    with a stray comma glued to it."""
    found = [raw for _s, _e, raw in numbers_in("Years 2023, 2024 and 2025.")]
    assert found == ["2023", "2024", "2025"]


def test_figures_and_year_references_are_presented_separately():
    """Measured on the real sheet after the enumerator fix: every remaining
    entry was a year, on 7 of 23 cards. A section headed "not found in
    passage" that only ever holds years trains the reader to skip it — and the
    one card with a real unmatched figure is the one that matters."""
    years_only = card_html(1, row(answer="Results improved in 2024 and 2025 [1].",
                                  assist_passage="Performance was strong."),
                           {"R-AAPL-SUPPLY-2024": []})
    assert "Numbers in the answer, not found in passage" not in years_only
    assert "Year references" in years_only and "2024" in years_only

    with_figure = card_html(1, row(answer="Revenue was $404,404 in 2025 [1].",
                                   assist_passage="Performance was strong."),
                            {"R-AAPL-SUPPLY-2024": []})
    assert "Numbers in the answer, not found in passage" in with_figure
    assert "404,404" in with_figure
