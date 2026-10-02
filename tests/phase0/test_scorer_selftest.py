"""
Phase 0 — self-test for eval/phase0/score_baseline.py.

The scorer is itself a measuring instrument, so it needs its own evidence.
These cases are synthetic; no pipeline is involved.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "eval" / "phase0"))

from score_baseline import numbers_in, score_numeric, score_multi, score_abstention  # noqa: E402


def test_number_formats_normalise_to_millions():
    assert numbers_in("$391,035 million")[0]["value_millions"] == 391035.0
    assert numbers_in("$391.0 billion")[0]["value_millions"] == 391000.0
    assert numbers_in("391.0B")[0]["value_millions"] == 391000.0
    assert numbers_in("45,183,036 thousand")[0]["value_millions"] == 45183.036
    assert numbers_in("31.5%")[0]["percent"] == 31.5


def test_form_types_are_not_quantities():
    """Regression: 'indexed SEC 10-K filings' must not yield the number 10."""
    assert numbers_in("I can only answer from indexed SEC 10-K filings.") == []
    assert numbers_in("See Item 1A and Item 7A of the 10-K.") == []
    nums = numbers_in("Revenue was $391,035 million per the 10-K.")
    assert [n["value_millions"] for n in nums] == [391035.0]


def test_numeric_pass_and_scale_error():
    assert score_numeric("Total net sales were $391,035 million.", 391035.0)["verdict"] == "PASS"
    assert score_numeric("Total net sales were $391.035 billion.", 391035.0)["verdict"] == "PASS"
    off = score_numeric("Total net sales were $391,035 thousand.", 391035.0)
    assert off["verdict"] == "FAIL" and "scale_error" in off["failure_stage"]
    assert score_numeric("Total net sales were $383,285 million.", 391035.0)["failure_stage"] == "wrong_value"
    assert score_numeric("Which company are you asking about?", 391035.0)["failure_stage"] == "no_number_in_answer"


def test_percent_tolerance():
    assert score_numeric("Operating margin was 31.5%.", 31.51, kind="percent")["verdict"] == "PASS"
    assert score_numeric("Operating margin was 24.0%.", 31.51, kind="percent")["verdict"] == "FAIL"


def test_compare_requires_attribution():
    # company NAMES must attribute, not just ticker symbols
    good = "Microsoft spent $29,510 million on R&D. Alphabet spent $49,326 million."
    assert score_multi(good, {"MSFT": 29510.0, "GOOGL": 49326.0})["verdict"] == "PASS"
    good2 = "MSFT spent $29,510 million on R&D; GOOGL spent $49,326 million."
    assert score_multi(good2, {"MSFT": 29510.0, "GOOGL": 49326.0})["verdict"] == "PASS"
    swapped = "Microsoft spent $49,326 million; Alphabet spent $29,510 million."
    assert score_multi(swapped, {"MSFT": 29510.0, "GOOGL": 49326.0})["verdict"] == "FAIL"


def test_trend_years_attribute():
    trend = ("In 2023 operating income was $36,852 million; "
             "in 2024 it was $68,593 million; in 2025 it was $79,975 million.")
    r = score_multi(trend, {"2023": 36852.0, "2024": 68593.0, "2025": 79975.0})
    assert r["verdict"] == "PASS"


def test_abstention():
    assert score_abstention("I cannot find any filings for that company.")["verdict"] == "PASS"
    bad = score_abstention("I cannot be certain, but revenue was about $391,035 million.")
    assert bad["verdict"] == "FAIL" and bad["failure_stage"] == "numeric_claim_present"
    assert score_abstention("Revenue was $391,035 million.")["failure_stage"] == "no_abstention_marker"
