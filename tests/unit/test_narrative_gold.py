"""T4-10: the narrative retrieval set is validated against the section audit.

Every test here plants something and asserts the validator notices. A
validator that has only ever been run on a good file has not been tested
(CLAUDE.md rule 15), and this one guards a measurement — D27's reranker
decision — rather than a user-visible behaviour, so nothing else would catch
a hole in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.gold.narrative_validator import (
    load_audit,
    read_items,
    validate,
    validate_item,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def audit_fixture() -> dict:
    """Two filings: AAPL_2024 clean, AAPL_2023 with a damaged Item 1A."""
    return {
        ("AAPL", 2024, "item_1_business"): "ok",
        ("AAPL", 2024, "item_1a_risk_factors"): "ok",
        ("AAPL", 2024, "item_1c_cyber"): "too_small",
        ("AAPL", 2023, "item_1_business"): "ok",
        ("AAPL", 2023, "item_1a_risk_factors"): "missing",
    }


def item(**overrides) -> dict:
    base = {
        "id": "R-AAPL-COMPETITION-2024",
        "category": "narrative",
        "question": "How does Apple Inc. describe the competition it faces?",
        "as_of": None,
        "expected": {"type": "text", "ticker": "AAPL", "fiscal_label": 2024,
                     "expected_sections": ["item_1_business"]},
        "source": {"ticker": "AAPL", "fiscal_label": 2024, "drafted_by": "claude",
                   "basis": "Item 1 describes the competitive landscape"},
        "verified_by": "auto",
        "notes": "",
    }
    base.update(overrides)
    return base


def test_a_good_item_has_no_problems():
    """The negative control for every test below: without it, a validator that
    rejected everything would pass them all."""
    assert validate_item(item(), audit_fixture()) == []


def test_an_item_pointing_at_an_unusable_section_is_rejected():
    """T4-10's planted case. too_small is not 'present but short' — D23 says a
    slice that fails the audit is not the section it claims to be."""
    bad = item(id="R-AAPL-CYBER-2024",
               expected={"type": "text", "ticker": "AAPL", "fiscal_label": 2024,
                         "expected_sections": ["item_1c_cyber"]})
    problems = validate_item(bad, audit_fixture())
    assert len(problems) == 1
    assert "item_1c_cyber" in problems[0] and "too_small" in problems[0]


def test_an_item_pointing_at_a_missing_section_is_rejected():
    bad = item(id="R-AAPL-SUPPLY-2023",
               expected={"type": "text", "ticker": "AAPL", "fiscal_label": 2023,
                         "expected_sections": ["item_1a_risk_factors"]})
    problems = validate_item(bad, audit_fixture())
    assert len(problems) == 1 and "missing" in problems[0]


def test_one_bad_section_among_several_still_rejects_the_item():
    """An item is scored a hit if ANY expected section is retrieved, so a set
    could hide an unusable section behind a usable one and still look fine."""
    bad = item(expected={"type": "text", "ticker": "AAPL", "fiscal_label": 2024,
                         "expected_sections": ["item_1_business", "item_1c_cyber"]})
    problems = validate_item(bad, audit_fixture())
    assert len(problems) == 1 and "item_1c_cyber" in problems[0]


def test_an_item_naming_a_filing_the_corpus_does_not_hold_is_rejected():
    bad = item(expected={"type": "text", "ticker": "TSLA", "fiscal_label": 2025,
                         "expected_sections": ["item_1_business"]})
    problems = validate_item(bad, audit_fixture())
    assert len(problems) == 1 and "not in the section audit" in problems[0]


def test_an_item_naming_a_section_the_audit_does_not_cover_is_rejected():
    bad = item(expected={"type": "text", "ticker": "AAPL", "fiscal_label": 2024,
                         "expected_sections": ["item_99_invented"]})
    problems = validate_item(bad, audit_fixture())
    assert len(problems) == 1 and "not audited" in problems[0]


def test_an_item_with_no_expected_section_is_rejected():
    """Nothing for section_hit@k to be true of, so it would score zero on every
    arm and drag all of them down equally."""
    bad = item(expected={"type": "text", "ticker": "AAPL", "fiscal_label": 2024,
                         "expected_sections": []})
    problems = validate_item(bad, audit_fixture())
    assert any("names no expected section" in p for p in problems)


def test_a_duplicate_id_is_reported():
    """Results join to items by id, so a repeat drops a score without changing
    a count."""
    problems = validate([item(), item()], audit_fixture())
    assert any("appears 2 times" in p for p in problems)


def test_the_committed_set_validates_against_the_live_audit():
    """The shipped artifact, not a fixture: at least 40 items (D27) and no
    problem against the audit of the corpus that is actually indexed."""
    items_path = REPO_ROOT / "eval/gold/narrative_retrieval_v1.jsonl"
    audit_path = REPO_ROOT / "reports/phase4/section_audit_live/section_audit.json"
    if not (items_path.is_file() and audit_path.is_file()):
        pytest.skip("the P4-13 set or the live audit is not present")
    items = read_items(items_path)
    assert len(items) >= 40, "D27 needs at least 40 narrative items"
    assert validate(items, load_audit(audit_path)) == []


def test_the_committed_set_is_spread_over_filers_and_sections():
    """A set drawn from three companies would make D27 a statement about those
    three; a set that was all Item 1A would make it a statement about Item 1A."""
    items_path = REPO_ROOT / "eval/gold/narrative_retrieval_v1.jsonl"
    if not items_path.is_file():
        pytest.skip("the P4-13 set is not present")
    items = read_items(items_path)
    tickers = {i["expected"]["ticker"] for i in items}
    sections = {s for i in items for s in i["expected"]["expected_sections"]}
    years = {i["expected"]["fiscal_label"] for i in items}
    assert len(tickers) >= 10, f"only {len(tickers)} filers"
    assert len(sections) >= 4, f"only {len(sections)} sections"
    assert len(years) >= 3, f"only {len(years)} fiscal years"


def test_the_set_is_reproducible():
    """Re-drafting from the same corpus and audit must give a byte-identical
    file, or no number measured on it can be reproduced."""
    items_path = REPO_ROOT / "eval/gold/narrative_retrieval_v1.jsonl"
    audit_path = REPO_ROOT / "reports/phase4/section_audit_live/section_audit.json"
    parsed_dir = REPO_ROOT / "data/parsed"
    if not (items_path.is_file() and audit_path.is_file() and parsed_dir.is_dir()):
        pytest.skip("the corpus or the live audit is not present")

    from scripts.make_narrative_gold import candidates, load_corpus, select

    pools = candidates(load_corpus(parsed_dir), load_audit(audit_path),
                       min_evidence=3)
    redrafted = "".join(json.dumps(i, sort_keys=False) + "\n"
                        for i in select(pools, 45))
    assert redrafted == items_path.read_text()
