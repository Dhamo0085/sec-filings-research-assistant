"""
Phase 0 / Step 2 (2.1 - 2.4, 2.7) — inline-XBRL feasibility study.

Decision D1: can numeric facts be extracted reliably from the *primary
documents* the pipeline already downloads, without an LLM reading tables?

This is a STANDALONE script. ingestion/parser.py is not imported or modified.

Outputs:
  reports/phase0/ixbrl_coverage.csv   one row per (filing, concept)
  reports/phase0/ixbrl_tagstats.json  2.1 tag counts + 2.2 context stats
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parents[2]
FILINGS = REPO_ROOT / ".cache" / "filings"
OUT = REPO_ROOT / "reports" / "phase0"
OUT.mkdir(parents=True, exist_ok=True)

# ── Step 2.3 concept list, with alternates in priority order ────────────────
CONCEPTS: dict[str, dict] = {
    "Revenue": {
        "kind": "duration",
        "names": [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            # banks / asset managers
            "RevenuesNetOfInterestExpense",
            "InterestAndDividendIncomeOperating",
            "NoninterestIncome",
        ],
    },
    "NetIncomeLoss":             {"kind": "duration", "names": ["NetIncomeLoss", "ProfitLoss"]},
    "OperatingIncomeLoss":       {"kind": "duration", "names": ["OperatingIncomeLoss"]},
    "GrossProfit":               {"kind": "duration", "names": ["GrossProfit"]},
    "ResearchAndDevelopmentExpense": {"kind": "duration", "names": ["ResearchAndDevelopmentExpense"]},
    "Assets":                    {"kind": "instant",  "names": ["Assets"]},
    "Liabilities":               {"kind": "instant",  "names": ["Liabilities"]},
    "StockholdersEquity":        {"kind": "instant",  "names": ["StockholdersEquity",
                                   "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]},
    "CashAndCashEquivalentsAtCarryingValue": {"kind": "instant",
                                   "names": ["CashAndCashEquivalentsAtCarryingValue"]},
    "NetCashProvidedByUsedInOperatingActivities": {"kind": "duration",
                                   "names": ["NetCashProvidedByUsedInOperatingActivities",
                                             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]},
    "PaymentsToAcquirePropertyPlantAndEquipment": {"kind": "duration",
                                   "names": ["PaymentsToAcquirePropertyPlantAndEquipment"]},
    "EarningsPerShareDiluted":   {"kind": "duration", "names": ["EarningsPerShareDiluted"]},
}


def _ln(el) -> str:
    """Local tag name, lowercased.

    lxml's HTMLParser does NOT do namespace processing: it keeps the literal
    prefixed name and lowercases it, so <ix:nonFraction> arrives as the plain
    string tag 'ix:nonfraction'.  (An XML parser would choke on these ~2-13 MB
    filings' HTML entities, so HTMLParser is the practical choice.)  Compare
    on the lowercased local part, and read attributes case-insensitively for
    the same reason (contextRef -> contextref).
    """
    t = el.tag
    if not isinstance(t, str):
        return ""
    return t.rsplit(":", 1)[-1].lower()


def _attr(el, name: str, default: str = "") -> str:
    """Attribute lookup that tolerates HTMLParser's lowercasing."""
    v = el.get(name)
    if v is None:
        v = el.get(name.lower())
    return default if v is None else v


def parse_contexts(root) -> dict[str, dict]:
    """Step 2.2 — context id -> {instant | start/end, dimensional?}"""
    ctxs: dict[str, dict] = {}
    for el in root.iter():
        if _ln(el) != "context":
            continue
        cid = el.get("id")
        if not cid:
            continue
        info = {"id": cid, "instant": None, "start": None, "end": None,
                "dimensional": False, "members": []}
        for sub in el.iter():
            ln = _ln(sub)
            if ln == "instant":
                info["instant"] = (sub.text or "").strip()
            elif ln == "startdate":
                info["start"] = (sub.text or "").strip()
            elif ln == "enddate":
                info["end"] = (sub.text or "").strip()
            elif ln in ("explicitmember", "typedmember"):
                info["dimensional"] = True
                info["members"].append(_attr(sub, "dimension") + "=" + (sub.text or "").strip())
        ctxs[cid] = info
    return ctxs


def _to_decimal(raw: str) -> float | None:
    s = (raw or "").strip().replace(",", "").replace(" ", "")
    s = re.sub(r"[^0-9.\-()]", "", s)
    if not s or s in ("-", "."):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def fact_value(el) -> tuple[float | None, int, str]:
    """Apply scale and sign per the inline-XBRL spec. Returns (value, scale, sign)."""
    raw = "".join(el.itertext())
    base = _to_decimal(raw)
    if base is None:
        return None, 0, ""
    fmt = _attr(el, "format")
    if "zerodash" in fmt and not re.search(r"\d", raw):
        base = 0.0
    try:
        scale = int(_attr(el, "scale", "0") or 0)
    except ValueError:
        scale = 0
    sign = _attr(el, "sign")
    val = base * (10 ** scale)
    if sign == "-":
        val = -val
    return val, scale, sign


def analyse(path, ticker: str, fy: int, accession: str, period_end: str,
            filing_date: str) -> tuple[list[dict], dict]:
    """`path` is one document, or a LIST of documents from the same filing.

    The list form exists for EX-13 filers (Wells Fargo): the 10-K wrapper holds
    the xbrli:context definitions while the EX-13 exhibit holds the
    ix:nonFraction facts that reference them. Neither document resolves alone --
    contexts and facts must be pooled across the whole submission.
    """
    paths = [path] if isinstance(path, (str, Path)) else list(path)
    parser = etree.HTMLParser(huge_tree=True, recover=True)
    roots = [etree.parse(str(x), parser).getroot() for x in paths]

    nonfrac, nonnum = [], []
    for root in roots:
        for el in root.iter():
            ln = _ln(el)
            if ln == "nonfraction":
                nonfrac.append(el)
            elif ln == "nonnumeric":
                nonnum.append(el)

    ctxs = {}
    for root in roots:
        ctxs.update(parse_contexts(root))
    name_counts = Counter(_attr(el, "name").split(":")[-1] for el in nonfrac)

    stats = {
        "ticker": ticker, "fiscal_year": fy, "accession": accession,
        "period_end": period_end, "filing_date": filing_date,
        "file_mb": round(sum(Path(p).stat().st_size for p in paths) / 1e6, 2),
        "documents": [Path(p).name for p in paths],
        "ix_nonFraction_count": len(nonfrac),
        "ix_nonNumeric_count": len(nonnum),
        "distinct_nonFraction_names": len(name_counts),
        "context_count": len(ctxs),
        "contexts_dimensional": sum(1 for c in ctxs.values() if c["dimensional"]),
        "contexts_nondimensional": sum(1 for c in ctxs.values() if not c["dimensional"]),
        "top_20_concepts": name_counts.most_common(20),
        "ix_tags_absent": len(nonfrac) == 0,
    }

    # Index facts by local concept name
    by_name: dict[str, list] = {}
    for el in nonfrac:
        n = _attr(el, "name").split(":")[-1]
        by_name.setdefault(n, []).append(el)

    pe = datetime.fromisoformat(period_end).date() if period_end else None

    def ctx_matches(cid: str, kind: str) -> tuple[bool, str]:
        c = ctxs.get(cid)
        if not c:
            return False, "context-not-found"
        if c["dimensional"]:
            return False, "dimensional"
        if kind == "instant":
            if not c["instant"]:
                return False, "not-instant"
            d = datetime.fromisoformat(c["instant"]).date()
            return (abs((d - pe).days) <= 3, f"instant={c['instant']}")
        if not (c["start"] and c["end"]):
            return False, "not-duration"
        s = datetime.fromisoformat(c["start"]).date()
        e = datetime.fromisoformat(c["end"]).date()
        dur = (e - s).days
        ok = abs((e - pe).days) <= 3 and abs(dur - 365) <= 10
        return ok, f"{c['start']}..{c['end']} ({dur}d)"

    rows = []
    for concept, spec in CONCEPTS.items():
        found = None
        for alt in spec["names"]:
            for el in by_name.get(alt, []):
                cid = _attr(el, "contextRef")
                ok, why = ctx_matches(cid, spec["kind"])
                if not ok:
                    continue
                val, scale, sign = fact_value(el)
                if val is None:
                    continue
                found = {
                    "ticker": ticker, "fiscal_year": fy, "accession": accession,
                    "period_end": period_end, "filing_date": filing_date,
                    "concept_group": concept, "found": True,
                    "matched_concept": alt, "value_usd": val, "scale": scale,
                    "sign": sign, "unit_ref": _attr(el, "unitRef"),
                    "decimals": _attr(el, "decimals"), "context_id": cid,
                    "context_period": why, "raw_text": "".join(el.itertext()).strip()[:40],
                }
                break
            if found:
                break
        rows.append(found or {
            "ticker": ticker, "fiscal_year": fy, "accession": accession,
            "period_end": period_end, "filing_date": filing_date,
            "concept_group": concept, "found": False, "matched_concept": "",
            "value_usd": "", "scale": "", "sign": "", "unit_ref": "",
            "decimals": "", "context_id": "", "context_period": "",
            "raw_text": "",
        })
    return rows, stats


def main() -> None:
    index = json.loads((FILINGS / "index.json").read_text(encoding="utf-8"))
    # EX-13 filers: additional documents of the SAME submission that must be
    # pooled with the primary document (see analyse()).
    EXTRA_DOCS = {"WFC_2024": ["WFC_2024_EX13_d2.htm"]}

    all_rows, all_stats = [], []
    for item in index:
        key = f"{item['ticker']}_{item['fiscal_year_v1_rule']}"
        p = [REPO_ROOT / item["local_path"]] + [
            FILINGS / n for n in EXTRA_DOCS.get(key, []) if (FILINGS / n).exists()
        ]
        if len(p) == 1:
            p = p[0]
        print(f"parsing {item['ticker']} FY{item['fiscal_year_v1_rule']} ({item['size_bytes']/1e6:.1f} MB) ...")
        try:
            rows, stats = analyse(
                p, item["ticker"], item["fiscal_year_v1_rule"], item["accession_number"],
                item["period_end"], item["filing_date"],
            )
        except Exception as exc:
            print(f"  ! FAILED: {type(exc).__name__}: {exc}")
            all_stats.append({"ticker": item["ticker"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        n_found = sum(1 for r in rows if r["found"])
        print(f"  ix:nonFraction={stats['ix_nonFraction_count']:>6}  "
              f"contexts={stats['context_count']:>5}  "
              f"concepts found {n_found}/{len(CONCEPTS)}")
        all_rows.extend(rows)
        all_stats.append(stats)

    with (OUT / "ixbrl_coverage.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    (OUT / "ixbrl_tagstats.json").write_text(json.dumps(all_stats, indent=2), encoding="utf-8")

    # ── 2.4 coverage matrix ────────────────────────────────────────────────
    print(f"\n{'TICKER/FY':14s} " + " ".join(f"{c[:11]:11s}" for c in CONCEPTS))
    per_filing = {}
    for r in all_rows:
        per_filing.setdefault(f"{r['ticker']}_{r['fiscal_year']}", {})[r["concept_group"]] = r["found"]
    for key, d in per_filing.items():
        cells = " ".join(("     Y     " if d.get(c) else "     .     ") for c in CONCEPTS)
        share = sum(1 for c in CONCEPTS if d.get(c)) / len(CONCEPTS)
        print(f"{key:14s} {cells}  {share:.0%}")
    print("\nLegend: Y = consolidated (non-dimensional) fact found for the FY period; . = not found")


if __name__ == "__main__":
    main()
