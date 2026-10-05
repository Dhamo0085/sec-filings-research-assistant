"""T4-02: the gold set's schema validator, and the committed set itself.

Every headline number in Phase 4 is n/N over ``eval/gold/gold_v1.jsonl``. A
malformed or mis-provenanced item there does not fail anything by itself — it
moves a percentage quietly, and the report states that percentage with a
confidence interval. So the validator is tested the way the audit script is:
each check gets a planted defect that it must catch (CLAUDE.md rule 15), and the
committed file is then run through it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from eval.gold.schema import (
    CATEGORY_TARGETS,
    VERIFICATION_STRENGTH,
    CatalogDates,
    abstain_reasons,
    category_counts,
    read_jsonl,
    validate,
    write_jsonl,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"
SEEDS_DIR = REPO_ROOT / "eval" / "gold" / "seeds"


def _numeric_item(**overrides) -> dict:
    item = {
        "id": "N-AAPL-REVENUE-2024",
        "category": "numeric",
        "question": "What was Apple's revenue in fiscal 2024?",
        "as_of": None,
        "expected": {
            "type": "numeric", "ticker": "AAPL", "metric": "revenue",
            "fiscal_label": 2024, "period_end": "2024-09-28",
            "value": "391035000000", "unit": "USD", "tolerance_rel": 0.0,
        },
        "source": {"accession": "0000320193-24-000123",
                   "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                   "oracle": "sec_companyfacts"},
        "verified_by": "companyfacts",
        "notes": "",
    }
    item.update(overrides)
    return item


def _fields(problems) -> set:
    return {p.field for p in problems}


# ── the validator accepts what it should ─────────────────────────────────────

def test_a_well_formed_item_has_no_problems():
    assert validate([_numeric_item()]) == []


def test_the_pinned_abstain_reasons_match_the_live_enum():
    """The fallback copy in schema.py is not allowed to drift.

    schema.py keeps a pinned tuple so it stays importable without the
    application package. That is a second place the truth is written down, so
    it is pinned to the first here rather than trusted.
    """
    from answering.outcome import AbstainReason
    from eval.gold.schema import _FALLBACK_ABSTAIN_REASONS

    assert tuple(r.value for r in AbstainReason) == _FALLBACK_ABSTAIN_REASONS
    assert abstain_reasons() == _FALLBACK_ABSTAIN_REASONS


# ── negative controls, one per property T4-02 names ──────────────────────────

@pytest.mark.parametrize("missing", ["source", "verified_by"])
def test_an_item_without_provenance_is_rejected(missing):
    """Headline metrics are published over owner/companyfacts items only.

    An item with no provenance cannot be placed in either group, so it must not
    pass silently as though it were the weakest kind.
    """
    item = _numeric_item()
    del item[missing]
    assert missing in _fields(validate([item]))


def test_an_unknown_verification_tier_is_rejected():
    problems = validate([_numeric_item(verified_by="trust_me")])
    assert "verified_by" in _fields(problems)
    assert all(tier in VERIFICATION_STRENGTH for tier in ("owner", "companyfacts", "auto"))


def test_claiming_the_oracle_without_naming_it_is_rejected():
    """`verified_by: companyfacts` is a claim that must stay re-checkable."""
    item = _numeric_item()
    del item["source"]["oracle"]
    assert "source.oracle" in _fields(validate([item]))


def test_duplicate_ids_are_reported():
    """Results are joined to items by id, so a duplicate silently overwrites."""
    problems = validate([_numeric_item(), _numeric_item()])
    id_problems = [p for p in problems if p.field == "id"]
    assert [p.item_id for p in id_problems] == ["N-AAPL-REVENUE-2024"]
    assert "appears 2 times" in id_problems[0].message


def test_an_abstain_item_must_name_a_reason():
    item = {
        "id": "X-1", "category": "abstain", "question": "Should I buy Apple stock?",
        "as_of": None, "expected": {"type": "abstain"},
        "source": {"drafted_by": "claude"}, "verified_by": "auto",
    }
    assert "expected.abstain_reason" in _fields(validate([item]))


def test_an_abstain_reason_outside_the_enum_is_rejected():
    item = {
        "id": "X-2", "category": "abstain", "question": "?", "as_of": None,
        "expected": {"type": "abstain", "abstain_reason": "because_i_said_so"},
        "source": {"drafted_by": "claude"}, "verified_by": "auto",
    }
    assert "expected.abstain_reason" in _fields(validate([item]))


def test_a_numeric_value_given_as_a_json_number_is_rejected():
    """391035000000.0 is not 391035000000, and a gold value cannot round."""
    item = _numeric_item()
    item["expected"]["value"] = 391035000000
    assert "expected.value" in _fields(validate([item]))


def test_a_category_and_an_expected_type_that_disagree_are_rejected():
    """One decides the report's grouping, the other the scoring rules."""
    item = _numeric_item(category="narrative")
    assert "expected.type" in _fields(validate([item]))


# ── as_of consistency with the catalog ───────────────────────────────────────

CATALOG = CatalogDates({
    ("AAPL", 2024): "2024-11-01",
    ("AAPL", 2023): "2023-11-03",
    ("MSFT", 2025): "2025-07-30",
})


def _look_ahead(as_of: str) -> dict:
    return {
        "id": "A-1", "category": "as_of",
        "question": f"As of {as_of}, what was Apple's revenue for fiscal 2024?",
        "as_of": as_of,
        "expected": {"type": "abstain", "abstain_reason": "period_not_filed_as_of",
                     "ticker": "AAPL", "fiscal_label": 2024},
        "source": {"filing_date": "2024-11-01"}, "verified_by": "auto",
    }


def test_a_genuine_look_ahead_item_passes():
    assert validate([_look_ahead("2024-10-31")], CATALOG) == []


def test_a_look_ahead_item_whose_filing_was_already_public_is_rejected():
    """Otherwise the item scores a correct answer as a failure.

    This is the check that makes "zero look-ahead violations" mean something:
    without it the metric can be satisfied by items that were never tests.
    """
    problems = validate([_look_ahead("2024-12-01")], CATALOG)
    assert "as_of" in _fields(problems)
    assert "2024-11-01" in problems[0].message


def test_an_answerable_as_of_item_dated_before_the_filing_is_rejected():
    item = {
        "id": "A-2", "category": "as_of", "question": "?", "as_of": "2024-01-01",
        "expected": {"type": "numeric", "ticker": "AAPL", "metric": "revenue",
                     "fiscal_label": 2024, "period_end": "2024-09-28",
                     "value": "391035000000", "unit": "USD", "tolerance_rel": 0.0},
        "source": {"accession": "x"}, "verified_by": "auto",
    }
    assert "as_of" in _fields(validate([item], CATALOG))


def test_an_as_of_item_for_a_filing_the_catalog_does_not_have_is_rejected():
    item = _look_ahead("2020-01-01")
    item["expected"]["fiscal_label"] = 1999
    problems = validate([item], CATALOG)
    assert "as_of" in _fields(problems)
    assert "no original 10-K" in problems[0].message


def test_as_of_checks_are_skipped_when_no_catalog_is_given():
    """The validator stays usable without the database (CI has no facts store)."""
    assert validate([_look_ahead("2024-12-01")], None) == []


# ── round-tripping ────────────────────────────────────────────────────────────

def test_write_then_read_round_trips_and_orders_keys(tmp_path):
    path = tmp_path / "g.jsonl"
    write_jsonl(path, [_numeric_item()])
    assert read_jsonl(path) == [_numeric_item()]
    first_line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert list(first_line)[:4] == ["id", "category", "question", "as_of"]


def test_a_malformed_line_names_its_line_number(tmp_path):
    path = tmp_path / "g.jsonl"
    path.write_text('{"id": "a"}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r":2:"):
        read_jsonl(path)


# ── the committed gold set ────────────────────────────────────────────────────

def test_the_committed_gold_set_validates():
    items = read_jsonl(GOLD_PATH)
    problems = validate(items)
    assert problems == [], "\n".join(str(p) for p in problems)


def test_the_committed_gold_set_has_the_shape_spec_section_11_fixes():
    counts = category_counts(read_jsonl(GOLD_PATH))
    assert counts == CATEGORY_TARGETS, f"got {counts}"


def test_every_generated_item_is_oracle_verified():
    """The four generated categories carry an independent value or they are not gold.

    Narrative, abstain and look-ahead items are `auto` by nature — there is no
    oracle for prose, for a refusal, or for a date — and become `owner` at the
    P4-02 gate. A numeric, computed or compare item that is `auto` means the
    oracle had no comparable row, which is worth failing on rather than
    discovering later in a results table footnote.
    """
    unverified = [
        item["id"] for item in read_jsonl(GOLD_PATH)
        if item["category"] in ("numeric", "computed", "compare_trend")
        and item["verified_by"] == "auto"
    ]
    assert unverified == [], f"not confirmed by SEC companyfacts: {unverified}"


def test_the_gold_set_covers_the_filers_spec_section_11_names():
    """Four sectors, and the thousands / split-document / multi-concept filers."""
    from eval.gold.plan import SECTORS

    items = read_jsonl(GOLD_PATH)
    tickers = {
        item["expected"]["ticker"] for item in items
        if isinstance(item.get("expected"), dict) and item["expected"].get("ticker")
    }
    assert {SECTORS[t] for t in tickers if t in SECTORS} == {
        "Technology", "Banking", "Asset Management", "Media",
    }
    assert "NFLX" in tickers, "no thousands-reporting filer"
    assert "WFC" in tickers, "no split-document filer"
    assert "BLK" in tickers, "no multi-concept filer"


def test_the_hand_drafted_seeds_are_all_auto_until_the_owner_signs_them():
    """Nothing hand-drafted may claim oracle or owner verification yet (D4)."""
    for name in ("narrative.jsonl", "abstain.jsonl"):
        for item in read_jsonl(SEEDS_DIR / name):
            assert item["verified_by"] == "auto", f"{name}: {item['id']}"


def test_the_abstain_seeds_cover_distinct_reasons():
    """Abstention is reported per reason, so the set has to exercise several."""
    reasons = {
        item["expected"]["abstain_reason"]
        for item in read_jsonl(SEEDS_DIR / "abstain.jsonl")
    }
    assert len(reasons) >= 7, f"only {len(reasons)} distinct reasons: {sorted(reasons)}"
    assert reasons <= set(abstain_reasons())


def test_no_two_gold_questions_are_the_same_string():
    """Two items with the same question cannot both be testing something.

    Not a schema rule — ids are already unique — but a drafting mistake the
    validator would not catch, and one that would inflate a category's count.
    """
    items = read_jsonl(GOLD_PATH)
    questions = [item["question"].strip().lower() for item in items]
    duplicates = {q for q in questions if questions.count(q) > 1}
    assert not duplicates, f"repeated questions: {duplicates}"


def test_mutating_the_committed_set_is_caught(tmp_path):
    """Negative control for the three tests above.

    They assert that a file on disk is clean, and a test that has only ever
    seen a clean file is not evidence. This plants each defect in a copy and
    requires the same assertions to fail.
    """
    items = read_jsonl(GOLD_PATH)

    broken = copy.deepcopy(items)
    broken[0]["verified_by"] = "auto"
    assert [
        i["id"] for i in broken
        if i["category"] in ("numeric", "computed", "compare_trend")
        and i["verified_by"] == "auto"
    ], "the oracle-coverage assertion would not notice an auto item"

    broken = copy.deepcopy(items)
    del broken[0]["source"]
    assert validate(broken), "the validator would not notice a missing source"

    broken = copy.deepcopy(items)
    broken.append(copy.deepcopy(broken[0]))
    assert category_counts(broken) != CATEGORY_TARGETS, \
        "the category-count assertion would not notice an extra item"


def test_the_committed_set_matches_a_fresh_build():
    """The file on disk is what the plan and the stores produce, today.

    This is what makes the gold set reproducible rather than a snapshot someone
    generated once: if the registry, an override or the facts store changes,
    the committed expectations stop matching and this fails instead of the
    change silently invalidating every Phase 4 number.

    Skipped at RUNTIME, not at collection time, when the derived stores are
    absent — they are gitignored, so CI does not have them, and a
    ``skipif`` evaluated at collection would also be making that decision
    before pytest-socket is in place (a Phase 2 gotcha).
    """
    from config import settings

    derived = Path(settings.data_dir) / "derived"
    for name in ("facts.sqlite", "catalog.sqlite"):
        if not (derived / name).is_file():
            pytest.skip(f"{name} not built; run `make facts` and `make catalog`")

    from eval.gold.build_gold import main

    assert main(["--check"]) == 0, "the committed gold set is not what a fresh build produces"
