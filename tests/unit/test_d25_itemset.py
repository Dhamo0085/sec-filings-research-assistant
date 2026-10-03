"""The D25 arm is only about the parser if both stores hold the same filings.

After P4-12 they do not: the frozen backup holds 25 collections, the live
store 39. An item naming a filing only one store has would make the other arm
score lower for a reason that is not the section-boundary rewrite — and the
report would read as if the rewrite had earned it. These tests pin the filter
that prevents that, including the planted case it exists for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.make_d25_itemset import item_pairs

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_a_single_filing_item_names_its_filing():
    item = {"expected": {"type": "text", "ticker": "AAPL", "fiscal_label": 2024}}
    assert item_pairs(item) == [("AAPL", 2024)]


def test_a_comparison_item_names_both_filings():
    """A compare item needs BOTH companies in both stores, or the arm is
    measuring coverage rather than retrieval."""
    item = {"expected": {"type": "multi", "values": [
        {"ticker": "JPM", "fiscal_label": 2024},
        {"ticker": "BAC", "fiscal_label": 2024}]}}
    assert item_pairs(item) == [("JPM", 2024), ("BAC", 2024)]


def test_an_item_naming_no_company_depends_on_nothing():
    """'Should I buy Apple stock?' is routed on the sentence, not on a filing."""
    assert item_pairs({"expected": {"type": "abstain"}}) == []


def test_the_committed_set_names_only_shared_filings():
    """The artifact itself: every filing it names must be in both stores."""
    path = REPO_ROOT / "eval/gold/d25_shared_items.jsonl"
    if not path.is_file():
        pytest.skip("the D25 item set has not been built")

    from scripts.make_d25_itemset import collection_pairs

    old = REPO_ROOT / "data/qdrant_v1_backup"
    new = REPO_ROOT / "data/qdrant"
    if not (old.is_dir() and new.is_dir()):
        pytest.skip("both stores are needed to check the set")
    shared = collection_pairs(old) & collection_pairs(new)

    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert items, "the set is empty"
    for item in items:
        pairs = item_pairs(item)
        assert pairs, f"{item['id']} names no filing and cannot be scored"
        for pair in pairs:
            assert pair in shared, f"{item['id']} names {pair}, not in both stores"


def test_the_set_excludes_the_filers_only_the_new_store_holds():
    """Negative control: without it, a filter that kept everything would pass
    the test above whenever the two stores happened to agree."""
    path = REPO_ROOT / "eval/gold/d25_shared_items.jsonl"
    if not path.is_file():
        pytest.skip("the D25 item set has not been built")
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    tickers = {t for item in items for t, _ in item_pairs(item)}
    assert not tickers & {"BAC", "IVZ", "STT", "TROW", "WFC"}, \
        "a filer the frozen backup never held is in the D25 set"
    assert "TSLA" not in tickers, "TSLA is in the backup but not the new store"
    assert len(tickers) >= 6, "the set collapsed to almost nothing"
