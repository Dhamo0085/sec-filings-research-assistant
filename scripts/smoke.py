#!/usr/bin/env python3
"""Read-only smoke check against a running instance (P1-11).

    python scripts/smoke.py --base-url http://localhost:8000

Checks GET /health and GET /collections, then asks at most 8 questions via
POST /query, asserting the HTTP status, the response shape and a latency
ceiling. Prints a pass/fail table and exits non-zero on any failure.

Deliberate limits, from CLAUDE.md rule 8:

* **Read-only.** The only non-GET call is POST /query, which answers questions
  and mutates nothing.
* **Never** /admin/* or /ingest against a hosted instance. Those are not even
  reachable from this script.
* At most 8 /query calls, because a hosted instance shares the owner's
  free-tier LLM quota with everything else.

A question that legitimately abstains or asks for clarification still passes:
this checks that the service is wired up and honest, not that it is correct —
accuracy is the Phase 4 evaluation's job.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests

# Eight questions spanning the shapes the system must handle. Chosen so a
# correct system produces a mix of answers and honest refusals.
SMOKE_QUESTIONS: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("numeric", "What were Apple total net sales in fiscal year 2024?",
     ("answered", "answered_text")),
    ("numeric", "What were Microsoft research and development expenses in fiscal year 2024?",
     ("answered", "answered_text")),
    ("computed", "What was Apple operating margin in fiscal year 2024?",
     ("answered", "answered_text")),
    ("compare", "Compare Microsoft and Alphabet research and development spending in 2024.",
     ("answered", "answered_text")),
    ("narrative", "What are the main risk factors JPMorgan Chase disclosed in its fiscal 2024 10-K?",
     ("answered", "answered_text")),
    ("unanswerable", "What was Acme Quantum Holdings' revenue in fiscal year 2024?",
     ("abstained", "clarification_needed")),
    ("unanswerable", "What will Apple's revenue be in fiscal year 2027?",
     ("abstained", "answered_text")),
    ("ambiguity", "What is the latest annual revenue for Apple?",
     ("answered", "answered_text", "clarification_needed")),
]

MAX_QUERIES = 8


class Result:
    def __init__(self, name: str, ok: bool, detail: str, seconds: float = 0.0) -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.seconds = seconds


def _get(base: str, path: str, timeout: float) -> Tuple[int, Any, float]:
    started = time.perf_counter()
    resp = requests.get(urljoin(base, path), timeout=timeout)
    elapsed = time.perf_counter() - started
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:200]
    return resp.status_code, body, elapsed


def check_health(base: str, timeout: float) -> Tuple[Result, Optional[Dict]]:
    try:
        status, body, secs = _get(base, "/health", timeout)
    except requests.RequestException as exc:
        return Result("GET /health", False, f"{type(exc).__name__}: {exc}"), None
    if status != 200:
        return Result("GET /health", False, f"HTTP {status}", secs), None
    if not isinstance(body, dict) or "status" not in body:
        return Result("GET /health", False, "unexpected body", secs), None
    llm = body.get("llm", "not reported")
    detail = (f"status={body['status']} llm={llm} "
              f"collections={body.get('collections_loaded', '?')}")
    # A degraded store or a dead model is reported, not hidden, but it is not a
    # smoke failure by itself — the /query checks below will show the effect.
    return Result("GET /health", True, detail, secs), body


def check_collections(base: str, timeout: float) -> Result:
    try:
        status, body, secs = _get(base, "/collections", timeout)
    except requests.RequestException as exc:
        return Result("GET /collections", False, f"{type(exc).__name__}: {exc}")
    if status != 200:
        return Result("GET /collections", False, f"HTTP {status}", secs)
    cols = (body or {}).get("collections") if isinstance(body, dict) else None
    if not isinstance(cols, list):
        return Result("GET /collections", False, "no collections list", secs)
    return Result("GET /collections", True, f"{len(cols)} collection(s)", secs)


def check_query(
    base: str, category: str, question: str,
    acceptable: Tuple[str, ...], timeout: float, max_latency: float,
) -> Result:
    name = f"POST /query [{category}]"
    started = time.perf_counter()
    try:
        resp = requests.post(
            urljoin(base, "/query"), json={"question": question}, timeout=timeout,
        )
    except requests.RequestException as exc:
        return Result(name, False, f"{type(exc).__name__}: {exc}")
    secs = time.perf_counter() - started

    if resp.status_code == 503:
        try:
            detail = resp.json().get("detail", {})
        except ValueError:
            detail = {}
        code = detail.get("error_code", "unknown") if isinstance(detail, dict) else "unknown"
        # A dependency failure is a real smoke failure, but report WHICH one —
        # that is the whole point of the P1-05 error codes.
        return Result(name, False, f"503 dependency failure: {code}", secs)
    if resp.status_code != 200:
        return Result(name, False, f"HTTP {resp.status_code}: {resp.text[:120]}", secs)

    try:
        body = resp.json()
    except ValueError:
        return Result(name, False, "response was not JSON", secs)

    for field in ("query", "answer", "query_type", "citations"):
        if field not in body:
            return Result(name, False, f"missing field '{field}'", secs)

    status = body.get("status", "answered_text")
    if status not in acceptable:
        return Result(
            name, False,
            f"status={status}, expected one of {list(acceptable)}", secs,
        )
    if secs > max_latency:
        return Result(name, False, f"{secs:.1f}s exceeds the {max_latency:.0f}s ceiling", secs)

    answer = body.get("answer") or ""
    if not answer.strip():
        return Result(name, False, "empty answer", secs)
    return Result(
        name, True,
        f"status={status} type={body['query_type']} "
        f"citations={len(body.get('citations') or [])}",
        secs,
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", required=True,
                    help="e.g. http://localhost:8000")
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="per-request timeout in seconds (default 180)")
    ap.add_argument("--max-latency", type=float, default=90.0,
                    help="per-query latency ceiling in seconds (default 90)")
    ap.add_argument("--queries", type=int, default=MAX_QUERIES,
                    help=f"how many /query calls to make (max {MAX_QUERIES})")
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="also print machine-readable results")
    args = ap.parse_args(argv)

    if args.queries > MAX_QUERIES:
        print(f"refusing to make more than {MAX_QUERIES} /query calls "
              f"(CLAUDE.md rule 8)", file=sys.stderr)
        return 2

    base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
    print(f"smoke check against {base}")
    print(f"  read-only; at most {args.queries} POST /query calls; "
          f"no /admin or /ingest calls are made\n")

    results: List[Result] = []
    health, _body = check_health(base, args.timeout)
    results.append(health)
    results.append(check_collections(base, args.timeout))

    for category, question, acceptable in SMOKE_QUESTIONS[: args.queries]:
        results.append(check_query(
            base, category, question, acceptable, args.timeout, args.max_latency,
        ))

    width = max(len(r.name) for r in results)
    print(f"{'CHECK'.ljust(width)}  {'RESULT':6s}  {'SECS':>6s}  DETAIL")
    for r in results:
        print(f"{r.name.ljust(width)}  {'PASS' if r.ok else 'FAIL':6s}  "
              f"{r.seconds:6.2f}  {r.detail}")

    failed = [r for r in results if not r.ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if args.as_json:
        print(json.dumps(
            [{"check": r.name, "ok": r.ok, "seconds": round(r.seconds, 3),
              "detail": r.detail} for r in results],
            indent=2,
        ))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
