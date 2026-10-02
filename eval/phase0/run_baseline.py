"""
Phase 0 / Step 3.2 — baseline run of the CURRENT pipeline.

Wraps (never edits) the pipeline's own functions to record classification,
collections searched, retrieved chunks, citations, per-stage latency and every
Groq call's token usage.  Resumable; saves after every question.

    python eval/phase0/run_baseline.py --resume
    python eval/phase0/run_baseline.py --only N1,N2,C1

Requires a working install (qdrant/fastembed/groq) AND an indexed corpus.
If either is missing it exits non-zero with a clear message rather than
producing misleading output.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

QUESTIONS = REPO_ROOT / "eval" / "phase0" / "baseline_questions.jsonl"
RESULTS = REPO_ROOT / "reports" / "phase0" / "baseline_results.jsonl"
LLM_CACHE = REPO_ROOT / ".cache" / "llm_phase0.jsonl"

# Step 3.2 priority order — if the Groq budget runs out, the earlier ones matter most.
PRIORITY = ["N1", "N2", "N3", "C1", "C2", "M3", "T3", "T4", "U1", "U3", "R1", "T1"]

TRACE: dict = {}


# ── LLM disk cache ──────────────────────────────────────────────────────────
def _cache_key(model: str, messages: list) -> str:
    blob = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _cache_load() -> dict:
    if not LLM_CACHE.exists():
        return {}
    out = {}
    for line in LLM_CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rec = json.loads(line)
                out[rec["key"]] = rec
            except json.JSONDecodeError:
                continue
    return out


def _cache_append(rec: dict) -> None:
    LLM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with LLM_CACHE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def install_instrumentation():
    """Monkeypatch pipeline functions at runtime. No source file is modified."""
    import query as Q
    import generation.generator as G

    cache = _cache_load()

    def timed(name, fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            finally:
                TRACE.setdefault("stage_latency_s", {}).setdefault(name, []).append(
                    round(time.perf_counter() - t0, 3))
        return wrapper

    # -- classification -------------------------------------------------------
    orig_classify = Q.classify_and_ensure

    @functools.wraps(orig_classify)
    def classify_wrapper(q):
        t0 = time.perf_counter()
        c = orig_classify(q)
        TRACE.setdefault("stage_latency_s", {}).setdefault("classify", []).append(
            round(time.perf_counter() - t0, 3))
        TRACE["classification"] = {
            "query_type": getattr(c, "query_type", None),
            "tickers": list(getattr(c, "tickers", []) or []),
            "years": list(getattr(c, "years", []) or []),
            "focus": getattr(c, "focus", None),
            "reasoning": getattr(c, "reasoning", None),
            "failed_lookups": list(getattr(c, "failed_lookups", []) or []),
            "ingest_failed": list(getattr(c, "ingest_failed", []) or []),
            "year_not_available": list(getattr(c, "year_not_available", []) or []),
        }
        return c

    Q.classify_and_ensure = classify_wrapper

    # -- retrieval ------------------------------------------------------------
    orig_retrieve = Q.retrieve

    @functools.wraps(orig_retrieve)
    def retrieve_wrapper(*a, **kw):
        t0 = time.perf_counter()
        out = orig_retrieve(*a, **kw)
        TRACE.setdefault("stage_latency_s", {}).setdefault("retrieve", []).append(
            round(time.perf_counter() - t0, 3))
        TRACE.setdefault("retrieval_calls", []).append({
            "tickers": kw.get("tickers", a[1] if len(a) > 1 else None),
            "years": kw.get("years", a[2] if len(a) > 2 else None),
            "top_k": kw.get("top_k"), "focus": kw.get("focus"),
            "chunks": [{
                "chunk_id": r.chunk.chunk_id, "ticker": r.chunk.ticker,
                "fiscal_year": r.chunk.fiscal_year, "section": r.chunk.section_name,
                "parent_id": r.chunk.parent_id, "chunk_type": r.chunk.chunk_type,
                "score": round(float(r.score), 4), "text": r.chunk.text,
            } for r in out],
        })
        return out

    Q.retrieve = retrieve_wrapper
    try:
        import generation.synthesizer as S
        S.retrieve = retrieve_wrapper
        S.generate_answer = timed("generate", S.generate_answer)
        Q.synthesize = timed("synthesize", Q.synthesize)
    except Exception:
        pass
    Q.generate_answer = timed("generate", Q.generate_answer)

    # -- collections actually searched ---------------------------------------
    try:
        import retrieval.retriever as R
        if hasattr(R, "_target_collections"):
            orig_tc = R._target_collections

            @functools.wraps(orig_tc)
            def tc_wrapper(*a, **kw):
                cols = orig_tc(*a, **kw)
                TRACE.setdefault("collections_searched", []).extend(list(cols))
                return cols

            R._target_collections = tc_wrapper
    except Exception:
        pass

    # -- Groq calls: cache + token accounting --------------------------------
    client = G._get_client()
    orig_create = client.chat.completions.create

    @functools.wraps(orig_create)
    def create_wrapper(**kwargs):
        key = _cache_key(kwargs.get("model", ""), kwargs.get("messages", []))
        if key in cache:
            rec = cache[key]
            TRACE.setdefault("groq_calls", []).append({**rec["meta"], "cached": True})

            class _M:   # minimal response shape
                content = rec["content"]
            return type("R", (), {"choices": [type("C", (), {"message": _M})()]})()

        attempts, last_exc = 0, None
        while attempts < 3:
            attempts += 1
            try:
                resp = orig_create(**kwargs)
                u = getattr(resp, "usage", None)
                meta = {
                    "model": kwargs.get("model"),
                    "prompt_tokens": getattr(u, "prompt_tokens", None),
                    "completion_tokens": getattr(u, "completion_tokens", None),
                    "retries": attempts - 1, "error": None, "cached": False,
                }
                TRACE.setdefault("groq_calls", []).append(meta)
                content = resp.choices[0].message.content
                _cache_append({"key": key, "content": content, "meta": {**meta, "cached": False}})
                cache[key] = {"key": key, "content": content, "meta": meta}
                return resp
            except Exception as exc:
                last_exc = exc
                name = type(exc).__name__
                TRACE.setdefault("groq_calls", []).append({
                    "model": kwargs.get("model"), "prompt_tokens": None,
                    "completion_tokens": None, "retries": attempts - 1,
                    "error": f"{name}: {exc}", "cached": False,
                })
                if "RateLimit" in name or "429" in str(exc):
                    wait = getattr(getattr(exc, "response", None), "headers", {}) or {}
                    delay = float(wait.get("retry-after", 20)) if hasattr(wait, "get") else 20.0
                    if "daily" in str(exc).lower() or "per day" in str(exc).lower():
                        TRACE["daily_limit_hit"] = True
                        raise
                    print(f"    rate-limited; sleeping {delay}s (attempt {attempts}/3)")
                    time.sleep(min(delay, 90))
                    continue
                raise
        raise last_exc

    client.chat.completions.create = create_wrapper
    return Q


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--sleep", type=float, default=4.0)
    args = ap.parse_args()

    if not QUESTIONS.exists():
        print(f"ERROR: {QUESTIONS} missing", file=sys.stderr)
        return 2

    questions = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
    order = {qid: i for i, qid in enumerate(PRIORITY)}
    questions.sort(key=lambda q: (order.get(q["id"], 999), q["id"]))

    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        questions = [q for q in questions if q["id"] in keep]

    done: set[str] = set()
    if args.resume and RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["id"])
        print(f"resuming — {len(done)} already done")

    try:
        Q = install_instrumentation()
    except Exception as exc:
        print(f"ERROR: cannot instrument the pipeline — {type(exc).__name__}: {exc}", file=sys.stderr)
        print("The pipeline's dependencies (qdrant-client, fastembed, groq) and an "
              "indexed corpus under data/ are both required for Step 3.", file=sys.stderr)
        return 3

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    for q in questions:
        if q["id"] in done:
            continue
        TRACE.clear()
        print(f"[{q['id']}] {q['question'][:70]}")
        t0 = time.perf_counter()
        rec = {"id": q["id"], "category": q["category"], "question": q["question"],
               "as_of": q.get("as_of"), "run_at": datetime.now(timezone.utc).isoformat()}
        try:
            result = Q.ask(q["question"])
            rec.update({
                "status": "ok",
                "answer": result.answer,
                "citations": result.citations,
                "query_type": result.query_type,
                "n_chunks_used": len(result.chunks_used),
            })
        except Exception as exc:
            rec.update({"status": "error", "error_type": type(exc).__name__,
                        "error": str(exc), "traceback": traceback.format_exc()[-2000:]})
            print(f"    ERROR {type(exc).__name__}: {exc}")
        rec["total_latency_s"] = round(time.perf_counter() - t0, 3)
        rec["trace"] = json.loads(json.dumps(TRACE, default=str))
        with RESULTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        if TRACE.get("daily_limit_hit"):
            print("Groq DAILY limit reached — stopping cleanly. "
                  "Remaining questions are not_run.")
            break
        time.sleep(args.sleep)

    print(f"\nwrote {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
