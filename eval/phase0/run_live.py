"""
Phase 0 / Step 4.2 — live deployment baseline (read-only, 5 POST /query calls).

The local checkout has no dependencies and no indexed corpus, so Step 3.2
cannot run here.  The live deployment IS the v1 pipeline, so these 5 calls are
the only real baseline measurements available in Phase 0.

Writes results in the same shape run_baseline.py produces so that
score_baseline.py can score them unchanged.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = "https://financialrag-production-420e.up.railway.app"
OUT = REPO_ROOT / "reports" / "phase0" / "baseline_results_live.jsonl"
QUESTIONS = REPO_ROOT / "eval" / "phase0" / "baseline_questions.jsonl"

# Step 4.2 fixes exactly these five.
LIVE_SET = ["N1", "C1", "M3", "T3", "U3"]


def main() -> None:
    qs = {json.loads(l)["id"]: json.loads(l)
          for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()}
    done = set()
    if OUT.exists():
        done = {json.loads(l)["id"] for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()}

    for qid in LIVE_SET:
        if qid in done:
            print(f"[{qid}] already recorded, skipping")
            continue
        q = qs[qid]
        print(f"[{qid}] POST /query  {q['question'][:62]}")
        t0 = time.perf_counter()
        rec = {"id": qid, "category": q["category"], "question": q["question"],
               "as_of": q.get("as_of"), "source": "live"}
        try:
            r = requests.post(f"{BASE}/query", json={"question": q["question"]}, timeout=180)
            rec["http_status"] = r.status_code
            if r.status_code == 200:
                d = r.json()
                rec.update({"status": "ok", "answer": d.get("answer", ""),
                            "citations": d.get("citations", []),
                            "query_type": d.get("query_type"),
                            "n_chunks_used": len(d.get("chunks_used", []) or []),
                            "raw": {k: v for k, v in d.items() if k != "chunks_used"}})
            else:
                rec.update({"status": "error", "error_type": f"HTTP{r.status_code}",
                            "error": r.text[:400]})
        except Exception as exc:
            rec.update({"status": "error", "error_type": type(exc).__name__, "error": str(exc)})
        rec["total_latency_s"] = round(time.perf_counter() - t0, 2)
        rec["trace"] = {}   # live API exposes no internal trace
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"    {rec.get('status')} in {rec['total_latency_s']}s  "
              f"type={rec.get('query_type')}  citations={len(rec.get('citations') or [])}")
        time.sleep(4)

    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
