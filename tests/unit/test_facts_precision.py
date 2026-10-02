"""T2-13 — precision preference (D22).

A filing can tag one concept, in one context, more than once at different
precisions. Apple's FY2024 10-K reports UnrecognizedTaxBenefits for context
c-21 as 22,000,000,000 with `decimals="-8"` (the narrative sentence) and as
22,038,000,000 with `decimals="-6"` (the tax-footnote table). Before D22 the
extractor kept whichever came first in document order.

The four claims the spec asks to be proved:

1. instances that agree within the coarser declared precision resolve to the
   larger-`decimals` one;
2. instances that disagree beyond it are **not** merged;
3. a negative control — turning the rule off brings the old behaviour back, so
   the tests above are measuring the rule and not something else;
4. the rebuild after the change is deterministic.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from facts.extract import (
    Context,
    Fact,
    annual_facts,
    decimals_rank,
    decimals_tolerance,
    parse_submission,
    prefer_precise_instances,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ixbrl"

INSTANT = Context(id="c-1", instant=date(2024, 9, 28))
DURATION = Context(id="c-2", start=date(2023, 10, 1), end=date(2024, 9, 28))


def fact(value: str, decimals: str | None, *, concept="us-gaap:Thing",
         context=INSTANT, unit="USD", element_id="f-1") -> Fact:
    return Fact(
        concept=concept, context_id=context.id, source_doc="d.htm",
        value=Decimal(value), unit=unit, decimals=decimals,
        element_id=element_id, context=context,
    )


# ── decimals semantics ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "rank"), [
    ("-8", -8), ("-6", -6), ("2", 2), ("0", 0), ("-3", -3),
    ("INF", 10_000), ("inf", 10_000), (" -6 ", -6),
    (None, None), ("", None), ("not a number", None),
])
def test_decimals_rank(raw, rank):
    assert decimals_rank(raw) == rank


@pytest.mark.parametrize(("raw", "tolerance"), [
    ("-8", Decimal("50000000")),      # nearest 100M -> +/- 50M
    ("-6", Decimal("500000")),        # nearest 1M   -> +/- 500k
    ("2", Decimal("0.005")),
    ("INF", Decimal(0)),              # exact
])
def test_decimals_tolerance_is_half_a_unit(raw, tolerance):
    assert decimals_tolerance(raw) == tolerance


def test_undeclared_precision_has_no_tolerance():
    assert decimals_tolerance(None) is None
    assert decimals_tolerance("") is None


# ── claim 1: agreeing instances collapse to the precise one ───────────────

def test_the_more_precise_instance_wins():
    """The real Apple case, with its real numbers."""
    coarse = fact("22000000000", "-8", element_id="f-prose")
    precise = fact("22038000000", "-6", element_id="f-table")
    kept, merges = prefer_precise_instances([coarse, precise])

    assert [f.value for f in kept] == [Decimal("22038000000")]
    assert len(merges) == 1
    assert merges[0].kept_value == Decimal("22038000000")
    assert merges[0].kept_decimals == "-6"
    assert merges[0].dropped_value == Decimal("22000000000")
    assert merges[0].dropped_decimals == "-8"
    assert merges[0].changed_the_value is True


def test_document_order_does_not_decide():
    """The old behaviour was "first in document order wins"."""
    coarse = fact("22000000000", "-8", element_id="f-001")
    precise = fact("22038000000", "-6", element_id="f-999")
    for order in ([coarse, precise], [precise, coarse]):
        kept, _ = prefer_precise_instances(order)
        assert [f.value for f in kept] == [Decimal("22038000000")], order


def test_exact_duplicates_collapse_without_a_recorded_merge():
    """Most duplicates are the same number tagged in several places."""
    a = fact("93736000000", "-6", element_id="f-102")
    b = fact("93736000000", "-6", element_id="f-117")
    c = fact("93736000000", "-6", element_id="f-265")
    kept, merges = prefer_precise_instances([a, b, c])
    assert len(kept) == 1
    assert merges == [], "nothing changed, so nothing to report"


def test_inf_beats_every_integer_precision():
    kept, _ = prefer_precise_instances([
        fact("1000000", "-6", element_id="f-1"),
        fact("1000001", "INF", element_id="f-2"),
    ])
    assert [f.value for f in kept] == [Decimal("1000001")]


# ── claim 2: real disagreements are never merged ──────────────────────────

def test_values_disagreeing_beyond_the_declared_precision_are_kept():
    """decimals="-6" tolerates +/-500k; 10,000,000 apart is a contradiction."""
    a = fact("22000000000", "-6", element_id="f-1")
    b = fact("22010000000", "-6", element_id="f-2")
    kept, merges = prefer_precise_instances([a, b])
    assert len(kept) == 2, "a contradiction must survive for the resolver to see"
    assert merges == []


def test_a_disagreement_reaches_the_resolver_as_ambiguous(tmp_path):
    """The consequence of claim 2, end to end."""
    from catalog.store import CatalogStore, Filing
    from facts.concepts import load_registry
    from facts.resolve import REASON_AMBIGUOUS, FactsResolver, PeriodSelector
    from facts.store import FactsStore

    catalog = CatalogStore(tmp_path / "c.sqlite")
    store = FactsStore(tmp_path / "f.sqlite")
    catalog.upsert([Filing(
        accession="ACC", ticker="ZZ", cik=1, entity_name="Z Inc.",
        form_type="10-K", period_end="2024-09-28", fiscal_label=2024,
        fiscal_label_source="dei", filing_date="2024-11-01")])

    disagreeing = [
        fact("391035000000", "-6", concept="us-gaap:Revenues",
             context=DURATION, element_id="f-1"),
        fact("391999000000", "-6", concept="us-gaap:Revenues",
             context=DURATION, element_id="f-2"),
    ]
    kept, _ = prefer_precise_instances(disagreeing)
    assert len(kept) == 2
    store.replace_submission(accession="ACC", ticker="ZZ", facts=kept,
                             source_hash_value="h", period_end="2024-09-28",
                             fiscal_label=2024, form_type="10-K")

    resolver = FactsResolver(catalog=catalog, store=store,
                             registry=load_registry(), statements=None,
                             today=date(2026, 10, 2))
    outcome = resolver.resolve("ZZ", "revenue", PeriodSelector.fiscal_label(2024))
    assert not outcome.resolved
    assert outcome.reason == REASON_AMBIGUOUS


def test_undeclared_precision_is_never_merged():
    """Without @decimals there is no basis for calling them one quantity."""
    a = fact("22000000000", None, element_id="f-1")
    b = fact("22038000000", "-6", element_id="f-2")
    kept, merges = prefer_precise_instances([a, b])
    assert len(kept) == 2
    assert merges == []


def test_different_contexts_are_different_quantities():
    """Two years of the same concept must not collapse into one."""
    other = Context(id="c-2", instant=date(2023, 9, 30))
    a = fact("22038000000", "-6", context=INSTANT, element_id="f-1")
    b = fact("19454000000", "-6", context=other, element_id="f-2")
    kept, _ = prefer_precise_instances([a, b])
    assert len(kept) == 2


def test_different_units_are_different_quantities():
    a = fact("6.08", "2", unit="USD/shares", element_id="f-1")
    b = fact("6.08", "2", unit="USD", element_id="f-2")
    kept, _ = prefer_precise_instances([a, b])
    assert len(kept) == 2


# ── claim 3: the negative control ──────────────────────────────────────────

def test_turning_the_rule_off_restores_the_old_behaviour():
    """A rule whose effect cannot be switched off has not been shown to have one.

    Parses the real Apple fixture both ways and asserts the counts differ.
    """
    paths = [FIXTURES / "aapl_fy2024.htm"]
    submission = parse_submission(paths)
    period_end = date(2024, 9, 28)

    without = annual_facts(submission, period_end, prefer_precise=False)
    with_rule = annual_facts(submission, period_end, prefer_precise=True)

    assert len(with_rule) < len(without), (
        "the rule dropped nothing, so these tests prove nothing about it")
    # ...and the kept facts are a subset of the originals, never invented.
    kept_ids = {id(f) for f in with_rule}
    assert kept_ids <= {id(f) for f in without}


def test_the_rule_changes_a_real_value_in_the_real_corpus():
    """Not just a count: the specific number from the real filing.

    `aapl_fy2024_precision.htm` exists for this test. It carries both
    UnrecognizedTaxBenefits instances Apple tags for one context, which is the
    exact defect D22 names, so this is a regression test on the real document
    rather than on a constructed example.
    """
    submission = parse_submission([FIXTURES / "aapl_fy2024_precision.htm"])
    instances = [f for f in submission.facts
                 if f.concept_local == "UnrecognizedTaxBenefits"
                 and not f.is_dimensional and f.value is not None]
    assert len(instances) >= 2, "the fixture must carry both instances"

    by_context: dict[str, list] = {}
    for f in instances:
        by_context.setdefault(f.context_id, []).append(f)
    doubled = {k: v for k, v in by_context.items() if len(v) > 1}
    assert doubled, "the fixture must carry a context tagged twice"

    _kept, merges = prefer_precise_instances(instances)
    assert merges, "the fixture should contain a precision merge"
    for merge in merges:
        assert merge.kept_decimals == "-6"
        assert merge.dropped_decimals == "-8"
        assert merge.changed_the_value

    # The exact values from Apple's FY2024 filing.
    kept_values = {m.kept_value for m in merges}
    dropped_values = {m.dropped_value for m in merges}
    assert Decimal("22038000000") in kept_values
    assert Decimal("22000000000") in dropped_values


def test_without_the_rule_the_coarse_value_survives():
    """The negative control for the test above, on the same real fixture."""
    submission = parse_submission([FIXTURES / "aapl_fy2024_precision.htm"])
    period_end = date(2024, 9, 28)

    def tax_values(prefer):
        return {f.value for f in annual_facts(submission, period_end,
                                              prefer_precise=prefer)
                if f.concept_local == "UnrecognizedTaxBenefits"}

    without, with_rule = tax_values(False), tax_values(True)
    assert Decimal("22000000000") in without, (
        "the coarse instance should be present when the rule is off")
    assert Decimal("22000000000") not in with_rule
    assert Decimal("22038000000") in with_rule


# ── claim 4: determinism ───────────────────────────────────────────────────

def test_the_result_is_deterministic_regardless_of_input_order():
    """A rebuild must produce identical content (T2-08 still applies)."""
    import random
    base = [
        fact("22000000000", "-8", element_id="f-a"),
        fact("22038000000", "-6", element_id="f-b"),
        fact("22038000000", "-6", element_id="f-c"),
    ]
    reference = [str(f.value) for f in prefer_precise_instances(base)[0]]
    # S311: a fixed-seed shuffle is the point — nothing here is a secret.
    rng = random.Random(1234)  # noqa: S311
    for _ in range(25):
        shuffled = base[:]
        rng.shuffle(shuffled)
        assert [str(f.value) for f in prefer_precise_instances(shuffled)[0]] \
            == reference


def test_ties_on_precision_are_broken_deterministically():
    """Same decimals, different values that agree: a total order is needed."""
    a = fact("1000000000", "-8", element_id="f-zzz")
    b = fact("1000000001", "-8", element_id="f-aaa")
    first = prefer_precise_instances([a, b])[0]
    second = prefer_precise_instances([b, a])[0]
    assert [f.element_id for f in first] == [f.element_id for f in second]


def test_rebuilding_the_same_fixture_twice_gives_identical_content(tmp_path):
    from facts.store import FactsStore, source_hash

    paths = [FIXTURES / "aapl_fy2024.htm"]
    submission = parse_submission(paths)
    facts = annual_facts(submission, date(2024, 9, 28))

    store = FactsStore(tmp_path / "f.sqlite")
    for _ in range(3):
        store.replace_submission(
            accession="ACC", ticker="AAPL", facts=facts,
            source_hash_value=source_hash(paths), period_end="2024-09-28",
            fiscal_label=2024, form_type="10-K")
    first = store.content_hash()

    # A fresh parse of the same bytes must reach the same content.
    again = annual_facts(parse_submission(paths), date(2024, 9, 28))
    store.replace_submission(
        accession="ACC", ticker="AAPL", facts=again,
        source_hash_value=source_hash(paths), period_end="2024-09-28",
        fiscal_label=2024, form_type="10-K")
    assert store.content_hash() == first


# ── the golden values must be unchanged by all this ───────────────────────

def test_registry_values_are_untouched_by_the_rule():
    """The rule must not move a number anyone can ask about.

    The P2-09 cross-check already showed 100% exactness on registry concepts
    before D22, so the only acceptable effect here is none.
    """
    submission = parse_submission([FIXTURES / "aapl_fy2024.htm"])
    period_end = date(2024, 9, 28)
    before = annual_facts(submission, period_end, prefer_precise=False)
    after = annual_facts(submission, period_end, prefer_precise=True)

    def values(facts):
        out = {}
        for f in facts:
            out.setdefault(f.concept, set()).add(f.value)
        return out

    b, a = values(before), values(after)
    for concept in ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "us-gaap:NetIncomeLoss", "us-gaap:Assets",
                    "us-gaap:EarningsPerShareDiluted"):
        if concept in b:
            assert a[concept] <= b[concept]
            assert len(a[concept]) == 1, f"{concept} should resolve to one value"
