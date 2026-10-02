"""
Phase 0 / Step 3.3 + 3.4 — deterministic scoring. NO LLM judge.

    python eval/phase0/score_baseline.py [--results reports/phase0/baseline_results.jsonl]

Scorers
  numeric / computed / units : parse every number out of the answer, normalise
      to USD millions (or percent), PASS within 0.5%. Separately flags
      SCALE_ERROR (off by exactly 10^3/10^6/10^9) and WRONG_YEAR.
  compare / trend : PASS only if every expected value is present AND attributed
      to the right company/year in the same sentence or table row.
  unanswerable : PASS if an abstention marker is present AND no numeric claim.
  lookahead : VIOLATION if any cited (ticker, fiscal_year) has filing_date > as_of,
      resolved through reports/phase0/filing_manifest.json.
  narrative : not auto-scored; reports cited sections vs expected_section_ids.
  3.4 retrieval diagnostic : was the expected number present in ANY retrieved
      chunk, and at what rank — separates "retrieval missed it" from "the LLM
      misread what it was given".
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "reports" / "phase0"
QUESTIONS = REPO_ROOT / "eval" / "phase0" / "baseline_questions.jsonl"

TOL = 0.005   # 0.5%

ABSTENTION_MARKERS = [
    "cannot", "can't", "unable", "not available", "no filings", "outside the scope",
    "don't have", "do not have", "not found", "no information", "not provided",
    "not disclosed", "doesn't appear", "does not appear", "couldn't find",
    "could not find", "i don't", "unavailable", "not able to",
]

_MULT = {
    "trillion": 1e6, "t": 1e6,
    "billion": 1e3, "b": 1e3, "bn": 1e3,
    "million": 1.0, "m": 1.0, "mm": 1.0,
    "thousand": 1e-3, "k": 1e-3,
}

# $391,035 million | $391.0 billion | 391,035 | 45,183,036 thousand | 31.5%
_NUM = re.compile(
    r"(?P<neg>\()?\$?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>trillion|billion|million|thousand|bn|mm|[btmk])?\b\)?(?P<pct>\s*%)?",
    re.IGNORECASE,
)


# Things that look like numbers but are not financial quantities:
# SEC form types (10-K, 10-Q, 8-K), item/section references (Item 1A, Item 7A),
# and ASC/IFRS standard numbers.
_NOT_A_QUANTITY = re.compile(
    r"\b10-[KQ]\b|\b8-K\b|\bitem\s+\d+[a-c]?\b|\bsection\s+\d+\b|"
    r"\basc\s+\d+\b|\bifrs\s+\d+\b|\bnote\s+\d+\b",
    re.IGNORECASE,
)


def numbers_in(text: str) -> list[dict]:
    text = text or ""
    # Blank out non-quantity spans (keeping offsets stable) before scanning.
    masked = list(text)
    for mt in _NOT_A_QUANTITY.finditer(text):
        for i in range(mt.start(), mt.end()):
            masked[i] = " "
    text = "".join(masked)

    out = []
    for mt in _NUM.finditer(text):
        raw = mt.group("num").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        unit = (mt.group("unit") or "").lower()
        is_pct = bool(mt.group("pct"))
        # A bare comma-grouped number in a 10-K answer is conventionally millions.
        mult = _MULT.get(unit, 1.0)
        out.append({
            "raw": mt.group(0).strip(),
            "value_millions": None if is_pct else v * mult,
            "percent": v if is_pct else None,
            "had_unit_word": bool(unit),
            "pos": mt.start(),
        })
    return out


def _close(a: float, b: float, tol: float = TOL) -> bool:
    return b != 0 and abs(a - b) / abs(b) <= tol


def score_numeric(answer: str, expected: float, kind: str = "millions") -> dict:
    nums = numbers_in(answer)
    cands = [n["percent"] for n in nums if n["percent"] is not None] if kind == "percent" \
        else [n["value_millions"] for n in nums if n["value_millions"] is not None]
    if not cands:
        return {"verdict": "FAIL", "failure_stage": "no_number_in_answer",
                "expected": expected, "found": []}
    if any(_close(c, expected) for c in cands):
        return {"verdict": "PASS", "expected": expected, "found": cands[:8]}
    for k, lbl in ((1e3, "x1000"), (1e-3, "/1000"), (1e6, "x1e6"), (1e-6, "/1e6")):
        if any(_close(c, expected * k) for c in cands):
            return {"verdict": "FAIL", "failure_stage": f"scale_error({lbl})",
                    "expected": expected, "found": cands[:8]}
    return {"verdict": "FAIL", "failure_stage": "wrong_value",
            "expected": expected, "found": cands[:8]}


# Answers say "Microsoft", not "MSFT", so attribution must accept both the
# ticker and the company's name (and its distinctive first word).
def _aliases(key: str) -> list[str]:
    out = [str(key)]
    if re.fullmatch(r"(19|20)\d{2}", str(key)):      # trend keys are years
        return out
    try:
        from config import TICKER_TO_COMPANY
        name = TICKER_TO_COMPANY.get(str(key), {}).get("name", "")
    except Exception:
        name = ""
    extra = {
        "GOOGL": ["Alphabet", "Google"], "AAPL": ["Apple"], "MSFT": ["Microsoft"],
        "AMZN": ["Amazon"], "JPM": ["JPMorgan", "JP Morgan"], "BAC": ["Bank of America"],
        "WFC": ["Wells Fargo"], "GS": ["Goldman Sachs"], "BLK": ["BlackRock"],
        "STT": ["State Street"], "TROW": ["T. Rowe Price", "T Rowe Price"],
        "IVZ": ["Invesco"], "NFLX": ["Netflix"],
    }.get(str(key), [])
    if name:
        out.append(name)
        out.append(re.split(r"[\s,]", name)[0])
    out.extend(extra)
    return sorted({a for a in out if a}, key=len, reverse=True)


def score_multi(answer: str, expected: dict) -> dict:
    """compare/trend: each expected value must appear AND be attributed to its own key."""
    per_key, lines = {}, re.split(r"[\n.;|]", answer or "")
    for key, want in expected.items():
        attributed = False
        present = False
        pattern = "|".join(re.escape(a) for a in _aliases(key))
        for ln in lines:
            vals = [n["value_millions"] for n in numbers_in(ln) if n["value_millions"] is not None]
            if any(_close(v, float(want)) for v in vals):
                present = True
                if re.search(pattern, ln, re.IGNORECASE):
                    attributed = True
                    break
        per_key[key] = {"expected": want, "present_anywhere": present,
                        "attributed": attributed, "aliases_tried": _aliases(key)}
    ok = all(v["attributed"] for v in per_key.values())
    any_present = all(v["present_anywhere"] for v in per_key.values())
    return {
        "verdict": "PASS" if ok else "FAIL",
        "failure_stage": None if ok else ("attribution_unclear" if any_present else "missing_value"),
        "per_key": per_key,
        "manual_review": not ok and any_present,
    }


def score_abstention(answer: str) -> dict:
    a = (answer or "").lower()
    marker = next((m for m in ABSTENTION_MARKERS if m in a), None)
    nums = [n for n in numbers_in(answer) if n["value_millions"] is not None]
    # years mentioned in prose are not numeric claims
    claims = [n for n in nums if not re.fullmatch(r"(19|20)\d{2}", n["raw"].strip("$ "))]
    ok = marker is not None and not claims
    return {"verdict": "PASS" if ok else "FAIL",
            "failure_stage": None if ok else ("no_abstention_marker" if not marker else "numeric_claim_present"),
            "abstention_marker": marker,
            "numeric_claims": [c["raw"] for c in claims][:5],
            "manual_review": True}


def score_lookahead(citations: list, as_of: str, manifest: dict) -> dict:
    by = {}
    for r in manifest["all_10k_filings"]:
        by.setdefault((r["ticker"], r["fiscal_year_v1_rule"]), r)
    viol, checked = [], []
    for c in citations or []:
        key = (c.get("ticker"), c.get("fiscal_year"))
        row = by.get(key)
        if not row:
            checked.append({**dict(zip(("ticker", "fiscal_year"), key)), "filing_date": None,
                            "status": "not_in_manifest"})
            continue
        bad = row["filing_date"] > as_of
        checked.append({"ticker": key[0], "fiscal_year": key[1],
                        "filing_date": row["filing_date"],
                        "status": "VIOLATION" if bad else "ok"})
        if bad:
            viol.append(checked[-1])
    return {"verdict": "VIOLATION" if viol else ("PASS" if checked else "NO_CITATIONS"),
            "as_of": as_of, "violations": viol, "cited": checked}


def retrieval_diagnostic(rec: dict, expected: float | None) -> dict:
    """Step 3.4 — was the expected number anywhere in the retrieved text?"""
    if expected is None:
        return {"applicable": False}
    forms = {f"{expected:,.0f}", f"{expected:.0f}",
             f"{expected/1000:,.1f}", f"{expected/1000:.1f}",
             f"{expected:,.0f}".replace(",", "")}
    rank, hits = None, []
    idx = 0
    for call in rec.get("trace", {}).get("retrieval_calls", []) or []:
        for ch in call.get("chunks", []) or []:
            idx += 1
            txt = ch.get("text", "") or ""
            for f in forms:
                if f and f in txt:
                    hits.append({"rank": idx, "ticker": ch.get("ticker"),
                                 "fiscal_year": ch.get("fiscal_year"),
                                 "section": ch.get("section"), "form_matched": f})
                    rank = rank or idx
                    break
    return {"applicable": True, "expected_forms": sorted(forms),
            "found_in_retrieved_chunks": bool(hits), "first_hit_rank": rank,
            "n_chunks_examined": idx, "hits": hits[:5],
            "diagnosis": ("retrieval_missed" if not hits else "retrieval_found_llm_misread")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(OUT / "baseline_results.jsonl"))
    args = ap.parse_args()

    res_path = Path(args.results)
    if not res_path.exists():
        print(f"No results file at {res_path} — Step 3.2 has not been run.")
        return 1

    questions = {json.loads(l)["id"]: json.loads(l)
                 for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()}
    manifest = json.loads((OUT / "filing_manifest.json").read_text(encoding="utf-8"))
    results = [json.loads(l) for l in res_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    scored = []
    for rec in results:
        q = questions.get(rec["id"], {})
        cat, ans = q.get("category"), rec.get("answer", "")
        row = {"id": rec["id"], "category": cat, "status": rec.get("status"),
               "query_type": rec.get("query_type"),
               "latency_s": rec.get("total_latency_s"),
               "answer_excerpt": (ans or "")[:300]}

        if rec.get("status") != "ok":
            row.update({"verdict": "ERROR", "failure_stage": rec.get("error_type")})
        elif cat in ("numeric", "units"):
            row.update(score_numeric(ans, q["expected_value_usd_millions"]))
        elif cat == "computed":
            row.update(score_numeric(ans, q["expected_value_percent"], kind="percent"))
        elif cat in ("compare", "trend"):
            row.update(score_multi(ans, q["expected_values"]))
        elif cat == "unanswerable":
            row.update(score_abstention(ans))
        elif cat == "lookahead":
            row.update(score_lookahead(rec.get("citations"), q["as_of"], manifest))
            ab = score_abstention(ans)
            row["abstained"] = ab["verdict"] == "PASS"
        elif cat == "narrative":
            cited = sorted({c.get("section") for c in (rec.get("citations") or []) if c.get("section")})
            want = set(q.get("expected_section_ids", []))
            row.update({"verdict": "MANUAL", "cited_sections": cited,
                        "expected_section_ids": sorted(want),
                        "any_expected_section_cited": bool(want & set(cited))})
        elif cat == "ambiguity":
            yrs = sorted({c.get("fiscal_year") for c in (rec.get("citations") or []) if c.get("fiscal_year")})
            row.update({"verdict": "MANUAL", "fiscal_years_cited": yrs,
                        "answer_names_the_year": bool(re.search(r"(fiscal\s*)?(year\s*)?20\d{2}", ans or ""))})

        exp = q.get("expected_value_usd_millions")
        row["retrieval_diagnostic"] = retrieval_diagnostic(rec, exp)
        scored.append(row)

    # Write next to the results file, not to a fixed reports/phase0/ path.
    # The fixed default silently overwrote the committed Phase 0 scores when
    # this scorer was re-used for the Phase 1 baseline.
    scores_path = res_path.with_name("baseline_scores.json")
    scores_path.write_text(json.dumps(scored, indent=2), encoding="utf-8")

    from collections import Counter, defaultdict
    by_cat = defaultdict(Counter)
    for r in scored:
        by_cat[r["category"]][r["verdict"]] += 1
    print(f"{'CATEGORY':14s} {'PASS':>5s} {'FAIL':>5s} {'OTHER':>6s}")
    for cat, c in sorted(by_cat.items()):
        other = sum(v for k, v in c.items() if k not in ("PASS", "FAIL"))
        print(f"{str(cat):14s} {c['PASS']:5d} {c['FAIL']:5d} {other:6d}")
    print(f"\nScored {len(scored)} of {len(questions)} questions; "
          f"{len(questions) - len(scored)} not_run.")
    print(f"wrote {scores_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
