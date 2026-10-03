"""The owner's gold verification sheet (P4-02).

The sheet is what the owner reads at the gate, so the properties tested here
are the ones that would make a signed sheet mean less than it appears to: a
core that quietly omits a sector or one of the filers spec section 11 names, a
row with no way to reach the filing, or a verdict column this script filled in
by itself.

The row-building half needs the derived stores and is skipped at runtime when
they are absent (they are gitignored, so CI has none). The stratification half
is pure and always runs.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.make_gold_verification_sheet import (
    CORE_ROWS,
    REQUIRED_TICKERS,
    choose_core,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SHEET = REPO_ROOT / "reports" / "phase4" / "gold_verification.csv"


def _row(gold_id: str, sector: str, category: str, ticker: str) -> dict:
    return {"gold_id": gold_id, "sector": sector, "category": category,
            "ticker": ticker}


def _grid(n_per_cell: int = 6) -> list:
    rows = []
    for sector, ticker in (("Technology", "AAPL"), ("Banking", "JPM"),
                           ("Asset Management", "BLK"), ("Media", "NFLX")):
        for category in ("numeric", "computed"):
            for i in range(n_per_cell):
                rows.append(_row(f"{ticker}-{category}-{i}", sector, category, ticker))
    return rows


def test_the_core_covers_every_sector_and_category():
    rows = _grid()
    choose_core(rows)
    core = [r for r in rows if r["priority"] == "core"]
    assert len(core) == CORE_ROWS
    assert {r["sector"] for r in core} == {
        "Technology", "Banking", "Asset Management", "Media",
    }
    assert {r["category"] for r in core} == {"numeric", "computed"}


def test_the_core_is_balanced_rather_than_the_first_n_rows():
    """Negative control: taking the first 25 rows of this grid misses two sectors."""
    rows = _grid()
    naive = {r["sector"] for r in rows[:CORE_ROWS]}
    assert len(naive) < 4, "the fixture no longer makes 'first N' a bad strategy"

    choose_core(rows)
    counts = {}
    for r in rows:
        if r["priority"] == "core":
            counts[r["sector"]] = counts.get(r["sector"], 0) + 1
    assert min(counts.values()) >= 4, counts


def test_the_named_filers_are_always_in_the_core():
    """NFLX, WFC and BLK are the three filer properties spec section 11 names."""
    rows = _grid(n_per_cell=1)
    rows += [_row("WFC-numeric-0", "Banking", "numeric", "WFC")]
    # Bury WFC at the end, where a quota filled in order would never reach it.
    rows += [_row(f"pad-{i}", "Technology", "numeric", "AAPL") for i in range(40)]
    choose_core(rows)
    core_tickers = {r["ticker"] for r in rows if r["priority"] == "core"}
    for ticker in REQUIRED_TICKERS:
        assert ticker in core_tickers, ticker


def test_choosing_the_core_is_deterministic():
    first, second = _grid(), _grid()
    choose_core(first)
    choose_core(second)
    assert [r["priority"] for r in first] == [r["priority"] for r in second]


def test_a_set_smaller_than_the_core_quota_is_all_core():
    rows = [_row("a", "Technology", "numeric", "AAPL")]
    choose_core(rows)
    assert rows[0]["priority"] == "core"


# ── the committed sheet ───────────────────────────────────────────────────────

def _sheet():
    if not SHEET.is_file():
        pytest.skip("gold_verification.csv not generated")
    with SHEET.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_the_committed_sheet_meets_the_spec_minimum():
    rows = _sheet()
    core = [r for r in rows if r["priority"] == "core"]
    assert len(core) >= CORE_ROWS
    assert {r["sector"] for r in core} == {
        "Technology", "Banking", "Asset Management", "Media",
    }
    assert REQUIRED_TICKERS[0] in {r["ticker"] for r in core}


def test_every_row_can_actually_be_checked():
    """A row the owner cannot trace to a filing is a row they cannot sign."""
    for row in _sheet():
        assert row["filing_url"].startswith("https://www.sec.gov/Archives/"), row["gold_id"]
        assert row["expected_value"], row["gold_id"]
        assert row["why_this_item"], row["gold_id"]
        assert row["statement_to_check"], row["gold_id"]


def test_numeric_rows_show_the_figure_as_the_filing_prints_it():
    """391,035 is checkable against a statement; 391035000000 is not."""
    numeric = [r for r in _sheet() if r["category"] == "numeric"]
    assert numeric
    for row in numeric:
        assert row["filing_value_as_printed"], row["gold_id"]
        if row["scale_attr"] in ("3", "6", "9"):
            assert row["filing_value_as_printed"] != row["expected_value"], (
                f"{row['gold_id']}: a scaled fact printed unscaled"
            )


def test_the_script_leaves_every_verdict_blank():
    """The gate is the owner's. Nothing here may pre-fill it."""
    for row in _sheet():
        assert row["verdict"] == "", row["gold_id"]
        assert row["owner_note"] == "", row["gold_id"]
