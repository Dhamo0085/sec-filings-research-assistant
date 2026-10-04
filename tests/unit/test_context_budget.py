"""T4-13: the generator context is bounded, windowed and collapsed (P4-16, D30).

Defect E, reproduced before anything was written: answering a question about
JPMorgan sent **3,323,116 characters** of context — three retrieved chunks
totalling 4,653 characters, expanded to their whole parent sections, one of
which (`JPM_2024 / fs_income_stmt`) is 1,526,355 characters and was emitted
twice. The estimate was ~831,000 tokens; every provider in the failover list
refused it (Gemini 250,000 TPM, Groq qwen 8,000 TPM) and the failure surfaced
as `llm_rate_limited`, which is why it read as a pacing problem for most of a
day.

Each test here is the behaviour that makes that impossible, and each has the
planted oversize case as its control.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from answering.text_answer import (
    MAX_SOURCE_TOKENS,
    TOTAL_CTX_BUDGET,
    _locate,
    build_context,
    estimate_tokens,
    window_around,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeChunk:
    def __init__(self, text, *, ticker="JPM", fiscal_year=2024,
                 section_name="fs_income_stmt", company="JPMorgan Chase & Co."):
        self.text = text
        self.ticker = ticker
        self.fiscal_year = fiscal_year
        self.section_name = section_name
        self.company = company


class FakeRetrieved:
    def __init__(self, chunk, parent_text, score=1.0):
        self.chunk = chunk
        self.parent_text = parent_text
        self.score = score


def filing(ticker="JPM", label=2024):
    return {"accession": "0000019617-25-000higher"[:20], "filing_date": "2025-02-14",
            "fiscal_label": label, "ticker": ticker, "cik": 19617,
            "entity_name": "JPMorgan Chase & Co."}


def collection_of(chunk):
    return f"{chunk.ticker}_{chunk.fiscal_year}"


# ── the window ───────────────────────────────────────────────────────────────

def test_the_window_is_centred_on_the_chunk_not_the_head_of_the_section():
    """The whole point of a window. The head of a 1.5 MB statement section is
    the XBRL preamble; the retrieved passage is what the question matched."""
    needle = "TOTAL NET REVENUE 177,599"
    parent = ("HEAD " * 20_000) + needle + (" TAIL" * 20_000)
    window = window_around(parent, [needle], budget_chars=4_000)
    assert needle in window
    assert not window.startswith("HEAD HEAD")


def test_the_retrieved_chunk_is_always_inside_the_window():
    parent = ("a" * 500_000) + "FINDME" + ("b" * 500_000)
    window = window_around(parent, ["FINDME"], budget_chars=2_000)
    assert "FINDME" in window
    assert len(window) <= 2_000 + len("FINDME") + 200


def test_a_chunk_the_parent_does_not_contain_still_yields_a_window():
    """Chunking normalises whitespace, so an exact match can fail. Falling back
    to the head is acceptable; returning nothing is not — the source would be
    cited with no text behind it."""
    parent = "x" * 50_000
    window = window_around(parent, ["not in the parent at all"], budget_chars=1_000)
    assert window and len(window) <= 1_200


def test_several_chunks_of_one_section_share_one_merged_window():
    first, second = "FIRSTHIT", "SECONDHIT"
    parent = "p" * 1_000 + first + "q" * 500 + second + "r" * 1_000
    window = window_around(parent, [first, second], budget_chars=4_000)
    assert first in window and second in window


def test_a_window_never_exceeds_its_budget_on_a_real_sized_section():
    parent = "word " * 400_000          # 2 MB, the shape of JPM fs_income_stmt
    window = window_around(parent, ["word word"], budget_chars=6_000)
    assert len(window) <= 6_500


# ── the budget ───────────────────────────────────────────────────────────────

def test_the_budget_is_below_the_smallest_provider_limit_in_the_failover_list():
    """D30's requirement, and the only thing that makes the rest of this work.
    The generator failover order ends at groq:qwen/qwen3.8-27b, measured at
    8,000 TPM (STATE section 3). The context budget plus the 900-token
    response plus the system prompt has to fit under that."""
    smallest_tpm = 8_000
    max_response = 900
    system_prompt_allowance = 600
    assert TOTAL_CTX_BUDGET + max_response + system_prompt_allowance < smallest_tpm


def test_the_assembled_context_for_the_reproduced_failure_is_within_budget():
    """The defect itself: JPM's 1.5 MB section, retrieved twice, plus a third.

    Negative control is the assertion below it — the unbounded body these
    inputs used to produce is orders of magnitude over, so a build_context
    that had quietly stopped windowing could not pass this test.
    """
    income = "INCOME " * 218_000          # ~1.5 MB
    cash = "CASH " * 54_000               # ~270 kB
    retrieved = [
        FakeRetrieved(FakeChunk("total net revenue line one"), income),
        FakeRetrieved(FakeChunk("total net revenue line two"), income),
        FakeRetrieved(FakeChunk("cash flow line", section_name="fs_cash_flow"), cash),
    ]
    by_collection = {"JPM_2024": filing()}

    context, _citations = build_context(retrieved, by_collection,
                                        collection_of=collection_of)

    assert estimate_tokens(context) <= TOTAL_CTX_BUDGET
    unbounded = len(income) * 2 + len(cash)
    assert len(context) < unbounded / 100, (
        "the context is not meaningfully smaller than the unbounded body")


def test_two_chunks_of_one_section_become_one_source():
    """The duplication half of defect E: the group was already formed, but the
    full parent was emitted once per chunk inside it."""
    parent = "SECTION " * 100_000
    retrieved = [
        FakeRetrieved(FakeChunk("line one"), parent),
        FakeRetrieved(FakeChunk("line two"), parent),
    ]
    context, citations = build_context(retrieved, {"JPM_2024": filing()},
                                       collection_of=collection_of)
    assert len(citations) == 1, "one section must yield one source"
    assert context.count("[1]") == 1
    assert "[2]" not in context


def test_both_chunk_texts_survive_the_collapse():
    """Collapsing must not silently drop the second passage — it was retrieved
    because something matched it."""
    parent = "x" * 2_000 + "line one" + "y" * 2_000 + "line two" + "z" * 2_000
    retrieved = [
        FakeRetrieved(FakeChunk("line one"), parent),
        FakeRetrieved(FakeChunk("line two"), parent),
    ]
    context, _ = build_context(retrieved, {"JPM_2024": filing()},
                               collection_of=collection_of)
    assert "line one" in context and "line two" in context


def test_lower_ranked_sources_are_dropped_before_the_budget_is_blown():
    big = "TEXT " * 200_000
    retrieved = [
        FakeRetrieved(FakeChunk(f"hit {i}", section_name=f"sec_{i}"), big, score=1.0 / (i + 1))
        for i in range(8)
    ]
    context, citations = build_context(retrieved, {"JPM_2024": filing()},
                                       collection_of=collection_of)
    assert estimate_tokens(context) <= TOTAL_CTX_BUDGET
    assert 1 <= len(citations) < 8, "some sources must have been dropped"
    assert citations[0].index == 1, "citations stay 1..n (G4/K1)"
    assert [c.index for c in citations] == list(range(1, len(citations) + 1))


def test_one_oversized_source_is_kept_rather_than_dropped_to_nothing():
    """A single strong match that is alone over budget must still be answered
    from, truncated — refusing would be worse than a shortened source."""
    huge = "BIG " * 500_000
    retrieved = [FakeRetrieved(FakeChunk("the only hit"), huge)]
    context, citations = build_context(retrieved, {"JPM_2024": filing()},
                                       collection_of=collection_of)
    assert len(citations) == 1
    assert context.strip()
    assert estimate_tokens(context) <= TOTAL_CTX_BUDGET


def test_a_per_source_budget_bounds_each_source():
    huge = "BIG " * 500_000
    retrieved = [
        FakeRetrieved(FakeChunk("hit a", section_name="sec_a"), huge),
        FakeRetrieved(FakeChunk("hit b", section_name="sec_b"), huge),
    ]
    context, _citations = build_context(retrieved, {"JPM_2024": filing()},
                                        collection_of=collection_of)
    for part in context.split("\n\n[")[0:]:
        assert estimate_tokens(part) <= MAX_SOURCE_TOKENS + 100


def test_a_small_section_is_passed_through_whole():
    """Negative control for the windowing: a section that fits must not be
    chopped or marked elided, or every short answer would lose context."""
    parent = "A short risk factors section about supply chain concentration."
    retrieved = [FakeRetrieved(FakeChunk("supply chain concentration"), parent)]
    context, _ = build_context(retrieved, {"JPM_2024": filing()},
                               collection_of=collection_of)
    assert parent in context


def test_a_chunk_outside_the_as_of_scope_is_still_dropped():
    """The budget work must not have disturbed the look-ahead guard."""
    retrieved = [FakeRetrieved(FakeChunk("x", fiscal_year=2025), "parent")]
    context, citations = build_context(retrieved, {"JPM_2024": filing()},
                                       collection_of=collection_of)
    assert citations == [] and context == ""


def test_estimate_tokens_is_conservative():
    """Budgeting on an underestimate would let an oversize prompt through.
    Dense XBRL tables run closer to 3 characters per token than 4."""
    assert estimate_tokens("a" * 3_000) >= 1_000


# ── locating the chunk inside its parent (the regression P4-16 caused) ───────

CHUNKER_HEADER = "BlackRock Inc. (BLK) FY2025 — Item 7: MD&A\n"


def test_a_chunk_carrying_the_chunkers_header_still_locates():
    """The bug the first version of this fix shipped.

    `ingestion/chunker.py` prefixes every chunk with a header line naming the
    company, year and section. That line does not exist in the parent section,
    so probing with the chunk's first 200 characters failed for **70%** of a
    480-chunk sample, and `window_around` quietly fell back to the head of the
    section — precisely the behaviour P4-16 exists to remove. It cost two
    correct narrative answers (R-GOOGL-REGULATION-2024, R-GS-MARKETRISK-2024)
    before the sample showed why.
    """
    needle = "Government investigations and antitrust enforcement actions"
    parent = ("PREAMBLE " * 3_000) + needle + (" AFTERWARD" * 3_000)
    chunk = CHUNKER_HEADER + needle + " and related proceedings continue."

    window = window_around(parent, [chunk], budget_chars=3_000)

    assert needle in window
    assert not window.startswith("PREAMBLE PREAMBLE"), \
        "fell back to the head of the section"


def test_a_chunk_whose_opening_was_reformatted_still_locates():
    """Table chunks begin with pipe-and-dash scaffolding that the parse joins
    differently, so the opening is the least reliable part of a chunk to probe
    with. Matching must not depend on it."""
    needle = "Total revenues increased 14% to $350,018 million"
    parent = ("x" * 40_000) + needle + ("y" * 40_000)
    chunk = CHUNKER_HEADER + "|  |  |  |\n| --- | --- |\n" + needle

    window = window_around(parent, [chunk], budget_chars=2_000)
    assert needle in window


def test_the_committed_corpus_locates_essentially_every_chunk():
    """The measurement itself, as a test: a regression in the chunker's header
    or the parser's joining would silently send section heads again."""
    import json
    import random

    parsed_dir = REPO_ROOT / "data/parsed"
    chunks_dir = REPO_ROOT / "data/chunks"
    if not (parsed_dir.is_dir() and chunks_dir.is_dir()):
        pytest.skip("the corpus is not present")

    parsed = {}
    for path in parsed_dir.glob("*.json"):
        doc = json.loads(path.read_text())
        parsed[doc["doc_id"]] = {
            s["section_id"]: " ".join(b.get("text", "") for b in s["content_blocks"])
            for s in doc["sections"]}

    files = sorted(chunks_dir.glob("*.json"))
    rng = random.Random(20261004)  # noqa: S311
    located = total = 0
    for path in rng.sample(files, min(6, len(files))):
        payload = json.loads(path.read_text())
        items = payload if isinstance(payload, list) else payload.get("chunks", payload)
        for chunk in rng.sample(items, min(25, len(items))):
            parent = parsed.get(chunk["doc_id"], {}).get(chunk["parent_id"])
            text = (chunk.get("text") or "").strip()
            if not parent or not text:
                continue
            total += 1
            # Assert on the locator, not on the window. A chunk that genuinely
            # sits at the head of its section produces a window starting at
            # character 0, which is indistinguishable from the fallback — the
            # first version of this test called those failures and reported
            # 53/150 when the locator was in fact finding all 150.
            located += int(_locate(parent, text) >= 0)

    assert total > 100, f"only {total} chunks sampled"
    assert located / total > 0.95, (
        f"only {located}/{total} ({located / total:.1%}) of chunks located; "
        f"the rest fell back to the head of their section")
