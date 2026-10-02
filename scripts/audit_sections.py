#!/usr/bin/env python3
"""Section-quality audit for the parsed 10-K corpus (P3-00a, spec D23).

Why this exists
---------------
D23 records that v1's parsed *statement* sections are unreliable: one filer's
income-statement section is 106 characters of heading, another's is empty, a
third's is over a megabyte. ``validation_status`` in the facts resolver was
therefore demoted to informational. But the **text path** reads the same
``ParsedSection`` slices — ``retrieval/retriever.py`` scrolls by section id for
``fs_income_stmt``, ``fs_balance_sheet``, ``fs_notes`` and for the narrative
focus sections (``item_1_business``, ``item_1a_risk_factors``, ``item_3_legal``,
``item_1c_cyber``), and ``retrieval/parent_store.py`` serves the whole section
as the LLM's context. So the question D23 defers to P3-00 is: how bad is it for
the sections a user-facing answer actually depends on?

This script answers that from the parsed documents on disk. It reads only
``data/parsed/*.json`` — no network, no LLM, no re-parse — so it is cheap to
re-run after a parser change and its output is a deterministic artifact.

What a verdict means
--------------------
Per (filing, audited section) the audit emits one of:

``missing``     the section id is absent from the parse entirely
``empty``       present with zero characters of text
``too_small``   present but below the floor at which the slice could hold the
                disclosure (see ``SectionSpec.min_chars`` for the per-class
                reason); in practice a heading and nothing else
``too_large``   present but above the cap a single section of that kind can
                honestly reach (``SectionSpec.max_chars``)
``ok``          within the band

Size is a *symptom*; it does not say what went wrong. So each row also carries
``absorbs``: the audited sections that are **missing from this filing** whose
heading text nevertheless appears inside this section's own text. That is
direct, falsifiable evidence of a boundary defect (section A's slice ran past
its end and consumed B) rather than an inference from a character count.

Negative control
----------------
CLAUDE.md rule 15: a check that has never failed is not evidence. ``--self-test``
builds synthetic ``ParsedDocument`` fixtures with a planted empty section, a
planted heading-only section and a planted oversized section, asserts each is
flagged with the expected verdict, asserts a clean fixture produces no findings,
and asserts two runs over the same input are byte-identical.

Exit codes: 0 no findings (or self-test passed) · 1 findings · 2 could not run.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

VERDICT_OK = "ok"
VERDICT_MISSING = "missing"
VERDICT_EMPTY = "empty"
VERDICT_TOO_SMALL = "too_small"
VERDICT_TOO_LARGE = "too_large"

# Verdicts that count as a finding (i.e. make the exit code non-zero).
FINDING_VERDICTS = (VERDICT_MISSING, VERDICT_EMPTY, VERDICT_TOO_SMALL, VERDICT_TOO_LARGE)


@dataclass(frozen=True)
class SectionSpec:
    """One audited section, with the thresholds and why they are what they are."""

    section_id: str
    kind: str                 # "narrative" | "statement" | "notes"
    used_by: str              # how the text path reaches this section
    min_chars: int
    max_chars: int
    # Patterns that identify this section's own heading inside another
    # section's text, for the `absorbs` evidence. Anchored to a whole line so a
    # cross-reference inside a sentence does not count.
    heading_patterns: Sequence[str] = ()


# The audited set is exactly the sections the text path depends on, plus the
# statement sections D23 is about.
#
# Thresholds are stated per kind and argued from what the document itself must
# contain, not fitted to this corpus:
#
# * ``min_chars`` for a narrative item = 1,000 characters, roughly 150 words.
#   A real Item 1/1A/7/7A disclosure cannot be shorter than a single paragraph.
#   Items that filers legitimately satisfy by cross-reference ("Item 3. Legal
#   Proceedings — see Note 12") will trip this floor; that is intentional, the
#   report distinguishes a legitimate cross-reference from a parser defect, and
#   either way such a slice is useless as retrieval context.
# * ``min_chars`` for a statement = 1,500 characters. A consolidated statement
#   rendered with two or three year columns and ~25 line items cannot be
#   smaller; below this the slice is a heading.
# * ``max_chars`` for a statement = 60,000 characters. At roughly 3,000
#   characters per dense financial page that is 20 pages, and no single
#   consolidated statement occupies 20 pages. Above it, the slice has run into
#   the notes or the next statement.
# * ``max_chars`` for a narrative item = 250,000 characters (~80 pages). Item 1
#   and Item 1A are the longest narrative items in a 10-K and even a bank's run
#   well under that; above it the slice has absorbed its neighbours.
# * ``fs_notes`` is the one section that is legitimately enormous — a large
#   bank's notes run hundreds of pages — so its cap is 600,000 characters
#   (~200 pages) and the report reads a near-cap value as plausible rather
#   than broken.
AUDITED_SECTIONS: Sequence[SectionSpec] = (
    SectionSpec(
        "item_1_business", "narrative",
        "retriever _FOCUS_SECTION_PASS['business_overview'] scroll",
        1_000, 250_000,
        (r"^item\s*1\.?\s*business$", r"^business$"),
    ),
    SectionSpec(
        "item_1a_risk_factors", "narrative",
        "retriever _FOCUS_SECTION_PASS['risk_factors'] scroll",
        1_000, 250_000,
        (r"^item\s*1a\.?\s*risk\s+factors$", r"^risk\s+factors$"),
    ),
    SectionSpec(
        "item_1c_cyber", "narrative",
        "retriever _FOCUS_SECTION_PASS['cybersecurity'] scroll",
        1_000, 250_000,
        (r"^item\s*1c\.?\s*cybersecurity$", r"^cybersecurity$"),
    ),
    SectionSpec(
        "item_3_legal", "narrative",
        "retriever _FOCUS_SECTION_PASS['legal_proceedings'] scroll",
        1_000, 250_000,
        (r"^item\s*3\.?\s*legal\s+proceedings$", r"^legal\s+proceedings$"),
    ),
    SectionSpec(
        "item_7_mda", "narrative",
        "hybrid search + parent context (MD&A is the densest prose source)",
        1_000, 250_000,
        (
            r"^item\s*7\.?\s*management.s\s+discussion\s+and\s+analysis.*$",
            r"^management.s\s+discussion\s+and\s+analysis.*$",
            r"^financial\s+review$",
        ),
    ),
    SectionSpec(
        "item_7a_market_risk", "narrative",
        "hybrid search + parent context",
        1_000, 250_000,
        (
            r"^item\s*7a\.?\s*quantitative\s+and\s+qualitative.*$",
            r"^quantitative\s+and\s+qualitative\s+disclosures.*$",
        ),
    ),
    SectionSpec(
        "fs_notes", "notes",
        "retriever scroll for focus='segment_info'; cited as the notes",
        10_000, 600_000,
        (r"^notes?\s+to\s+(the\s+)?(consolidated\s+)?financial\s+statements?$",),
    ),
    SectionSpec(
        "fs_income_stmt", "statement",
        "retriever unconditional scroll on every query",
        1_500, 60_000,
        (
            r"^consolidated\s+statements?\s+of\s+operations$",
            r"^consolidated\s+statements?\s+of\s+(income|earnings)$",
            r"^consolidated\s+(income|earnings)\s+statements?$",
        ),
    ),
    SectionSpec(
        "fs_balance_sheet", "statement",
        "retriever scroll for focus='balance_sheet'",
        1_500, 60_000,
        (
            r"^consolidated\s+balance\s+sheets?$",
            r"^consolidated\s+statements?\s+of\s+financial\s+(condition|position)$",
            r"^consolidated\s+statements?\s+of\s+condition$",
        ),
    ),
    SectionSpec(
        "fs_cash_flow", "statement",
        "hybrid search + parent context",
        1_500, 60_000,
        (r"^consolidated\s+statements?\s+of\s+cash\s+flows?$",),
    ),
    SectionSpec(
        "fs_equity", "statement",
        "hybrid search + parent context",
        1_500, 60_000,
        (
            r"^consolidated\s+statements?\s+of\s+(stockholders|shareholders).*$",
            r"^consolidated\s+statements?\s+of\s+changes\s+in\s+equity$",
        ),
    ),
)

SPEC_BY_ID: Dict[str, SectionSpec] = {s.section_id: s for s in AUDITED_SECTIONS}

_COMPILED_HEADINGS: Dict[str, List[re.Pattern[str]]] = {
    s.section_id: [re.compile(p, re.IGNORECASE) for p in s.heading_patterns]
    for s in AUDITED_SECTIONS
}


class Row(NamedTuple):
    """One audited (filing, section) pair."""

    filing: str          # e.g. "AAPL_2024"
    ticker: str
    fiscal_year: int
    section_id: str
    kind: str
    verdict: str
    chars: int
    blocks: int
    share_of_doc: float   # fraction of the filing's parsed characters
    title: str
    absorbs: str          # ";"-joined ids of missing sections whose heading is inside


class ParsedFiling(NamedTuple):
    """The slice of a ParsedDocument the audit needs."""

    filing: str
    ticker: str
    fiscal_year: int
    # section_id -> (title, chars, blocks, text)
    sections: Dict[str, tuple]
    total_chars: int


# ── loading ────────────────────────────────────────────────────────────────────

def load_filing(path: Path) -> ParsedFiling:
    """Read one ``data/parsed/*.json`` into the audit's own shape."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    sections: Dict[str, tuple] = {}
    total = 0
    for sec in doc.get("sections", []):
        blocks = sec.get("content_blocks", []) or []
        text = "\n\n".join(b.get("text", "") for b in blocks)
        total += len(text)
        sid = sec.get("section_id", "")
        # ingestion/parser.py disambiguates repeated ids with a suffix, so a
        # bare duplicate means two slices really do claim the same id. Keep the
        # larger one and let the smaller contribute to total_chars only;
        # reporting the larger is the generous reading of the parse.
        if sid in sections and len(text) <= sections[sid][1]:
            continue
        sections[sid] = (sec.get("title", ""), len(text), len(blocks), text)

    ticker = doc.get("ticker") or path.stem.rsplit("_", 1)[0]
    try:
        fy = int(doc.get("fiscal_year") or path.stem.rsplit("_", 1)[1])
    except (ValueError, IndexError):
        fy = 0
    return ParsedFiling(path.stem, ticker, fy, sections, total)


def load_corpus(parsed_dir: Path) -> List[ParsedFiling]:
    """Every parsed filing, in a stable order."""
    return [load_filing(p) for p in sorted(parsed_dir.glob("*.json"))]


# ── auditing ───────────────────────────────────────────────────────────────────

def _verdict(spec: SectionSpec, chars: int) -> str:
    if chars == 0:
        return VERDICT_EMPTY
    if chars < spec.min_chars:
        return VERDICT_TOO_SMALL
    if chars > spec.max_chars:
        return VERDICT_TOO_LARGE
    return VERDICT_OK


def _heading_hits(text: str, section_ids: Iterable[str]) -> List[str]:
    """Which of ``section_ids`` have their own heading on a line of ``text``.

    Line-anchored on purpose: "see the Consolidated Balance Sheets on page 62"
    inside a sentence is a cross-reference, not a swallowed heading, and
    counting it would manufacture evidence.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and len(ln) <= 120]
    hits = []
    for sid in section_ids:
        patterns = _COMPILED_HEADINGS.get(sid, [])
        if any(p.match(ln) for ln in lines for p in patterns):
            hits.append(sid)
    return sorted(hits)


def audit_filing(filing: ParsedFiling) -> List[Row]:
    """Audit one filing against every ``AUDITED_SECTIONS`` entry."""
    missing_ids = [s.section_id for s in AUDITED_SECTIONS if s.section_id not in filing.sections]

    rows: List[Row] = []
    for spec in AUDITED_SECTIONS:
        entry = filing.sections.get(spec.section_id)
        if entry is None:
            rows.append(Row(
                filing.filing, filing.ticker, filing.fiscal_year, spec.section_id,
                spec.kind, VERDICT_MISSING, 0, 0, 0.0, "", "",
            ))
            continue

        title, chars, blocks, text = entry
        # A section cannot absorb itself, and only a MISSING section is evidence
        # of a boundary defect — a heading for a section that was also parsed
        # separately is just a cross-reference or a repeated header.
        candidates = [sid for sid in missing_ids if sid != spec.section_id]
        absorbs = _heading_hits(text, candidates)
        share = (chars / filing.total_chars) if filing.total_chars else 0.0
        rows.append(Row(
            filing.filing, filing.ticker, filing.fiscal_year, spec.section_id,
            spec.kind, _verdict(spec, chars), chars, blocks, round(share, 4),
            title, ";".join(absorbs),
        ))
    return rows


def audit_corpus(corpus: Sequence[ParsedFiling]) -> List[Row]:
    rows: List[Row] = []
    for filing in corpus:
        rows.extend(audit_filing(filing))
    # Deterministic order regardless of filesystem iteration order.
    return sorted(rows, key=lambda r: (r.filing, r.section_id))


# ── reporting ──────────────────────────────────────────────────────────────────

def summarize(rows: Sequence[Row]) -> dict:
    """Counts per section and per verdict, plus the corpus totals."""
    filings = sorted({r.filing for r in rows})
    per_section: Dict[str, Dict[str, int]] = {}
    for spec in AUDITED_SECTIONS:
        counts = dict.fromkeys((VERDICT_OK, *FINDING_VERDICTS), 0)
        for r in rows:
            if r.section_id == spec.section_id:
                counts[r.verdict] += 1
        per_section[spec.section_id] = counts

    per_verdict = dict.fromkeys((VERDICT_OK, *FINDING_VERDICTS), 0)
    for r in rows:
        per_verdict[r.verdict] += 1

    absorb_pairs: Dict[str, int] = {}
    for r in rows:
        for sid in filter(None, r.absorbs.split(";")):
            absorb_pairs[f"{r.section_id} absorbs {sid}"] = (
                absorb_pairs.get(f"{r.section_id} absorbs {sid}", 0) + 1
            )

    return {
        "filings": len(filings),
        "audited_sections": len(AUDITED_SECTIONS),
        "pairs": len(rows),
        "per_verdict": per_verdict,
        "per_section": per_section,
        "absorb_evidence": dict(sorted(absorb_pairs.items())),
        "thresholds": {
            s.section_id: {
                "kind": s.kind, "min_chars": s.min_chars, "max_chars": s.max_chars,
                "used_by": s.used_by,
            }
            for s in AUDITED_SECTIONS
        },
    }


def write_outputs(rows: Sequence[Row], summary: dict, out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "section_audit.json"
    csv_path = out_dir / "section_audit.csv"

    json_path.write_text(
        json.dumps(
            {"summary": summary, "rows": [r._asdict() for r in rows]},
            indent=2, sort_keys=False,
        ) + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(Row._fields)
        for r in rows:
            writer.writerow(r)
    return [json_path, csv_path]


def print_summary(rows: Sequence[Row], summary: dict) -> None:
    print(f"\nsection audit: {summary['filings']} filings × "
          f"{summary['audited_sections']} audited sections = {summary['pairs']} pairs")
    pv = summary["per_verdict"]
    print("  verdicts: " + "  ".join(f"{k}={v}" for k, v in pv.items()))
    print()
    header = f"  {'section_id':22s} {'kind':10s} " + "  ".join(
        f"{v:>9s}" for v in (VERDICT_OK, *FINDING_VERDICTS)
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for spec in AUDITED_SECTIONS:
        c = summary["per_section"][spec.section_id]
        cells = "  ".join(f"{c[v]:>9d}" for v in (VERDICT_OK, *FINDING_VERDICTS))
        print(f"  {spec.section_id:22s} {spec.kind:10s} {cells}")

    if summary["absorb_evidence"]:
        print("\n  boundary evidence (a present section holds a missing section's heading):")
        for pair, n in summary["absorb_evidence"].items():
            print(f"    {n:3d}  {pair}")


# ── self-test (negative controls, T3-12) ──────────────────────────────────────

def _fixture_doc(ticker: str, fy: int, sections: Sequence[tuple]) -> dict:
    """A minimal ParsedDocument-shaped dict: sections of (id, title, text)."""
    return {
        "doc_id": f"{ticker}-{fy}",
        "source_path": f"synthetic/{ticker}_{fy}.htm",
        "company": ticker,
        "ticker": ticker,
        "filing_type": "10-K",
        "fiscal_year": fy,
        "sections": [
            {
                "section_id": sid,
                "title": title,
                "order": i,
                "content_blocks": (
                    [] if text == "" else
                    [{"block_id": f"b{i}", "block_type": "text", "text": text, "position": 0}]
                ),
            }
            for i, (sid, title, text) in enumerate(sections)
        ],
    }


def _clean_sections() -> List[tuple]:
    """Every audited section, sized inside its own band."""
    out = []
    for spec in AUDITED_SECTIONS:
        size = spec.min_chars + 500
        out.append((spec.section_id, spec.section_id, "w " * (size // 2)))
    return out


def self_test(tmp_root: Optional[Path] = None) -> int:
    """Plant one known defect per verdict and assert each is caught."""
    import tempfile

    print("section audit self-test: planting known defects in a scratch corpus")
    failures: List[str] = []
    with tempfile.TemporaryDirectory(dir=tmp_root) as td:
        parsed = Path(td) / "parsed"
        parsed.mkdir()

        # 1. A clean filing: every audited section inside its band.
        (parsed / "CLEAN_2024.json").write_text(
            json.dumps(_fixture_doc("CLEAN", 2024, _clean_sections())), encoding="utf-8"
        )

        # 2. A filing with one planted defect of each kind. The missing section
        #    is item_3_legal, and item_1_business carries its heading on a line
        #    of its own, so `absorbs` must report it.
        defective = []
        for spec in AUDITED_SECTIONS:
            if spec.section_id == "item_3_legal":
                continue                                  # planted: missing
            if spec.section_id == "item_1a_risk_factors":
                defective.append((spec.section_id, "planted empty", ""))
            elif spec.section_id == "item_1c_cyber":
                defective.append((spec.section_id, "planted tiny", "x" * 12))
            elif spec.section_id == "fs_income_stmt":
                defective.append((spec.section_id, "planted oversized",
                                  "y" * (spec.max_chars + 1)))
            elif spec.section_id == "item_1_business":
                body = "w " * (spec.min_chars // 2 + 500)
                defective.append((spec.section_id, "absorbs legal",
                                  body + "\n\nItem 3. Legal Proceedings\n\n" + body))
            else:
                defective.append((spec.section_id, spec.section_id,
                                  "w " * ((spec.min_chars + 500) // 2)))
        (parsed / "BROKEN_2024.json").write_text(
            json.dumps(_fixture_doc("BROKEN", 2024, defective)), encoding="utf-8"
        )

        rows = audit_corpus(load_corpus(parsed))
        by_key = {(r.filing, r.section_id): r for r in rows}

        expectations = {
            ("CLEAN_2024", s.section_id): VERDICT_OK for s in AUDITED_SECTIONS
        }
        expectations.update({
            ("BROKEN_2024", "item_3_legal"): VERDICT_MISSING,
            ("BROKEN_2024", "item_1a_risk_factors"): VERDICT_EMPTY,
            ("BROKEN_2024", "item_1c_cyber"): VERDICT_TOO_SMALL,
            ("BROKEN_2024", "fs_income_stmt"): VERDICT_TOO_LARGE,
        })
        for key, want in sorted(expectations.items()):
            got = by_key.get(key)
            if got is None:
                failures.append(f"no row for {key}")
            elif got.verdict != want:
                failures.append(f"{key}: expected {want}, got {got.verdict} ({got.chars} chars)")

        # The boundary evidence must fire, and only where the heading really is.
        absorber = by_key.get(("BROKEN_2024", "item_1_business"))
        if absorber is None or "item_3_legal" not in absorber.absorbs:
            failures.append("item_1_business did not report absorbing item_3_legal")
        clean_absorbs = [
            r for r in rows if r.filing == "CLEAN_2024" and r.absorbs
        ]
        if clean_absorbs:
            failures.append(f"clean filing reported absorb evidence: {clean_absorbs}")

        # Determinism: the same input twice must give byte-identical output.
        again = audit_corpus(load_corpus(parsed))
        if [r._asdict() for r in rows] != [r._asdict() for r in again]:
            failures.append("audit output is not deterministic across two runs")

    if failures:
        print("\nSELF-TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("  all planted defects detected; clean fixture passed; output deterministic")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--parsed-dir", type=Path, default=REPO_ROOT / "data" / "parsed",
                    help="directory of ParsedDocument JSON files")
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "reports" / "phase3",
                    help="where section_audit.{json,csv} are written")
    ap.add_argument("--no-write", action="store_true", help="print only")
    ap.add_argument("--self-test", action="store_true",
                    help="run the negative controls and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if not args.parsed_dir.is_dir():
        print(f"error: {args.parsed_dir} is not a directory", file=sys.stderr)
        return 2
    corpus = load_corpus(args.parsed_dir)
    if not corpus:
        print(f"error: no parsed documents in {args.parsed_dir}", file=sys.stderr)
        return 2

    rows = audit_corpus(corpus)
    summary = summarize(rows)
    print_summary(rows, summary)

    if not args.no_write:
        for p in write_outputs(rows, summary, args.out_dir):
            print(f"\nwrote {p.relative_to(REPO_ROOT) if REPO_ROOT in p.parents else p}")

    findings = sum(summary["per_verdict"][v] for v in FINDING_VERDICTS)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
