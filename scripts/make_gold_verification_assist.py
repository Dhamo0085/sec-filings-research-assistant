#!/usr/bin/env python3
"""Assisted verification sheet (P4-02b, D29).

    python scripts/make_gold_verification_assist.py

Reads ``reports/phase4/gold_verification.csv`` (P4-02) and writes
``reports/phase4/gold_verification_assisted.csv``: every original column
unchanged, plus ``assist_*`` columns that say **where in the filing** the value
is printed, so the owner checks a located line instead of hunting for it.

What this script may and may not write (D29, CLAUDE.md rule 17)
---------------------------------------------------------------
It never writes ``verdict``, never writes ``owner_note``/``notes``, never sets
``verified_by`` to ``owner``, and never edits any existing column. Those are the
owner's. Assistance lives only in the new ``assist_*`` columns, and the output
goes to a **separate file** so the signed sheet can never be overwritten by a
re-run (D4-01).

How the location is found
-------------------------
By searching the **raw HTML of the filing document**, not the facts store. The
store is consulted for one thing only: the ``@decimals`` scale the filer
declared for that fact, which is what turns ``391035000000`` into the
``391,035`` a reader actually sees. The search itself is text matching over the
document's own tables and sentences.

``assist_match``
    ``found_exact``   the value was found printed at the filer's declared scale
                      (the sheet's ``filing_value_as_printed``).
    ``found_scaled``  the value was found only at a different scale — units,
                      thousands, millions or billions. Worth the owner's eye:
                      it usually means the sheet's scale column is misleading
                      even when the number is right.
    ``not_found``     no printed occurrence. Not evidence that the value is
                      wrong; it means the assistance failed and the owner must
                      find the line unaided.

Computed rows have no single printed value — a margin is not on the page — so
their **operands** are located instead, one per operand, in each operand's own
filing. The row is ``found_exact`` only when every operand was found.

``assist_spotcheck``
    D29 requires the owner to check the assistance itself on a seeded random
    sample of at least 5 rows: open the filing, confirm the located line really
    is the line. The seed is printed into the cell so the selection in a signed
    sheet can be reproduced years later.

Rows with ``assist_match=not_found`` sort first; everything else keeps the
input sheet's order.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import warnings
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from catalog.store import CatalogStore  # noqa: E402
from config import settings  # noqa: E402
from eval.gold.schema import read_jsonl  # noqa: E402
from facts.resolve import FactsResolver  # noqa: E402
from facts.store import FactsStore  # noqa: E402

# lxml's HTML parser is the right tool for iXBRL filings; bs4 only warns
# because the documents declare an XML prolog.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

GOLD_PATH = REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl"
DEFAULT_IN = REPO_ROOT / "reports" / "phase4" / "gold_verification.csv"
DEFAULT_OUT = REPO_ROOT / "reports" / "phase4" / "gold_verification_assisted.csv"
DEFAULT_CACHE = REPO_ROOT / ".cache" / "filings"

#: D29's minimum sample the owner must open in the real filing.
SPOTCHECK_N = 5
DEFAULT_SEED = 20261003

ASSIST_COLUMNS = (
    "assist_match",
    "assist_value_as_printed",
    "assist_row_label",
    "assist_column_header",
    "assist_units_header",
    "assist_printed_line",
    "assist_link",
    "assist_spotcheck",
)

#: Columns this script must never write (D29). Checked at runtime, not trusted
#: to code review: a future edit that starts filling one of these fails loudly.
OWNER_ONLY_COLUMNS = ("verdict", "owner_note", "notes")

#: Scales to try when the declared scale does not find the value. 0 = as filed
#: in units, 3 = thousands, 6 = millions, 9 = billions.
FALLBACK_SCALES = (0, 3, 6, 9)

_SCALE_WORD = {0: "units", 3: "thousands", 6: "millions", 9: "billions"}

#: A parenthesised units note, preferred because the filer closed it itself:
#: "(In millions, except number of shares)". Without the bracket the match
#: has no end and drags the next sentence into the column.
_UNITS_PAREN_RE = re.compile(
    r"\(([^()\n]{0,40}?\bin\s+(?:thousands|millions|billions)[^()\n]{0,160})\)",
    re.IGNORECASE,
)
_UNITS_RE = re.compile(
    r"(?:\$|US\$|dollars?|amounts?)?\s*\bin\s+(?:thousands|millions|billions)",
    re.IGNORECASE,
)
_HEADER_HINT_RE = re.compile(
    r"(19|20)\d{2}|January|February|March|April|May|June|July|August|"
    r"September|October|November|December",
    re.IGNORECASE,
)
_NUMERIC_CELL_RE = re.compile(r"^[\s$()\-—–\d.,%]*$")

#: The metric registry's statement name -> the `statement_to_check` wording the
#: sheet uses. Operand rows carry no statement column of their own, and without
#: one the search scored every table equally: Netflix's revenue operand landed
#: on the MD&A "Streaming revenues" line, whose label is not what was resolved.
STATEMENT_HINT = {
    "income": "Consolidated Statements of Operations / of Income",
    "balance": "Consolidated Balance Sheets / Statements of Financial Condition",
    "cash_flow": "Consolidated Statements of Cash Flows",
}

#: `statement_to_check` text -> phrases that identify that statement's heading.
STATEMENT_KEYWORDS = (
    # Filers do not agree on the name: Microsoft heads the page "INCOME
    # STATEMENTS", Goldman "Consolidated Statements of Earnings", the banks
    # "Statements of Financial Condition". All of them are listed.
    ("operations", ("statements of operations", "statement of operations",
                    "statements of income", "statement of income",
                    "income statement", "statements of earnings",
                    "statement of earnings")),
    ("balance", ("balance sheet", "statements of financial condition",
                 "statement of financial condition")),
    ("cash flow", ("statements of cash flows", "statement of cash flows",
                   "cash flows statement")),
)


class AssistError(RuntimeError):
    """A failure that should stop the run rather than produce a silent blank."""


# ── numeral handling ──────────────────────────────────────────────────────


def normalize_numeral(text: str) -> Optional[str]:
    """``"$ (1,234.5)"`` -> ``"-1234.5"``; ``None`` if this is not a numeral.

    Only the characters a financial table actually uses are accepted, so a
    cell like ``"2024"`` normalizes (it is a numeral — the caller decides what
    that means) but ``"Total net sales"`` does not.
    """
    if text is None:
        return None
    cleaned = text.replace("\xa0", " ").strip()
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    cleaned = cleaned.replace("$", "").replace("%", "")
    # Unicode minus and en/em dashes used as a minus sign.
    cleaned = cleaned.replace("−", "-").replace("–", "-").replace("—", "-")
    cleaned = cleaned.replace(",", "").replace(" ", "")
    if cleaned.startswith("-"):
        negative = True
        cleaned = cleaned[1:]
    if not cleaned or not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if negative:
        value = -value
    return _canonical(value)


def match_key(value: Decimal) -> str:
    """The key both sides compare on: magnitude only.

    A cash-flow statement prints capital expenditure as ``(82,999)`` while the
    facts store holds the positive magnitude, and an income statement prints a
    loss the same way. Matching on magnitude finds the line; the owner still
    sees the sign, because ``assist_value_as_printed`` is the cell verbatim,
    parentheses and all.
    """
    return _canonical(abs(value))


def _canonical(value: Decimal) -> str:
    """One spelling per quantity, so ``7093.60`` and ``7093.6`` compare equal."""
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def candidate_forms(value: Decimal, declared_scale: Optional[str]) -> Dict[str, Tuple[str, int]]:
    """Normalized numeral -> (match kind, scale) for every scale worth trying.

    The declared scale wins when a document prints the value at two scales
    (a filing in millions whose cover page repeats a figure in units), because
    the declared scale is the one the sheet's ``filing_value_as_printed``
    column shows the owner.
    """
    try:
        declared = int(declared_scale) if declared_scale not in (None, "") else 0
    except (TypeError, ValueError):
        declared = 0

    forms: Dict[str, Tuple[str, int]] = {}
    for scale in (declared, *FALLBACK_SCALES):
        key = match_key(value.scaleb(-scale))
        if key in forms:
            continue
        forms[key] = ("found_exact" if scale == declared else "found_scaled", scale)
    return forms


# ── HTML table handling ───────────────────────────────────────────────────


def _cell_key(text: str) -> Optional[str]:
    """``normalize_numeral`` reduced to the key tables are matched on."""
    normalized = normalize_numeral(text)
    return None if normalized is None else match_key(Decimal(normalized))


def _cell_text(cell) -> str:
    return re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()


def expand_table(table) -> List[List[str]]:
    """A rectangular grid with ``colspan``/``rowspan`` expanded.

    Expanded rather than read cell-by-cell because a filing's column headers
    span the ``$`` column and the figure column; without expansion the header
    for a matched figure lands under the wrong index and the sheet would tell
    the owner to look in the prior-year column.
    """
    grid: List[List[str]] = []
    pending: Dict[int, Tuple[str, int]] = {}

    for tr in table.find_all("tr"):
        row: List[str] = []
        column = 0

        def drain(column: int, row: List[str]) -> int:
            while column in pending:
                text, left = pending[column]
                row.append(text)
                if left - 1 <= 0:
                    del pending[column]
                else:
                    pending[column] = (text, left - 1)
                column += 1
            return column

        column = drain(column, row)
        for cell in tr.find_all(["td", "th"]):
            text = _cell_text(cell)
            try:
                colspan = max(1, int(cell.get("colspan", 1) or 1))
                rowspan = max(1, int(cell.get("rowspan", 1) or 1))
            except ValueError:
                colspan = rowspan = 1
            for _ in range(colspan):
                column = drain(column, row)
                row.append(text)
                if rowspan > 1:
                    pending[column] = (text, rowspan - 1)
                column += 1
        grid.append(row)
    return grid


def row_label(grid: List[List[str]], row_index: int, column_index: int) -> str:
    """The leftmost non-numeric cell on the matched row — the line's name."""
    for cell in grid[row_index][:column_index]:
        if cell and not _NUMERIC_CELL_RE.fullmatch(cell):
            return cell
    # Some filers indent by nesting, leaving the label on the row above.
    for above in range(row_index - 1, max(-1, row_index - 3), -1):
        for cell in grid[above][: column_index or None]:
            if cell and not _NUMERIC_CELL_RE.fullmatch(cell):
                return cell
    return ""


def column_header(grid: List[List[str]], row_index: int, column_index: int) -> str:
    """The nearest date-like heading above the matched cell, same column."""
    for above in range(row_index - 1, -1, -1):
        row = grid[above]
        if column_index >= len(row):
            continue
        cell = row[column_index]
        if cell and _HEADER_HINT_RE.search(cell):
            return cell
    return ""


def units_header(table_text: str, preamble: str) -> str:
    """``"in millions"`` and friends, from the table itself or just above it."""
    for haystack in (table_text, preamble):
        found = _UNITS_PAREN_RE.search(haystack or "")
        if found:
            return re.sub(r"\s+", " ", found.group(1)).strip(" ,")
    for haystack in (table_text, preamble):
        found = _UNITS_RE.search(haystack or "")
        if found:
            return re.sub(r"\s+", " ", found.group(0)).strip(" ,")
    return ""


def table_preamble(table, limit: int = 1500) -> str:
    """Up to ``limit`` characters of text immediately before the table."""
    chunks: List[str] = []
    total = 0
    for string in table.find_all_previous(string=True, limit=600):
        text = re.sub(r"\s+", " ", str(string)).strip()
        if not text:
            continue
        chunks.append(text)
        total += len(text) + 1
        if total >= limit:
            break
    return " ".join(reversed(chunks))[-limit:]


#: How close to the table the statement heading has to be to count for full
#: marks. A heading sits immediately above its table; a mention this far back
#: is as likely to be MD&A prose about the statement as the statement itself.
NEAR_WINDOW = 300


def _squash(text: str) -> str:
    """Lowercase with every space removed, for heading comparison."""
    return re.sub(r"\s+", "", (text or "").lower())


def statement_score(preamble: str, statement_to_check: str,
                    table_head: str = "") -> int:
    """How much the text above the table looks like the statement asked for.

    A figure like Apple's 391,035 appears in five tables of the same filing
    (segment, product, geography, the income statement, the segment note).
    Without this the sheet would point the owner at whichever came first.
    Proximity is weighted because MD&A prose discussing the balance sheet
    would otherwise score as highly as the balance sheet's own heading.
    """
    target = (statement_to_check or "").lower()
    # Whitespace is removed from both sides before comparing. Microsoft sets
    # its statement headings with letter-spacing, which reaches the text layer
    # as "INC OME STATE MENTS"; matching on the spaced form silently scored
    # the real income statement 0 and handed the row to an MD&A summary table.
    # The heading counts as "near" whether it sits just above the table or in
    # the table's own first rows. Bank of America's real Consolidated Balance
    # Sheet carries its title inside the table, so scoring the preamble alone
    # gave it 0 and handed the row to the MD&A "Balance Sheet Overview".
    whole = _squash(preamble) + _squash(table_head)
    near = _squash((preamble or "")[-NEAR_WINDOW:]) + _squash(table_head[:NEAR_WINDOW])
    for marker, phrases in STATEMENT_KEYWORDS:
        if marker not in target:
            continue
        squashed = [_squash(phrase) for phrase in phrases]
        # "Consolidated Balance Sheet" is the statement's own title; "Table 5
        # Selected Balance Sheet Data" is MD&A quoting it. Both are headings
        # directly above a table holding the same figure, and this is what
        # separates them.
        if any("consolidated" + phrase in near for phrase in squashed):
            return 5
        if any(phrase in near for phrase in squashed):
            return 3
        if any(phrase in whole for phrase in squashed):
            return 1
    return 0


def label_score(label: str, metric_label: str) -> int:
    """+1 per shared word between the row label and the metric's label."""
    if not label or not metric_label:
        return 0
    stop = {"total", "net", "the", "of", "and", "consolidated"}
    left = {w for w in re.findall(r"[a-z]+", label.lower()) if w not in stop}
    right = {w for w in re.findall(r"[a-z]+", metric_label.lower()) if w not in stop}
    return len(left & right)


# ── the search ────────────────────────────────────────────────────────────


class Hit:
    __slots__ = ("header", "kind", "label", "line", "printed", "scale", "score", "units")

    def __init__(self, kind, scale, printed, label, header, units, line, score):
        self.kind = kind
        self.scale = scale
        self.printed = printed
        self.label = label
        self.header = header
        self.units = units
        self.line = line
        self.score = score


def search_document(
    html: str,
    value: Decimal,
    declared_scale: Optional[str],
    *,
    statement_to_check: str = "",
    metric_label: str = "",
) -> Optional[Hit]:
    """Best printed occurrence of ``value`` in one filing document, or None."""
    forms = candidate_forms(value, declared_scale)
    soup = BeautifulSoup(html, "lxml")
    # iXBRL carries facts that are never displayed inside a hidden block.
    # Matching one of those would point the owner at nothing on the page.
    for hidden in soup.find_all(lambda tag: tag.name and "hidden" in tag.name):
        hidden.decompose()

    best: Optional[Hit] = None
    for table in soup.find_all("table"):
        grid = expand_table(table)
        located: List[Tuple[int, int, str]] = []
        for row_index, row in enumerate(grid):
            for column_index, cell in enumerate(row):
                key = _cell_key(cell)
                if key is not None and key in forms:
                    located.append((row_index, column_index, cell))
        if not located:
            continue

        preamble = table_preamble(table)
        table_text = re.sub(r"\s+", " ", table.get_text(" ", strip=True))
        base = statement_score(preamble, statement_to_check, table_text[:NEAR_WINDOW])
        units = units_header(table_text, preamble)

        for row_index, column_index, cell in located:
            kind, scale = forms[_cell_key(cell)]
            label = row_label(grid, row_index, column_index)
            header = column_header(grid, row_index, column_index)
            score = base + label_score(label, metric_label)
            if kind == "found_exact":
                score += 2
            # A dated column header is what makes a located line checkable:
            # it tells the owner which year's column the figure is in. A hit
            # without one is a weaker hit even when everything else ties.
            if header:
                score += 1
            if label:
                score += 1
            if best is None or score > best.score:
                best = Hit(
                    kind=kind,
                    scale=scale,
                    printed=cell,
                    label=label,
                    header=header,
                    units=units,
                    line=_render_line(grid[row_index]),
                    score=score,
                )

    if best is not None:
        return best
    return _search_prose(soup, forms)


def _render_line(row: Sequence[str], limit: int = 400) -> str:
    """The matched table row as text, with colspan duplicates collapsed."""
    cells: List[str] = []
    for cell in row:
        if cell and (not cells or cells[-1] != cell):
            cells.append(cell)
    line = " | ".join(cells)
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _search_prose(soup, forms: Dict[str, Tuple[str, int]]) -> Optional[Hit]:
    """Fall back to a sentence: some filers state a figure only in prose."""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    for token in re.finditer(r"\(?\s*\$?\s*-?[\d][\d,]*(?:\.\d+)?\s*\)?", text):
        key = _cell_key(token.group(0))
        if key is None or key not in forms:
            continue
        kind, scale = forms[key]
        start = max(0, token.start() - 200)
        end = min(len(text), token.end() + 200)
        sentence = text[start:end]
        return Hit(
            kind=kind,
            scale=scale,
            printed=token.group(0).strip(),
            label="",
            header="",
            units=units_header(sentence, ""),
            line="…" + sentence.strip() + "…",
            score=0,
        )
    return None


# ── sheet assembly ────────────────────────────────────────────────────────


def _document_for(store: FactsStore, accession: str, concept: str,
                  value: str) -> Tuple[Optional[str], str]:
    """The filer's declared scale and the document that carries this fact."""
    for fact in store.query(accession=accession, concept=concept):
        if str(fact.value) == value or format(fact.value, "f") == value:
            return fact.scale_raw, fact.source_doc or ""
    return None, ""


def _local_path(cache_dir: Path, ticker: str, accession: str, doc: str) -> Optional[Path]:
    if not doc:
        return None
    path = cache_dir / f"{ticker.upper()}_{accession}" / doc
    return path if path.is_file() else None


def _edgar_url(catalog: CatalogStore, accession: str, doc: str) -> str:
    filing = catalog.get(accession)
    if not filing:
        return ""
    name = doc or filing.primary_doc or ""
    return (f"https://www.sec.gov/Archives/edgar/data/{filing.cik}/"
            f"{accession.replace('-', '')}/{name}")


def _blank_assist() -> Dict[str, str]:
    return dict.fromkeys(ASSIST_COLUMNS, "")


def _assist_for_numeric(row: Dict[str, str], store: FactsStore,
                        catalog: CatalogStore, cache_dir: Path) -> Dict[str, str]:
    accession = row["accession"]
    concept = row["concept"]
    scale_raw, source_doc = _document_for(store, accession, concept, row["expected_value"])
    doc = source_doc or row["filing_url"].rsplit("/", 1)[-1]
    path = _local_path(cache_dir, row["ticker"], accession, doc)

    assist = _blank_assist()
    assist["assist_link"] = _edgar_url(catalog, accession, doc) or row["filing_url"]
    if path is None:
        assist["assist_match"] = "not_found"
        assist["assist_printed_line"] = f"document not in the local cache: {doc or '(unknown)'}"
        return assist

    hit = search_document(
        path.read_text(encoding="utf-8", errors="replace"),
        Decimal(row["expected_value"]),
        scale_raw if scale_raw is not None else row["scale_attr"],
        statement_to_check=row["statement_to_check"],
        metric_label=row["metric_label"],
    )
    if hit is None:
        assist["assist_match"] = "not_found"
        return assist

    assist["assist_match"] = hit.kind
    assist["assist_value_as_printed"] = hit.printed
    assist["assist_row_label"] = hit.label
    assist["assist_column_header"] = hit.header
    assist["assist_units_header"] = hit.units or _SCALE_WORD.get(hit.scale, "")
    assist["assist_printed_line"] = hit.line
    return assist


def _statement_hint(resolver: FactsResolver, metric: str) -> str:
    """The statement an operand's metric lives on, or "" if it is not a metric."""
    try:
        spec = resolver.registry.metric(metric)
    except Exception:
        # An unknown metric simply gets no hint: the search then ranks by
        # label and header alone, which is the behaviour before P4-02b.
        return ""
    return STATEMENT_HINT.get(spec.statement, spec.statement or "")


def _assist_for_computed(row: Dict[str, str], operands: Sequence[Dict], store: FactsStore,
                         catalog: CatalogStore, resolver: FactsResolver,
                         cache_dir: Path) -> Dict[str, str]:
    """Locate each operand in its own filing; the row is only as good as both."""
    assist = _blank_assist()
    kinds: List[str] = []
    printed: List[str] = []
    labels: List[str] = []
    headers: List[str] = []
    units: List[str] = []
    lines: List[str] = []
    links: List[str] = []

    for operand in operands:
        accession = operand["accession"]
        scale_raw, source_doc = _document_for(
            store, accession, operand["concept"], operand["value"]
        )
        doc = source_doc or (catalog.get(accession).primary_doc if catalog.get(accession) else "")
        links.append(_edgar_url(catalog, accession, doc))
        path = _local_path(cache_dir, row["ticker"], accession, doc)
        tag = f"{operand['metric']} FY{operand['fiscal_label']}"
        if path is None:
            kinds.append("not_found")
            lines.append(f"{tag}: document not in the local cache")
            continue
        hit = search_document(
            path.read_text(encoding="utf-8", errors="replace"),
            Decimal(operand["value"]),
            scale_raw,
            statement_to_check=_statement_hint(resolver, operand["metric"]),
            metric_label=operand["metric"].replace("_", " "),
        )
        if hit is None:
            kinds.append("not_found")
            lines.append(f"{tag}: not found")
            continue
        kinds.append(hit.kind)
        printed.append(f"{tag} = {hit.printed}")
        labels.append(f"{tag}: {hit.label}")
        headers.append(f"{tag}: {hit.header}")
        units.append(hit.units or _SCALE_WORD.get(hit.scale, ""))
        lines.append(f"{tag}: {hit.line}")

    if not kinds or "not_found" in kinds:
        assist["assist_match"] = "not_found"
    elif "found_scaled" in kinds:
        assist["assist_match"] = "found_scaled"
    else:
        assist["assist_match"] = "found_exact"

    assist["assist_value_as_printed"] = " ; ".join(printed)
    assist["assist_row_label"] = " ; ".join(labels)
    assist["assist_column_header"] = " ; ".join(headers)
    assist["assist_units_header"] = " ; ".join(dict.fromkeys(u for u in units if u))
    assist["assist_printed_line"] = " ⏎ ".join(lines)
    assist["assist_link"] = " ".join(dict.fromkeys(link for link in links if link))
    return assist


def choose_spotcheck(rows: Sequence[Dict[str, str]], seed: int,
                     count: int = SPOTCHECK_N) -> List[str]:
    """A seeded sample of gold_ids, drawn from the rows that carry assistance.

    Drawn from located rows because D29's sample checks the *assistance*: a
    ``not_found`` row has nothing to check. If fewer than ``count`` rows were
    located, the sample is topped up from the rest so the owner still opens
    ``count`` filings.
    """
    located = sorted(r["gold_id"] for r in rows if r["assist_match"] != "not_found")
    rest = sorted(r["gold_id"] for r in rows if r["assist_match"] == "not_found")
    rng = random.Random(seed)  # noqa: S311 — sample selection, not cryptography
    chosen = rng.sample(located, min(count, len(located)))
    if len(chosen) < count and rest:
        chosen += rng.sample(rest, min(count - len(chosen), len(rest)))
    return sorted(chosen)


def read_sheet(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        return list(reader), fieldnames


def build(in_path: Path, *, gold_path: Path, cache_dir: Path, seed: int) -> List[Dict[str, str]]:
    rows, _ = read_sheet(in_path)
    if not rows:
        raise AssistError(f"{in_path} has no rows")

    gold = {item["id"]: item for item in read_jsonl(gold_path)}
    catalog = CatalogStore(Path(settings.catalog_path))
    store = FactsStore(Path(settings.facts_db_path))
    resolver = FactsResolver(catalog=catalog, store=store)

    for row in rows:
        if row["category"] == "computed":
            item = gold.get(row["gold_id"])
            operands = item["source"]["operands"] if item else []
            assist = _assist_for_computed(row, operands, store, catalog, resolver, cache_dir)
        else:
            assist = _assist_for_numeric(row, store, catalog, cache_dir)
        row.update(assist)

    for gold_id in choose_spotcheck(rows, seed):
        for row in rows:
            if row["gold_id"] == gold_id:
                row["assist_spotcheck"] = f"yes (seed {seed})"

    rows.sort(key=lambda r: (r["assist_match"] != "not_found",))
    return rows


def assert_unchanged(rows: Sequence[Dict[str, str]], original: Sequence[Dict[str, str]],
                     original_fieldnames: Sequence[str]) -> None:
    """Every original column, for every row, is the input's value verbatim.

    D29's rule enforced at write time rather than left to code review: if any
    future edit starts filling ``verdict`` or ``owner_note``, or quietly
    rewrites a column the owner reads, the run fails instead of producing a
    plausible-looking sheet.
    """
    before = {row["gold_id"]: row for row in original}
    if set(before) != {row["gold_id"] for row in rows}:
        raise AssistError("refusing to write: the row set changed")
    for row in rows:
        source = before[row["gold_id"]]
        for column in original_fieldnames:
            if row.get(column, "") != source.get(column, ""):
                raise AssistError(
                    f"refusing to write: column {column!r} of {row['gold_id']} was "
                    f"modified ({source.get(column, '')!r} -> {row.get(column, '')!r})"
                )
    for column in OWNER_ONLY_COLUMNS:
        if column in ASSIST_COLUMNS:  # pragma: no cover — guards a future rename
            raise AssistError(f"owner-only column {column!r} is in the assist set")


def write_sheet(rows: Sequence[Dict[str, str]], out_path: Path,
                original_fieldnames: Sequence[str]) -> None:
    fieldnames = list(original_fieldnames) + [
        c for c in ASSIST_COLUMNS if c not in original_fieldnames
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="in_path", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--gold", type=Path, default=GOLD_PATH)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    if not args.in_path.is_file():
        print(f"error: {args.in_path} does not exist; run "
              f"scripts/make_gold_verification_sheet.py first", file=sys.stderr)
        return 2
    if args.out.resolve() == args.in_path.resolve():
        print("error: refusing to overwrite the owner's sheet in place (D29)",
              file=sys.stderr)
        return 2

    original, original_fieldnames = read_sheet(args.in_path)
    rows = build(args.in_path, gold_path=args.gold, cache_dir=args.cache_dir, seed=args.seed)
    assert_unchanged(rows, original, original_fieldnames)
    write_sheet(rows, args.out, original_fieldnames)

    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["assist_match"]] = counts.get(row["assist_match"], 0) + 1
    not_found = [r["gold_id"] for r in rows if r["assist_match"] == "not_found"]
    core_located = sum(1 for r in rows
                       if r.get("priority") == "core" and r["assist_match"] != "not_found")

    try:
        shown = args.out.relative_to(REPO_ROOT)
    except ValueError:
        shown = args.out
    print(f"wrote {shown}")
    print(f"  {len(rows)} rows: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"  core rows located: {core_located} of "
          f"{sum(1 for r in rows if r.get('priority') == 'core')}")
    print(f"  spot-check sample (seed {args.seed}): "
          + ", ".join(r["gold_id"] for r in rows if r["assist_spotcheck"]))
    if not_found:
        print(f"  not_found ({len(not_found)}): " + ", ".join(not_found))
    print("  verdict / owner_note are untouched — D29, CLAUDE.md rule 17")
    return 0


if __name__ == "__main__":
    sys.exit(main())
