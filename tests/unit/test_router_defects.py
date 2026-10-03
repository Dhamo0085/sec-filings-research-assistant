"""T4-08 — the three router defects the Phase 4 evaluation found (P4-11, D28).

Each test here was written from a gold item that the evaluation got wrong, and
each failed before the fix:

A. ``C-AAPL-DEBTEQUITY-2024`` ("ratio of total liabilities to stockholders'
   equity") routed to ``ratio`` with both metrics, and then answered with
   total liabilities alone: ``query._compute`` only knew how to pair two
   metrics for a margin. ``C-AMZN-FCF-2024`` ("operating cash flow less
   capital expenditures") did not route to a computation at all — "less" was
   not a difference cue.
B. "cash flow from operating activities" resolved to ``cash_and_equivalents``.
   The router already matches the longest alias first; the phrase simply was
   not an alias, so the only thing that matched was "cash". That is the worst
   kind of defect this project can have — a confident, cited, wrong number —
   so the test is table-driven over *every* multi-word alias rather than over
   the one phrase that was reported.
C. ``X-PERIOD-NOT-COVERED`` ("Apple's revenue in fiscal 2019") refused with
   ``metric_not_found_in_filing``. The filing is in the catalog; its facts
   were never extracted. The refusal named the wrong cause.

Offline: the routing tests are pure, and the resolver and pipeline tests build
a catalog and facts store from the committed fixtures.
"""

from __future__ import annotations

import pytest

import query as Q
from answering.outcome import Status
from facts.concepts import load_registry
from facts.resolve import (
    REASON_METRIC_NOT_IN_FILING,
    REASON_PERIOD_NOT_COVERED,
    PeriodSelector,
)
from llm.fake import FakeLLM
from routing.router import Computation, _metric_mentions, route
from tests.fixture_world import build_world

pytestmark = pytest.mark.unit

#: The two gold questions verbatim, so the test cannot drift from the item.
RATIO_QUESTION = ("What was Apple's ratio of total liabilities to "
                  "stockholders' equity in fiscal 2024?")
DIFFERENCE_QUESTION = ("What was Amazon's operating cash flow less capital "
                       "expenditures in fiscal 2024?")


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path, collections=True)


def deps_for(world) -> Q.Deps:
    return Q.Deps(llm=FakeLLM(default='{"found": false, "answer": ""}'),
                  catalog=world.catalog, resolver=world.resolver)


# ── A. ratio and difference ───────────────────────────────────────────────


def test_the_ratio_gold_item_routes_to_ratio_with_both_metrics():
    decided = route(RATIO_QUESTION, llm=None)
    assert decided.computation is Computation.RATIO
    assert decided.metric == "total_liabilities"
    assert decided.metric_denominator == "stockholders_equity"
    assert not decided.used_llm, "a rule decides this; it must not cost a request"


def test_the_difference_gold_item_routes_to_difference_with_both_metrics():
    decided = route(DIFFERENCE_QUESTION, llm=None)
    assert decided.computation is Computation.DIFFERENCE
    assert decided.metric == "operating_cash_flow"
    assert decided.metric_denominator == "capex"
    assert not decided.used_llm


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What was Apple's revenue minus its cost of revenue in fiscal 2024?",
         Computation.DIFFERENCE),
        ("What was Apple's total assets subtracting total liabilities in 2024?",
         Computation.DIFFERENCE),
    ],
)
def test_other_subtraction_phrasings_route_to_difference(question, expected):
    assert route(question, llm=None).computation is expected


def test_net_of_is_a_line_item_name_not_a_subtraction():
    """Negative control: "net of" names a line, it does not ask for arithmetic.

    "Total revenue, net of interest expense" is Bank of America's own income
    statement caption and a plain numeric gold item. A first attempt at the
    P4-11 A fix made it a difference.
    """
    decided = route("What was Bank of America's total revenue, net of "
                    "interest expense in fiscal 2024?", llm=None)
    assert decided.computation is not Computation.DIFFERENCE


def test_less_than_is_a_comparison_not_a_subtraction():
    """Negative control: "less" alone must not turn a question into a subtraction.

    "Was Apple's revenue less than Microsoft's" is a comparison. A naive
    ``\\bless\\b`` cue makes it a difference and the answer stops being an
    answer to the question asked.
    """
    decided = route("Was Apple's revenue less than Microsoft's in fiscal 2024?",
                    llm=None)
    assert decided.computation is not Computation.DIFFERENCE


def test_a_two_metric_ratio_is_computed_rather_than_answered_with_the_numerator(world):
    """The defect: the pipeline answered with total liabilities alone."""
    out = Q.ask("What was Bank of America's ratio of total liabilities to "
                "stockholders' equity in fiscal 2024?", deps=deps_for(world))
    assert out.status is Status.ANSWERED
    assert "ratio" in out.answer.lower()
    # 2,965,960 / 295,564 = 10.0349...; the point is that the answer is a
    # ratio of the two, not either figure on its own.
    assert "10.0" in out.answer, out.answer
    assert len(out.citations) >= 2, "both operands must be cited"


def test_a_two_metric_difference_is_computed(world):
    out = Q.ask("What was Bank of America's total assets less total "
                "liabilities in fiscal 2024?", deps=deps_for(world))
    assert out.status is Status.ANSWERED
    assert len(out.citations) >= 2


def test_a_ratio_naming_one_metric_still_abstains():
    """Unchanged by P4-11: inventing the denominator is not an improvement."""
    decided = route("What was Apple's revenue ratio in fiscal 2024?", llm=None)
    assert decided.abstain_reason is not None


# ── B. longest-match aliases ──────────────────────────────────────────────


def _multi_word_aliases():
    registry = load_registry()
    return sorted(
        (alias, metric)
        for alias, metric in registry.alias_to_metric.items()
        if len(alias.split()) > 1
    )


@pytest.mark.parametrize("alias,metric", _multi_word_aliases())
def test_every_multi_word_alias_beats_its_own_shorter_prefixes(alias, metric):
    """A question naming the full phrase must resolve to the full phrase's metric.

    The failure this guards is specific: a shorter alias of a *different*
    metric sitting inside a longer one. "cash flow from operating activities"
    contains "cash", and first-match resolution answered a cash-flow question
    with the balance-sheet cash line.
    """
    question = f"What was Apple's {alias} in fiscal 2024?"
    mentions, _ = _metric_mentions(question, load_registry())
    assert mentions, f"{alias!r} was not recognised at all"
    assert mentions[0][1] == metric, (
        f"{alias!r} resolved to {mentions[0][1]} via {mentions[0][2]!r}"
    )


def test_first_match_resolution_would_fail_this_table():
    """Negative control: the rule the fix relies on is doing real work.

    Shortest-alias-first — the order a plain dict iteration can produce — is
    run over the same table here. If it passed, the longest-first rule would
    be untested by the table above.
    """
    registry = load_registry()
    aliases = sorted(registry.alias_to_metric, key=len)  # the WRONG order
    wrong = []
    for alias, metric in _multi_word_aliases():
        haystack = f" what was apple s {alias} in fiscal 2024 "
        for candidate in aliases:
            if candidate and f" {candidate} " in haystack:
                if registry.alias_to_metric[candidate] != metric:
                    wrong.append((alias, candidate))
                break
    assert wrong, "shortest-first no longer mis-resolves anything; the table is toothless"


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What was Apple's cash flow from operating activities in fiscal 2024?",
         "operating_cash_flow"),
        ("What was Apple's net cash provided by operating activities in fiscal 2024?",
         "operating_cash_flow"),
        ("What was Apple's cash and cash equivalents in fiscal 2024?",
         "cash_and_equivalents"),
        ("What was Apple's cash in fiscal 2024?", "cash_and_equivalents"),
    ],
)
def test_the_reported_cash_flow_phrases_resolve_to_the_right_metric(question, expected):
    assert route(question, llm=None).metric == expected


# ── C. a period with no extracted facts ───────────────────────────────────


def test_a_catalogued_year_with_no_facts_is_period_not_covered(world, tmp_path):
    """The filing exists; its facts were never extracted. That is coverage.

    ``metric_not_found_in_filing`` says the filer did not report the measure,
    which is a statement about Apple. Saying it about a year this project
    simply never ingested is a claim about the filing that is not true.
    """
    from catalog.store import Filing

    world.catalog.upsert([Filing(
        ticker="AAPL", cik=320193, entity_name="Apple Inc.",
        accession="0000320193-19-000119", form_type="10-K",
        period_end="2019-09-28", fiscal_label=2019, filing_date="2019-10-31",
        fiscal_label_source="dei", primary_doc="a10-k20199282019.htm",
    )])
    outcome = world.resolver.resolve("AAPL", "revenue",
                                     PeriodSelector.fiscal_label(2019))
    assert not outcome.resolved
    assert outcome.reason == REASON_PERIOD_NOT_COVERED, outcome.detail
    assert "2024" in outcome.detail, "the refusal must say what is covered"


def test_a_year_whose_facts_exist_still_reports_the_missing_metric(world):
    """Negative control: the new rule must not swallow the real metric gap.

    BlackRock FY2024 is fully extracted and simply does not tag gross profit.
    That is ``metric_not_found_in_filing``, and it must stay so — otherwise
    the fix for C turns every missing line item into a coverage excuse.
    """
    outcome = world.resolver.resolve("BLK", "gross_profit",
                                     PeriodSelector.fiscal_label(2024))
    assert not outcome.resolved
    assert outcome.reason == REASON_METRIC_NOT_IN_FILING, outcome.detail


def test_the_pipeline_surfaces_period_not_covered_for_an_unextracted_year(world):
    from catalog.store import Filing

    world.catalog.upsert([Filing(
        ticker="AAPL", cik=320193, entity_name="Apple Inc.",
        accession="0000320193-19-000119", form_type="10-K",
        period_end="2019-09-28", fiscal_label=2019, filing_date="2019-10-31",
        fiscal_label_source="dei", primary_doc="a10-k20199282019.htm",
    )])
    out = Q.ask("What was Apple's revenue in fiscal 2019?", deps=deps_for(world))
    assert out.status is Status.ABSTAINED
    assert out.abstain_reason.value == "period_not_covered"
    assert "2024" in out.answer
