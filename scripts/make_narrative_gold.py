#!/usr/bin/env python3
"""Draft the expanded narrative retrieval set for D27 (P4-13).

    .venv/bin/python scripts/make_narrative_gold.py
    .venv/bin/python scripts/make_narrative_gold.py --target 45 --out <path>

D27 needs at least 40 narrative items before the reranker decision can be made
on anything but noise; the shipped set has 15, and the ablation intervals at
n=15 were too wide to act on (Phase 4 report section 6).

**These items score retrieval, not wording.** A narrative item's expectation is
the *section* an answer must be drawn from — ``section_hit@k`` and MRR — so a
correct item needs two things to be true of the filing, and both are checked
here rather than asserted:

1. the expected section is ``ok`` in the live section audit for that exact
   filing (``--audit``), so the item cannot be scored against a slice the
   parser did not recover; and
2. the section's own text carries the topic, at least ``--min-evidence``
   matches of the topic's pattern, so the question is one the filing actually
   answers. A question whose answer is not in the expected section would
   measure the drafter's imagination, not retrieval.

Topics are a hand-written table — the questions are the part no generator can
supply — but *which filings* each becomes an item for is decided by the two
checks above, over the whole corpus, so the set cannot quietly be the filings
that happened to work.

Deterministic: same corpus and audit in, byte-identical file out. Selection is
round-robin over topics and then over (ticker, year) in sorted order, never
random, so there is no seed to record and no run that cannot be reproduced.

Every item is written ``verified_by: auto``. P4-13 asks the owner to review a
sample of 10; until that happens these are drafts (D29, CLAUDE.md rule 17) and
``scripts/make_narrative_gold.py --sample 10`` prints the sample to review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: topic id → (question template, expected sections, evidence pattern, basis)
#: ``{company}`` is the filer's own name as the filing states it.
TOPICS: Tuple[Tuple[str, str, Tuple[str, ...], str, str], ...] = (
    ("SUPPLYCHAIN",
     "What risks does {company} disclose about its suppliers and manufacturing?",
     ("item_1a_risk_factors",),
     r"single source|sole source|outsourc|supplier|manufactur",
     "Item 1A carries the supplier and manufacturing concentration risks"),
    ("COMPETITION",
     "How does {company} describe the competition it faces?",
     ("item_1_business",),
     r"competit",
     "Item 1 describes the competitive landscape the filer operates in"),
    ("HUMANCAPITAL",
     "What does {company} say about its employees and human capital?",
     ("item_1_business",),
     r"human capital|employees|workforce",
     "Item 1 carries the human capital disclosure the SEC requires"),
    ("REGULATION",
     "What regulatory risks does {company} describe?",
     ("item_1a_risk_factors",),
     r"regulat|supervis|compliance",
     "Item 1A carries the regulatory and supervisory risks"),
    ("LIQUIDITY",
     "What does {company} say about liquidity and funding risk?",
     ("item_1a_risk_factors",),
     r"liquidity|funding",
     "Item 1A carries the liquidity and funding risks"),
    ("CLIMATE",
     "What climate and environmental risks does {company} disclose?",
     ("item_1a_risk_factors",),
     r"climate|environmental",
     "Item 1A carries the climate and environmental risks"),
    ("CYBER",
     "How does {company} manage cybersecurity risk?",
     ("item_1c_cyber",),
     r"cybersecurity|information security",
     "Item 1C is the dedicated cybersecurity risk management disclosure"),
    ("INTERESTRATE",
     "What does {company} say about interest rate risk?",
     ("item_1a_risk_factors",),
     r"interest rate",
     "Item 1A carries the interest rate risk disclosure"),
    ("IP",
     "What intellectual property risks does {company} describe?",
     ("item_1a_risk_factors",),
     r"intellectual property|patent|trademark",
     "Item 1A carries the intellectual property risks"),
    ("CREDITRISK",
     "How does {company} describe its credit risk?",
     ("item_1a_risk_factors",),
     r"credit risk|credit loss",
     "Item 1A carries the credit risk disclosure"),
    ("SEGMENTS",
     "What business segments does {company} report?",
     ("item_1_business",),
     r"segment",
     "Item 1 names the filer's reportable segments"),
    ("AI",
     "What does {company} say about risks from artificial intelligence?",
     ("item_1a_risk_factors",),
     r"artificial intelligence|machine learning",
     "Item 1A carries the artificial intelligence risk disclosure"),
    ("STRATEGY",
     "How does {company} describe its products and services?",
     ("item_1_business",),
     r"product|service|offering",
     "Item 1 describes what the filer sells"),
    ("MARKETRISK",
     "How does {company} describe its market risk?",
     ("item_7a_market_risk",),
     r"market risk",
     "Item 7A is the dedicated quantitative and qualitative market risk item"),
    ("LEGAL",
     "What legal proceedings does {company} describe?",
     ("item_3_legal",),
     r"proceeding|litigation|lawsuit",
     "Item 3 is the legal proceedings disclosure"),
)


def load_audit(path: Path) -> Dict[Tuple[str, int, str], str]:
    payload = json.loads(path.read_text())
    return {(r["ticker"], int(r["fiscal_year"]), r["section_id"]): r["verdict"]
            for r in payload["rows"]}


def load_corpus(parsed_dir: Path) -> Dict[Tuple[str, int], dict]:
    corpus: Dict[Tuple[str, int], dict] = {}
    for path in sorted(parsed_dir.glob("*.json")):
        doc = json.loads(path.read_text())
        corpus[(doc["ticker"], int(doc["fiscal_year"]))] = doc
    return corpus


def section_text(doc: Mapping, section_id: str) -> str:
    for section in doc["sections"]:
        if section["section_id"] == section_id:
            return " ".join(b.get("text", "") for b in section["content_blocks"])
    return ""


def candidates(corpus, audit, *, min_evidence: int) -> Dict[str, List[dict]]:
    """Every (topic, filing) pair that passes both checks, topic by topic."""
    out: Dict[str, List[dict]] = {}
    for topic, question, sections, pattern, basis in TOPICS:
        regex = re.compile(pattern, re.I)
        rows: List[dict] = []
        for (ticker, year) in sorted(corpus):
            doc = corpus[(ticker, year)]
            if any(audit.get((ticker, year, s)) != "ok" for s in sections):
                continue
            evidence = sum(len(regex.findall(section_text(doc, s))) for s in sections)
            if evidence < min_evidence:
                continue
            rows.append({
                "id": f"R-{ticker}-{topic}-{year}",
                "category": "narrative",
                "question": question.format(company=doc["company"]),
                "as_of": None,
                "expected": {"type": "text", "ticker": ticker,
                             "fiscal_label": year,
                             "expected_sections": list(sections)},
                "source": {"ticker": ticker, "fiscal_label": year,
                           "drafted_by": "claude",
                           "basis": f"{basis}; {evidence} topic matches in the "
                                    f"audited section text of {ticker}_{year}"},
                "verified_by": "auto",
                "notes": "P4-13 retrieval item: scored on section hit@k and MRR, "
                         "never on wording",
            })
        out[topic] = rows
    return out


def _spread(rows: List[dict], rotation: int) -> List[dict]:
    """One topic's pool, reordered to cycle filers and rotated by ``rotation``.

    The pool arrives sorted by (ticker, year), so taking the first three rows
    takes three years of one filer. Cycling tickers first means an early cut of
    the pool is spread across companies, and rotating by the topic's position
    means two topics do not both open on Apple.
    """
    by_ticker: Dict[str, List[dict]] = {}
    for row in rows:
        by_ticker.setdefault(row["expected"]["ticker"], []).append(row)
    tickers = sorted(by_ticker)
    if not tickers:
        return []
    tickers = tickers[rotation % len(tickers):] + tickers[:rotation % len(tickers)]
    # Rotate each filer's years too. Without this every topic takes each
    # filer's earliest year and fiscal 2025 never enters the set, which would
    # make the whole measurement blind to the newest filings.
    for index, ticker in enumerate(tickers):
        years = by_ticker[ticker]
        shift = (rotation + index) % len(years)
        by_ticker[ticker] = years[shift:] + years[:shift]
    ordered: List[dict] = []
    depth = 0
    while len(ordered) < len(rows):
        for ticker in tickers:
            if depth < len(by_ticker[ticker]):
                ordered.append(by_ticker[ticker][depth])
        depth += 1
    return ordered


def select(pools: Dict[str, List[dict]], target: int) -> List[dict]:
    """Round-robin across topics, so no single topic dominates the set.

    A set that was 30 risk-factor questions would make section_hit@k a measure
    of one section's retrievability, and D27's decision would then be about
    Item 1A rather than about the reranker. The same argument applies to
    filers, which is why each pool is spread over tickers before it is cut:
    13 filers differ far more in how their 10-Ks are written than three years
    of one filer do, and a set drawn from three companies would make the
    reranker decision a statement about those three.
    """
    spread = {topic: _spread(rows, rotation)
              for rotation, topic in enumerate(sorted(pools))
              for rows in [pools[topic]]}
    chosen: List[dict] = []
    depth = 0
    while len(chosen) < target:
        added = False
        for topic in sorted(spread):
            rows = spread[topic]
            if depth < len(rows):
                chosen.append(rows[depth])
                added = True
                if len(chosen) >= target:
                    break
        if not added:
            break
        depth += 1
    return sorted(chosen, key=lambda item: item["id"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--parsed-dir", type=Path, default=REPO_ROOT / "data/parsed")
    ap.add_argument("--audit", type=Path,
                    default=REPO_ROOT / "reports/phase4/section_audit_live/section_audit.json")
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "eval/gold/narrative_retrieval_v1.jsonl")
    ap.add_argument("--target", type=int, default=45)
    ap.add_argument("--min-evidence", type=int, default=3)
    ap.add_argument("--sample", type=int, default=0,
                    help="print this many items for the owner's review and exit")
    args = ap.parse_args(argv)

    audit = load_audit(args.audit)
    corpus = load_corpus(args.parsed_dir)
    pools = candidates(corpus, audit, min_evidence=args.min_evidence)
    items = select(pools, args.target)

    if args.sample:
        step = max(1, len(items) // args.sample)
        for item in items[::step][:args.sample]:
            print(f"{item['id']}\n  Q: {item['question']}\n"
                  f"  expects: {', '.join(item['expected']['expected_sections'])}\n"
                  f"  basis:   {item['source']['basis']}\n")
        return 0

    args.out.write_text("".join(json.dumps(i, sort_keys=False) + "\n" for i in items))

    print(f"topics with candidates: {sum(1 for v in pools.values() if v)} of {len(pools)}")
    for topic in sorted(pools):
        taken = sum(1 for i in items if f"-{topic}-" in i["id"])
        print(f"  {topic:14} pool {len(pools[topic]):>3}   taken {taken:>3}")
    tickers = sorted({i['expected']['ticker'] for i in items})
    years = sorted({i['expected']['fiscal_label'] for i in items})
    sections = sorted({s for i in items for s in i['expected']['expected_sections']})
    print(f"\n{len(items)} items · {len(tickers)} tickers · years {years} · "
          f"sections {sections}")
    print(f"wrote {args.out.relative_to(REPO_ROOT)}")
    return 0 if len(items) >= 40 else 1


if __name__ == "__main__":
    raise SystemExit(main())
