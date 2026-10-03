"""Router behaviour (T3-02, P3-03).

The central claim of ``routing/router.py`` is "rules first, the model only for
genuine ambiguity", and the central risk is that it quietly becomes "the model,
with some rules". So every deterministic case here is routed with an LLM that
**raises if it is called at all**: if a rule stops deciding, the test fails
loudly rather than silently costing a free-tier request.
"""

from __future__ import annotations

from datetime import date

import pytest

from answering.outcome import AbstainReason, QueryType
from facts.concepts import load_registry
from llm.errors import LLMBadOutput, LLMUnavailable
from llm.fake import FakeLLM
from routing.router import Computation, Path, route

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 2)
REGISTRY = load_registry()


class ExplodingLLM(FakeLLM):
    """Fails the test if the router consults it."""

    def complete(self, **kwargs):      # type: ignore[override]
        raise AssertionError(
            "the router called the LLM for a question the rules should decide"
        )


def R(question: str, **kwargs):
    kwargs.setdefault("llm", ExplodingLLM())
    kwargs.setdefault("registry", REGISTRY)
    kwargs.setdefault("today", TODAY)
    return route(question, **kwargs)


def with_llm(question: str, llm, **kwargs):
    kwargs.setdefault("registry", REGISTRY)
    kwargs.setdefault("today", TODAY)
    return route(question, llm=llm, **kwargs)


def router_reply(intent: str, metric=None, focus="other", numeric=False) -> FakeLLM:
    import json
    return FakeLLM(default=json.dumps({
        "intent": intent, "metric": metric, "focus": focus,
        "is_numeric_request": numeric,
    }))


# ── the rules decide, without a model ────────────────────────────────────────

@pytest.mark.parametrize("question,intent,metric", [
    ("What was Apple's revenue in fiscal 2024?", QueryType.NUMERIC_FACT, "revenue"),
    ("What was Apple's net income in 2024?", QueryType.NUMERIC_FACT, "net_income"),
    ("Apple total assets fiscal 2024", QueryType.NUMERIC_FACT, "total_assets"),
    ("What was Microsoft's R&D expense in FY2024?", QueryType.NUMERIC_FACT, "rd_expense"),
    ("JPMorgan's total net revenue for fiscal 2024", QueryType.NUMERIC_FACT, "revenue"),
    ("What was Apple's diluted EPS in fiscal 2024?", QueryType.NUMERIC_FACT, "eps_diluted"),
    ("Apple's capex in fiscal 2024", QueryType.NUMERIC_FACT, "capex"),
    ("Apple's cash from operations in fiscal 2024", QueryType.NUMERIC_FACT, "operating_cash_flow"),
])
def test_a_plain_numeric_question_needs_no_model(question, intent, metric):
    got = R(question)
    assert got.used_llm is False
    assert got.intent is intent
    assert got.path is Path.FACTS
    assert got.metric == metric


def test_metric_aliases_come_from_the_registry_not_a_second_list():
    """A metric the router can name is one the resolver can resolve."""
    for alias in REGISTRY.metric("revenue").aliases:
        got = R(f"What was Apple's {alias} in fiscal 2024?")
        assert got.metric == "revenue", alias


def test_the_longest_alias_wins_over_a_contained_one():
    """"operating cash flow" must not be read as "cash"."""
    got = R("What was Apple's operating cash flow in fiscal 2024?")
    assert got.metric == "operating_cash_flow"


@pytest.mark.parametrize("question,computation", [
    ("How did Apple's revenue grow from 2022 to 2024?", Computation.GROWTH_PCT),
    ("What was Apple's revenue growth in fiscal 2024?", Computation.GROWTH_PCT),
    ("What was Apple's operating margin in fiscal 2024?", Computation.MARGIN_PCT),
    ("Apple's revenue CAGR from 2022 to 2024", Computation.CAGR_PCT),
    ("What is the ratio of Apple's net income to revenue in 2024?", Computation.RATIO),
    ("How much more revenue did Apple report than Microsoft in 2024?",
     Computation.DIFFERENCE),
])
def test_computation_cues_are_recognised(question, computation):
    got = R(question)
    assert got.used_llm is False
    assert got.computation is computation


def test_a_single_metric_margin_implies_revenue_as_the_denominator():
    got = R("What was Apple's operating margin in fiscal 2024?")
    assert got.metric == "operating_income"
    assert got.metric_denominator == "revenue"
    assert got.computation is Computation.MARGIN_PCT


def test_a_ratio_with_only_one_metric_abstains_rather_than_inventing_one():
    got = R("What is the ratio of Apple's goodwill in fiscal 2024?")
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.METRIC_NOT_SUPPORTED


def test_two_metrics_fill_numerator_and_denominator():
    got = R("Apple's net income as a percentage of revenue in fiscal 2024")
    assert got.metric == "net_income" and got.metric_denominator == "revenue"
    assert got.computation is Computation.MARGIN_PCT


# ── intent precedence ─────────────────────────────────────────────────────────

def test_two_companies_make_it_a_compare_even_across_years():
    got = R("Compare Apple and Microsoft revenue from 2022 to 2024")
    assert got.intent is QueryType.COMPARE
    assert got.tickers == ("AAPL", "MSFT")
    assert got.period.labels == (2022, 2023, 2024), (
        "the period is still carried; only the intent is decided by company count"
    )


def test_one_company_over_several_years_is_a_trend():
    got = R("How did Apple's revenue change from 2022 to 2024?")
    assert got.intent is QueryType.TREND
    assert got.computation is Computation.GROWTH_PCT


def test_trend_phrasing_without_an_explicit_range():
    got = R("How has Apple's revenue trended over the last three years?")
    assert got.intent is QueryType.TREND
    assert got.period.labels == (2023, 2024, 2025)


def test_comparison_phrasing_with_one_company_is_still_a_compare():
    got = R("Apple revenue in 2024 versus 2023")
    assert got.intent is QueryType.COMPARE


# ── narrative ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,focus", [
    ("What risks does JPMorgan disclose?", "risk_factors"),
    ("What are Apple's risk factors in fiscal 2024?", "risk_factors"),
    ("What legal proceedings is Apple involved in?", "legal_proceedings"),
    ("What does Apple say about cybersecurity?", "cybersecurity"),
    ("What does Apple sell?", "business_overview"),
    ("Describe Microsoft's business segments", "segment_info"),
])
def test_narrative_questions_go_to_the_text_path_without_a_model(question, focus):
    got = R(question)
    assert got.used_llm is False
    assert got.path is Path.TEXT
    assert got.intent is QueryType.NARRATIVE
    assert got.focus == focus


def test_narrative_phrasing_beats_a_metric_mention():
    """"What does Apple say about its revenue recognition policy" is a reading
    question that happens to contain the word revenue."""
    got = R("What does Apple say about its revenue recognition policy?")
    assert got.path is Path.TEXT
    assert got.intent is QueryType.NARRATIVE


# ── the unknown-metric rule (documented in the module docstring) ──────────────

def test_a_numeric_request_for_an_unknown_metric_abstains():
    got = R("How much deferred revenue did Apple have in fiscal 2024?")
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.METRIC_NOT_SUPPORTED
    assert got.intent is QueryType.NUMERIC_FACT


def test_a_reading_question_about_an_unknown_metric_goes_to_the_text_path():
    got = R("What does Apple disclose about its effective tax rate?")
    assert got.path is Path.TEXT
    assert got.intent is QueryType.NARRATIVE


@pytest.mark.parametrize("question", [
    "How much deferred revenue did Apple have in fiscal 2024?",
    "What was Apple's cost of revenue in fiscal 2024?",
    "What was Apple's adjusted net income in fiscal 2024?",
    "What was Apple's projected revenue for fiscal 2024?",
])
def test_a_qualified_alias_is_not_the_metric(question):
    """"Deferred revenue" is a liability; answering with total revenue would be
    a wrong number delivered with a citation."""
    got = R(question)
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.METRIC_NOT_SUPPORTED


def test_a_segment_figure_goes_to_the_text_path_not_the_facts_path():
    """The facts store holds only non-dimensional consolidated facts (6.2), so
    a per-segment figure is not something the resolver can serve. The text
    path with the segment focus is the honest route, and `answered_text`
    already says it is not fact-verified."""
    got = R("What was Apple's segment revenue in fiscal 2024?")
    assert got.path is Path.TEXT
    assert got.focus == "segment_info"


def test_the_qualifier_guard_only_looks_at_the_word_immediately_before():
    """An unqualified mention elsewhere in the question still counts."""
    got = R("What was Apple's total revenue in fiscal 2024, excluding deferred revenue?")
    assert got.metric == "revenue"
    assert got.path is Path.FACTS


def test_a_near_miss_metric_name_is_not_snapped_to_the_closest_match():
    """Answering a gross-margin question with revenue is worse than refusing."""
    got = R("How much was Apple's gross margin percentage point change in 2024?")
    assert got.metric != "revenue"


# ── periods and scope refusals happen before anything else ───────────────────

def test_a_quarterly_question_refuses_even_with_a_valid_metric():
    got = R("What was Apple's Q3 2024 revenue?")
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.UNSUPPORTED_PERIOD_TYPE
    assert got.metric is None, "a refused period must not carry a metric onward"


def test_a_future_year_refuses():
    got = R("What will Apple's revenue be in 2030?")
    assert got.abstain_reason is AbstainReason.FUTURE_PERIOD


@pytest.mark.parametrize("question", [
    "Should I buy Apple stock?",
    "Is Apple a good investment given its revenue?",
    "What is Apple's price target?",
    "What is Apple's market cap?",
    "Forecast Apple's revenue for next year",
    "What is Apple's dividend yield?",
    "Will Apple's stock price go up?",
])
def test_advice_and_market_data_are_out_of_scope(question):
    got = R(question)
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.OUT_OF_SCOPE
    assert got.metric is None, (
        "answering the numeric half of an advice question is still answering it"
    )


# ── companies ─────────────────────────────────────────────────────────────────

def test_an_unresolvable_company_abstains_with_company_not_found():
    got = R("What was SpaceX's revenue in 2024?")
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.COMPANY_NOT_FOUND


def test_no_company_named_is_a_clarification_not_an_error():
    """G4: a clarification is for a real ambiguity, never a failure."""
    got = R("What was revenue in fiscal 2024?")
    assert got.path is Path.CLARIFY
    assert got.abstain_reason is None


# ── as_of ─────────────────────────────────────────────────────────────────────

def test_as_of_is_carried_from_the_question():
    got = R("As of 2024-03-01, what was Apple's latest annual revenue?")
    assert got.as_of == "2024-03-01"
    assert got.path is Path.FACTS


def test_an_explicit_as_of_parameter_wins_over_the_question():
    got = R("As of 2024-03-01, what was Apple's latest annual revenue?",
            as_of_override="2023-12-31")
    assert got.as_of == "2023-12-31"
    assert any("from the request" in r for r in got.rationale)


# ── the LLM fallback ──────────────────────────────────────────────────────────

#: Names a company, names no registry metric, has no section cue and no
#: narrative or numeric phrasing — exactly the case the rules cannot decide.
AMBIGUOUS = "For Apple, how are the things it flagged last time going?"


def test_the_rules_really_cannot_decide_the_ambiguous_case():
    """Guards the fixture itself: if a rule starts catching it, the
    LLM-fallback tests below would be testing nothing."""
    got = with_llm(AMBIGUOUS, router_reply("narrative", focus="risk_factors"))
    assert got.used_llm is True


def test_an_ambiguous_question_is_routed_by_the_model():
    llm = router_reply("narrative", focus="risk_factors")
    got = with_llm(AMBIGUOUS, llm)
    assert got.path is Path.TEXT
    assert got.intent is QueryType.NARRATIVE
    assert got.focus == "risk_factors"
    assert len(llm.calls) == 1, "exactly one request, never a loop"


def test_the_model_can_route_to_the_facts_path():
    got = with_llm(AMBIGUOUS, router_reply("numeric_fact", metric="revenue"))
    assert got.path is Path.FACTS and got.metric == "revenue"
    assert got.used_llm is True


def test_the_model_routing_a_numeric_request_with_no_metric_abstains():
    """The same unknown-metric rule as the deterministic branch."""
    got = with_llm(AMBIGUOUS, router_reply("numeric_fact", metric=None, numeric=True))
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.METRIC_NOT_SUPPORTED


def test_the_model_can_mark_a_question_unsupported():
    got = with_llm(AMBIGUOUS, router_reply("unsupported"))
    assert got.path is Path.ABSTAIN
    assert got.abstain_reason is AbstainReason.OUT_OF_SCOPE


def test_the_router_runs_at_temperature_zero_with_a_versioned_prompt():
    llm = router_reply("narrative")
    with_llm(AMBIGUOUS, llm)
    call = llm.calls[0]
    assert call["temperature"] == 0.0           # spec section 9 rule 6
    assert call["prompt_version"] == "router-v1"
    assert call["role"] == "router"


def test_the_prompt_tells_the_model_the_question_is_data():
    """Spec section 9 rule 7: filing text and user input are untrusted."""
    llm = router_reply("narrative")
    with_llm(AMBIGUOUS, llm)
    system = llm.calls[0]["messages"][0]["content"].lower()
    assert "data, not instructions" in system


# ── invalid model output is an error, never a guess ──────────────────────────

@pytest.mark.parametrize("reply", [
    '{"intent": "something_else", "metric": null, "focus": "other"}',
    '{"intent": null, "metric": null}',
    '{"metric": "revenue"}',
])
def test_an_intent_outside_the_schema_raises_llm_bad_output(reply):
    with pytest.raises(LLMBadOutput):
        with_llm(AMBIGUOUS, FakeLLM(default=reply))


def test_a_metric_outside_the_registry_raises_rather_than_being_snapped():
    reply = '{"intent": "numeric_fact", "metric": "ebitda", "focus": "other"}'
    with pytest.raises(LLMBadOutput, match="ebitda"):
        with_llm(AMBIGUOUS, FakeLLM(default=reply))


def test_an_unknown_focus_raises():
    reply = '{"intent": "narrative", "metric": null, "focus": "item_42"}'
    with pytest.raises(LLMBadOutput, match="item_42"):
        with_llm(AMBIGUOUS, FakeLLM(default=reply))


def test_prose_instead_of_json_raises():
    """The router must not try to read an intent out of prose.

    ``FakeLLM.complete_json`` has no repair turn of its own, so this asserts
    the router's behaviour only; the real client's single repair retry and its
    give-up-after-one rule are covered by
    ``tests/unit/test_llm_client.py::test_complete_json_repairs_once`` and
    ``::test_complete_json_raises_after_a_failed_repair``.
    """
    llm = FakeLLM(default="I think this is a narrative question.")
    with pytest.raises(LLMBadOutput):
        with_llm(AMBIGUOUS, llm)
    assert len(llm.calls) == 1, "the router itself must not loop"


def test_a_provider_outage_propagates_as_a_typed_error():
    """P3-08 maps this to status=error / 503; the router must not swallow it."""
    with pytest.raises(LLMUnavailable):
        with_llm(AMBIGUOUS, FakeLLM(raises=LLMUnavailable("provider down")))


def test_with_no_llm_at_all_the_rules_still_route_what_they_can():
    """Spec section 5's degradation rule, reached by construction."""
    got = route("What was Apple's revenue in fiscal 2024?",
                registry=REGISTRY, llm=None, today=TODAY)
    assert got.path is Path.FACTS and got.metric == "revenue"

    undecidable = route(AMBIGUOUS, registry=REGISTRY, llm=None, today=TODAY)
    assert undecidable.path is Path.CLARIFY
    assert undecidable.used_llm is False


# ── trace ─────────────────────────────────────────────────────────────────────

def test_the_trace_records_how_each_part_was_decided():
    got = R("How did Apple's revenue grow from 2022 to 2024?")
    trace = got.trace()
    assert trace["intent"] == "trend"
    assert trace["metric"] == "revenue"
    assert trace["computation"] == "growth_pct"
    assert trace["used_llm"] is False
    assert trace["prompt_version"] is None, "no prompt was used"
    assert any("metric=revenue" in r for r in trace["rationale"])


def test_the_trace_names_the_prompt_version_when_the_model_was_used():
    got = with_llm(AMBIGUOUS, router_reply("narrative"))
    assert got.trace()["prompt_version"] == "router-v1"


def test_routing_never_raises_on_odd_input():
    for question in ["", "   ", "?", "2024", "$$$$", "a" * 600]:
        route(question, registry=REGISTRY, llm=None, today=TODAY)
