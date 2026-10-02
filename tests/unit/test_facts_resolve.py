"""T2-04, T2-05, T2-09 — the resolution policy, as_of, amendments, golden values.

Everything here runs offline from the committed fixtures: each test builds a
small FactsStore and CatalogStore in tmp_path by parsing
``tests/fixtures/ixbrl/`` and declaring the catalog rows the filings imply. No
network, no `.env`, no shared database — so a test cannot pass because of
something a previous run left behind.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from catalog.store import CatalogStore, Filing
from facts.concepts import load_registry
from facts.extract import annual_facts, parse_submission
from facts.resolve import (
    REASON_AMBIGUOUS,
    REASON_COMPANY_NOT_FOUND,
    REASON_FUTURE_PERIOD,
    REASON_METRIC_NOT_IN_FILING,
    REASON_METRIC_NOT_SUPPORTED,
    REASON_PERIOD_NOT_COVERED,
    REASON_PERIOD_NOT_FILED,
    FactsResolver,
    PeriodSelector,
    parse_as_of,
)
from facts.store import FactsStore, source_hash

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ixbrl"
MANIFEST = {e["name"]: e for e in json.loads(
    (FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))}

# The catalog rows the fixtures stand for. filing_date values are the real ones
# from the catalog, because the as_of tests depend on them being true.
FIXTURE_FILINGS = {
    "aapl_fy2024": {"ticker": "AAPL", "cik": 320193, "entity_name": "Apple Inc.",
                        "accession": "0000320193-24-000123", "form_type": "10-K",
                        "period_end": "2024-09-28", "fiscal_label": 2024,
                        "filing_date": "2024-11-01"},
    "nflx_fy2024_thousands": {"ticker": "NFLX", "cik": 1065280,
                                  "entity_name": "Netflix, Inc.",
                                  "accession": "0001065280-25-000044",
                                  "form_type": "10-K", "period_end": "2024-12-31",
                                  "fiscal_label": 2024, "filing_date": "2025-01-27"},
    "blk_fy2024_dual_revenue": {"ticker": "BLK", "cik": 2012383,
                                    "entity_name": "BlackRock, Inc.",
                                    "accession": "0000950170-25-026584",
                                    "form_type": "10-K", "period_end": "2024-12-31",
                                    "fiscal_label": 2024, "filing_date": "2025-02-25"},
    "bac_fy2024": {"ticker": "BAC", "cik": 70858,
                       "entity_name": "Bank of America Corporation",
                       "accession": "0000070858-25-000139", "form_type": "10-K",
                       "period_end": "2024-12-31", "fiscal_label": 2024,
                       "filing_date": "2025-02-25"},
    "wfc_fy2024_split": {"ticker": "WFC", "cik": 72971,
                             "entity_name": "WELLS FARGO & COMPANY/MN",
                             "accession": "0000072971-25-000066", "form_type": "10-K",
                             "period_end": "2024-12-31", "fiscal_label": 2024,
                             "filing_date": "2025-02-21"},
    "gs_fy2023_10ka": {"ticker": "GS", "cik": 886982,
                           "entity_name": "The Goldman Sachs Group, Inc.",
                           "accession": "0000886982-24-000012", "form_type": "10-K/A",
                           "period_end": "2023-12-31", "fiscal_label": 2023,
                           "filing_date": "2024-02-28",
                           "amends": "0000886982-24-000006"},
}


@pytest.fixture
def world(tmp_path):
    """A catalog + facts store built from the fixtures, and a resolver on them.

    `today` is pinned so "is fiscal 2030 in the future" cannot change answer
    with the calendar — the same injectable-clock requirement as P3-02.
    """
    catalog = CatalogStore(tmp_path / "catalog.sqlite")
    store = FactsStore(tmp_path / "facts.sqlite")

    filings = []
    for name, spec in FIXTURE_FILINGS.items():
        entry = MANIFEST[name]
        paths = [FIXTURES / f for f in entry["files"]]
        submission = parse_submission(paths, accession=spec["accession"])
        facts = annual_facts(submission,
                             date.fromisoformat(spec["period_end"]))
        filings.append(Filing(fiscal_label_source="dei", primary_doc=entry["files"][0],
                              **spec))
        store.replace_submission(
            accession=spec["accession"], ticker=spec["ticker"], facts=facts,
            source_hash_value=source_hash(paths), documents=entry["files"],
            cik=spec["cik"], form_type=spec["form_type"],
            period_end=spec["period_end"], fiscal_label=spec["fiscal_label"],
        )
    catalog.upsert(filings)

    return FactsResolver(catalog=catalog, store=store, registry=load_registry(),
                         statements=None, today=date(2026, 10, 2))


def value(world, ticker, metric, label=2024, **kw):
    outcome = world.resolve(ticker, metric, PeriodSelector.fiscal_label(label), **kw)
    assert outcome.resolved, f"{ticker} {metric} FY{label}: {outcome.reason} " \
                             f"{outcome.detail}"
    return outcome.value


# ── T2-09: golden values (>= 20 tuples, all oracle-verified) ───────────────
#
# Every value below is the SEC's own, not mine. They were produced by
# resolving each (ticker, metric) against this fixture-built store and then
# looking up the CHOSEN CONCEPT in companyfacts for the same accession
# (reports/phase2/crosscheck.csv), keeping only pairs that matched exactly.
#
# Doing it the other way round — writing down the numbers I remembered — is how
# the first draft of this list got four of them wrong. It also matters that the
# lookup is by the chosen concept: BlackRock's `us-gaap:Revenues` (12,794M) is
# ALSO oracle-exact, and a list built by concept name alone would have enshrined
# the component instead of the 20,407M total the override selects.

GOLDEN = [
    # Apple FY2024 — technology, 52/53-week year
    ("AAPL", "revenue",             2024, "391035000000"),
    ("AAPL", "net_income",          2024, "93736000000"),
    ("AAPL", "operating_income",    2024, "123216000000"),
    ("AAPL", "gross_profit",        2024, "180683000000"),
    ("AAPL", "rd_expense",          2024, "31370000000"),
    ("AAPL", "total_assets",        2024, "364980000000"),
    ("AAPL", "stockholders_equity", 2024, "56950000000"),
    ("AAPL", "eps_diluted",         2024, "6.08"),
    # Netflix FY2024 — reports in THOUSANDS
    ("NFLX", "revenue",             2024, "39000966000"),
    ("NFLX", "net_income",          2024, "8711631000"),
    ("NFLX", "operating_income",    2024, "10417614000"),
    ("NFLX", "total_assets",        2024, "53630374000"),
    ("NFLX", "eps_diluted",         2024, "19.83"),
    # BlackRock FY2024 — asset manager; revenue comes from the override
    ("BLK",  "revenue",             2024, "20407000000"),
    ("BLK",  "net_income",          2024, "6369000000"),
    ("BLK",  "operating_income",    2024, "7574000000"),
    ("BLK",  "total_assets",        2024, "138615000000"),
    # Bank of America FY2024 — bank
    ("BAC",  "revenue",             2024, "101887000000"),
    ("BAC",  "net_income",          2024, "27132000000"),
    ("BAC",  "total_assets",        2024, "3261519000000"),
    ("BAC",  "stockholders_equity", 2024, "295559000000"),
    # Wells Fargo FY2024 — facts in an EX-13 exhibit
    ("WFC",  "revenue",             2024, "82296000000"),
    ("WFC",  "net_income",          2024, "19722000000"),
    ("WFC",  "total_assets",        2024, "1929845000000"),
    ("WFC",  "stockholders_equity", 2024, "179120000000"),
]


def test_golden_set_has_at_least_twenty_tuples():
    assert len(GOLDEN) >= 20, "T2-09 requires at least 20 (ticker, metric, year)"
    assert len({(t, m) for t, m, _, _ in GOLDEN}) >= 20


@pytest.mark.parametrize(("ticker", "metric", "label", "expected"), GOLDEN,
                         ids=[f"{t}-{m}-{y}" for t, m, y, _ in GOLDEN])
def test_golden_value_resolves_exactly(world, ticker, metric, label, expected):
    assert value(world, ticker, metric, label) == Decimal(expected)


def test_netflix_magnitude_is_billions_not_trillions(world):
    """K2 regression at the resolver level, not just the formatter."""
    revenue = value(world, "NFLX", "revenue")
    assert Decimal("3.8e10") < revenue < Decimal("4.0e10")


# ── T2-04: overrides, and ambiguity without one ────────────────────────────

def test_blackrock_override_is_honoured(world):
    """D0.3: the override picks total revenue, not the 12,794 component."""
    outcome = world.resolve("BLK", "revenue", PeriodSelector.fiscal_label(2024))
    assert outcome.resolved
    assert outcome.value == Decimal("20407000000")
    assert outcome.selection == "override"
    assert outcome.concept.endswith("RevenueFromContractWithCustomerExcludingAssessedTax")
    assert "0000950170-25-026584" in outcome.override_evidence
    assert "12,794" in outcome.override_evidence


def registry_with(metric_name: str, **changes):
    """A copy of the real registry with one metric altered.

    `Metric` and `Registry` are frozen dataclasses, so this uses
    `dataclasses.replace` rather than monkeypatching an attribute — which is
    the point of freezing them: the registry a resolver was built with cannot
    change under it at runtime.
    """
    from dataclasses import replace
    registry = load_registry()
    metric = replace(registry.metric(metric_name), **changes)
    return replace(registry, metrics={**registry.metrics, metric_name: metric})


def test_without_the_override_blackrock_is_ambiguous(world):
    """The negative control for D2-02: tiers must not paper over a real tie.

    Both BLK revenue concepts are in tier 1 with different values, so removing
    the override has to produce `ambiguous_concept` — never a silent pick of
    whichever is listed first. Without this test, "zero ambiguous rows in the
    coverage report" would be indistinguishable from "the ambiguity path is
    dead code" (CLAUDE.md rule 15).
    """
    world = FactsResolver(catalog=world.catalog, store=world.store,
                          registry=registry_with("revenue", overrides={}),
                          statements=None, today=date(2026, 10, 2))
    outcome = world.resolve("BLK", "revenue", PeriodSelector.fiscal_label(2024))
    assert not outcome.resolved
    assert outcome.reason == REASON_AMBIGUOUS
    assert len(outcome.candidates) == 2
    # The message must be readable: plain digits, not Decimal's exponent form.
    values = " ".join(outcome.candidate_values.values())
    assert "12794000000" in values and "20407000000" in values, values
    assert "E+" not in values, f"exponent notation in a user-facing message: {values}"
    assert "evidence" in outcome.detail


def test_bank_sector_candidates_are_used(world):
    """BAC and WFC resolve through the bank list, not the default one."""
    bac = world.resolve("BAC", "revenue", PeriodSelector.fiscal_label(2024))
    wfc = world.resolve("WFC", "revenue", PeriodSelector.fiscal_label(2024))
    assert bac.concept == "us-gaap:Revenues"
    assert wfc.concept == "us-gaap:RevenuesNetOfInterestExpense"
    for outcome in (bac, wfc):
        assert all("RevenueFromContract" not in c
                   for c in outcome.candidates_considered)


def test_tier_two_is_only_reached_when_tier_one_is_absent(world):
    """net_income must never come back as ProfitLoss while NetIncomeLoss exists."""
    for ticker in ("AAPL", "BLK", "BAC", "WFC", "NFLX"):
        outcome = world.resolve(ticker, "net_income",
                                PeriodSelector.fiscal_label(2024))
        assert outcome.resolved
        assert outcome.concept == "us-gaap:NetIncomeLoss", ticker


def test_an_override_naming_an_absent_concept_abstains(world):
    """A stale override is a contradiction to surface, not a cue to fall back."""
    from facts.concepts import Override
    registry = registry_with("net_income", overrides={
        "AAPL": (Override(ticker="AAPL", concept="us-gaap:NotATaggedConcept",
                          evidence="deliberately wrong, for this test"),)
    })
    world = FactsResolver(catalog=world.catalog, store=world.store,
                          registry=registry, statements=None,
                          today=date(2026, 10, 2))
    outcome = world.resolve("AAPL", "net_income", PeriodSelector.fiscal_label(2024))
    assert not outcome.resolved
    assert outcome.reason == REASON_METRIC_NOT_IN_FILING
    assert "override" in outcome.detail and "stale" in outcome.detail


# ── T2-05: as_of eligibility, amendments, the restated flag ────────────────

def test_resolving_before_the_filing_date_abstains(world):
    """G2: never answer from a filing the asker could not have seen.

    Apple's FY2024 10-K was filed 2024-11-01. On 2024-10-01 the fiscal year
    had ended but the filing did not exist.
    """
    outcome = world.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024),
                            as_of="2024-10-01")
    assert not outcome.resolved
    assert outcome.reason == REASON_PERIOD_NOT_FILED
    assert "2024-11-01" in outcome.detail


def test_resolving_on_the_filing_date_succeeds(world):
    """Eligibility is inclusive of the filing date itself."""
    assert world.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024),
                         as_of="2024-11-01").resolved


def test_latest_respects_as_of(world):
    """`latest` must mean latest-as-of, not latest-we-have."""
    outcome = world.resolve("AAPL", "revenue", PeriodSelector.latest(),
                            as_of="2024-10-31")
    assert not outcome.resolved, (
        "with only FY2024 in this fixture world, nothing was public on "
        "2024-10-31 and `latest` must abstain rather than look ahead")


def test_amendment_is_recognised_and_restates_nothing(world):
    """D14, the real case. The GS FY2023 10-K/A carries no financial facts.

    Resolving FY2023 from the amendment alone has to abstain — and not with a
    reason that implies Goldman does not report revenue.
    """
    outcome = world.resolve("GS", "revenue", PeriodSelector.fiscal_label(2023))
    assert not outcome.resolved
    assert outcome.reason == REASON_METRIC_NOT_IN_FILING


def test_an_amendment_supersedes_its_original_when_it_restates(world, tmp_path):
    """D14: newest-filed wins, and `restated` says so.

    Built here rather than from a fixture because no bundled filer has a 10-K/A
    that actually restates a registry metric — which is itself the finding in
    `test_amendment_is_recognised_and_restates_nothing`.
    """
    from facts.extract import Context, Fact

    catalog, store = world.catalog, world.store
    context = Context(id="c1", start=date(2024, 1, 1), end=date(2024, 12, 31))

    def make(value: str):
        return Fact(concept="us-gaap:Revenues", context_id="c1",
                    source_doc="doc.htm", value=Decimal(value), unit="USD",
                    scale_raw="6", context=context)

    catalog.upsert([
        Filing(accession="ACC-ORIG", ticker="ZZ", cik=1, entity_name="Z Inc.",
               form_type="10-K", period_end="2024-12-31", fiscal_label=2024,
               fiscal_label_source="dei", filing_date="2025-02-01"),
        Filing(accession="ACC-AMEND", ticker="ZZ", cik=1, entity_name="Z Inc.",
               form_type="10-K/A", period_end="2024-12-31", fiscal_label=2024,
               fiscal_label_source="dei", filing_date="2025-06-01",
               amends="ACC-ORIG"),
    ])
    store.replace_submission(accession="ACC-ORIG", ticker="ZZ",
                             facts=[make("1000000000")],
                             source_hash_value="h1", period_end="2024-12-31",
                             fiscal_label=2024, form_type="10-K")
    store.replace_submission(accession="ACC-AMEND", ticker="ZZ",
                             facts=[make("1200000000")],
                             source_hash_value="h2", period_end="2024-12-31",
                             fiscal_label=2024, form_type="10-K/A")

    outcome = world.resolve("ZZ", "revenue", PeriodSelector.fiscal_label(2024))
    assert outcome.resolved
    assert outcome.value == Decimal("1200000000"), "the amendment must win"
    assert outcome.restated is True
    assert outcome.accession == "ACC-AMEND"

    # ...but not before it was filed.
    earlier = world.resolve("ZZ", "revenue", PeriodSelector.fiscal_label(2024),
                            as_of="2025-03-01")
    assert earlier.resolved
    assert earlier.value == Decimal("1000000000"), (
        "on 2025-03-01 the amendment did not exist yet")
    assert earlier.restated is False
    assert earlier.amendment_accession is None


def test_amendment_accession_is_reported_even_when_it_restates_nothing(world):
    """So an answer can say "a 10-K/A exists but did not change this figure"."""
    from facts.extract import Context, Fact
    context = Context(id="c1", start=date(2024, 1, 1), end=date(2024, 12, 31))
    world.catalog.upsert([
        Filing(accession="ACC-O2", ticker="YY", cik=2, entity_name="Y Inc.",
               form_type="10-K", period_end="2024-12-31", fiscal_label=2024,
               fiscal_label_source="dei", filing_date="2025-02-01"),
        Filing(accession="ACC-A2", ticker="YY", cik=2, entity_name="Y Inc.",
               form_type="10-K/A", period_end="2024-12-31", fiscal_label=2024,
               fiscal_label_source="dei", filing_date="2025-06-01",
               amends="ACC-O2"),
    ])
    world.store.replace_submission(
        accession="ACC-O2", ticker="YY",
        facts=[Fact(concept="us-gaap:Revenues", context_id="c1",
                    source_doc="d.htm", value=Decimal("5000000000"),
                    unit="USD", context=context)],
        source_hash_value="h3", period_end="2024-12-31", fiscal_label=2024,
        form_type="10-K")
    world.store.replace_submission(accession="ACC-A2", ticker="YY", facts=[],
                                   source_hash_value="h4",
                                   period_end="2024-12-31", fiscal_label=2024,
                                   form_type="10-K/A")

    outcome = world.resolve("YY", "revenue", PeriodSelector.fiscal_label(2024))
    assert outcome.resolved
    assert outcome.value == Decimal("5000000000")
    assert outcome.restated is False, "the figure came from the original"
    assert outcome.amendment_accession == "ACC-A2", (
        "the reader still needs to know an amendment exists")


# ── abstain reasons are each reachable and distinct ────────────────────────

def test_unknown_company_abstains(world):
    outcome = world.resolve("ZZZZ", "revenue", PeriodSelector.latest())
    assert outcome.reason == REASON_COMPANY_NOT_FOUND


def test_unknown_metric_abstains(world):
    outcome = world.resolve("AAPL", "ebitda", PeriodSelector.latest())
    assert outcome.reason == REASON_METRIC_NOT_SUPPORTED
    assert "revenue" in outcome.detail      # lists what IS supported


def test_future_period_is_distinct_from_not_covered(world):
    """A year that has not happened is not the same as one we never ingested."""
    future = world.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2030))
    past = world.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2015))
    assert future.reason == REASON_FUTURE_PERIOD
    assert "2026-10-02" in future.detail        # the injected clock
    assert past.reason == REASON_PERIOD_NOT_COVERED
    assert "2024" in past.detail                # says what IS covered


def test_metric_absent_from_the_filing_abstains(world):
    """Banks have no gross-profit line; that is not an error."""
    outcome = world.resolve("BAC", "gross_profit",
                            PeriodSelector.fiscal_label(2024))
    assert outcome.reason == REASON_METRIC_NOT_IN_FILING


# ── selectors and as_of parsing ────────────────────────────────────────────

def test_period_selectors_agree(world):
    by_label = world.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024))
    by_end = world.resolve("AAPL", "revenue",
                           PeriodSelector.period_end("2024-09-28"))
    by_calendar = world.resolve("AAPL", "revenue",
                                PeriodSelector.calendar_year(2024))
    by_latest = world.resolve("AAPL", "revenue", PeriodSelector.latest())
    values = {o.value for o in (by_label, by_end, by_calendar, by_latest)}
    assert values == {Decimal("391035000000")}


def test_period_selector_validates_its_arguments():
    with pytest.raises(ValueError):
        PeriodSelector("quarter", 1)
    with pytest.raises(ValueError):
        PeriodSelector("fiscal_label")


def test_malformed_as_of_raises_rather_than_disabling_the_filter():
    """A silent None here would turn the look-ahead guard into no guard."""
    assert parse_as_of(None) is None
    assert parse_as_of("") is None
    assert parse_as_of("2024-11-01") == "2024-11-01"
    assert parse_as_of("2024-11-01T12:00:00") == "2024-11-01"
    with pytest.raises(ValueError):
        parse_as_of("not a date")
    with pytest.raises(ValueError):
        parse_as_of("11/01/2024")


def test_definition_note_names_the_concept_and_the_period(world):
    outcome = world.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024))
    note = outcome.definition_note()
    assert "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" in note
    assert "2024-09-28" in note


# ── D23: validation_status is informational only ──────────────────────────

def test_validation_status_never_changes_a_resolved_value(world, tmp_path):
    """D23. v1's parsed statement sections are unreliable, so nothing
    user-visible may depend on them.

    The same resolution is run with statement text available and with it
    switched off. Only `validation_status` and its detail may differ; the
    value, concept, filing and selection must be identical.
    """
    from facts.resolve import UNVALIDATED, StatementText

    without = world.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024))
    with_text = FactsResolver(
        catalog=world.catalog, store=world.store, registry=world.registry,
        statements=StatementText(tmp_path),       # empty dir: nothing to find
        today=date(2026, 10, 2),
    ).resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024))

    assert without.resolved and with_text.resolved
    for field in ("value", "concept", "accession", "selection", "unit",
                  "fiscal_label", "end_date", "restated"):
        assert getattr(without, field) == getattr(with_text, field), field
    assert without.validation_status == UNVALIDATED


def test_a_conflicting_validation_does_not_suppress_the_answer(world, tmp_path):
    """A `conflict` is a note for a human, not a veto on the number."""
    from facts.resolve import CONFLICT, StatementText

    # A statement section with plenty of numbers, none of them ours.
    parsed = tmp_path / "AAPL_2024.json"
    filler = " ".join(str(1000 + i) for i in range(200))
    parsed.write_text(json.dumps({
        "sections": [{"title": "Consolidated Statements of Operations",
                      "content_blocks": [{"text": filler, "raw_table": None}]}]
    }), encoding="utf-8")

    resolver = FactsResolver(catalog=world.catalog, store=world.store,
                             registry=world.registry,
                             statements=StatementText(tmp_path),
                             today=date(2026, 10, 2))
    outcome = resolver.resolve("AAPL", "revenue", PeriodSelector.fiscal_label(2024))
    assert outcome.resolved, "a conflict must not turn into an abstention"
    assert outcome.value == Decimal("391035000000")
    assert outcome.validation_status == CONFLICT
    assert "not found among" in outcome.validation_detail
