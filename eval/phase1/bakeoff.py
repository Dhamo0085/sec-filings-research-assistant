"""Provider bake-off (P1-04): pick the ordered model lists per role.

    python eval/phase1/bakeoff.py            # run (cached; safe to re-run)
    python eval/phase1/bakeoff.py --dry-run  # show the plan and call budget

Spec P1-04 caps this at 40 calls total and 5 calls on any 20-requests/day
model. Every call goes through llm/client.py, so repeats are served from the
disk cache and a re-run costs nothing.

Three things are measured, because they are the three ways a model can fail
this project:

* **JSON validity** — the router must return a parseable object. A model that
  wraps JSON in prose or runs out of tokens mid-object cannot route.
* **Citation compliance** — the generator must cite with ``[N]`` markers drawn
  only from the sources it was given. A model that invents ``[7]`` when three
  sources were supplied produces an untraceable answer, which breaks G1.
* **Honest refusal** — asked something the context does not contain, the model
  must say so rather than fill the gap. This is the behaviour the whole
  abstention design depends on.

Latency and token counts are recorded too, but they are tie-breakers: on a free
tier the binding limit is requests per day, not speed (spec Appendix A).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from llm.client import Entry, LLMClient, extract_json  # noqa: E402
from llm.errors import LLMBadOutput, LLMError  # noqa: E402

# Candidates. Gemma 4 IDs were discovered from the provider's models endpoint
# (P1-04); the spec's Appendix A listed them as "discover via the endpoint".
CANDIDATES: List[Entry] = [
    Entry("groq", "openai/gpt-oss-20b"),
    Entry("groq", "openai/gpt-oss-120b"),
    Entry("groq", "qwen/qwen3.8-27b"),
    Entry("gemini", "gemini-3.5-flash-lite"),
    Entry("gemini", "gemini-3.1-flash-lite"),
    Entry("gemini", "gemma-4-26b-a4b-it"),
]

# Models whose declared RPD is tiny get at most 5 calls (spec P1-04). None of
# the candidates above is in that class, but the guard stays so adding one
# cannot quietly blow the budget.
LOW_RPD_MODELS = {"gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                  "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash",
                  "gemini-2.5-flash-lite"}
LOW_RPD_CALL_CAP = 5
TOTAL_CALL_CAP = 40

ROUTER_SYSTEM = (
    "You classify questions about SEC 10-K filings. Reply with ONLY a JSON "
    "object with keys: query_type (one of single_doc, multi_doc, temporal, "
    "out_of_scope), tickers (list of uppercase tickers), years (list of "
    "integers), focus (a short string)."
)

GENERATOR_SYSTEM = (
    "You answer questions from SEC 10-K excerpts.\n"
    "RULES:\n"
    "1. Use ONLY the numbered SOURCES below.\n"
    "2. Cite every factual claim with [N], where N is a source number.\n"
    "3. Never cite a number that is not in the SOURCES list.\n"
    "4. If the answer is not in the sources, say so plainly and cite nothing."
)

SOURCES_BLOCK = (
    "SOURCES:\n"
    "[1] Apple Inc. FY2024, Consolidated Statements of Operations: "
    "Total net sales 391,035 (in millions).\n"
    "[2] Apple Inc. FY2024, Consolidated Statements of Operations: "
    "Operating income 123,216 (in millions).\n"
    "[3] Apple Inc. FY2024, Item 1 Business: The Company's reportable segments "
    "are Americas, Europe, Greater China, Japan and Rest of Asia Pacific.\n"
)

# Fixed prompt set. Small on purpose: Gemma 4's declared TPM is 16K.
PROMPTS: List[Dict] = [
    {
        "id": "router-simple",
        "role": "router",
        "kind": "json",
        "system": ROUTER_SYSTEM,
        "user": "What were Apple total net sales in fiscal year 2024?",
        "expect": {"tickers": ["AAPL"], "years": [2024]},
    },
    {
        "id": "router-compare",
        "role": "router",
        "kind": "json",
        "system": ROUTER_SYSTEM,
        "user": "Compare Microsoft and Alphabet research and development spending in 2024.",
        "expect": {"tickers_any": ["MSFT", "GOOGL"], "query_type": "multi_doc"},
    },
    {
        "id": "generator-cited",
        "role": "generator",
        "kind": "citation",
        "system": GENERATOR_SYSTEM,
        "user": (SOURCES_BLOCK + "\nQUESTION: What were Apple's total net sales "
                 "and operating income in fiscal 2024?"),
        "expect": {"must_cite": {1, 2}, "max_source": 3, "must_contain": ["391,035"]},
    },
    {
        "id": "generator-refusal",
        "role": "generator",
        "kind": "refusal",
        "system": GENERATOR_SYSTEM,
        "user": (SOURCES_BLOCK + "\nQUESTION: What was Apple's dividend per share "
                 "in fiscal 2024?"),
        "expect": {"should_refuse": True},
    },
]

# Citation markers. ASCII [N] is what the prompt asks for and the only style
# generation/citations.py recognises. The Groq gpt-oss models answer with
# FULLWIDTH brackets (U+3010/U+3011) instead, so a scorer - or a pipeline -
# that only looks for [N] sees zero citations in a correctly-cited answer.
# Both styles are detected and the style is reported, because "cites correctly
# but in a format the pipeline cannot read" is a different failure from
# "does not cite".
_MARKER_ASCII_RE = re.compile(r"\[(\d+)\]")
_MARKER_FULLWIDTH_RE = re.compile("\u3010(\\d+)\u3011")


def _normalise_apostrophes(text: str) -> str:
    """Fold typographic apostrophes to ASCII.

    Models write "isn\u2019t" with U+2019, so an ASCII-only pattern misses the
    contraction - the third time in this project a hand-written text matcher
    under-matched real model output.
    """
    return (text or "").replace("\u2019", "'").replace("\u02bc", "'")

# Refusal detection. The first version of this list missed
# "that information isn't included in the provided sources" - a textbook
# refusal - because it listed "doesn't" and "can't" but not "isn't". That is
# precisely the K11 defect this project is fixing (a hand-written refusal regex
# with poor recall), reproduced in its own measuring instrument. Matching on
# negated auxiliaries plus a "not"-stem covers the contraction families instead
# of enumerating phrasings.
_NEGATION_RE = re.compile(
    r"\b(?:do(?:es)?\s+not|did\s+not|is\s+not|are\s+not|was\s+not|were\s+not|"
    r"cannot|could\s+not|"
    r"don'?t|doesn'?t|didn'?t|isn'?t|aren'?t|wasn'?t|weren'?t|can'?t|couldn'?t|"
    r"unable|no\s+information|unavailable|not\s+\w+)\b",
    re.IGNORECASE,
)

# A four-digit year is not a financial claim. Counting it as one is the same
# mistake the Phase 0 scorer made when it read the "10" in "10-K" as a number.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def score_json(data: Optional[Dict], expect: Dict) -> Dict:
    if data is None:
        return {"json_valid": False, "fields_ok": False}
    tickers = [str(t).upper() for t in (data.get("tickers") or [])]
    years = []
    for y in data.get("years") or []:
        try:
            years.append(int(y))
        except (TypeError, ValueError):
            pass
    ok = True
    if "tickers" in expect:
        ok = ok and tickers == [t.upper() for t in expect["tickers"]]
    if "tickers_any" in expect:
        ok = ok and all(t in tickers for t in expect["tickers_any"])
    if "years" in expect:
        ok = ok and years == expect["years"]
    if "query_type" in expect:
        ok = ok and data.get("query_type") == expect["query_type"]
    return {"json_valid": True, "fields_ok": bool(ok),
            "tickers": tickers, "years": years,
            "query_type": data.get("query_type")}


def score_citation(text: str, expect: Dict) -> Dict:
    ascii_cited = {int(m) for m in _MARKER_ASCII_RE.findall(text or "")}
    wide_cited = {int(m) for m in _MARKER_FULLWIDTH_RE.findall(text or "")}
    cited = ascii_cited | wide_cited
    if ascii_cited and wide_cited:
        style = "mixed"
    elif wide_cited:
        style = "fullwidth"
    elif ascii_cited:
        style = "ascii"
    else:
        style = "none"
    max_source = expect["max_source"]
    invented = sorted(n for n in cited if n < 1 or n > max_source)
    required = expect.get("must_cite", set())
    contains = all(s in (text or "") for s in expect.get("must_contain", []))
    cites_ok = required.issubset(cited) and not invented
    return {
        "cited": sorted(cited),
        "marker_style": style,
        "cites_required": required.issubset(cited),
        "invented_citations": invented,
        "contains_expected_value": contains,
        "cites_correctly": bool(cites_ok and contains),
        # Compliant means USABLE by this pipeline, which parses ASCII [N].
        # A fullwidth-bracket answer is right in substance but unreadable
        # downstream, so it is reported separately rather than passed.
        "compliant": bool(cites_ok and contains and style == "ascii"),
    }


def score_refusal(text: str) -> Dict:
    """Did the model decline, without smuggling a figure in anyway?"""
    refused = bool(_NEGATION_RE.search(_normalise_apostrophes(text)))
    # Years are not financial claims; a refusal may legitimately repeat the
    # fiscal year it was asked about.
    numbers = [n for n in re.findall(r"\d[\d,]{2,}", text or "")
               if not _YEAR_RE.match(n)]
    return {"refused": refused, "numbers_in_refusal": numbers[:4],
            "compliant": bool(refused and not numbers)}


def run(client: LLMClient, *, dry_run: bool = False) -> Dict:
    planned = len(CANDIDATES) * len(PROMPTS)
    print(f"bake-off plan: {len(CANDIDATES)} models x {len(PROMPTS)} prompts "
          f"= {planned} calls (cap {TOTAL_CALL_CAP})")
    if planned > TOTAL_CALL_CAP:
        raise SystemExit(f"plan exceeds the {TOTAL_CALL_CAP}-call cap")
    for entry in CANDIDATES:
        if entry.model in LOW_RPD_MODELS and len(PROMPTS) > LOW_RPD_CALL_CAP:
            raise SystemExit(f"{entry.key} is a low-RPD model; "
                             f"cap is {LOW_RPD_CALL_CAP} calls")
    if dry_run:
        for entry in CANDIDATES:
            print(f"  {entry.key}")
        return {}

    rows: List[Dict] = []
    for entry in CANDIDATES:
        print(f"\n=== {entry.key} ===")
        for prompt in PROMPTS:
            row = {"provider": entry.provider, "model": entry.model,
                   "prompt_id": prompt["id"], "kind": prompt["kind"]}
            messages = [{"role": "system", "content": prompt["system"]},
                        {"role": "user", "content": prompt["user"]}]
            started = time.perf_counter()
            try:
                completion = client.complete(
                    role=prompt["role"], messages=messages, temperature=0.0,
                    max_tokens=900, json_object=(prompt["kind"] == "json"),
                    prompt_version=f"bakeoff-{prompt['id']}",
                    order=[entry],
                )
                row.update({
                    "ok": True, "cached": completion.cached,
                    "latency_s": round(completion.latency_s or
                                       (time.perf_counter() - started), 3),
                    "prompt_tokens": completion.prompt_tokens,
                    "completion_tokens": completion.completion_tokens,
                    "content_chars": len(completion.content),
                })
                if prompt["kind"] == "json":
                    try:
                        data = extract_json(completion.content)
                    except LLMBadOutput:
                        data = None
                    row.update(score_json(data, prompt["expect"]))
                    row["compliant"] = bool(row.get("json_valid") and row.get("fields_ok"))
                elif prompt["kind"] == "citation":
                    row.update(score_citation(completion.content, prompt["expect"]))
                else:
                    row.update(score_refusal(completion.content))
            except LLMError as exc:
                row.update({"ok": False, "compliant": False,
                            "error": f"{type(exc).__name__}: {exc}"})
            except Exception as exc:          # recorded, keep going
                row.update({"ok": False, "compliant": False,
                            "error": f"{type(exc).__name__}: {exc}"})
            status = "ok " if row.get("ok") else "ERR"
            mark = "PASS" if row.get("compliant") else "fail"
            extra = row.get("error", "")
            print(f"  {prompt['id']:20s} {status} {mark} "
                  f"{row.get('latency_s', 0):6.2f}s "
                  f"{'(cached)' if row.get('cached') else ''} {extra[:80]}")
            rows.append(row)

    return summarise(rows, client)


def summarise(rows: List[Dict], client: LLMClient) -> Dict:
    by_model: Dict[str, List[Dict]] = {}
    for r in rows:
        by_model.setdefault(f"{r['provider']}:{r['model']}", []).append(r)

    summary = []
    for key, items in by_model.items():
        jsons = [i for i in items if i["kind"] == "json"]
        cites = [i for i in items if i["kind"] == "citation"]
        refus = [i for i in items if i["kind"] == "refusal"]
        lat = [i["latency_s"] for i in items if i.get("latency_s")]
        summary.append({
            "entry": key,
            "calls": len(items),
            "errors": sum(1 for i in items if not i.get("ok")),
            "json_valid": sum(1 for i in jsons if i.get("json_valid")),
            "json_total": len(jsons),
            "json_fields_ok": sum(1 for i in jsons if i.get("fields_ok")),
            "citation_compliant": sum(1 for i in cites if i.get("compliant")),
            "citation_total": len(cites),
            "invented_citations": sum(len(i.get("invented_citations") or [])
                                      for i in cites),
            "cites_correctly": sum(1 for i in cites if i.get("cites_correctly")),
            "marker_styles": sorted({i.get("marker_style") for i in cites
                                     if i.get("marker_style")}),
            "refusal_compliant": sum(1 for i in refus if i.get("compliant")),
            "refusal_total": len(refus),
            "median_latency_s": round(statistics.median(lat), 2) if lat else None,
            "total_tokens": sum((i.get("prompt_tokens") or 0) +
                                (i.get("completion_tokens") or 0) for i in items),
        })
    summary.sort(key=lambda s: (
        -(s["json_fields_ok"] + s["citation_compliant"] + s["refusal_compliant"]),
        s["errors"], s["median_latency_s"] or 1e9,
    ))

    print(f"\n{'ENTRY':32s} {'ERR':>3s} {'JSON':>6s} {'CITE_OK':>8s} "
          f"{'USABLE':>7s} {'STYLE':>11s} {'REFUSE':>7s} {'MED_S':>7s}")
    for s in summary:
        print(f"{s['entry']:32s} {s['errors']:3d} "
              f"{s['json_fields_ok']}/{s['json_total']:<4} "
              f"{s['cites_correctly']}/{s['citation_total']:<6} "
              f"{s['citation_compliant']}/{s['citation_total']:<5} "
              f"{','.join(s['marker_styles']) or '-':>11s} "
              f"{s['refusal_compliant']}/{s['refusal_total']:<5} "
              f"{s['median_latency_s']!s:>7s}")

    payload = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "call_cap": TOTAL_CALL_CAP,
        "calls_made": len(rows),
        "prompts": [{"id": p["id"], "role": p["role"], "kind": p["kind"]}
                    for p in PROMPTS],
        "summary": summary,
        "rows": rows,
        "budget_snapshot": client.usage_snapshot(),
    }
    out = REPO_ROOT / "reports" / "phase1" / "bakeoff.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    client = LLMClient()
    try:
        run(client, dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"bake-off failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
