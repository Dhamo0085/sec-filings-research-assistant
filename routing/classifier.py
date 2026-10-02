"""
Query classifier — routes each query to a pipeline type using Groq llama-3.1-8b.

Output types:
  single_doc   — one company, one year
  multi_doc    — multiple companies or sector-level comparison
  temporal     — trend over multiple years, one company
  out_of_scope — cannot be answered from available documents
"""

import json
import re
import time
from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel

from config import COMPANIES
from llm import get_client as get_llm
from llm.errors import LLMBadOutput, LLMError

VALID_TICKERS = {c["ticker"] for c in COMPANIES}

# ---------------------------------------------------------------------------
# Fiscal-year discovery (P1-08, fixes K5)
#
# v1 pinned VALID_YEARS = {2023, 2024, 2025} here and filtered the model's
# extracted years through it, so any other year was silently dropped and the
# query continued as though no year had been named. Phase 0 measured the cost:
# Microsoft's FY2026 10-K (filed 2026-07-29) was unrequestable, and the live
# deployment held an indexed NVDA_2026 collection no year-qualified query
# could reach.
#
# Two separate questions were conflated. "Is this a plausible fiscal year?"
# is a parsing question, answered by a sanity range. "Do we actually have it?"
# is a data question, answered downstream by routing/resolver.py, which
# reports it as `year_not_available` (and may auto-ingest it first). Only the
# first belongs here; the second must not silently delete the year.
#
# The discovered years are injected into the prompt so the model knows what is
# covered. This is interim: the Phase 3 catalog becomes the authority.
# ---------------------------------------------------------------------------

# Collections are named "{TICKER}_{fiscal_year}" (retrieval/vector_store.py).
_COLLECTION_RE = re.compile(r"^(?P<ticker>[A-Z][A-Z0-9.\-]*)_(?P<year>\d{4})$")

# EDGAR electronic filing became general in 1993; a 10-K for next fiscal year
# can legitimately be filed late in the current calendar year.
_EARLIEST_FISCAL_YEAR = 1993
_YEARS_TTL_SECONDS = 60.0

_years_cache: Optional[tuple] = None


def _list_collections() -> List[str]:
    """Indirection so tests can patch collection listing without Qdrant.

    Imported lazily: importing retrieval.vector_store at module scope would
    pull in the Qdrant client (and fastembed) just to classify a query.
    """
    from retrieval.vector_store import list_collections
    return list(list_collections())


def _is_plausible_fiscal_year(year: int) -> bool:
    current_year = datetime.now(timezone.utc).year
    return _EARLIEST_FISCAL_YEAR <= year <= current_year + 1


def reset_year_cache() -> None:
    """Drop the cached year set (used by tests and after on-demand ingest)."""
    global _years_cache
    _years_cache = None


def available_years(force_refresh: bool = False) -> frozenset:
    """Fiscal years that currently have at least one indexed collection.

    Cached for _YEARS_TTL_SECONDS because on-demand ingestion can add a year
    mid-process, so a permanent cache would hide it, while listing collections
    on every query is wasteful in Qdrant local mode.
    Returns an empty set if the store cannot be listed; callers must treat
    "unknown" as "do not filter" rather than "nothing is available".
    """
    global _years_cache
    now = time.monotonic()
    if not force_refresh and _years_cache is not None:
        stamp, cached = _years_cache
        if now - stamp < _YEARS_TTL_SECONDS:
            return cached
    try:
        years = frozenset(
            int(m.group("year"))
            for name in _list_collections()
            if (m := _COLLECTION_RE.match(name))
        )
    except Exception as exc:
        logger.warning(f"Could not list collections for fiscal-year discovery: {exc}")
        years = frozenset()
    _years_cache = (now, years)
    return years

VALID_QUERY_TYPES = {"single_doc", "multi_doc", "temporal", "out_of_scope"}

# Logged in traces so a result can be tied to the prompt that produced it
# (spec section 9 rule 6).
PROMPT_VERSION = "classifier-v1"

# Reasoning models (Groq's gpt-oss family) spend completion tokens on the
# chain of thought before emitting the answer, so v1's max_tokens=200 can be
# consumed entirely by reasoning and return empty content. P1-00 measured 124
# and 158 completion tokens for the two gpt-oss models on this prompt.
_ROUTER_MAX_TOKENS = 700

def _system_prompt() -> str:
    """Build the classifier prompt, naming the fiscal years actually indexed."""
    years = sorted(available_years())
    if years:
        years_line = "Fiscal years currently indexed: " + ", ".join(str(y) for y in years)
    else:
        years_line = (
            "Fiscal years currently indexed: none yet (filings are fetched on demand)"
        )
    # NOTE: str.format() cannot be used here — the prompt contains a literal
    # JSON example, and its braces would be parsed as format fields.
    return _SYSTEM_PROMPT_TEMPLATE.replace("{years_line}", years_line)


# What the retriever should prioritize surfacing, replacing what used to be a
# growing pile of hand-maintained regex keyword lists in retriever.py (one
# per metric: "revenue"/"net sales"/... , "research"/"r&d"/..., etc.). That
# approach only ever covers phrasings someone thought to enumerate — this
# question is answered by the SAME classifier call that already runs per
# query (no added latency/cost), so it generalizes to any phrasing the model
# understands as being about a given metric, not just literal keyword hits.
VALID_FOCUS = {
    "revenue", "rd_expense", "net_income", "operating_income",
    "business_overview", "risk_factors", "legal_proceedings",
    "balance_sheet", "segment_info", "cybersecurity", "other",
}

_SYSTEM_PROMPT_TEMPLATE = """\
You are a query classifier for a financial document RAG system.

Documents already indexed and ready: 10-K annual filings for these companies:
  Technology    : AAPL, MSFT, GOOGL, AMZN
  Banking       : JPM, WFC, BAC, GS
  Asset Mgmt    : BLK, STT, TROW, IVZ
{years_line}

The system is NOT limited to that list — it can fetch and index the latest
10-K for ANY publicly traded US company on demand. So never treat a company
outside the list above as out_of_scope just because it's unfamiliar; extract
it into "other_companies" instead (see below) so it can be looked up.

Classify the user query into exactly one type:
  single_doc   — asks about ONE company in ONE specific year — this includes
                 ANY question a 10-K would answer: financial metrics, but also
                 qualitative ones like business description, products sold,
                 segments, risk factors, or strategy. "What does Apple sell?"
                 is single_doc, NOT out_of_scope — 10-Ks include a full
                 business description (Item 1) precisely for questions like this.
  multi_doc    — compares companies, asks about a sector, or involves multiple firms
  temporal     — asks about a trend or change across MULTIPLE years for one company
  out_of_scope — ONLY for topics no 10-K could ever answer: macro/market
                 trivia (stock prices, crypto), general knowledge unrelated to
                 any company, or the query mentions no identifiable company at
                 all. If a real company is named or implied, it is NOT out_of_scope.

Also extract every company mentioned or clearly implied, as either a ticker
symbol or company name:
  - "tickers": tickers FROM THE INDEXED LIST ABOVE ONLY.
  - "other_companies": any other company/ticker mentioned that is NOT in the
    indexed list above (e.g. "Netflix", "NFLX", "Tesla") — pass through
    whatever the user wrote, ticker or name, don't normalize it.

Also extract any fiscal years mentioned or clearly implied. Use the four-digit
year; do not restrict yourself to the indexed years listed above.
If the user says "last year" or "recent" without a year, include all three years.
If no specific company is mentioned, return empty lists for both company fields.

Also classify what the query is actually asking about, as "focus":
  revenue            — net sales / total revenue
  rd_expense         — research & development spending
  net_income         — net income / earnings / profit / loss
  operating_income   — operating income / income from operations
  business_overview  — what the company does, sells, makes, or its segments/
                        products/industry/strategy (NOT a financial figure)
  risk_factors       — risks, threats, uncertainties the company discloses
  legal_proceedings  — lawsuits, litigation, regulatory/legal disputes
  balance_sheet      — assets, liabilities, cash and cash equivalents, debt levels
  segment_info       — geographic or product/business segment breakdowns
  cybersecurity      — cybersecurity risk management, incidents, governance
  other              — anything else (financial statements in general, MD&A
                        narrative, governance, etc.)

Respond with ONLY valid JSON — no markdown, no extra text:
{
  "query_type": "<type>",
  "tickers": ["TICKER", ...],
  "other_companies": ["Netflix", ...],
  "years": [2023, ...],
  "focus": "<focus>",
  "reasoning": "<one short sentence>"
}"""


class ClassifiedQuery(BaseModel):
    query_type: str
    tickers:    List[str]
    years:      List[int]
    reasoning:  str
    unresolved: List[str] = []      # company/ticker mentions outside the bundled 12
    focus:      str        = "other"  # see VALID_FOCUS
    failed_lookups: List[str] = []   # unresolved mentions that turned out to have
                                      # no SEC filings at all (set by classify_and_ensure)
    ingest_failed:  List[str] = []   # mentions that DID resolve to a real filer, but
                                      # download/parse/embed itself failed (transient —
                                      # distinct from failed_lookups, which means "no
                                      # such filer exists" and would otherwise mislead)
    year_not_available: List[str] = []  # resolved and indexed fine, but not for the
                                         # SPECIFIC fiscal year asked about


def classify_query(query: str) -> ClassifiedQuery:
    """Classify a user query and extract target tickers / years."""
    try:
        # All LLM access goes through llm/client.py (spec section 9 rule 1):
        # ordered free-tier failover, disk cache, budgets, typed errors.
        data, completion = get_llm().complete_json(
            role="router",
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user",   "content": query},
            ],
            temperature=0.0,
            max_tokens=_ROUTER_MAX_TOKENS,
            prompt_version=PROMPT_VERSION,
        )
        logger.debug(
            f"classifier: {completion.provider}:{completion.model} "
            f"tokens={completion.total_tokens} cached={completion.cached}"
        )

        query_type = data.get("query_type", "single_doc")
        if query_type not in VALID_QUERY_TYPES:
            query_type = "single_doc"

        tickers = [t.upper() for t in data.get("tickers", []) if t.upper() in VALID_TICKERS]
        years = []
        for raw_year in data.get("years", []) or []:
            try:
                year = int(raw_year)
            except (TypeError, ValueError):
                continue
            # Plausibility only. Whether the year is INDEXED is decided by
            # routing/resolver.py, which can auto-ingest it or report
            # `year_not_available` — dropping it here would make the query
            # look like it never named a year (K5).
            if _is_plausible_fiscal_year(year) and year not in years:
                years.append(year)

        # Anything the model tagged as a ticker but that isn't in our bundled
        # list is also an unresolved mention (models sometimes put it there
        # despite instructions), in addition to the dedicated field.
        raw_tickers    = [t for t in data.get("tickers", []) if t.upper() not in VALID_TICKERS]
        other_mentions = data.get("other_companies", [])
        unresolved = [m.strip() for m in (raw_tickers + other_mentions) if m and m.strip()]

        focus = data.get("focus", "other")
        if focus not in VALID_FOCUS:
            focus = "other"

        result = ClassifiedQuery(
            query_type=query_type,
            tickers=tickers,
            years=years,
            reasoning=data.get("reasoning", ""),
            unresolved=unresolved,
            focus=focus,
        )
        logger.debug(
            f"Classified '{query[:60]}…' → {result.query_type} | {result.tickers} | "
            f"{result.years} | focus={result.focus} | unresolved={result.unresolved}"
        )
        return result

    except LLMError:
        # Fail loud (P1-05, D7). v1 swallowed every exception here and returned
        # single_doc with no tickers, which query.ask() rendered as "Which
        # company are you asking about?" — so a provider outage was
        # indistinguishable from a user forgetting to name a company. Phase 0
        # found exactly that in production (F1): five live queries, all
        # answered with that message in ~0.2 s.
        raise
    except json.JSONDecodeError as exc:
        raise LLMBadOutput(
            f"classifier reply was not valid JSON: {exc}",
        ) from exc
