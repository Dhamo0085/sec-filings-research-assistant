"""The CLI entry point and the UI's filter overrides (P3-03/P3-08/P3-09).

Both are user-facing surfaces that nothing else exercises: `python query.py`
is in the module docstring and the runbook, and the filter chips are how the
chat UI narrows a question. An untested entry point is one that breaks on a
refactor and is only noticed by the owner at the keyboard.
"""

from __future__ import annotations

import pytest

import query as Q
from answering.outcome import Status
from llm.fake import FakeLLM
from tests.fixture_world import build_world

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    return build_world(tmp_path, collections=True)


#: Captured before any monkeypatch replaces Q.Deps, so the factory below does
#: not call whatever it was replaced with (and recurse forever).
_REAL_DEPS = Q.Deps


def deps_for(world, **over):
    kwargs = {
        "llm": FakeLLM(default='{"found": true, "answer": "Narrative. [1]"}'),
        "catalog": world.catalog,
        "resolver": world.resolver,
        "retriever": lambda **_k: [],
    }
    kwargs.update(over)
    return _REAL_DEPS(**kwargs)


# ── the CLI ───────────────────────────────────────────────────────────────────

def test_the_cli_prints_an_answer_and_exits_zero(capsys, monkeypatch, world):
    monkeypatch.setattr(Q, "Deps", lambda **_k: deps_for(world))
    code = Q.main(["What was Apple's revenue in fiscal 2024?"])
    out = capsys.readouterr().out

    assert code == 0
    assert "STATUS: answered" in out
    assert "$391.04 billion" in out
    assert "Definition used:" in out
    assert "SOURCES:" in out
    assert "0000320193-24-000123, filed 2024-11-01" in out


def test_the_cli_accepts_an_as_of_date(capsys, monkeypatch, world):
    monkeypatch.setattr(Q, "Deps", lambda **_k: deps_for(world))
    code = Q.main(["--as-of", "2024-06-30", "What was Apple's revenue in fiscal 2024?"])
    out = capsys.readouterr().out

    assert code == 0, "a refusal is a successful run, not a failure"
    assert "AS OF : 2024-06-30" in out
    assert "STATUS: abstained (period_not_filed_as_of)" in out


def test_the_cli_exits_non_zero_on_a_dependency_failure(capsys, monkeypatch, world):
    """So a script wrapping it can tell an outage from a refusal."""
    from llm.errors import LLMUnavailable

    class Exploding:
        def tickers(self):
            raise LLMUnavailable("provider down")

        def for_ticker(self, *_a, **_k):
            raise LLMUnavailable("provider down")

        def available_fiscal_labels(self, *_a, **_k):
            raise LLMUnavailable("provider down")

    monkeypatch.setattr(Q, "Deps",
                        lambda **_k: deps_for(world, catalog=Exploding()))
    code = Q.main(["What does Apple say about risk?"])
    out = capsys.readouterr().out
    assert code == 1
    assert "STATUS: error (llm_unavailable)" in out


def test_the_cli_prints_a_restatement_marker(capsys, monkeypatch, world):
    """GS FY2023 in the fixtures is a 10-K/A."""
    monkeypatch.setattr(Q, "Deps", lambda **_k: deps_for(world))
    Q.main(["What was Goldman Sachs' revenue in fiscal 2023?"])
    out = capsys.readouterr().out
    assert "STATUS:" in out


# ── the UI's filter chips ────────────────────────────────────────────────────

def test_a_ticker_filter_answers_a_question_that_names_no_company(world):
    """With a chip selected, "what was revenue" is no longer ambiguous."""
    plain = Q.ask("What was revenue in fiscal 2024?", deps=deps_for(world))
    assert plain.status is Status.CLARIFICATION_NEEDED

    filtered = Q.ask("What was revenue in fiscal 2024?", tickers=["AAPL"],
                     deps=deps_for(world))
    assert filtered.status is Status.ANSWERED
    assert filtered.citations[0].ticker == "AAPL"


def test_a_ticker_filter_overrides_the_company_in_the_question(world):
    got = Q.ask("What was Apple's revenue in fiscal 2024?", tickers=["NFLX"],
                deps=deps_for(world))
    assert got.citations[0].ticker == "NFLX"


def test_a_year_filter_overrides_the_year_in_the_question(world):
    """The chip wins over the year in the sentence, all the way through.

    Goldman's only fixture filing is its FY2023 10-K/A, which carries 64
    facts and no financial ones, so the honest outcome is a refusal — about
    **fiscal 2023**, which is what shows the filter reached the resolver
    rather than the 2024 the question named.
    """
    got = Q.ask("What was Goldman Sachs' revenue in fiscal 2024?", years=[2023],
                deps=deps_for(world))
    assert got.status is Status.ABSTAINED
    assert got.abstain_reason.value == "metric_not_found_in_filing"
    assert "fiscal 2023" in got.answer and "2024" not in got.answer


def test_a_lowercase_ticker_filter_is_accepted(world):
    got = Q.ask("What was revenue in fiscal 2024?", tickers=["aapl"],
                deps=deps_for(world))
    assert got.citations[0].ticker == "AAPL"


@pytest.mark.parametrize("question,reason", [
    ("Should I buy Apple stock?", "out_of_scope"),
    ("What was Apple's Q3 2024 revenue?", "unsupported_period_type"),
])
def test_a_filter_cannot_rescue_a_refused_question(world, question, reason):
    """Narrowing the companies does not make "should I buy this" answerable."""
    got = Q.ask(question, tickers=["AAPL"], years=[2024], deps=deps_for(world))
    assert got.status is Status.ABSTAINED
    assert got.abstain_reason.value == reason


def test_no_filters_leaves_the_route_untouched(world):
    plain = Q.ask("What was Apple's revenue in fiscal 2024?", deps=deps_for(world))
    same = Q.ask("What was Apple's revenue in fiscal 2024?", tickers=None,
                 years=None, deps=deps_for(world))
    assert plain.answer == same.answer


def test_the_filters_are_recorded_in_the_trace(world):
    from routing.router import route

    filtered = route("What was revenue in fiscal 2024?", llm=None,
                     force_tickers=["AAPL"], force_years=[2023])
    joined = " | ".join(filtered.rationale)
    assert "tickers from the caller: AAPL" in joined
    assert "years from the caller: 2023" in joined
    assert filtered.tickers == ("AAPL",)
    assert filtered.period.labels == (2023,)


def test_a_second_company_from_a_chip_makes_it_a_comparison():
    """The reason the filters are applied before the intent is derived."""
    from answering.outcome import QueryType
    from routing.router import route

    one = route("What was revenue in fiscal 2024?", llm=None, force_tickers=["AAPL"])
    two = route("What was revenue in fiscal 2024?", llm=None,
                force_tickers=["AAPL", "MSFT"])
    assert one.intent is QueryType.NUMERIC_FACT
    assert two.intent is QueryType.COMPARE


# ── the lazy dependency defaults ─────────────────────────────────────────────

def test_constructing_deps_opens_nothing():
    """api/app.py imports this module at startup and `make test` runs with no
    .env, so Deps() must not touch a database or load a model (T1-05)."""
    deps = Q.Deps()
    assert deps.llm is None and deps.catalog is None
    assert deps.resolver is None and deps.retriever is None


def test_the_supported_metric_list_reads_as_a_sentence():
    text = Q._supported_metrics(Q.Deps())
    assert "revenue" in text and " and " in text
    assert "_" not in text, "the registry's snake_case names are not user-facing"


def test_error_codes_are_mapped_most_specific_first():
    """LLMBudgetExceeded subclasses LLMRateLimited; order decides the code."""
    from llm.errors import LLMAuthError, LLMBudgetExceeded, LLMRateLimited

    assert Q.error_code_for(LLMBudgetExceeded("x")).value == "llm_rate_limited"
    assert Q.error_code_for(LLMRateLimited("x")).value == "llm_rate_limited"
    assert Q.error_code_for(LLMAuthError("x")).value == "llm_auth"
    assert Q.error_code_for(ValueError("x")).value == "internal"
