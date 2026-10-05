#!/usr/bin/env python3
"""One command that says whether the demo will work: ``make demo-check``.

    python scripts/demo_check.py                       # start a server, check, stop it
    python scripts/demo_check.py --base-url URL        # check a server already running
    python scripts/demo_check.py --skip-smoke          # the demo queries only

It prints a PASS/FAIL table over three things and exits non-zero if any row
fails:

1. ``GET /health`` — the service answers, and **what state the LLM is in**.
   A demo whose narrative question will fail should say so before the
   interview, not during it.
2. Collections loaded — the text index is actually attached. A zero here is
   the single most likely reason a narrative question comes back empty.
3. The **eight demo queries from ``docs/DEMO.md``, run for real** through
   ``POST /query``. Seven of them take the facts path and call **no model at
   all**; only ``D8`` needs the LLM, and it is last so that everything that
   can be checked without quota is checked first.

Then it runs ``scripts/smoke.py`` against the same server, which is the
read-only service contract check (P1-11) and a different question from this
one: smoke asks "is the service wired up and honest", demo-check asks "will
this specific demo run".

A query row passes on an **expected status**, not on a correct number.
Accuracy is the evaluation's job (``docs/EVAL.md``); this is a pre-flight.

Budget (CLAUDE.md rule 7): one narrative generation here, one in smoke, both
served from the LLM cache on any re-run within the cache's lifetime.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

#: id, label, question, as_of, accepted statuses, needs an LLM.
#: These are exactly the eight in ``docs/DEMO.md`` and in the README, in the
#: order to demo them: facts first, narrative last.
DEMO_QUERIES: List[Tuple[str, str, str, Optional[str], Tuple[str, ...], bool]] = [
    ("D1", "numeric — a filer that reports in thousands",
     "What was Netflix's revenue in fiscal 2024?", None,
     ("answered",), False),
    ("D2", "numeric — a bank's own revenue line",
     "What was JPMorgan's total net revenue for fiscal 2024?", None,
     ("answered",), False),
    ("D3", "computed — deterministic calculator",
     "What was Apple's operating margin in fiscal 2024?", None,
     ("answered",), False),
    ("D4", "compare — two companies, each figure attributed",
     "Compare Apple and Microsoft revenue in fiscal 2024", None,
     ("answered",), False),
    ("D5", "point in time — the filing was not public yet",
     "What was Apple's revenue in fiscal 2024?", "2024-06-30",
     ("abstained",), False),
    ("D6", "abstention — no such filer",
     "What was SpaceX's revenue in fiscal 2024?", None,
     ("abstained", "clarification_needed"), False),
    ("D7", "abstention — out of scope, not investment advice",
     "Should I buy Apple stock?", None,
     ("abstained", "clarification_needed"), False),
    ("D8", "narrative — retrieval + the only model call in this table",
     "What risks does Apple disclose about its supply chain?", None,
     ("answered_text",), True),
]


class Row:
    def __init__(self, name: str, ok: bool, detail: str, seconds: float = 0.0) -> None:
        self.name, self.ok, self.detail, self.seconds = name, ok, detail, seconds


def _print_table(title: str, rows: Sequence[Row]) -> None:
    width = max([len(r.name) for r in rows] + [6])
    print(f"\n{title}")
    print(f"  {'check'.ljust(width)}  {'result':6s}  {'secs':>6s}  detail")
    print(f"  {'-' * width}  {'-' * 6}  {'-' * 6}  {'-' * 40}")
    for r in rows:
        print(f"  {r.name.ljust(width)}  {'PASS' if r.ok else 'FAIL':6s}  "
              f"{r.seconds:6.2f}  {r.detail}")


# ── the server ────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(base_url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=5).status_code == 200:
                return True
        except requests.RequestException:
            time.sleep(0.4)
    return False


class LocalServer:
    """uvicorn in a thread of this process.

    A thread rather than a subprocess on purpose: Qdrant in local mode takes
    an exclusive lock on ``data/qdrant``, so a second process holding it would
    make every query fail with a lock error that looks like a data problem.
    One process, one lock. If the owner already has ``make up`` running, pass
    ``--base-url`` instead and nothing here opens the store at all.
    """

    def __init__(self, port: int) -> None:
        self.port = port
        self._server = None
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> str:
        import uvicorn

        from api.app import app

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                log_level="error", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self.port}"

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)


# ── the checks ────────────────────────────────────────────────────────────────

def check_health(base_url: str) -> Tuple[List[Row], Dict]:
    rows: List[Row] = []
    started = time.time()
    try:
        response = requests.get(f"{base_url}/health", timeout=30)
        body = response.json()
    except Exception as exc:
        rows.append(Row("GET /health", False, f"{type(exc).__name__}: {exc}",
                        time.time() - started))
        return rows, {}

    elapsed = time.time() - started
    status = body.get("status")
    rows.append(Row("GET /health", response.status_code == 200 and status == "ok",
                    f"status={status} qdrant={body.get('qdrant_mode')}", elapsed))

    llm = body.get("llm") or {}
    llm_text = json.dumps(llm, sort_keys=True) if isinstance(llm, dict) else str(llm)
    # The LLM is reported, and a bad state fails the row, because D8 depends
    # on it. The seven facts questions do not, and are judged on their own.
    llm_ok = _llm_looks_usable(llm)
    rows.append(Row("LLM state", llm_ok, llm_text[:160]))

    loaded = int(body.get("collections_loaded") or 0)
    rows.append(Row("collections loaded", loaded > 0, f"{loaded} collections"))
    return rows, body


def _llm_looks_usable(llm) -> bool:
    """True unless /health says, in its own words, that no provider is usable."""
    if isinstance(llm, str):
        return llm.lower() not in {"unavailable", "down", "error", "not_configured"}
    if not isinstance(llm, dict):
        return False
    for key in ("status", "state"):
        value = str(llm.get(key, "")).lower()
        if value in {"ok", "up", "available", "ready"}:
            return True
        if value in {"unavailable", "down", "error", "not_configured", "no_key"}:
            return False
    if "available" in llm:
        return bool(llm["available"])
    if "providers" in llm:
        providers = llm["providers"]
        if isinstance(providers, dict):
            return any(bool(v) for v in providers.values())
        return bool(providers)
    return bool(llm)


def check_demo_queries(base_url: str, timeout: float) -> List[Row]:
    rows: List[Row] = []
    for qid, label, question, as_of, expected, needs_llm in DEMO_QUERIES:
        payload: Dict[str, object] = {"question": question}
        if as_of:
            payload["as_of"] = as_of
        started = time.time()
        try:
            response = requests.post(f"{base_url}/query", json=payload, timeout=timeout)
        except Exception as exc:
            rows.append(Row(f"{qid} {label}", False,
                            f"{type(exc).__name__}: {exc}", time.time() - started))
            continue
        elapsed = time.time() - started

        if response.status_code == 503:
            detail = (response.json().get("detail") or {})
            code = detail.get("error_code") if isinstance(detail, dict) else ""
            rows.append(Row(f"{qid} {label}", False,
                            f"503 dependency failure ({code})", elapsed))
            continue
        if response.status_code != 200:
            rows.append(Row(f"{qid} {label}", False,
                            f"HTTP {response.status_code}", elapsed))
            continue

        body = response.json()
        status = body.get("status")
        ok = status in expected
        note = f"status={status}"
        if status in {"abstained", "clarification_needed"} and body.get("abstain_reason"):
            note += f" reason={body['abstain_reason']}"
        if body.get("citations"):
            note += f" cites={len(body['citations'])}"
        if not ok:
            note += f" (expected {'/'.join(expected)})"
        if needs_llm:
            note += " [LLM]"
        rows.append(Row(f"{qid} {label}", ok, note, elapsed))
    return rows


def run_smoke(base_url: str) -> Row:
    started = time.time()
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "smoke.py"),
         "--base-url", base_url],
        capture_output=True, text=True, check=False,
    )
    elapsed = time.time() - started
    if completed.stdout.strip():
        print("\nscripts/smoke.py output")
        for line in completed.stdout.rstrip().splitlines():
            print(f"  | {line}")
    if completed.returncode != 0 and completed.stderr.strip():
        for line in completed.stderr.rstrip().splitlines()[-10:]:
            print(f"  ! {line}")
    return Row("scripts/smoke.py", completed.returncode == 0,
               f"exit {completed.returncode}", elapsed)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=None,
                    help="check a server that is already running instead of starting one")
    ap.add_argument("--skip-smoke", action="store_true")
    ap.add_argument("--timeout", type=float, default=90.0,
                    help="per-query HTTP timeout in seconds")
    args = ap.parse_args(argv)

    overall_started = time.time()
    if args.base_url:
        print(f"demo-check against {args.base_url} (started elsewhere)")
        return _run(args.base_url, args, overall_started)

    port = _free_port()
    print(f"demo-check: starting the API in-process on port {port}")
    with LocalServer(port) as base_url:
        if not _wait_for(base_url, timeout=120):
            print(f"\nFAIL  the API did not answer /health on {base_url}")
            return 1
        return _run(base_url, args, overall_started)


def _run(base_url: str, args, overall_started: float) -> int:
    rows, _ = check_health(base_url)
    _print_table("service", rows)

    query_rows = check_demo_queries(base_url, args.timeout)
    _print_table("demo queries (docs/DEMO.md)", query_rows)
    rows += query_rows

    if not args.skip_smoke:
        smoke_row = run_smoke(base_url)
        _print_table("service contract", [smoke_row])
        rows.append(smoke_row)

    failed = [r for r in rows if not r.ok]
    total = time.time() - overall_started
    print(f"\n{len(rows) - len(failed)}/{len(rows)} PASS in {total:.1f}s")
    if failed:
        print("FAILED: " + ", ".join(r.name for r in failed))
        return 1
    print("demo-check: PASS")
    return 0


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # os._exit, not SystemExit: Qdrant's local client and the embedding
    # model are torn down by the interpreter at shutdown and have been seen
    # to abort there ("recursive_mutex lock failed") *after* the table has
    # printed. The exit status is already decided; what that abort changes is
    # only whether the last thing on screen during a demo pre-flight is a
    # C++ crash message. Measured: exit status is unaffected either way.
    import os
    os._exit(_code)
