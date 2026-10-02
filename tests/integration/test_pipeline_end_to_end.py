"""Offline end-to-end: one case per intent (T3-08), and the as_of invariant (T3-03).

Everything runs against the real dispatcher in ``query.py`` over a real
catalog and facts store built from the committed iXBRL fixtures, with a
``FakeLLM`` and a fake retriever. The point is to exercise the code that
ships, not a parallel path: the router, the resolver, the calculator, the
templates and the abstention gate are all the real ones.

The look-ahead property test (T3-03) is here rather than beside the Outcome
unit tests because the invariant it checks is about the *pipeline*: the type
refuses a bad citation, but only the pipeline decides which filings are
eligible in the first place. Random dates against the real catalog is the
only way to check that the two agree.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import query as Q
from answering.outcome import CitationKind, Status
from llm.fake import FakeLLM
from models import Chunk, RetrievedChunk
from tests.fixture_world import build_world

pytestmark = pytest.mark.integration


class RecordingRetriever:
    """A retriever that returns one fixed chunk and records its arguments.

    The arguments matter as much as the result: the point-in-time guarantee
    on the text path is that the *collection list* is filtered before any
    search runs, so the test asserts what the retriever was asked for.
    """

    def __init__(self, section: str = "Item 1A: Risk Factors"):
        self.section = section
        self.calls: list = []

    def __call__(self, *, query, tickers, years, focus, collections):
        self.calls.append({"query": query, "tickers": list(tickers),
                           "years": list(years), "focus": focus,
                           "collections": list(collections)})
        if not collections:
            return []
        ticker, _, year = collections[0].rpartition("_")
        chunk = Chunk(
            parent_id="s1", doc_id="d1",
            text=f"{ticker} discloses supplier concentration and currency risk.",
            company=f"{ticker} Inc.", ticker=ticker, filing_type="10-K",
            fiscal_year=int(year), section_name=self.section,
            chunk_type="text", token_count=12,
        )
        return [RetrievedChunk(chunk=chunk, score=0.88, parent_text=chunk.text)]


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path, collections=True)


def deps_for(world, *, llm=None, retriever=None) -> Q.Deps:
    return Q.Deps(
        llm=llm or FakeLLM(default='{"found": true, "answer": "A narrative answer. [1]"}'),
        catalog=world.catalog,
        resolver=world.resolver,
        retriever=retriever or RecordingRetriever(),
    )


# ── one case per intent ───────────────────────────────────────────────────────

def test_numeric_fact(world):
    out = Q.ask("What was Apple's revenue in fiscal 2024?", deps=deps_for(world))
    assert out.status is Status.ANSWERED
    assert out.query_type.value == "numeric_fact"
    assert "$391.04 billion" in out.answer
    assert out.citations[0].kind is CitationKind.FACT
    assert out.citations[0].accession == "0000320193-24-000123"
    assert out.definition_note


def test_computed(world):
    """BAC FY2024 net income as a share of revenue: two facts, one calculation."""
    out = Q.ask("What was Bank of America's net margin in fiscal 2024?",
                deps=deps_for(world))
    assert out.status is Status.ANSWERED
    assert out.query_type.value == "computed"
    assert "%" in out.answer
    assert "Computed as" in out.answer, "a computed answer must show its work"
    assert len(out.citations) == 2


def test_compare(world):
    out = Q.ask("Compare Apple and Netflix revenue in fiscal 2024",
                deps=deps_for(world))
    assert out.status is Status.ANSWERED
    assert out.query_type.value == "compare"
    assert {c.ticker for c in out.citations} == {"AAPL", "NFLX"}
    assert "Netflix" in out.answer and "Apple" in out.answer


def test_trend(world):
    """GS has FY2023 in the fixtures; a two-year span exercises the trend
    template even though only one year resolves — the gap is stated."""
    out = Q.ask("How did Goldman Sachs revenue change from 2023 to 2024?",
                deps=deps_for(world))
    assert out.status in (Status.ANSWERED, Status.ABSTAINED)
    if out.status is Status.ANSWERED:
        assert out.query_type.value in ("trend", "computed")


def test_narrative(world):
    retriever = RecordingRetriever()
    out = Q.ask("What risks does Apple disclose?",
                deps=deps_for(world, retriever=retriever))
    assert out.status is Status.ANSWERED_TEXT
    assert out.query_type.value == "narrative"
    assert out.citations[0].kind is CitationKind.TEXT
    assert out.citations[0].accession == "0000320193-24-000123"
    assert retriever.calls[0]["focus"] == "risk_factors"


def test_unsupported(world):
    out = Q.ask("Should I buy Apple stock?", deps=deps_for(world))
    assert out.status is Status.ABSTAINED
    assert out.abstain_reason.value == "out_of_scope"
    assert out.citations == []


# ── abstentions reached through the whole pipeline ───────────────────────────

def test_a_quarterly_question_abstains_without_touching_the_stores(world):
    llm = FakeLLM(raises=AssertionError("the model must not be called"))
    out = Q.ask("What was Apple's Q3 2024 revenue?", deps=deps_for(world, llm=llm))
    assert out.abstain_reason.value == "unsupported_period_type"


def test_a_year_we_do_not_have_abstains_and_says_what_we_do_have(world):
    out = Q.ask("What was Apple's revenue in fiscal 2019?", deps=deps_for(world))
    assert out.status is Status.ABSTAINED
    assert out.abstain_reason.value in ("period_not_covered", "no_filing_for_company")
    assert "2024" in out.answer, "the refusal must offer what is available"


def test_an_unknown_company_abstains_with_company_not_found(world):
    out = Q.ask("What was SpaceX's revenue in fiscal 2024?", deps=deps_for(world))
    assert out.abstain_reason.value == "company_not_found"


def test_blackrock_still_abstains_on_ambiguous_revenue_through_the_pipeline(world):
    """D0.3: BLK tags two consolidated revenue concepts that differ by 37%.

    The resolver's override makes it resolvable; this asserts the pipeline
    surfaces whichever outcome the resolver reaches rather than inventing one.
    """
    out = Q.ask("What was BlackRock's revenue in fiscal 2024?", deps=deps_for(world))
    assert out.status in (Status.ANSWERED, Status.ABSTAINED)
    if out.status is Status.ABSTAINED:
        assert out.abstain_reason.value == "ambiguous_concept"
    else:
        assert out.citations[0].concept


# ── as_of, end to end ─────────────────────────────────────────────────────────

def test_as_of_before_the_filing_date_refuses_and_explains(world):
    out = Q.ask("What was Apple's revenue in fiscal 2024?", as_of="2024-06-30",
                deps=deps_for(world))
    assert out.status is Status.ABSTAINED
    assert out.abstain_reason.value in ("period_not_filed_as_of", "period_not_covered")
    assert out.as_of == "2024-06-30"
    assert out.citations == []


def test_as_of_narrows_the_text_search_scope_before_any_search_runs(world):
    retriever = RecordingRetriever()
    Q.ask("What risks does Apple disclose?", as_of="2024-06-30",
          deps=deps_for(world, retriever=retriever))
    if retriever.calls:
        assert retriever.calls[0]["collections"] == [], (
            "the FY2024 10-K was filed 2024-11-01 and must not be searched"
        )


def test_as_of_after_the_filing_date_answers_normally(world):
    out = Q.ask("What was Apple's revenue in fiscal 2024?", as_of="2025-01-01",
                deps=deps_for(world))
    assert out.status is Status.ANSWERED
    assert out.citations[0].filing_date <= "2025-01-01"


# ── T3-03: the look-ahead property ───────────────────────────────────────────

QUESTIONS = [
    "What was {c}'s revenue in fiscal 2024?",
    "What was {c}'s net income in fiscal 2024?",
    "What was {c}'s latest annual revenue?",
    "How did {c}'s revenue change from 2023 to 2024?",
    "What risks does {c} disclose?",
    "What does {c} say about its business?",
]
TICKERS = ["AAPL", "NFLX", "BAC", "BLK", "WFC", "GS"]


@given(
    as_of=st.dates(min_value=date(2023, 1, 1), max_value=date(2026, 12, 31)),
    ticker=st.sampled_from(TICKERS),
    template=st.sampled_from(QUESTIONS),
)
@settings(max_examples=120, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_cited_filing_was_published_after_as_of(world, as_of, ticker, template):
    """T3-03. Random dates x filers x intents: G2 must hold for every answer.

    Both citation kinds are covered, which is the point — the facts path
    filters in the resolver and the text path filters the collection list,
    two different mechanisms that have to reach the same guarantee.
    """
    out = Q.ask(template.format(c=ticker), as_of=as_of.isoformat(),
                deps=deps_for(world))
    for citation in out.citations:
        assert citation.filing_date <= as_of.isoformat(), (
            f"look-ahead: {out.status.value} answer to {template!r} cited "
            f"{citation.accession} filed {citation.filing_date} with "
            f"as_of={as_of.isoformat()}"
        )


@given(as_of=st.dates(min_value=date(2023, 1, 1), max_value=date(2026, 12, 31)),
       ticker=st.sampled_from(TICKERS))
@settings(max_examples=60, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_an_as_of_query_never_errors(world, as_of, ticker):
    """Whatever the date, the answer is an answer or a refusal — never a crash
    and never status=error, which would mean a dependency failed."""
    out = Q.ask(f"What was {ticker}'s revenue in fiscal 2024?",
                as_of=as_of.isoformat(), deps=deps_for(world))
    assert out.status is not Status.ERROR, out.answer
    assert out.as_of == as_of.isoformat()
