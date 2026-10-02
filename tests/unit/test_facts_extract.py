"""T2-01, T2-02, T2-03 — inline-XBRL extraction.

The fixtures are trimmed real filings (``tests/fixtures/ixbrl/``, built by
``scripts/make_ixbrl_fixtures.py``), not hand-written XML. That matters: a
hand-written fixture encodes what the author believed filers do, so a test over
it can only confirm the author's assumptions. Every expected number below was
read from the real document and cross-checked against the Phase 0 oracle run.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from facts.errors import UnknownTransformError, ValueParseError
from facts.extract import (
    ANNUAL_MAX_DAYS,
    ANNUAL_MIN_DAYS,
    NUMERIC_TRANSFORMS,
    Context,
    annual_facts,
    parse_submission,
    survey_formats,
    unsupported_numeric_formats,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ixbrl"
MANIFEST = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
BY_NAME = {entry["name"]: entry for entry in MANIFEST}


def load(name: str):
    entry = BY_NAME[name]
    return parse_submission([FIXTURES / f for f in entry["files"]])


def annual(name: str):
    entry = BY_NAME[name]
    return annual_facts(load(name), date.fromisoformat(entry["period_end"]))


def value_of(facts, concept: str):
    values = {f.value for f in facts if f.concept_local == concept}
    assert len(values) == 1, f"{concept}: expected one value, got {sorted(map(str, values))}"
    return values.pop()


# ── the fixtures are what they claim to be ─────────────────────────────────

def test_every_manifest_fixture_exists_and_is_small():
    """Spec section 10 caps each excerpt at 200 KB."""
    assert len(MANIFEST) >= 8
    for entry in MANIFEST:
        for name in entry["files"]:
            path = FIXTURES / name
            assert path.exists(), f"{name} is in the manifest but not on disk"
            assert path.stat().st_size <= 200 * 1024, f"{name} exceeds 200 KB"


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: e["name"])
def test_manifest_expectations_hold(entry):
    """The manifest's own expectations, re-checked here rather than only in the
    generator — the generator needs .cache/filings, which is not committed."""
    submission = parse_submission([FIXTURES / f for f in entry["files"]])
    facts = annual_facts(submission, date.fromisoformat(entry["period_end"]))
    for concept, expected in entry["expect"].items():
        values = {f.value for f in facts if f.concept_local == concept}
        assert Decimal(expected) in values, f"{concept}: {sorted(map(str, values))}"
    dei = submission.dei()
    for key, expected in entry["expect_dei"].items():
        assert dei.get(key) == expected, f"dei:{key} = {dei.get(key)!r}"


# ── T2-01: scale, sign and format transforms ───────────────────────────────

def test_millions_scale_is_applied():
    """Apple tags 294,866 with scale=6; the fact is 391,035,000,000 in total."""
    facts = annual("aapl_fy2024")
    assert value_of(facts, "RevenueFromContractWithCustomerExcludingAssessedTax") \
        == Decimal("391035000000")
    assert value_of(facts, "NetIncomeLoss") == Decimal("93736000000")


def test_thousands_scale_is_applied_netflix():
    """K2 regression: v1's prompt told the LLM to 'assume millions'.

    Netflix reports in THOUSANDS (scale=3). Assuming millions makes this
    revenue 39,000,966,000,000 — a 1000x error, and the single most likely way
    for a confident-sounding answer to be nonsense.
    """
    facts = annual("nflx_fy2024_thousands")
    revenue = value_of(facts, "Revenues")
    assert revenue == Decimal("39000966000")
    assert Decimal("3.8e10") < revenue < Decimal("4.0e10"), (
        "Netflix FY2024 revenue is about $39 billion, not $39 trillion"
    )
    assert value_of(facts, "NetIncomeLoss") == Decimal("8711631000")


def test_scale_is_read_never_inferred():
    """The two filings use different scales for the same magnitude of number."""
    aapl = annual("aapl_fy2024")
    nflx = annual("nflx_fy2024_thousands")
    aapl_rev = next(f for f in aapl
                    if f.concept_local == "RevenueFromContractWithCustomerExcludingAssessedTax")
    nflx_rev = next(f for f in nflx if f.concept_local == "Revenues")
    assert aapl_rev.scale_raw == "6"
    assert nflx_rev.scale_raw == "3"


def test_negative_sign_attribute_is_applied():
    """sign="-" on a positive numeral. Apple's FY2023 comparative column."""
    submission = load("aapl_fy2024_negatives")
    signed = [f for f in submission.facts if f.sign_raw == "-"]
    assert signed, "the fixture should carry sign='-' facts"
    assert all(f.value < 0 for f in signed), (
        "a sign='-' fact whose value is positive means the attribute was ignored"
    )
    values = {f.value for f in submission.facts
              if f.concept_local == "NonoperatingIncomeExpense"}
    assert Decimal("-565000000") in values


def test_fixed_zero_is_zero_not_missing():
    """ixt:fixed-zero renders as an em-dash and carries scale=6. 0, not None."""
    submission = load("aapl_fy2024_edge_values")
    fixed = [f for f in submission.facts
             if (f.format_raw or "").endswith("fixed-zero")]
    assert fixed, "the fixture should carry ixt:fixed-zero facts"
    assert all(f.value == 0 for f in fixed)
    assert all(not f.is_nil for f in fixed), "fixed-zero is a value, nil is not"


def test_nil_is_not_zero():
    """xsi:nil means 'not reported'. Reading it as 0 would assert a disclosure.

    All 32 nil facts in the corpus are us-gaap:CommitmentsAndContingencies;
    'zero commitments' is a claim the filer did not make.
    """
    submission = load("aapl_fy2024_edge_values")
    nils = [f for f in submission.facts if f.is_nil]
    assert nils, "the fixture should carry xsi:nil facts"
    assert all(f.value is None for f in nils)
    assert all(f not in submission.numeric_facts for f in nils)
    assert {f.concept_local for f in nils} == {"CommitmentsAndContingencies"}


def test_fixed_zero_and_nil_are_distinguishable():
    """The two look alike in a rendered table and mean opposite things."""
    submission = load("aapl_fy2024_edge_values")
    zeros = [f for f in submission.facts
             if not f.is_nil and f.value == 0]
    nils = [f for f in submission.facts if f.is_nil]
    assert zeros and nils
    assert not ({id(f) for f in zeros} & {id(f) for f in nils})


@pytest.mark.parametrize(("transform", "text", "expected"), [
    ("num-dot-decimal", "1,234,567", "1234567"),
    ("num-dot-decimal", "6.11", "6.11"),
    ("num-dot-decimal", "(1,234)", "-1234"),
    ("num-dot-decimal", "$1,234.56", "1234.56"),
    ("num-dot-decimal", "1 234", "1234"),          # non-breaking space
    ("num-comma-decimal", "1.234.567,89", "1234567.89"),
    ("num-dot-decimal-apos", "1'234'567", "1234567"),
    ("fixed-zero", "—", "0"),
    ("fixed-zero", "anything at all", "0"),
    ("zerodash", "-", "0"),
    ("nocontent", "", "0"),
    ("numwordsen", "two", "2"),
    ("numwordsen", "three", "3"),
    ("numwordsen", "twenty-one", "21"),
    ("numwordsen", "one hundred", "100"),
    ("numwordsen", "none", "0"),
    # Both of these took whole filings down before the wider survey: "nil" is
    # State Street FY2022-2025, the scale word is Bank of America FY2021.
    ("numwordsen", "nil", "0"),
    ("numwordsen", "no", "0"),
    ("numwordsen", "three million", "3000000"),
    ("numwordsen", "two million five hundred", "2000500"),
    ("numwordsen", "fifteen", "15"),
])
def test_numeric_transform_table(transform, text, expected):
    assert NUMERIC_TRANSFORMS[transform](text) == Decimal(expected)


def test_numwordsen_rejects_a_word_it_does_not_know():
    """Better a loud failure than a silent 0 where a count was meant."""
    with pytest.raises(ValueParseError):
        NUMERIC_TRANSFORMS["numwordsen"]("umpteen")


def test_unknown_numeric_format_fails_loudly(tmp_path):
    """Spec P2-02: handle all formats, fail loudly on unknown.

    Skipping the fact would be just as wrong as mis-reading it: the resolver
    would then pick a different candidate and report THAT as the answer.
    """
    doc = tmp_path / "weird.htm"
    doc.write_text(
        '<!DOCTYPE html><html><head>'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
        '</head><body>'
        '<ix:header><ix:resources><xbrli:context id="c1">'
        '<xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate>'
        '<xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>'
        '</xbrli:context></ix:resources></ix:header>'
        '<ix:nonFraction name="us-gaap:Revenues" contextRef="c1" '
        'unitRef="usd" scale="6" format="ixt:num-martian-decimal">1 234</ix:nonFraction>'
        '</body></html>',
        encoding="utf-8",
    )
    with pytest.raises(UnknownTransformError) as exc:
        parse_submission([doc])
    assert "num-martian-decimal" in str(exc.value)
    assert "us-gaap:Revenues" in str(exc.value)
    # The message must say what to do, not just that something went wrong.
    assert "NUMERIC_TRANSFORMS" in str(exc.value)


def test_the_committed_fixtures_use_no_unsupported_numeric_format():
    """A negative control for the survey: it reports nothing here, and the test
    above proves it can report something."""
    docs = sorted(FIXTURES.glob("*.htm"))
    counts = survey_formats(docs)
    assert counts["nonfraction"], "the survey found no numeric facts at all"
    assert unsupported_numeric_formats(counts) == {}


def test_a_failing_nonnumeric_transform_does_not_lose_the_filing(tmp_path):
    """One unreadable cover-page date must not discard a filing's numbers.

    This is a regression test for a real defect in this module: a date arriving
    as "SeptemberÂ 28, 2024" raised out of parse_document and made the
    whole submission unextractable, throwing away 1,127 good numeric facts.
    """
    doc = tmp_path / "baddate.htm"
    doc.write_text(
        '<!DOCTYPE html><html><head>'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
        '</head><body>'
        '<ix:header><ix:resources><xbrli:context id="c1">'
        '<xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate>'
        '<xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>'
        '</xbrli:context></ix:resources></ix:header>'
        '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c1" '
        'format="ixt:date-monthname-day-year-en">Septembre 28 2024</ix:nonNumeric>'
        '<ix:nonFraction name="us-gaap:Revenues" contextRef="c1" '
        'unitRef="usd" scale="6">1,234</ix:nonFraction>'
        '</body></html>',
        encoding="utf-8",
    )
    submission = parse_submission([doc])
    revenue = next(f for f in submission.facts if f.concept_local == "Revenues")
    assert revenue.value == Decimal("1234000000")
    bad = next(f for f in submission.facts
               if f.concept_local == "DocumentPeriodEndDate")
    assert bad.transform_failed is True
    assert bad.text_value == "Septembre 28 2024", "the raw text must be kept"


def test_undeclared_utf8_document_is_not_read_as_latin1(tmp_path):
    """libxml2 defaults undeclared HTML to latin-1; UTF-8 bytes then mojibake."""
    doc = tmp_path / "nodeclaration.htm"
    doc.write_text(
        "<!DOCTYPE html><html><body>"
        '<ix:header><ix:resources><xbrli:context id="c1">'
        "<xbrli:period><xbrli:instant>2024-09-28</xbrli:instant>"
        "</xbrli:period></xbrli:context></ix:resources></ix:header>"
        '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="c1" '
        'format="ixt:date-monthname-day-year-en">September 28, 2024'
        "</ix:nonNumeric></body></html>",
        encoding="utf-8",
    )
    submission = parse_submission([doc])
    assert submission.dei()["DocumentPeriodEndDate"] == "2024-09-28"


# ── T2-02: contexts — dimensions, annual windows, instants ─────────────────

def test_dimensional_facts_are_excluded_from_consolidated():
    """45 of Apple's 54 revenue facts are segment/product breakdowns."""
    submission = load("aapl_fy2024")
    assert any(f.is_dimensional for f in submission.facts), (
        "the fixture should retain some dimensional facts to exclude"
    )
    assert all(not f.is_dimensional for f in submission.consolidated_facts)
    assert all(not f.is_dimensional for f in annual("aapl_fy2024"))


def test_52_53_week_year_is_accepted():
    """Apple's FY2024 is 363 days (2023-10-01 to 2024-09-28), not 365.

    Spec issue N7. A 365-day window with a small tolerance rejects every annual
    fact Apple reports, which looks exactly like "Apple does not tag revenue".
    """
    submission = load("aapl_fy2024")
    facts = annual_facts(submission, date(2024, 9, 28))
    durations = {f.context.duration_days for f in facts
                 if f.context and f.context.period_type == "duration"}
    assert 363 in durations
    assert facts, "no annual facts survived the window"


@pytest.mark.parametrize(("days", "accepted"), [
    (364, True),    # 52-week year
    (371, True),    # 53-week year
    (363, True),    # Apple FY2024
    (365, True),    # calendar year
    (ANNUAL_MIN_DAYS, True),
    (ANNUAL_MAX_DAYS, True),
    (ANNUAL_MIN_DAYS - 1, False),
    (ANNUAL_MAX_DAYS + 1, False),
    (92, False),    # a quarter
    (183, False),   # a half year
    (730, False),   # two years
])
def test_annual_duration_window(days, accepted):
    start = date(2024, 1, 1)
    context = Context(id="c", start=start, end=start.replace() + __import__(
        "datetime").timedelta(days=days))
    assert context.is_annual_duration() is accepted


def test_short_and_transition_periods_are_excluded():
    """A quarterly context ending at the period end must not be an annual fact."""
    submission = load("aapl_fy2024")
    quarterly = [f for f in submission.facts
                 if f.context and f.context.period_type == "duration"
                 and f.context.duration_days is not None
                 and f.context.duration_days < ANNUAL_MIN_DAYS]
    facts = annual_facts(submission, date(2024, 9, 28))
    assert not ({id(f) for f in quarterly} & {id(f) for f in facts})


def test_instant_facts_are_taken_at_period_end():
    """Balance-sheet items are instants; the prior-year column must not win."""
    facts = annual("aapl_fy2024")
    assets = [f for f in facts if f.concept_local == "Assets"]
    assert assets
    assert value_of(facts, "Assets") == Decimal("364980000000")
    for fact in assets:
        assert fact.context.instant is not None
        assert abs((fact.context.instant - date(2024, 9, 28)).days) <= 3


def test_prior_year_columns_are_excluded_by_the_window():
    """The same filing carries FY2023 and FY2022; only FY2024 may come back."""
    submission = load("aapl_fy2024")
    all_revenue = {f.value for f in submission.facts
                   if f.concept_local == "RevenueFromContractWithCustomerExcludingAssessedTax"
                   and not f.is_dimensional}
    facts = annual_facts(submission, date(2024, 9, 28))
    annual_revenue = {f.value for f in facts
                      if f.concept_local == "RevenueFromContractWithCustomerExcludingAssessedTax"}
    assert annual_revenue == {Decimal("391035000000")}
    assert len(all_revenue) > len(annual_revenue), (
        "the fixture should contain prior-year columns for the window to exclude"
    )


def test_units_are_resolved_to_labels():
    facts = annual("aapl_fy2024")
    revenue = next(f for f in facts
                   if f.concept_local == "RevenueFromContractWithCustomerExcludingAssessedTax")
    eps = next(f for f in facts if f.concept_local == "EarningsPerShareDiluted")
    assert revenue.unit == "USD"
    assert eps.unit == "USD/shares", (
        "EPS must not look like a USD amount, or the calculator cannot refuse "
        "to add it to revenue"
    )


# ── T2-03: pooled submissions ──────────────────────────────────────────────

def test_wfc_resolves_only_when_the_submission_is_pooled():
    """Spec 6.4 rule 1. The wrapper holds the contexts, the EX-13 the facts."""
    entry = BY_NAME["wfc_fy2024_split"]
    files = [FIXTURES / name for name in entry["files"]]
    assert len(files) == 2, "the split fixture must be two documents"
    period_end = date(2024, 12, 31)

    pooled = annual_facts(parse_submission(files), period_end)
    assert value_of(pooled, "RevenuesNetOfInterestExpense") == Decimal("82296000000")
    assert value_of(pooled, "NetIncomeLoss") == Decimal("19722000000")

    alone = [len(annual_facts(parse_submission([f]), period_end)) for f in files]
    assert all(count < len(pooled) for count in alone), (
        f"pooling gained nothing: pooled {len(pooled)}, separately {alone}"
    )
    # Specifically: the exhibit's facts have no periods without the wrapper.
    exhibit = parse_submission([files[1]])
    unresolved = [f for f in exhibit.facts if f.context is None]
    assert unresolved, (
        "the EX-13 exhibit should reference contexts it does not define"
    )


def test_pooling_keeps_the_first_definition_of_a_context_id():
    """A later document must not silently rebind an id the wrapper already set."""
    entry = BY_NAME["wfc_fy2024_split"]
    files = [FIXTURES / name for name in entry["files"]]
    first = parse_submission([files[0]])
    pooled = parse_submission(files)
    for cid, ctx in first.contexts.items():
        assert pooled.contexts[cid] == ctx


# ── dual-concept and bank cases, which P2-05/P2-06 build on ────────────────

def test_blackrock_tags_two_different_revenues():
    """D0.3: a fixed global priority list understates BLK revenue by 37%."""
    facts = annual("blk_fy2024_dual_revenue")
    revenues = value_of(facts, "Revenues")
    contract = value_of(facts, "RevenueFromContractWithCustomerExcludingAssessedTax")
    assert revenues == Decimal("12794000000")
    assert contract == Decimal("20407000000")
    assert revenues < contract
    # The understatement a naive priority list would produce.
    assert abs(1 - revenues / contract) > Decimal("0.35")


def test_bank_fixture_offers_several_revenue_candidates():
    submission = load("bac_fy2024")
    names = {f.concept_local for f in submission.consolidated_facts}
    assert {"Revenues", "InterestAndDividendIncomeOperating"} <= names


def test_amendment_is_identifiable_and_may_restate_nothing():
    """D14/T2-05: a 10-K/A does not necessarily carry financial facts.

    This GS amendment has 64 facts and no annual financial facts at all — it
    re-files an exhibit. Resolving "the latest FY2023 filing" to it would
    return nothing, so the resolver must fall back to the 10-K it amends.
    """
    submission = load("gs_fy2023_10ka")
    dei = submission.dei()
    assert dei["DocumentType"] == "10-K/A"
    assert dei["AmendmentFlag"] == "true"
    assert annual_facts(submission, date(2023, 12, 31)) == []


# ── DEI (P2-03) ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "aapl_fy2024", "nflx_fy2024_thousands", "blk_fy2024_dual_revenue",
    "bac_fy2024", "wfc_fy2024_split", "gs_fy2023_10ka",
])
def test_all_six_dei_concepts_are_extracted(name):
    """P2-03's six concepts, on every filer fixture.

    They are not all in ix:hidden: for Apple, DocumentFiscalYearFocus and
    EntityRegistrantName are hidden while the other four sit inline on the
    cover page. Reading only the hidden block finds four of six.
    """
    dei = load(name).dei()
    missing = [c for c in (
        "DocumentFiscalYearFocus", "DocumentPeriodEndDate", "DocumentType",
        "AmendmentFlag", "EntityRegistrantName", "EntityCentralIndexKey",
    ) if c not in dei]
    assert not missing, f"{name} is missing {missing}"


def test_hidden_facts_are_collected():
    """ix:hidden holds thousands of us-gaap facts for the banks, not just dei."""
    submission = load("aapl_fy2024")
    assert any(f.hidden for f in submission.facts)
