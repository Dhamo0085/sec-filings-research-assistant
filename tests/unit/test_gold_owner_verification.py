"""P4-15: the owner's signed sheet sets ``verified_by=owner``, and nothing else.

The sheet is the owner's signature (D29 / CLAUDE.md rule 17). These tests pin
the three properties that make applying it safe: only the tier moves, only the
``OK`` rows move, and a sheet that disagrees with the gold set is an error
rather than a silent skip.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from eval.gold.build_gold import SIGNED_SHEET, BuildError, apply_owner_verification

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"


def _gold():
    return [json.loads(line) for line in GOLD_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sheet(tmp_path: Path, rows) -> Path:
    path = tmp_path / "signed.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gold_id", "verdict"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_committed_gold_carries_the_owner_tier_on_exactly_37_items():
    """37 of 80 are owner-verified; the other 43 are companyfacts or auto."""
    items = _gold()
    owner = [i for i in items if i["verified_by"] == "owner"]
    assert len(owner) == 37
    assert len(items) - len(owner) == 43
    # The owner checked numeric and computed values against the filings; the
    # compare/trend, as_of, narrative and abstain items were never in that sheet.
    assert {i["category"] for i in owner} == {"numeric", "computed"}
    assert all(i["verified_by"] in {"companyfacts", "auto"}
               for i in items if i["verified_by"] != "owner")


def test_the_signed_sheet_and_the_gold_set_agree_on_which_items_are_owner_verified():
    with SIGNED_SHEET.open(newline="", encoding="utf-8") as handle:
        signed = {r["gold_id"] for r in csv.DictReader(handle)
                  if (r.get("verdict") or "").strip().upper() == "OK"}
    owner = {i["id"] for i in _gold() if i["verified_by"] == "owner"}
    assert signed == owner


def test_only_ok_rows_are_promoted(tmp_path):
    items = [
        {"id": "A", "verified_by": "companyfacts"},
        {"id": "B", "verified_by": "companyfacts"},
    ]
    apply_owner_verification(items, _sheet(tmp_path, [
        {"gold_id": "A", "verdict": "OK"},
        {"gold_id": "B", "verdict": ""},
    ]))
    assert [i["verified_by"] for i in items] == ["owner", "companyfacts"]


def test_no_value_from_the_sheet_reaches_the_gold_item(tmp_path):
    """The sheet is read by id only: a wrong number in it cannot change gold."""
    item = {"id": "A", "verified_by": "companyfacts",
            "expected": {"type": "number", "value": 100}}
    path = tmp_path / "signed.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["gold_id", "verdict", "expected_value", "owner_note"])
        writer.writeheader()
        writer.writerow({"gold_id": "A", "verdict": "OK",
                         "expected_value": "999999", "owner_note": "ignore me"})
    apply_owner_verification([item], path)
    assert item == {"id": "A", "verified_by": "owner",
                    "expected": {"type": "number", "value": 100}}


def test_an_unknown_gold_id_in_the_sheet_is_an_error(tmp_path):
    """Negative control: a sheet row that matches nothing must be loud."""
    with pytest.raises(BuildError, match="not in the gold set"):
        apply_owner_verification(
            [{"id": "A", "verified_by": "auto"}],
            _sheet(tmp_path, [{"gold_id": "GHOST", "verdict": "OK"}]),
        )


def test_a_wrong_verdict_fails_the_build(tmp_path):
    with pytest.raises(BuildError, match="WRONG"):
        apply_owner_verification(
            [{"id": "A", "verified_by": "companyfacts"}],
            _sheet(tmp_path, [{"gold_id": "A", "verdict": "WRONG"}]),
        )


def test_an_unrecognised_verdict_fails_the_build(tmp_path):
    with pytest.raises(BuildError, match="expected OK or WRONG"):
        apply_owner_verification(
            [{"id": "A", "verified_by": "companyfacts"}],
            _sheet(tmp_path, [{"gold_id": "A", "verdict": "maybe"}]),
        )


def test_a_missing_sheet_promotes_nothing_and_says_so(tmp_path):
    items = [{"id": "A", "verified_by": "companyfacts"}]
    notes = apply_owner_verification(items, tmp_path / "absent.csv")
    assert items[0]["verified_by"] == "companyfacts"
    assert any("no item is marked verified_by=owner" in n for n in notes)
