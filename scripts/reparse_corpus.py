#!/usr/bin/env python3
"""Re-parse the already-downloaded filings, offline (P3-00).

Why this exists separately from ``run_ingestion.py``: that entry point parses
whatever the download manifest lists, and the manifest only covers the bundled
tickers downloaded into ``data/raw/``. Filings that arrived through on-demand
ingestion (Netflix) are in ``data/parsed/`` with their primary document in
``.cache/filings/<TICKER>_<accession>/`` instead, so a manifest-driven re-parse
silently leaves them on the old parser. A parser change has to be measured over
the whole parsed corpus or the before/after numbers are not comparable.

Each existing ``data/parsed/*.json`` names the source document it came from.
This script re-parses that source with the current parser and writes the result
to ``--out-dir``, which defaults to a scratch directory so a measurement run
cannot clobber the corpus the index was built from. Pass
``--out-dir data/parsed`` to adopt the new parse.

Offline: it reads only files already on disk and never contacts SEC.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CACHE_FILINGS = REPO_ROOT / ".cache" / "filings"


def resolve_source(doc: dict) -> Optional[Path]:
    """Where this filing's primary document can be read from today.

    The path recorded at parse time is tried first. Filings ingested on demand,
    or parsed from a checkout that no longer exists, fall back to the SEC
    response cache, which keeps one directory per accession.
    """
    recorded = Path(doc.get("source_path", ""))
    if recorded.is_file():
        return recorded

    accession = doc.get("accession_number") or ""
    ticker = doc.get("ticker") or ""
    if not accession or not ticker:
        return None

    cache_dir = CACHE_FILINGS / f"{ticker}_{accession}"
    if not cache_dir.is_dir():
        return None
    # The cache holds the filing's own documents plus FilingSummary.xml. The
    # primary document is the largest .htm/.html file; the exhibits and the
    # summary are all smaller.
    candidates = [
        p for p in cache_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".htm", ".html")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def reparse_one(src: Path, doc: dict, out_dir: Path) -> Tuple[str, int, float]:
    """Parse ``src`` with the current parser and write the JSON. Returns
    (filing, section count, seconds)."""
    from ingestion.parser import parse_filing

    started = time.monotonic()
    parsed = parse_filing(
        file_path=src,
        company=doc.get("company") or doc.get("ticker", ""),
        ticker=doc.get("ticker", ""),
        fiscal_year=int(doc.get("fiscal_year") or 0),
        accession_number=doc.get("accession_number") or "",
        filing_date=doc.get("filing_date"),
    )
    name = f"{parsed.ticker}_{parsed.fiscal_year}.json"
    # Atomic: a run killed mid-write must leave either the old parse or the new
    # one, never half of one that --skip-existing would then treat as done.
    target = out_dir / name
    tmp = target.with_name(target.name + ".partial")
    tmp.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return name[:-5], len(parsed.sections), time.monotonic() - started


def parse_is_complete(path: Path) -> bool:
    """True when ``path`` holds a readable parse with at least one section.

    The resume predicate for ``--skip-existing``. Deliberately strict about
    what counts as done: re-parsing a filing costs seconds, while treating a
    truncated file as finished poisons every chunk and point built from it.
    """
    if not path.is_file():
        return False
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return bool(isinstance(doc, dict) and doc.get("doc_id") and doc.get("sections"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--parsed-dir", type=Path, default=REPO_ROOT / "data" / "parsed",
                    help="the existing parses, used for their source paths and metadata")
    ap.add_argument("--out-dir", type=Path, required=True,
                    help="where the new parses go (use data/parsed to adopt them)")
    ap.add_argument("--only", default="",
                    help="comma-separated filing stems, e.g. JPM_2024,GS_2024")
    ap.add_argument("--skip-existing", action="store_true",
                    help="leave filings already parsed into --out-dir alone (resume)")
    args = ap.parse_args(argv)

    if not args.parsed_dir.is_dir():
        print(f"error: {args.parsed_dir} is not a directory", file=sys.stderr)
        return 2
    args.out_dir.mkdir(parents=True, exist_ok=True)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    inputs = sorted(args.parsed_dir.glob("*.json"))
    if only:
        inputs = [p for p in inputs if p.stem in only]

    done: List[Tuple[str, int, float]] = []
    skipped: List[Tuple[str, str]] = []
    reused = 0
    for path in inputs:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if args.skip_existing and parse_is_complete(args.out_dir / path.name):
            reused += 1
            print(f"  reuse {path.stem}")
            continue
        src = resolve_source(doc)
        if src is None:
            skipped.append((path.stem, "no readable source document on disk"))
            print(f"  SKIP {path.stem}: no readable source document on disk")
            continue
        try:
            result = reparse_one(src, doc, args.out_dir)
        except Exception as exc:   # reported per filing, never swallowed
            skipped.append((path.stem, f"{type(exc).__name__}: {exc}"))
            print(f"  FAIL {path.stem}: {type(exc).__name__}: {exc}")
            continue
        done.append(result)
        print(f"  ok   {result[0]:14s} {result[1]:3d} sections  {result[2]:6.1f}s")

    print(f"\nre-parsed {len(done)} of {len(inputs)} filings into {args.out_dir}"
          + (f" ({reused} reused)" if reused else ""))
    if skipped:
        print("skipped:")
        for stem, why in skipped:
            print(f"  {stem}: {why}")
    return 0 if (done or reused) else 1


if __name__ == "__main__":
    sys.exit(main())
