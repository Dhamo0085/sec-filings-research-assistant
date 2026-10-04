#!/usr/bin/env python3
"""A blind, read-only reading view of the narrative rating sheet (P4-14b, T4-15).

    .venv/bin/python scripts/make_rating_view.py

One self-contained HTML page, one card per sheet row, in sheet order, so the
owner can read question, answer and cited passage together instead of scrolling
a CSV with 1,500-character cells.

**Blind on purpose.** The page shows no automated verdict, no score, no scorer
output of any kind. P4-15 reports the agreement between the scorer's verdicts
and the owner's ratings; showing the scorer's answer first would make that
measure meaningless, because a rating anchored on the thing it is being
compared against is not independent evidence.

**Read-only on purpose.** No form fields, no JavaScript, nothing that writes.
The owner's `verdict`, `issue` and `notes` stay in the CSV and are never
rendered here (D29, CLAUDE.md rule 17) — not even when populated, so a
half-rated sheet cannot anchor the rest of the rating.

**The number highlighting is assistance, not a judgement.** It answers "where
is this figure in the cited text", never "is this answer right". A number
listed as *not found in passage* may be correct and simply stated elsewhere in
the filing, or carried in a sibling citation — and where that second case is
true the page says so, because the bare label would otherwise read as an
accusation. Citation markers like ``[1]`` are excluded: they are references,
not claims.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Columns the owner signs. Never rendered (D29).
OWNER_COLUMNS = ("verdict", "issue", "notes")

#: A number as these texts write one: 1,234 · 39000966 · 4.5 · 12.3% · $177,556.
#: Bracketed citation markers are stripped before this runs.
#: Trailing separators are excluded: "2023, 2024" must yield 2023 and 2024,
#: not "2023," — matching survived it (normalise strips commas) but the
#: owner was shown a figure with a stray comma glued on.
_NUMBER = re.compile(r"\d(?:[\d,]*\d)?(?:\.\d+)?")

#: ``[1]`` and ``[1] [2]`` are references to sources, not figures in the answer.
#: ``(7)`` is the same thing one bracket shape over: an enumerator in a list the
#: model wrote. Measured on the real sheet, EVERY bare integer the first version
#: listed as missing was one of these — "(7) consumer electronics; (8) grocery
#: sellers; (9) advertising services; (10) providers of…" — six false positives
#: and no true ones, which teaches the reader to ignore the list.
_CITATION_MARKER = re.compile(r"[\[(]\s*\d{1,3}\s*[\])]")

#: A four-digit number in this range, read as a period label rather than a
#: figure. Years are NOT dropped — period correctness is what this project is
#: for, and an answer naming a year its cited passage does not is worth the
#: owner's eye — but they are annotated, so a date does not look like a missing
#: financial figure.
_YEAR = re.compile(r"^(19|20)\d\d$")


def normalise(number: str) -> str:
    """Digits of a number, for comparing across renderings.

    ``177,556`` and ``177556`` are the same figure; a trailing ``.0`` is not a
    different number either. Scale words are deliberately NOT resolved — this
    function must not decide that "$39.0 billion" and "39,000,966" are the same
    claim, because that is exactly the judgement the owner is making.
    """
    cleaned = number.replace(",", "")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned or "0"


def numbers_in(text: str) -> List[Tuple[int, int, str]]:
    """(start, end, text) for every number, with citation markers blanked out."""
    masked = _CITATION_MARKER.sub(lambda m: " " * len(m.group(0)), text or "")
    return [(m.start(), m.end(), m.group(0)) for m in _NUMBER.finditer(masked)]


def highlight(passage: str, wanted: Sequence[str]) -> str:
    """Escape ``passage`` and wrap every occurrence of a wanted number.

    Spans come from scanning the PASSAGE, so a highlight can only ever cover a
    substring that genuinely exists in it (T4-15). Matching is on the
    normalised digits, so the passage's own rendering is what gets marked.
    """
    targets = {normalise(w) for w in wanted}
    out: List[str] = []
    cursor = 0
    for start, end, raw in numbers_in(passage):
        if normalise(raw) not in targets:
            continue
        out.append(html.escape(passage[cursor:start]))
        out.append(f"<mark>{html.escape(raw)}</mark>")
        cursor = end
    out.append(html.escape(passage[cursor:]))
    return "".join(out)


def read_sheet(path: Path) -> Tuple[List[str], List[dict]]:
    """The rubric lines above the header, and the rows below it."""
    with path.open(encoding="utf-8") as handle:
        raw = list(csv.reader(handle))
    header = next(i for i, row in enumerate(raw) if row and row[0] == "item_id")
    rubric = [r[0] for r in raw[:header] if r and r[0].strip()]
    rows = [dict(zip(raw[header], r, strict=False)) for r in raw[header + 1:] if r]
    return rubric, rows


def card_html(index: int, row: dict, siblings: Dict[str, List[dict]]) -> str:
    passage = row.get("assist_passage") or ""
    answer = row.get("answer") or ""
    recovered = passage and passage != "PASSAGE NOT RECOVERED"

    answer_numbers = [raw for _s, _e, raw in numbers_in(answer)]
    passage_norms = {normalise(raw) for _s, _e, raw in numbers_in(passage)} if recovered else set()

    # Where else this answer's figures might legitimately be: the other
    # passages cited by the same answer. Without this the label reads as an
    # accusation for a number that is simply in source 2.
    elsewhere: set = set()
    for other in siblings.get(row["item_id"], ()):
        if other is row:
            continue
        other_passage = other.get("assist_passage") or ""
        if other_passage and other_passage != "PASSAGE NOT RECOVERED":
            elsewhere |= {normalise(raw) for _s, _e, raw in numbers_in(other_passage)}

    missing: List[Tuple[str, bool, bool]] = []
    seen: set = set()
    for raw in answer_numbers:
        key = normalise(raw)
        if key in passage_norms or key in seen:
            continue
        seen.add(key)
        missing.append((raw, key in elsewhere, bool(_YEAR.match(key))))

    flag = row.get("assist_flags") or ""
    bits: List[str] = [f'<article class="card" id="row-{index}">']
    bits.append(
        f'<header><span class="rowid">row {index}</span>'
        f'<span class="item">{html.escape(row["item_id"])}</span>'
        f'<span class="src">source {html.escape(row.get("citation_index") or "-")}</span>'
        "</header>"
    )
    if flag:
        label = ("the system DECLINED to answer this one — leave verdict blank and use "
                 "issue to say whether declining was right"
                 if flag.startswith("refused:")
                 else "the system answered without citing anything")
        bits.append(f'<p class="flag"><strong>{html.escape(flag)}</strong> — {label}</p>')

    bits.append(f'<h2>{html.escape(row.get("question") or "")}</h2>')
    if row.get("as_of"):
        bits.append(f'<p class="asof">as_of {html.escape(row["as_of"])}</p>')

    bits.append('<section><h3>Answer</h3>'
                f'<div class="answer">{html.escape(answer)}</div></section>')

    cite = " · ".join(filter(None, [
        html.escape(row.get("cited_ticker") or ""),
        f'FY{html.escape(row.get("cited_fiscal_label") or "")}'
        if row.get("cited_fiscal_label") else "",
        html.escape(row.get("cited_section") or ""),
        html.escape(row.get("cited_accession") or ""),
    ]))
    link = row.get("edgar_link") or ""
    link_html = (f' <a href="{html.escape(link)}">open on EDGAR</a>' if link else "")
    bits.append(f'<section><h3>Cited source</h3><p class="cite">{cite}{link_html}</p>')

    if recovered:
        bits.append('<div class="passage">'
                    + highlight(passage, answer_numbers) + "</div>")
    else:
        bits.append('<p class="nopassage">PASSAGE NOT RECOVERED — rate this row only '
                    "on what is shown, and say so in notes.</p>")
    bits.append("</section>")

    figures = [m for m in missing if not m[2]]
    years = [m for m in missing if m[2]]

    if years:
        # Demoted to one quiet line. Years ARE listed — period correctness is
        # what this project exists for — but a section headed "not found in
        # passage" that only ever holds dates competes with the evidence and
        # teaches the reader to skip it.
        shown = ", ".join(html.escape(raw) for raw, _a, _y in years)
        bits.append(f'<p class="years note">Year references in the answer not in '
                    f"this passage: {shown}. A period label, not a figure.</p>")

    missing = figures
    if missing:
        items = "".join(
            f"<li><code>{html.escape(raw)}</code>"
            + (' <span class="note">(appears in another cited passage for this '
               "answer)</span>" if also else "")
            + "</li>"
            for raw, also, _is_year in missing)
        bits.append(
            '<section class="missing"><h3>Numbers in the answer, not found in '
            f"passage</h3><ul>{items}</ul>"
            '<p class="note">Assistance only, never a verdict: this says where a '
            "figure is, not whether the answer is right. A figure may be stated "
            "elsewhere in the filing, or written at a different scale.</p></section>")

    bits.append("</article>")
    return "".join(bits)


STYLE = """
:root { --fg:#1a1a1a; --muted:#666; --line:#ddd; --mark:#fff3b0; --bg:#fff; }
* { box-sizing:border-box; }
body { font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
       color:var(--fg); background:var(--bg); margin:0; padding:24px; }
.wrap { max-width:900px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 4px; }
.sub { color:var(--muted); margin:0 0 24px; }
.rubric { border:1px solid var(--line); border-left:4px solid #555; padding:12px 16px;
          margin-bottom:28px; background:#fafafa; }
.rubric h2 { font-size:14px; text-transform:uppercase; letter-spacing:.04em; margin:0 0 8px; }
.rubric li { margin:4px 0; }
.card { border:1px solid var(--line); border-radius:6px; padding:18px 20px; margin:0 0 22px; }
.card header { display:flex; gap:12px; align-items:baseline; color:var(--muted);
               font-size:13px; border-bottom:1px solid var(--line); padding-bottom:8px;
               margin-bottom:12px; }
.rowid { font-weight:700; color:var(--fg); }
.item { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.src { margin-left:auto; }
.card h2 { font-size:17px; margin:0 0 10px; }
.card h3 { font-size:12px; text-transform:uppercase; letter-spacing:.04em;
           color:var(--muted); margin:16px 0 6px; }
.asof { color:var(--muted); font-size:13px; margin:0 0 8px; }
.answer { white-space:pre-wrap; }
.passage { white-space:pre-wrap; font-size:14px; background:#fafafa;
           border:1px solid var(--line); border-radius:4px; padding:12px;
           max-height:460px; overflow:auto; }
mark { background:var(--mark); padding:0 1px; }
.cite { font-size:14px; color:var(--muted); margin:0 0 10px; }
.flag { background:#fff6f0; border:1px solid #f0cdb4; border-radius:4px;
        padding:8px 12px; font-size:14px; margin:0 0 12px; }
.nopassage { color:var(--muted); font-style:italic; }
.missing ul { margin:6px 0; padding-left:22px; }
.years { margin:10px 0 0; }
.missing code { background:#fafafa; border:1px solid var(--line); padding:0 4px;
                border-radius:3px; }
.note { color:var(--muted); font-size:13px; }
footer { color:var(--muted); font-size:13px; border-top:1px solid var(--line);
         padding-top:14px; margin-top:28px; }
@media print { .card { break-inside:avoid; } .passage { max-height:none; } }
"""


def build(rubric: Sequence[str], rows: Sequence[dict], *, source: str) -> str:
    siblings: Dict[str, List[dict]] = {}
    for row in rows:
        siblings.setdefault(row["item_id"], []).append(row)

    cards = "\n".join(card_html(i, row, siblings) for i, row in enumerate(rows, start=1))
    rubric_items = "".join(f"<li>{html.escape(line)}</li>" for line in rubric)
    items = len({r["item_id"] for r in rows})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Narrative rating — reading view</title>
<style>{STYLE}</style></head>
<body><div class="wrap">
<h1>Narrative rating — reading view</h1>
<p class="sub">{len(rows)} row(s) over {items} question(s), in sheet order.
Read-only. Record verdicts in the CSV, not here.</p>
<div class="rubric"><h2>Rubric</h2><ul>{rubric_items}</ul></div>
{cards}
<footer>Generated from <code>{html.escape(source)}</code>. This page shows no
automated verdict, score or scorer output: the ratings are compared against the
scorer afterwards, and a rating anchored on it would not be independent.
Highlighting and the &ldquo;not found in passage&rdquo; list are assistance only,
never a verdict.</footer>
</div></body></html>
"""


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sheet", type=Path,
                    default=REPO_ROOT / "reports/phase4/narrative_rating_sheet.csv")
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "reports/phase4/narrative_rating_view.html")
    args = ap.parse_args(argv)

    rubric, rows = read_sheet(args.sheet)
    try:
        source = str(args.sheet.relative_to(REPO_ROOT))
    except ValueError:
        source = args.sheet.name
    page = build(rubric, rows, source=source)
    args.out.write_text(page, encoding="utf-8")

    leaked = [c for c in OWNER_COLUMNS
              for r in rows if (r.get(c) or "").strip() and (r.get(c) or "").strip() in page]
    marks = page.count("<mark>")
    # Count the SECTIONS, not the phrase: the footer explains the feature and
    # would otherwise be counted as a card that uses it.
    missing = page.count('<section class="missing">')
    years = page.count('class="years note"')
    print(f"{len(rows)} row(s), {len({r['item_id'] for r in rows})} question(s) → "
          f"{args.out.relative_to(REPO_ROOT) if args.out.is_relative_to(REPO_ROOT) else args.out}")
    print(f"  highlighted number occurrences : {marks}")
    print(f"  cards listing an unmatched figure: {missing}")
    print(f"  cards noting a year reference    : {years}")
    print(f"  owner columns rendered         : {len(leaked)} (must be 0)")
    print(f"  form fields                    : {page.count('<input')} "
          f"(must be 0)   scripts: {page.count('<script')} (must be 0)")
    print(f"  sha256[:16] {hashlib.sha256(page.encode()).hexdigest()[:16]}")
    return 1 if leaked else 0


if __name__ == "__main__":
    raise SystemExit(main())
