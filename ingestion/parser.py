import json
import re
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from io import StringIO
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

import pandas as pd
from bs4 import BeautifulSoup, Tag
from loguru import logger

from config import settings
from models import ContentBlock, ParsedDocument, ParsedSection

# ---------------------------------------------------------------------------
# Section patterns — two levels deep:
#
#  Level 1  "Item N" headers — split the 10-K into major parts
#  Level 2  Financial statement headers — break Item 8 into individual
#           statements so citations say "Consolidated Statements of
#           Operations" instead of the 243k-char "Item 8" blob
#
# Order matters: more-specific patterns must come first.
# ---------------------------------------------------------------------------
SECTION_PATTERNS: List[Tuple[str, str, str]] = [

    # ── Item N patterns (Level 1) ──────────────────────────────────────────
    (r"item\s*1a[\.\s:—–-]*risk\s*factor",          "item_1a_risk_factors", "Item 1A: Risk Factors"),
    (r"item\s*1b[\.\s:—–-]*unresolved",              "item_1b_staff",        "Item 1B: Unresolved Staff Comments"),
    (r"item\s*1c[\.\s:—–-]*cyber",                   "item_1c_cyber",        "Item 1C: Cybersecurity"),
    (r"item\s*1[\.\s:—–-]*business",                 "item_1_business",      "Item 1: Business"),
    (r"item\s*2[\.\s:—–-]*propert",                  "item_2_properties",    "Item 2: Properties"),
    (r"item\s*3[\.\s:—–-]*legal",                    "item_3_legal",         "Item 3: Legal Proceedings"),
    (r"item\s*4[\.\s:—–-]*mine",                     "item_4_mine",          "Item 4: Mine Safety"),
    (r"item\s*5[\.\s:—–-]*market",                   "item_5_market",        "Item 5: Market for Equity"),
    (r"item\s*6[\.\s:—–-]*selected",                 "item_6_selected",      "Item 6: Selected Financial Data"),
    (r"item\s*7a[\.\s:—–-]*quantitative",            "item_7a_market_risk",  "Item 7A: Quantitative Disclosures"),
    (r"item\s*7[\.\s:—–-]*management",               "item_7_mda",           "Item 7: MD&A"),
    (r"item\s*8[\.\s:—–-]*financial",                "item_8_financials",    "Item 8: Financial Statements"),
    (r"item\s*9a[\.\s:—–-]*controls",                "item_9a_controls",     "Item 9A: Controls and Procedures"),
    (r"item\s*9b[\.\s:—–-]*other",                   "item_9b_other",        "Item 9B: Other Information"),
    (r"item\s*9[\.\s:—–-]*change",                   "item_9_accountants",   "Item 9: Disagreements with Accountants"),
    (r"item\s*1[0-4][\.\s:]",                     "item_governance",      "Items 10-14: Corporate Governance"),
    (r"item\s*15[\.\s:—–-]*exhibit",                 "item_15_exhibits",     "Item 15: Exhibits"),

    # ── Financial statement sub-sections (Level 2, inside Item 8) ─────────
    # These give precise citations ("Consolidated Statements of Operations")
    # instead of the giant "Item 8" blob.  Patterns cover naming variants
    # across tech, banking, and asset-management 10-Ks.
    (r"consolidated\s+statements?\s+of\s+operations",
        "fs_income_stmt",   "Consolidated Statements of Operations"),
    (r"consolidated\s+statements?\s+of\s+(income|earnings)",
        "fs_income_stmt",   "Consolidated Statements of Income"),
    (r"consolidated\s+(income|earnings)\s+statements?",
        "fs_income_stmt",   "Consolidated Statements of Income"),
    (r"consolidated\s+balance\s+sheets?",
        "fs_balance_sheet", "Consolidated Balance Sheets"),
    (r"consolidated\s+statements?\s+of\s+financial\s+(condition|position)",
        "fs_balance_sheet", "Consolidated Statements of Financial Condition"),
    # Some bank holding companies (State Street observed) drop "financial"
    # entirely — "Consolidated Statement of Condition", not "...of Financial
    # Condition". Distinct enough from the pattern above to need its own
    # entry rather than making "financial" optional there, which would also
    # start matching unrelated phrases like "consolidated statement of ...
    # condition" in prose that has nothing to do with the balance sheet.
    (r"consolidated\s+statements?\s+of\s+condition\b",
        "fs_balance_sheet", "Consolidated Statement of Condition"),
    (r"consolidated\s+statements?\s+of\s+cash\s+flows?",
        "fs_cash_flow",     "Consolidated Statements of Cash Flows"),
    (r"consolidated\s+statements?\s+of\s+(stockholders|shareholders|changes\s+in\s+equity)",
        "fs_equity",        "Consolidated Statements of Equity"),
    (r"notes?\s+to\s+(consolidated\s+)?financial\s+statements?",
        "fs_notes",         "Notes to Financial Statements"),

    # ── Standalone title fallbacks (companies that omit "Item N") ──────────
    (r"^risk\s+factors$",                           "item_1a_risk_factors", "Item 1A: Risk Factors"),
    (r"management.s\s+discussion\s+and\s+analysis", "item_7_mda",           "Item 7: MD&A"),
    # Some bank annual-report exhibits (e.g. WFC's EX-13) label MD&A "Financial
    # Review" instead — must be anchored ($) so it doesn't match compound
    # headings like "Financial Review — Risk Factors" in a table of contents.
    (r"^financial\s+review$",                       "item_7_mda",           "Item 7: MD&A"),
    (r"^quantitative\s+and\s+qualitative",          "item_7a_market_risk",  "Item 7A: Quantitative Disclosures"),
    (r"financial\s+statements\s+and\s+supplementary","item_8_financials",   "Item 8: Financial Statements"),
    (r"^controls\s+and\s+procedures$",              "item_9a_controls",     "Item 9A: Controls and Procedures"),
]

# Expected ordering of section_ids — used to discard out-of-order detections
# (cross-references near the end of filings that fool "keep last" strategy).
# Anchored patterns for the bank-style recovery pass.
# These are stricter than SECTION_PATTERNS: the line must START and END with
# the section title so that cross-references like "Consolidated balance sheets
# analysis" or "Impact of derivatives on the Consolidated statements of income"
# don't produce false matches.
_FS_RECOVERY_PATTERNS: List[Tuple[str, str, str]] = [
    # Standard naming (JPM, GS, most companies)
    (r"^consolidated\s+statements?\s+of\s+(income|earnings)\s*$",
        "fs_income_stmt",   "Consolidated Statements of Income"),
    # Tech-industry naming (AAPL, GOOGL, MSFT, ...) — was entirely absent
    # from this anchored list, so it never got recovered from a TOC/index
    # table cell even though the un-anchored SECTION_PATTERNS above already
    # recognizes it in plain text.
    (r"^consolidated\s+statements?\s+of\s+operations\s*$",
        "fs_income_stmt",   "Consolidated Statements of Operations"),
    (r"^consolidated\s+(income|earnings)\s+statements?\s*$",
        "fs_income_stmt",   "Consolidated Statements of Income"),
    (r"^consolidated\s+balance\s+sheets?\s*$",
        "fs_balance_sheet", "Consolidated Balance Sheets"),
    (r"^consolidated\s+statements?\s+of\s+financial\s+(condition|position)\s*$",
        "fs_balance_sheet", "Consolidated Statements of Financial Condition"),
    (r"^consolidated\s+statements?\s+of\s+condition\s*$",
        "fs_balance_sheet", "Consolidated Statement of Condition"),
    (r"^consolidated\s+statements?\s+of\s+cash\s+flows?\s*$",
        "fs_cash_flow",     "Consolidated Statements of Cash Flows"),
    (r"^notes?\s+to\s+(consolidated\s+)?financial\s+statements?\s*$",
        "fs_notes",         "Notes to Financial Statements"),
    (r"^consolidated\s+statements?\s+of\s+(stockholders|shareholders|changes\s+in\s+equity)\s*$",
        "fs_equity",        "Consolidated Statements of Equity"),
    # BAC / WFC variants — all-caps or abbreviated headers
    (r"^consolidated\s+statement\s+of\s+income\s*$",
        "fs_income_stmt",   "Consolidated Statement of Income"),
    (r"^consolidated\s+balance\s+sheet\s*$",
        "fs_balance_sheet", "Consolidated Balance Sheet"),
    (r"^consolidated\s+statement\s+of\s+cash\s+flows?\s*$",
        "fs_cash_flow",     "Consolidated Statement of Cash Flows"),
    (r"^financial\s+statements?\s*$",
        "fs_income_stmt",   "Financial Statements"),
]

# Anchored recovery patterns for Item-level headers that some filers (TROW,
# IVZ, ...) render as a <table><td> cell — often a table-of-contents hyperlink
# — rather than plain paragraph text. _extract_tables() decomposes non-data
# tables entirely, so without a sentinel this text is lost before it ever
# reaches the plain-text stream, and no amount of post-hoc line scanning
# (unlike the plain-TOC-zone case AAPL/NVDA hit) can recover it.
#
# P4-00 (D25) extends this list from "Item 1. Business" alone to every Item
# heading the text path audits. D3-00 recorded that doing so in Phase 3
# recovered AMZN's Item 1C but *cost* AMZN's Item 1, because a sentinel then
# won outright over the plain-text heading. That is no longer how a sentinel is
# used: _collect_candidates scores it like any other candidate and
# _assign_sections resolves the competition globally, so a table-of-contents
# sentinel loses to a real heading instead of displacing it.
_ITEM_RECOVERY_PATTERNS: List[Tuple[str, str, str]] = [
    (r"^item\s*1\.?\s*business$",                 "item_1_business",      "Item 1: Business"),
    (r"^item\s*1a\.?\s*risk\s*factors?$",         "item_1a_risk_factors", "Item 1A: Risk Factors"),
    (r"^item\s*1b\.?\s*unresolved\s*staff\s*comments?$",
        "item_1b_staff",        "Item 1B: Unresolved Staff Comments"),
    (r"^item\s*1c\.?\s*cybersecurity$",           "item_1c_cyber",        "Item 1C: Cybersecurity"),
    (r"^item\s*2\.?\s*propert(y|ies)$",           "item_2_properties",    "Item 2: Properties"),
    (r"^item\s*3\.?\s*legal\s*proceedings?$",     "item_3_legal",         "Item 3: Legal Proceedings"),
    (r"^item\s*4\.?\s*mine\s*safety.*$",          "item_4_mine",          "Item 4: Mine Safety"),
    (r"^item\s*5\.?\s*market\s*for.*$",           "item_5_market",        "Item 5: Market for Equity"),
    (r"^item\s*7\.?\s*management.{0,3}s\s*discussion.*$",
        "item_7_mda",           "Item 7: MD&A"),
    (r"^item\s*7a\.?\s*quantitative.*$",          "item_7a_market_risk",  "Item 7A: Quantitative Disclosures"),
    (r"^item\s*8\.?\s*financial\s*statements.*$", "item_8_financials",    "Item 8: Financial Statements"),
    (r"^item\s*9a\.?\s*controls\s*and\s*procedures$",
        "item_9a_controls",     "Item 9A: Controls and Procedures"),
]

_SECTION_PRIORITY: Dict[str, int] = {
    "item_1_business":     10,  "item_1a_risk_factors": 15,
    "item_1b_staff":       16,  "item_1c_cyber":        17,
    "item_2_properties":    20,
    "item_3_legal":        30,  "item_4_mine":          40,
    "item_5_market":       50,  "item_6_selected":      60,
    "item_7_mda":          70,  "item_7a_market_risk":  75,
    "item_8_financials":   80,
    "fs_income_stmt":      81,  "fs_balance_sheet":     82,
    "fs_cash_flow":        83,  "fs_equity":            84,
    "fs_notes":            85,
    "item_9_accountants":  90,  "item_9a_controls":     91,
    "item_9b_other":       92,  "item_governance":      100,
    "item_15_exhibits":    150,
}


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

def _strip_ixbrl(html: str) -> str:
    html = re.sub(r"^\s*<\?xml[^?]*\?>", "", html, count=1, flags=re.IGNORECASE)
    html = re.sub(r"<(/?)ix:[^>]*>",      "", html, flags=re.IGNORECASE)
    html = re.sub(r"<(/?)xbrli:[^>]*>",   "", html, flags=re.IGNORECASE)
    html = re.sub(r"<(/?)xbrldi:[^>]*>",  "", html, flags=re.IGNORECASE)
    return html


# ---------------------------------------------------------------------------
# Table utilities
# ---------------------------------------------------------------------------

def _is_data_table(tag: Tag) -> bool:
    rows = tag.find_all("tr", recursive=True)
    if len(rows) < 2:
        return False
    first_row_cells = rows[0].find_all(["td", "th"])
    if len(first_row_cells) < 2:
        return False
    return len(tag.get_text(strip=True)) > 60


def _table_to_markdown(df: pd.DataFrame) -> str:
    df       = df.fillna("").astype(str)
    headers  = list(df.columns)
    rows     = df.values.tolist()
    # Pandas names XBRL columns as integers (0, 1, 2, ...) when no <th>
    # headers are present.  Replace them with empty strings so the
    # markdown doesn't start with "| 0 | 1 | 2 | ..." which dominates
    # BM25 tokens and makes table chunks impossible to retrieve.
    clean_headers = ["" if str(h).lstrip("-").isdigit() else str(h) for h in headers]

    h_line   = "| " + " | ".join(clean_headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in clean_headers)  + " |"
    d_lines  = ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join([h_line, sep_line, *d_lines])


def _extract_tables(soup: BeautifulSoup, start_idx: int = 0) -> Tuple[Dict[str, Tuple[str, List]], int]:
    """Extract data tables, returning (placeholder -> (markdown, raw), next_free_idx).

    start_idx/next_free_idx let callers extract tables from multiple
    documents into one shared placeholder namespace (see
    _split_embedded_documents) without collisions.
    """
    tables: Dict[str, Tuple[str, List]] = {}
    idx = start_idx

    for tag in soup.find_all("table"):
        if tag.parent is None:
            continue
        if not _is_data_table(tag):
            tag.decompose()
            continue

        table_html  = str(tag)
        placeholder = f"<<<TABLE_{idx}>>>"
        idx        += 1

        try:
            dfs = pd.read_html(StringIO(table_html), flavor="lxml")
            if dfs and not dfs[0].empty:
                df       = dfs[0]
                markdown = _table_to_markdown(df)
                raw      = df.fillna("").astype(str).values.tolist()
                tables[placeholder] = (markdown, raw)
                tag.replace_with(soup.new_string(f"\n{placeholder}\n"))
            else:
                tag.decompose()
        except ValueError as exc:
            logger.debug(f"Table {idx}: pd.read_html ValueError — {exc}")
            tag.decompose()
        except Exception as exc:
            logger.debug(f"Table {idx}: {type(exc).__name__}: {exc}")
            tag.decompose()

    return tables, idx


# ---------------------------------------------------------------------------
# Section detection
# ---------------------------------------------------------------------------

def _match_section(text: str) -> Optional[Tuple[str, str]]:
    t = text.lower().strip()
    for pattern, section_id, title in SECTION_PATTERNS:
        if re.search(pattern, t):
            return section_id, title
    return None


def _match_section_at(
    lines: List[str], i: int, total: int
) -> Optional[Tuple[str, str, str, int]]:
    """
    Returns ``(section_id, title, matched_text, span_end)`` or ``None``.

    ``matched_text`` is the text the pattern actually matched — the line itself,
    or the joined pair described below — and ``span_end`` is the last line index
    consumed, so a caller scoring this candidate looks for the following prose
    past the join rather than at the second half of the heading.

    _match_section on lines[i], with a lookahead fallback: some filers split
    "Item N." and its description across two separate lines/text nodes
    (AMZN, WFC, TROW, ...) — a short "Item N" line that doesn't match alone
    is joined with the next non-empty line and retried.

    Some filers (MSFT, BLK) go further and split the description WORD
    itself across two adjacent inline tags (e.g. "ITEM 1. B" / "USINESS"),
    so a space-joined retry still fails ("B usiness" != "business"). If the
    space join doesn't match, also retry with no separator at all.
    """
    stripped = lines[i].strip()
    match = _match_section(stripped)
    if match:
        return match[0], match[1], stripped, i

    if len(stripped) < 35 and re.search(r"^item\s*\d", stripped.lower()):
        for j in range(i + 1, min(i + 5, total)):
            next_stripped = lines[j].strip()
            if next_stripped and len(next_stripped) < 100:
                for joined in (stripped + " " + next_stripped, stripped + next_stripped):
                    match = _match_section(joined)
                    if match:
                        return match[0], match[1], joined, j
                break

    return None


def _collect_occurrences(
    lines: List[str], skip_start: int, skip_end: int
) -> Tuple[Dict[str, List[Tuple[int, str, str]]], Dict[str, Tuple[int, str, str]]]:
    """Scan lines[skip_start:skip_end] for section-header candidates."""
    total = len(lines)

    all_occurrences: Dict[str, List[Tuple[int, str, str]]] = defaultdict(list)
    # Sentinels injected by _annotate_fs_header_tables take absolute priority —
    # they point directly at the table that is the section header, bypassing the
    # last/first occurrence heuristics that work on plain text.
    sentinel_hits: Dict[str, Tuple[int, str, str]] = {}

    for i, line in enumerate(lines):
        if i < skip_start or i > skip_end:
            continue
        stripped = line.strip()
        if not stripped or len(stripped) > 250:
            continue

        if stripped.startswith(_FS_SENTINEL_PREFIX):
            title = stripped[len(_FS_SENTINEL_PREFIX):]
            for _, sid, t in _HEADER_TABLE_RECOVERY_PATTERNS:
                if t.lower() == title.lower():
                    sentinel_hits[sid] = (i, sid, t)
                    break
            continue

        match = _match_section_at(lines, i, total)

        if match:
            section_id, title = match[0], match[1]
            all_occurrences[section_id].append((i, section_id, title))

    return all_occurrences, sentinel_hits


# Descriptions given to the LLM recovery pass below — deliberately phrased
# by MEANING (what the statement contains), not by wording, since filer
# wording is exactly what regex can't keep up with.
_FS_LLM_LABELS: Dict[str, str] = {
    "fs_income_stmt":   "the primary income statement / statement of operations (net income, revenue, expenses)",
    "fs_balance_sheet": "the balance sheet / statement of financial condition (assets, liabilities, equity balances at a point in time)",
    "fs_cash_flow":     "the statement of cash flows (operating/investing/financing activities)",
    "fs_equity":        "the statement of stockholders'/shareholders' equity (changes in equity across the period)",
    "fs_notes":         "the start of the notes to the financial statements (often begins with 'Note 1' or a similar first footnote)",
}

_FS_LLM_TITLES: Dict[str, str] = {
    "fs_income_stmt":   "Consolidated Statements of Operations",
    "fs_balance_sheet": "Consolidated Balance Sheets",
    "fs_cash_flow":     "Consolidated Statements of Cash Flows",
    "fs_equity":        "Consolidated Statements of Equity",
    "fs_notes":         "Notes to Financial Statements",
}


def _llm_locate_fs_headings(
    lines:   List[str],
    start:   int,
    end:     int,
    missing: List[str],
) -> List[Tuple[int, str, str]]:
    """
    Regex can't enumerate every filer's wording/structure for the 5 standard
    financial statements — verified against real filings: "Statements of
    Operations" vs "...of Income" alone broke AAPL/GOOGL/MSFT, and filers
    present these 5 statements in different relative orders (GOOGL: Balance
    Sheet before Income Statement; most others: the reverse). Rather than
    keep hand-enumerating wording variants, ask an LLM to locate each
    missing statement's heading directly within lines[start:end] by what it
    IS, not what it's called.

    The LLM returns VERBATIM heading text, never a line number — it doesn't
    see the document indexed the way this code does, so any line number it
    reported would be a guess. This code locates that exact text itself via
    substring search, so a wrong/hallucinated answer just fails to match
    (safe no-op) instead of silently mis-splitting the document.
    """
    span = lines[start:end]
    if not span or not missing:
        return []

    from llm import get_client as get_llm
    client = get_llm()

    # 500 lines routinely hit Groq's per-request TPM limit outright (verified:
    # "Requested 14144/21038/... tokens, Limit 6000" 413 errors on real
    # filings — this isn't a burst/retry-able rate limit, the single request
    # itself was oversized). 100 lines keeps individual requests comfortably
    # under that ceiling; MAX_PAGES raised to still cover a comparable total
    # span across more, smaller pages.
    PAGE_LINES = 100
    MAX_PAGES  = 15
    recovered: List[Tuple[int, str, str]] = []
    found: set = set()

    for page_idx, page_start in enumerate(range(0, len(span), PAGE_LINES)):
        if len(found) == len(missing) or page_idx >= MAX_PAGES:
            break
        page = span[page_start:page_start + PAGE_LINES]
        page_text = "\n".join(page)
        if not page_text.strip():
            continue

        still_needed = [sid for sid in missing if sid not in found]
        prompt = (
            "This is a slice of a 10-K SEC filing's financial statements. "
            "Table content has been replaced with <<<TABLE_N>>> placeholders. "
            "Find the EXACT heading line (a short standalone title — NOT a "
            "passing mention inside a sentence or footnote) that introduces "
            "each of these, if present on this page:\n"
            + "\n".join(f"- {sid}: {_FS_LLM_LABELS[sid]}" for sid in still_needed)
            + "\n\nRespond with ONLY JSON mapping section id to the heading line "
            "copied EXACTLY as it appears verbatim (do not paraphrase). Omit any "
            "id not present on this page. Example: "
            '{"fs_income_stmt": "CONSOLIDATED STATEMENTS OF OPERATIONS"}'
            f"\n\nTEXT:\n{page_text}"
        )
        try:
            # Through llm/client.py: cached, budgeted, failover-capable.
            # The cache matters here — this runs per page per filing, which is
            # the largest single consumer of free-tier requests in the project.
            data, _completion = client.complete_json(
                role="router",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=900,
                prompt_version="fs-heading-v1",
                repair=False,
            )
        except Exception as exc:
            logger.warning(f"LLM fs-heading recovery failed on a page: {exc}")
            continue

        for sid, heading_text in data.items():
            if sid in found or sid not in missing or not isinstance(heading_text, str):
                continue
            heading_text = heading_text.strip()
            if not heading_text:
                continue
            for i, line in enumerate(page):
                if heading_text.lower() in line.lower():
                    abs_line = start + page_start + i
                    recovered.append((abs_line, sid, _FS_LLM_TITLES[sid]))
                    found.add(sid)
                    logger.info(f"  LLM-recovered '{_FS_LLM_TITLES[sid]}' at line {abs_line}: {line.strip()[:80]}")
                    break

    return recovered


# ---------------------------------------------------------------------------
# Candidate scoring and global boundary assignment (P4-00, D25)
# ---------------------------------------------------------------------------
#
# What this replaces, and why
# ---------------------------
# Until P4-00 the parser kept exactly ONE occurrence per section id — the first
# one past a 15 % table-of-contents skip zone, for item_* sections — and then
# applied a greedy monotonic Item-priority filter. P3-00 measured the result
# over the whole parsed corpus with scripts/audit_sections.py: Item 1A was
# missing from 17 of 39 filings and Item 7 from 17 of 39. Two structural causes,
# both recorded in docs/DECISIONS.md under D3-00:
#
#   * The skip zone is a FRACTION of the document, not a bound on where a table
#     of contents can end. Filers whose primary document is the whole annual
#     report start Part I well inside it — JPM FY2024's real
#     "Item 1A. Risk Factors." heading is at line 229 of 6,897 (3.3 %),
#     GS FY2024's at 9.1 %, STT FY2024's at 10.9 %, MSFT FY2026's at 14.2 %.
#     The real heading was never a candidate, so the preceding section absorbed
#     its text (GS FY2024's Item 1 ran to 295,735 characters).
#   * The greedy filter is irreversible. One early false candidate raises the
#     priority watermark and silently drops every lower-priority section after
#     it. TROW FY2024 matched "Items 10-14" (priority 100) before its real
#     Item 7 heading at line 680, so Items 5, 7, 7A and 8 were all discarded;
#     IVZ FY2024 lost Items 3, 4, 5, 7 and 7A the same way to an early Item 8.
#     This is why D3-00 found local heuristics could not be made net-positive:
#     each new early candidate both misplaced its own section and blocked the
#     rest.
#
# The replacement makes both decisions globally rather than locally:
#
#   1. Every matching line in the segment is a candidate (_collect_candidates).
#      There is no skip zone for item_* sections: a table of contents is now
#      rejected by what it LOOKS like, not by where it sits.
#   2. Each candidate is scored on evidence that it is a heading rather than a
#      contents entry or a cross-reference (_score_candidate). Lines that refer
#      to a section ("Refer to Part I, Item 1A: Risk Factors on pages 10-37")
#      or mention it mid-sentence ("elsewhere in this Item 1A") are rejected
#      outright, not merely down-weighted, because no amount of surrounding
#      evidence makes them the heading.
#   3. _assign_sections picks the maximum-weight subsequence whose line numbers
#      AND Item priorities both strictly increase — a weighted longest-
#      increasing subsequence. Choosing a bad early candidate now has to pay
#      for every section it blocks, so the chain that keeps Items 1, 1A, 1B,
#      1C, 2, 3, 4, 5, 7, 7A and 8 beats the chain that stops at an early
#      "Items 10-14".
#
# fs_* sub-sections deliberately stay OUT of the assignment and keep their
# existing selection rule: filers present the five statements in different
# relative orders (GOOGL puts its balance sheet before its income statement),
# so an ordering constraint among them drops real statements. See
# _select_and_validate's docstring.

class _Candidate(NamedTuple):
    """One scored possibility for where a section begins."""

    line:       int
    section_id: str
    title:      str
    score:      int
    has_prose:  bool  # is there real prose shortly after it (see _followed_by_prose)
    source:     str   # "text" | "sentinel"


_TABLE_PLACEHOLDER_RE = re.compile(r"^<<<TABLE_\d+>>>$")

# A line that REFERS to a section is never that section's heading. These are the
# forms the corpus actually contains: "Refer to Part I, Item 1A: Risk Factors on
# pages 10-37" (JPM), "In addition to risks described elsewhere in this Item 1A"
# (Amazon's own Item 1A prose), "see Item 1A Risk Factors of this Annual Report"
# (GOOGL), "...are incorporated by reference in this Item 1" (BAC).
_CROSS_REFERENCE_RE = re.compile(
    r"\b(refer(?:s|red)?\s+to"
    r"|see\b"
    r"|described\s+in|discussed\s+in|contained\s+in|set\s+forth\s+in"
    r"|elsewhere\s+in"
    r"|incorporated\s+by\s+reference"
    r"|on\s+pages?\b|pages?\s+\d)",
    re.I,
)

# Filers prefix a heading with its Part — "PART I - ITEM 1A. RISK FACTORS",
# "Part II, Item 1A. Risk Factors" — and that is still an anchored heading.
_PART_PREFIX_RE = re.compile(r"^\s*part\s+[ivx]+\s*[,.:;—–-]*\s*", re.I)

# A contents entry carries its page number: "Item 1A. Risk Factors    10".
_TRAILING_PAGE_NUMBER_RE = re.compile(r"[\s.·…]\d{1,3}\s*$")

# A POINTER sentence says where the disclosure is, rather than being it. This is
# deliberately much narrower than _CROSS_REFERENCE_RE, which disqualifies a
# candidate HEADING: ordinary disclosure prose mentions other sections all the
# time ("...appearing elsewhere in this Annual Report on Form 10-K"), so only an
# explicit page or incorporation pointer counts. JPM FY2024's entire Item 7 body
# is one such sentence: the MD&A "appears on pages 44-115 of this 2024 Form
# 10-K".
_POINTER_RE = re.compile(
    r"(incorporated\s+(herein\s+)?by\s+reference"
    r"|^\s*refer\s+to\b"
    r"|\bon\s+pages?\s+\d"
    r"|\bpages?\s+\d+\s*[–—-]\s*\d+)",
    re.I,
)

# A heading is a short standalone line. The longest real heading in the corpus
# ("Item 7. Management's Discussion and Analysis of Financial Condition and
# Results of Operations") is 95 characters; 120 leaves room for a Part prefix.
_HEADING_MAX_LEN = 120

# A line long enough to be the disclosure itself rather than another list entry.
# Set from what a contents line cannot be, not fitted: the longest contents
# entry in the corpus is well under 200 characters, and the opening sentence of
# a real Item 1A ("The following discussion sets forth the material risk
# factors...") is well over it.
_PROSE_MIN_LEN = 200

# How far past a heading to look for that prose, counted in non-empty lines.
# Filers put a sub-heading, a date line and a table between a heading and its
# first paragraph; 15 covers every case in the corpus without reaching into the
# next section.
_PROSE_LOOKAHEAD = 15


def _is_prose(line: str) -> bool:
    """A line that is the disclosure, not a heading, a placeholder or a marker."""
    stripped = line.strip()
    if len(stripped) < _PROSE_MIN_LEN:
        return False
    return not (
        _TABLE_PLACEHOLDER_RE.match(stripped)
        or stripped.startswith(_FS_SENTINEL_PREFIX)
    )


def _followed_by_prose(lines: List[str], span_end: int, total: int) -> bool:
    """Does the section behind this candidate actually have content?

    This is the feature that separates a real heading from a contents entry
    without any assumption about WHERE the contents table sits: a contents entry
    is followed by more contents entries, a real heading by the disclosure
    itself.

    Two bounds make it a question about THIS section rather than about the
    neighbourhood:

    * the scan stops at the next heading candidate, so prose belonging to a
      later section is not credited to this one. Without that bound a bank's
      cross-reference index passes — JPM FY2024 prints Items 6, 7, 7A, 8, 9 and
      9A as consecutive one-line pointers, and the real prose under Item 9A is
      only eleven lines below the Item 7 heading;
    * a pointer sentence does not count as content (_POINTER_RE). JPM's Item 7
      body is one sentence saying the MD&A "appears on pages 44-115 of this
      2024 Form 10-K" — long enough to pass a length test, and still not an
      MD&A.
    """
    seen = 0
    for j in range(span_end + 1, total):
        if not lines[j].strip():
            continue
        if _match_section_at(lines, j, total) is not None:
            break
        if _is_prose(lines[j]) and not _POINTER_RE.search(lines[j]):
            return True
        seen += 1
        if seen >= _PROSE_LOOKAHEAD:
            break
    return False


def _next_is_section_heading(lines: List[str], span_end: int, total: int) -> bool:
    """Is the next non-empty line itself a section match?

    A run of section matches with nothing between them is the shape of a table
    of contents. ``span_end`` (not ``line``) is the starting point so that the
    second half of a heading split across two lines — Netflix writes
    "Item 1A." then "Risk Factors" — is not mistaken for the next entry in a
    list.
    """
    for j in range(span_end + 1, total):
        if not lines[j].strip():
            continue
        return _match_section_at(lines, j, total) is not None
    return False


def _anchored_section_ids(text: str) -> set:
    """Section ids whose pattern matches at the START of ``text``.

    A heading begins with its own name. Any optional Part prefix is stripped
    first so "PART I - ITEM 1A. RISK FACTORS" still counts as anchored — the
    spellings pinned in tests/unit/test_parser_sections.py.
    """
    rest = _PART_PREFIX_RE.sub("", text.lower().strip())
    return {
        section_id
        for pattern, section_id, _title in SECTION_PATTERNS
        if re.match(pattern, rest)
    }


def _score_candidate(
    lines:        List[str],
    line_no:      int,
    span_end:     int,
    total:        int,
    matched_text: str,
    section_id:   str,
    title:        str,
    source:       str,
) -> Optional[_Candidate]:
    """Score one possible heading, or reject it outright.

    Rejection (returning None) is reserved for the two shapes that can never be
    a heading however they score: a cross-reference to the section, and a
    mention of it inside a sentence. Everything else is a matter of degree and
    is left to _assign_sections to weigh against the alternatives.
    """
    stripped = lines[line_no].strip()

    if source == "text":
        if _CROSS_REFERENCE_RE.search(matched_text):
            return None
        if section_id not in _anchored_section_ids(matched_text):
            return None
        explicit_item = bool(re.match(r"item\s*\d", _PART_PREFIX_RE.sub("", matched_text.lower().strip())))
    else:
        # A sentinel is a heading recovered verbatim from a table cell by an
        # anchored ^...$ pattern, so it is anchored and heading-shaped by
        # construction. It is still only a candidate: before P4-00 a sentinel
        # won outright, which is why extending _ITEM_RECOVERY_PATTERNS cost
        # Amazon its Item 1 (D3-00).
        explicit_item = True

    score = 0
    if source == "sentinel":
        score += 2
    if explicit_item:
        score += 4                     # "Item 7. ..." beats a bare-title fallback
    if len(stripped) <= _HEADING_MAX_LEN:
        score += 3
    if _TRAILING_PAGE_NUMBER_RE.search(stripped):
        score -= 5                     # a contents entry carries its page number
    if _next_is_section_heading(lines, span_end, total):
        score -= 4                     # a run of headings is a contents list

    # Whether the section has content is not one signal among several — it
    # decides which of the two weight classes the candidate falls into. See
    # _assign_sections.
    has_prose = _followed_by_prose(lines, span_end, total)

    return _Candidate(line_no, section_id, title, score, has_prose, source)


def _collect_candidates(
    lines: List[str], skip_end: int
) -> List[_Candidate]:
    """Every scored item_* heading candidate in lines[:skip_end].

    Unlike _collect_occurrences there is no ``skip_start``: the whole document
    is scanned and contents entries are rejected on their own evidence.
    ``skip_end`` is kept because the tail of a filing is an exhibit index whose
    entries are contents entries by nature.
    """
    total = len(lines)
    candidates: List[_Candidate] = []

    for i, line in enumerate(lines):
        if i > skip_end:
            break
        stripped = line.strip()
        if not stripped or len(stripped) > 250:
            continue

        if stripped.startswith(_FS_SENTINEL_PREFIX):
            title = stripped[len(_FS_SENTINEL_PREFIX):]
            for _pattern, sid, sentinel_title in _HEADER_TABLE_RECOVERY_PATTERNS:
                if sentinel_title.lower() == title.lower():
                    if sid.startswith("fs_"):
                        break        # fs_* sentinels are handled by the fs_ path
                    scored = _score_candidate(
                        lines, i, i, total, sentinel_title, sid, sentinel_title, "sentinel"
                    )
                    if scored is not None:
                        candidates.append(scored)
                    break
            continue

        match = _match_section_at(lines, i, total)
        if not match:
            continue
        section_id, title, matched_text, span_end = match
        if section_id.startswith("fs_"):
            continue
        scored = _score_candidate(
            lines, i, span_end, total, matched_text, section_id, title, "text"
        )
        if scored is not None:
            candidates.append(scored)

    return candidates


def _assign_sections(candidates: Iterable[_Candidate]) -> List[_Candidate]:
    """Maximum-weight subsequence with strictly increasing line AND priority.

    This is the whole point of P4-00. Because Item priorities are distinct, a
    strictly-increasing-priority chain holds at most one candidate per section
    id, so picking the chain IS picking one boundary per section — the two
    decisions the old code made separately and greedily (keep the first
    occurrence; then drop anything out of order) are made together here.

    Weighting is in two classes, because a boundary is only worth having if the
    section behind it has content:

      * a candidate with prose after it is worth ``20 + score`` (floored at 1),
        so among real headings the constant dominates and a chain that keeps one
        more section beats a chain of fewer, better-looking ones;
      * a candidate with no prose after it is worth exactly 1.

    The second class is what stops a bank's mid-document Form 10-K
    cross-reference index from winning. JPM, WFC, GS and BAC satisfy Items 3,
    5, 7, 7A and 8 by pointing at their annual report, and print that pointer
    list as a run of perfectly-formed Item headings each followed by one line of
    "Refer to pages 44-115". Those entries are anchored, explicit and
    heading-shaped, so on score alone a run of nine of them outranks the two or
    three real sections elsewhere in the document, and before this rule the
    assignment picked them: JPM FY2024's Item 7 came out as a 300-character
    pointer. At weight 1 a run of nine such entries is worth less than one real
    section, which is the honest ordering — a 300-character pointer is not an
    answerable MD&A.

    Deterministic: candidates are sorted by (line, priority) and ties in the
    dynamic program resolve to the earliest index.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (c.line, _SECTION_PRIORITY.get(c.section_id, 500), c.section_id),
    )
    n = len(ordered)
    if n == 0:
        return []

    priority = [_SECTION_PRIORITY.get(c.section_id, 500) for c in ordered]
    weight   = [max(1, 20 + c.score) if c.has_prose else 1 for c in ordered]
    best     = list(weight)
    prev     = [-1] * n

    for j in range(n):
        for k in range(j):
            if ordered[k].line < ordered[j].line and priority[k] < priority[j]:
                through_k = best[k] + weight[j]
                if through_k > best[j]:
                    best[j] = through_k
                    prev[j] = k

    end = max(range(n), key=lambda idx: (best[idx], -idx))
    chain: List[_Candidate] = []
    while end != -1:
        chain.append(ordered[end])
        end = prev[end]
    chain.reverse()
    return chain


def _assign_sections_with_second_chance(
    candidates: Iterable[_Candidate],
) -> List[_Candidate]:
    """_assign_sections, then one more pass over the section ids it dropped.

    One chain assumes the document lays its sections out in Form 10-K Item
    order. That holds for a 10-K, but not for an annual-report exhibit
    incorporated by reference: Wells Fargo's EX-13 puts its "Financial Review"
    (Item 7) before its "Risk Factors" (Item 1A), because it is ordered by the
    bank's own layout. A single increasing chain has to drop one of the two
    wholesale, and it drops Item 1A — 95,265 characters of real risk factors in
    FY2023 — to keep the longer MD&A. Nor does one chain fit a document that
    interleaves a Form 10-K skeleton with the annual report it points at.

    So ids with no candidate at all in the first chain get a second assignment
    among themselves. The result is internally ordered, is merged by line
    position, and is restricted to candidates with prose after them, so this
    recovers a block of real sections laid out in another order without
    re-admitting the contents entries and cross-reference stubs the first pass
    rejected.

    Deliberately NOT extended to ids the first chain filled with a content-free
    entry. Doing that was implemented and measured: it recovers JPM's real MD&A
    (Item 7 usable 25 of 40 to 28) and costs BAC its Risk Factors (Item 1A 40 of
    40 to 37), because the recovered BAC boundary produces a longer slice than
    the right one and the "longest instance keeps the canonical id" rule then
    prefers it. The corpus total is 298 either way. The numbers are in
    reports/phase4/REPORT.md; a filing that interleaves a Form 10-K skeleton
    with the annual report it points at needs the two to be separated, not one
    more selection rule.
    """
    pool = list(candidates)   # consumed twice
    first = _assign_sections(pool)
    taken = {c.section_id for c in first}
    leftover = [
        c for c in pool
        if c.section_id not in taken and c.has_prose
    ]
    if not leftover:
        return first
    return sorted(first + _assign_sections(leftover), key=lambda c: c.line)


def _select_and_validate(
    lines: List[str],
    all_occurrences: Dict[str, List[Tuple[int, str, str]]],
    sentinel_hits: Dict[str, Tuple[int, str, str]],
    item_candidates: List[_Candidate],
    skip_start: int,
    skip_end: int,
) -> List[Tuple[int, str, str]]:
    """
    Hybrid boundary selection — different strategies for Item-level vs
    financial-statement sub-sections.

    item_* sections  → the maximum-weight chain over ALL scored candidates
      (_collect_candidates then _assign_sections, P4-00/D25). Rewritten
      because "first occurrence past the 15 % TOC zone" misses the real
      heading whenever Part I starts inside that zone, which is every filer
      whose primary document is the whole annual report. See the block
      comment above _Candidate for the measurements behind the change.

    fs_* sub-sections → LAST HEADING-LIKE occurrence inside the valid window.
      Rationale: fs_* headers ("Consolidated Statements of Operations")
      appear first in the Item 8 mini-table-of-contents, then again as
      the actual statement header. "Keep first" would pick the TOC
      listing (tiny content); "keep last" picks the actual statement —
      EXCEPT the plain-text scan also matches prose that merely mentions
      the phrase in passing ("...shown in the Consolidated Statements of
      Operations for 2024..." inside a footnote, or the auditor's report's
      "...the related consolidated statements of operations, comprehensive
      income..."). Verified against AAPL's actual filing: these trailing
      prose mentions run 100-400+ chars and land AFTER the true heading
      line (which is a short, standalone, ALL-CAPS line), so blind "last
      occurrence" picked a sentence out of the auditor's opinion letter
      instead of the real 38-char heading. Restricting to short
      (heading-length) lines before taking the last one fixes this without
      the "first occurrence" TOC problem returning.

    Item ordering is enforced inside the assignment itself rather than by a
    second greedy pass, so a misdetection is weighed against what it would
    cost instead of silently winning. fs_* sub-sections are spliced in
    afterward with no ordering constraint among themselves: different filers present the 5 standard
    financial statements in different relative orders (verified: GOOGL
    presents its Balance Sheet BEFORE its Income Statement; most others do
    the reverse), so forcing all of them through one monotonically-
    increasing priority sequence causes a real, validly-positioned
    statement to be dropped as "out of order" whenever a different filer's
    ordering doesn't match the assumed one.
    """
    # A true section heading is a short, standalone line; a prose sentence
    # that happens to contain the same phrase runs much longer.
    _FS_HEADING_MAX_LEN = 100

    selected_fs: Dict[str, Tuple[int, str, str]] = {}

    # fs_* selection is unchanged by P4-00. Its failure mode is different from
    # the item_* one (a prose mention of a statement title landing after the
    # real heading, not a skip zone or an ordering filter), the statements have
    # no reliable relative order to assign against, and leaving it alone keeps
    # the before/after of this change attributable to the item_* rewrite.
    for section_id, occurrences in all_occurrences.items():
        if not section_id.startswith("fs_"):
            continue
        if section_id in sentinel_hits:
            selected_fs[section_id] = sentinel_hits[section_id]   # sentinel wins
            continue
        heading_like = [
            o for o in occurrences
            if len(lines[o[0]].strip()) <= _FS_HEADING_MAX_LEN
        ]
        # Fall back to the true last occurrence if nothing looks
        # heading-like, rather than dropping the section entirely.
        selected_fs[section_id] = heading_like[-1] if heading_like else occurrences[-1]

    # Any fs_* sentinel sections not found via text patterns also get included
    for sid, entry in sentinel_hits.items():
        if sid.startswith("fs_") and sid not in selected_fs:
            selected_fs[sid] = entry

    # item_* boundaries: one global assignment over every scored candidate.
    validated: List[Tuple[int, str, str]] = [
        (c.line, c.section_id, c.title)
        for c in _assign_sections_with_second_chance(item_candidates)
    ]

    # Splice in fs_* candidates unconditionally (see docstring) and re-sort
    # by line position so the merged list stays in document order.
    validated.extend(selected_fs.values())
    validated.sort(key=lambda x: x[0])

    # --- Recovery pass for bank-style filings --------------------------------
    # Banks (JPM, GS, BAC, WFC) do not place audited financial statements
    # inline within Item 8.  Instead they appear either:
    #   (a) after Item 15 — the monotonic filter drops them (priority 81 < 150)
    #   (b) inside a large item_* section such as item_governance
    #
    # Strategy: scan within every section that is at least 1,000 lines long
    # for anchored fs_* headers.  The anchored patterns in _FS_RECOVERY_PATTERNS
    # require the line to start AND end with the section title, which eliminates
    # cross-references like "Consolidated balance sheets analysis" (fails $ anchor)
    # or "Impact of derivatives on the Consolidated statements of income" (fails ^).
    already_found: set = {e[1] for e in validated}
    missing_fs = [
        (p, sid, t) for p, sid, t in _FS_RECOVERY_PATTERNS
        if sid not in already_found
    ]

    if missing_fs:
        recovered: List[Tuple[int, str, str]] = []
        found_fs: set = set()

        # Build (start, end) pairs for each validated section
        section_ranges = [
            (validated[i][0],
             validated[i + 1][0] if i + 1 < len(validated) else skip_end)
            for i in range(len(validated))
        ]

        for start_ln, end_ln in section_ranges:
            if end_ln - start_ln < 1_000:
                continue
            for j in range(start_ln, end_ln):
                stripped = lines[j].strip()
                if not stripped or len(stripped) > 120:
                    continue
                for pattern, section_id, title in missing_fs:
                    if section_id in found_fs:
                        continue
                    if re.search(pattern, stripped.lower()):
                        recovered.append((j, section_id, title))
                        found_fs.add(section_id)
                        break
                if len(found_fs) == len(missing_fs):
                    break

        if recovered:
            validated.extend(recovered)
            validated.sort(key=lambda x: x[0])

    # --- LLM recovery pass for fs_* headings regex still can't find ----------
    # Filer wording for the 5 standard financial statements varies too much
    # to enumerate by regex (verified: "Statements of Operations" vs "...of
    # Income" alone broke AAPL/GOOGL/MSFT). Runs ONLY for whatever's still
    # missing after every regex-based pass above — bounded cost (a handful
    # of Groq calls), triggered rarely since most filers are already handled
    # by regex. See _llm_locate_fs_headings for why it returns verbatim text
    # instead of trusting the LLM's own line numbers.
    #
    # fs_equity excluded deliberately: verified against JPM/BAC that this
    # specific statement type produces confident-looking but WRONG matches
    # ("Selected capital and other metrics", a fair-value reconciliation
    # table) rather than failing safely. Equity statements are also the
    # least-queried of the 5 in practice — a missing section here is a much
    # smaller cost than silently mislabeling unrelated content as it.
    still_missing = [
        sid for sid in ("fs_income_stmt", "fs_balance_sheet", "fs_cash_flow", "fs_notes")
        if sid not in {e[1] for e in validated}
    ]
    if still_missing:
        section_ranges = [
            (validated[i][0], validated[i + 1][0] if i + 1 < len(validated) else skip_end)
            for i in range(len(validated))
        ]
        for start_ln, end_ln in section_ranges:
            if not still_missing:
                break
            if end_ln - start_ln < 1_000:
                continue
            try:
                recovered = _llm_locate_fs_headings(lines, start_ln, end_ln, still_missing)
            except Exception as exc:
                logger.warning(f"LLM fs-heading recovery pass failed: {exc}")
                recovered = []
            if recovered:
                validated.extend(recovered)
                validated.sort(key=lambda x: x[0])
                still_missing = [sid for sid in still_missing if sid not in {r[1] for r in recovered}]

    # The Item 1 TOC-zone recovery pass that used to sit here is gone: it
    # existed only because item_* candidates were restricted to lines past the
    # 15 % skip zone, and Item 1 is almost always before it. _collect_candidates
    # scans the whole segment, so Item 1 is now an ordinary candidate. The
    # ``skip_start`` argument is kept because the fs_* path above still uses
    # the window it came from.
    return validated


# ---------------------------------------------------------------------------
# Embedded-document splitting
# ---------------------------------------------------------------------------

# Filers occasionally incorporate content BY REFERENCE to a separately-filed
# exhibit (e.g. Wells Fargo's Item 1A/7/8 point to EX-13, its Annual Report
# to Shareholders) instead of including it inline in the 10-K. The downloader
# concatenates such exhibits into one HTML file, delimited by this marker.
# Each embedded document is a full standalone <html>...</html> tree, so they
# must be parsed as SEPARATE BeautifulSoup documents — concatenating the raw
# HTML and parsing it as one soup causes lxml to silently drop everything
# after the first </html> close tag.
_EMBEDDED_DOC_MARKER = re.compile(r"<!--\s*=====\s*embedded document:.*?=====\s*-->")


def _split_embedded_documents(html: str) -> List[str]:
    parts = [p for p in _EMBEDDED_DOC_MARKER.split(html) if p.strip()]
    return parts if parts else [html]


# ---------------------------------------------------------------------------
# Section content assembly
# ---------------------------------------------------------------------------

def _build_section(
    section_id: str,
    title:      str,
    text_slice: str,
    tables:     Dict[str, Tuple[str, List]],
    order:      int,
) -> ParsedSection:
    blocks: List[ContentBlock] = []
    position = 0

    parts = re.split(r"(<<<TABLE_\d+>>>)", text_slice)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if part.startswith("<<<TABLE_") and part in tables:
            markdown, raw = tables[part]
            blocks.append(ContentBlock(
                block_type="table",
                text=markdown,
                raw_table=raw,
                position=position,
            ))
            position += 1
            continue

        for para in re.split(r"\n{2,}", part):
            para = re.sub(r"[ \t]{2,}", " ", para).replace("\n", " ").strip()
            if len(para) < 30:
                continue
            is_footnote = len(para) < 500 and bool(re.match(r"^[\(\*\d\†‡§¶]", para))
            blocks.append(ContentBlock(
                block_type="footnote" if is_footnote else "text",
                text=para,
                position=position,
            ))
            position += 1

    return ParsedSection(
        section_id=section_id,
        title=title,
        content_blocks=blocks,
        order=order,
    )


# ---------------------------------------------------------------------------
# Pre-extraction annotation
# ---------------------------------------------------------------------------

# Sentinel prefix written into the soup before table extraction so that
# _collect_occurrences can pick up the section_id from plain text.
_FS_SENTINEL_PREFIX = "FS_SECTION_HEADER:"


_HEADER_TABLE_RECOVERY_PATTERNS = _FS_RECOVERY_PATTERNS + _ITEM_RECOVERY_PATTERNS


def _annotate_fs_header_tables(soup: BeautifulSoup) -> None:
    """
    Scan every <table> for a cell (within the first 5 rows) whose text
    matches an fs_* or Item-level header pattern. When found, insert a
    sentinel text node immediately before the <table> so the header
    survives as a plain-text line after _extract_tables replaces the table
    with a placeholder.

    Targets issuers like BAC/WFC where section headers such as
    "Consolidated Statement of Income" live inside <td> cells — sometimes
    after leading empty spacer rows — and issuers like TROW/IVZ where even
    "Item 1. Business" is a table-of-contents hyperlink cell rather than
    plain paragraph text. Either way the text is otherwise lost when the
    table is replaced by a <<<TABLE_N>>> placeholder or decomposed outright.
    A sentinel this early in the document (e.g. a genuine TOC entry) is
    still subject to the normal skip_start/skip_end window in
    _collect_occurrences, so a TOC hyperlink match doesn't win over a real
    later heading — it's just one more candidate line.
    """
    for table_tag in soup.find_all("table"):
        rows = table_tag.find_all("tr", recursive=True)
        matched = False

        # fs_* headers sit near the top of a financial-statement table — keep
        # this scoped to the original 5-row window so a wide table containing
        # BOTH an fs_ cell and (further down) something matching an item-level
        # pattern still resolves to the fs_ header, not gets skipped past it.
        #
        # NOTE: widening this window to catch headers further down a table
        # (tried and reverted) causes a worse regression: AAPL/GOOGL-style
        # TOC/index tables list ALL statement titles in one table (Balance
        # Sheet, Income Statement, Comprehensive Income, Equity, Cash Flows,
        # Notes, ...), but this loop takes the FIRST match per table and
        # stops (`matched = True; break`) — verified locally that widening
        # the window just changes WHICH single statement wins that one
        # sentinel slot per table, silently merging others (e.g. fs_notes'
        # 56 blocks got absorbed into fs_equity) instead of fixing anything.
        # Properly splitting AAPL/GOOGL/MSFT's financial statements needs
        # per-row sentinel insertion (multiple sentinels per table), which
        # is a bigger, more careful change than this pass attempted — left
        # for a dedicated follow-up rather than shipping a regression.
        for row in rows[:5]:
            for cell in row.find_all(["td", "th"]):
                cell_text = cell.get_text(separator=" ", strip=True).lower()
                if not cell_text or len(cell_text) > 120:
                    continue
                for pattern, _section_id, title in _FS_RECOVERY_PATTERNS:
                    if re.search(pattern, cell_text):
                        sentinel = soup.new_string(f"\n{_FS_SENTINEL_PREFIX}{title}\n")
                        table_tag.insert_before(sentinel)
                        logger.debug(f"  Pre-annotated table: '{title}' ({cell_text[:50]})")
                        matched = True
                        break
                if matched:
                    break
            if matched:
                break

        if matched:
            continue

        # Item-level headings rendered as table rows (AMZN, TROW, IVZ) — only
        # reached if no fs_ header already claimed this table above.
        #
        # P4-00 (D25) changed this from one sentinel per TABLE to one per
        # matching ROW. Amazon renders every item heading as its own two-cell
        # row, so stopping at the first match recovered Item 1 and lost Item 1A,
        # Item 7 and the rest — the audit found zero occurrences of those
        # headings anywhere in AMZN's parsed text. Emitting one sentinel per row
        # is safe now that a sentinel is a scored candidate rather than an
        # outright winner: a contents table yields a run of sentinels with no
        # prose after them, which _score_candidate penalises and
        # _assign_sections then rejects in favour of the real headings.
        item_rows_matched = 0
        for row in rows[:60]:
            cells = row.find_all(["td", "th"])
            cell_texts = [c.get_text(separator=" ", strip=True).lower() for c in cells]

            # A row matches either on one cell holding the whole title, or —
            # AMZN/WFC style — on adjacent cells splitting it ("Item 1." in one
            # <td>, "Business" in the next). Concatenating non-empty cell texts
            # with no separator recovers the split form; the anchored ^...$
            # patterns still reject a contents row carrying a trailing
            # page-number cell ("item 1." + "business" + "3" fails "$").
            joined = "".join(t for t in cell_texts if t)
            row_texts = [t for t in cell_texts if t and len(t) <= 120]
            if joined and len(joined) <= 120:
                row_texts.append(joined)

            for pattern, _section_id, title in _ITEM_RECOVERY_PATTERNS:
                if any(re.search(pattern, t) for t in row_texts):
                    sentinel = soup.new_string(f"\n{_FS_SENTINEL_PREFIX}{title}\n")
                    table_tag.insert_before(sentinel)
                    logger.debug(f"  Pre-annotated row: '{title}'")
                    item_rows_matched += 1
                    break

        if item_rows_matched:
            matched = True


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def parse_filing(
    file_path:        Path,
    company:          str,
    ticker:           str,
    fiscal_year:      int,
    accession_number: str,
    filing_date:      Optional[str] = None,
) -> ParsedDocument:
    logger.info(f"Parsing {ticker} FY{fiscal_year} — {file_path.name}")

    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
        raw_html = fh.read()

    cleaned_html = _strip_ixbrl(raw_html)
    segments     = _split_embedded_documents(cleaned_html)

    # Each embedded document (the 10-K wrapper, plus any incorporated-by-
    # reference exhibit like EX-13) is parsed as its own BeautifulSoup tree
    # AND boundary-validated independently, then the resulting section lists
    # are concatenated in document order. This matters because monotonic
    # Item-priority validation must not span documents: the 10-K wrapper's
    # own stub sections (e.g. "Items 10-14: Corporate Governance") already
    # advance the priority watermark, which would wrongly reject the exhibit's
    # real, lower-priority sections (Risk Factors, MD&A) as "out of order" if
    # validated together. Scoping validation per segment avoids that, and
    # also keeps each document's own 15%-97% TOC-skip window correctly local.
    tables: Dict[str, Tuple[str, List]] = {}
    next_table_idx = 0
    lines: List[str] = []
    boundaries: List[Tuple[int, str, str]] = []

    for seg_html in segments:
        soup = BeautifulSoup(seg_html, "lxml")
        for tag in soup(["script", "style", "meta", "link", "head", "noscript"]):
            tag.decompose()

        # Pre-annotate financial statement header tables before extraction.
        # Some issuers (BAC, WFC) put headers like "Consolidated Statement of
        # Income" inside <td> cells of the statement tables rather than as
        # stand-alone <div> or <p> elements.  After _extract_tables replaces
        # those tables with <<<TABLE_N>>> placeholders, the text disappears
        # from the plain-text stream and boundary detection misses it.  By
        # inserting a sentinel text node BEFORE the table here, the marker
        # survives into get_text() output so boundary detection finds it.
        _annotate_fs_header_tables(soup)

        seg_tables, next_table_idx = _extract_tables(soup, start_idx=next_table_idx)
        tables.update(seg_tables)

        seg_text = soup.get_text(separator="\n")
        seg_text = re.sub(r"\n{4,}", "\n\n\n", seg_text)
        seg_lines = seg_text.split("\n")

        seg_total      = len(seg_lines)
        seg_skip_start = int(seg_total * 0.15)
        seg_skip_end   = int(seg_total * 0.97)
        seg_occurrences, seg_sentinels = _collect_occurrences(
            seg_lines, seg_skip_start, seg_skip_end
        )
        # item_* candidates are collected over the WHOLE segment (P4-00): the
        # skip zone hid the real Part I headings of every filer whose primary
        # document is the annual report itself.
        seg_item_candidates = _collect_candidates(seg_lines, seg_skip_end)
        seg_boundaries = _select_and_validate(
            seg_lines, seg_occurrences, seg_sentinels,
            seg_item_candidates, seg_skip_start, seg_skip_end,
        )

        offset = len(lines)
        boundaries.extend((i + offset, s, t) for (i, s, t) in seg_boundaries)
        lines.extend(seg_lines)

    # Disambiguate section_ids that recur across segments (e.g. the 10-K
    # wrapper's stub "Notes to Financial Statements" pointer vs. the real one
    # in the exhibit) so each stays independently addressable by parent_id —
    # otherwise ParsedDocument.section_by_id() would always resolve to the
    # first (possibly stub) match regardless of which section a chunk came
    # from. Display titles are untouched; only the internal id gets suffixed.
    #
    # P4-00 (D25): the UNSUFFIXED id goes to the longest instance, not the
    # first one in the document. A filer that incorporates by reference prints
    # a stub in the 10-K wrapper and the real section in the exhibit, and the
    # wrapper comes first — so "first wins" handed the canonical id to the
    # stub. With the item sentinels extended, WFC FY2024's canonical
    # item_1a_risk_factors became a 229-character pointer while its real
    # 102,037-character Risk Factors sat under item_1a_risk_factors__2, where
    # nothing that resolves a section by id would ever look.
    boundaries.sort(key=lambda b: b[0])
    slice_chars = [
        sum(
            len(lines[ln])
            for ln in range(
                start_line + 1,
                boundaries[i + 1][0] if i + 1 < len(boundaries) else len(lines),
            )
        )
        for i, (start_line, _sid, _title) in enumerate(boundaries)
    ]

    rank_in_id: Dict[int, int] = {}
    by_id: Dict[str, List[int]] = defaultdict(list)
    for idx, (_line_no, sid, _title) in enumerate(boundaries):
        by_id[sid].append(idx)
    for indices in by_id.values():
        # Longest first; ties keep document order, so the result is
        # deterministic for two identically-sized slices.
        for rank, idx in enumerate(
            sorted(indices, key=lambda i: (-slice_chars[i], i)), start=1
        ):
            rank_in_id[idx] = rank

    disambiguated: List[Tuple[int, str, str]] = []
    for idx, (line_no, sid, title) in enumerate(boundaries):
        rank = rank_in_id[idx]
        disambiguated.append((line_no, sid if rank == 1 else f"{sid}__{rank}", title))
    boundaries = disambiguated

    logger.info(
        f"  → {len(boundaries)} sections detected, "
        f"{len(tables)} tables extracted"
    )

    sections: List[ParsedSection] = []

    if not boundaries:
        logger.warning(f"  No sections found for {ticker} FY{fiscal_year}; using full document")
        full_text = "\n".join(lines)
        sections.append(_build_section("full_document", "Full Document", full_text, tables, 0))
    else:
        for i, (start_line, section_id, title) in enumerate(boundaries):
            end_line   = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(lines)
            slice_text = "\n".join(lines[start_line + 1 : end_line])
            section    = _build_section(section_id, title, slice_text, tables, i)
            if section.content_blocks:
                sections.append(section)

    doc = ParsedDocument(
        # Deterministic, not the random-UUID default: chunks embedded from an
        # earlier parse of the same filing reference doc_id as a foreign key
        # into ParentStore for full-section context. A random doc_id would
        # orphan every already-embedded chunk each time a filing gets
        # re-parsed (e.g. a parser bug fix) without also being re-embedded —
        # ParentStore silently falls back to the smaller child-chunk text,
        # degrading (not breaking) answers in a way that's easy to miss.
        doc_id=f"{ticker}_{fiscal_year}",
        source_path=str(file_path),
        company=company,
        ticker=ticker,
        fiscal_year=fiscal_year,
        filing_date=filing_date,
        accession_number=accession_number,
        sections=sections,
    )

    block_count = sum(len(s.content_blocks) for s in sections)
    logger.success(f"  {ticker} FY{fiscal_year}: {len(sections)} sections, {block_count} blocks")
    return doc


def _parse_record_worker(record: dict, parsed_dir: Path) -> Optional[ParsedDocument]:
    """Top-level worker so ProcessPoolExecutor can pickle it on Windows (spawn)."""
    out_file = parsed_dir / f"{record['ticker']}_{record['fiscal_year']}.json"
    try:
        doc = parse_filing(
            file_path        = Path(record["file_path"]),
            company          = record["company"],
            ticker           = record["ticker"],
            fiscal_year      = record["fiscal_year"],
            accession_number = record["accession_number"],
            filing_date      = record.get("filing_date"),
        )
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(doc.model_dump_json(indent=2))
        return doc
    except Exception as exc:
        logger.error(f"Failed to parse {record['ticker']} FY{record['fiscal_year']}: {exc}")
        return None


def parse_all_filings(
    manifest:   List[dict],
    parsed_dir: Path,
) -> List[ParsedDocument]:
    parsed_dir.mkdir(parents=True, exist_ok=True)
    documents:  List[ParsedDocument] = []
    to_parse:   List[dict]           = []

    for record in manifest:
        out_file = parsed_dir / f"{record['ticker']}_{record['fiscal_year']}.json"
        if out_file.exists():
            logger.info(f"Skipping {record['ticker']} FY{record['fiscal_year']} (already parsed)")
            with open(out_file, encoding="utf-8") as f:
                documents.append(ParsedDocument.model_validate(json.load(f)))
        else:
            to_parse.append(record)

    if to_parse:
        # Each ProcessPoolExecutor worker is a whole separate Python process
        # (lxml/BeautifulSoup imports and all) — settings.parse_workers
        # defaults to 1 on the assumption of a memory-capped host, in which
        # case skip the pool entirely rather than pay for even one extra
        # process. See config.py's parse_workers for why os.cpu_count() is
        # never used to size this.
        max_workers = min(len(to_parse), settings.parse_workers)
        if max_workers <= 1:
            logger.info(f"Parsing {len(to_parse)} filings sequentially …")
            for rec in to_parse:
                doc = _parse_record_worker(rec, parsed_dir)
                if doc is not None:
                    documents.append(doc)
        else:
            logger.info(f"Parsing {len(to_parse)} filings in parallel (workers={max_workers}) …")
            with ProcessPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_parse_record_worker, rec, parsed_dir): rec
                    for rec in to_parse
                }
                for future in as_completed(futures):
                    rec = futures[future]
                    try:
                        doc = future.result()
                        if doc is not None:
                            documents.append(doc)
                    except Exception as exc:
                        logger.error(
                            f"Worker failed for {rec['ticker']} FY{rec['fiscal_year']}: {exc}"
                        )

    logger.success(f"Parsed {len(documents)} / {len(manifest)} filings")
    return documents
