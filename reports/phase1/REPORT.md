# Phase 1 report — foundation, hardening, catalog, baseline

**Branch:** `phase-1-foundation` · **Base:** `main` (`6cd37fc`)
**Run date:** 2026-10-02 · **Host:** macOS 26.6.2, arm64, **8 GB RAM**, CPython 3.12.14
**Explainer for the owner:** `docs/explainers/phase1.md`

---

## 1. Summary

1. **The Phase 0 outage has a confirmed root cause, and it is worse than "wrong model name".**
   The Groq key can see 11 models and **neither model v1 asks for is among them** — both are
   Enterprise-only. On top of that, the available models are *reasoning* models that bill their
   chain of thought against `max_tokens` and emit it **before** the answer, so v1's
   `max_tokens=200` came back with `finish_reason="length"` and **zero characters of content**.
   v1's `except Exception` rendered that as *"Which company are you asking about?"*.
2. **The baseline run reproduced that failure live: 4 of 9 questions** (C1, U1, R3, T2) returned
   the clarification message in under a second. It is also *probabilistic* — T2 failed in the run
   but parsed in a later isolated call — which is why v1 looked flaky rather than broken.
3. **Baseline result on what could run: numeric 2/2 PASS, computed 0/1, trend 0/1,
   unanswerable 2/3.** v1 *can* read a simple number correctly when retrieval works.
4. **16 of 25 baseline questions are `not_run`, for a measured hardware reason.** v1 embeds at
   ~1.6 s/chunk here; the bundled 12 are 35,838 chunks, so a full index is a **~16 hour** job on
   8 GB. A first attempt drove swap to 9.6 GB of 10 GB with no collection written after 68
   minutes and was stopped rather than left to be OOM-killed.
5. **Every K-issue Phase 1 owns is fixed with a test**: F1 (fail-loud), K1 (citations), K4+N1
   (catalog), K5 (years), K7+N5 (fail-closed auth), K8 (chat DB), N4 (lazy settings), N2/N3
   (Python pin, requirements split). K9 is now **REFUTED on a real collection**, and the stale
   comment that claimed otherwise is corrected.
6. **The provider bake-off changed the plan.** Both gpt-oss models cite with **fullwidth
   brackets** `【1】` — correct sources, unparseable by the pipeline — and Gemma 4 produced 0/2
   valid JSON at ~81 s. Role orders are now chosen by measurement, with the citation format
   enforced as a capability.
7. **O1 partially resolved**: one real Groq free-plan limit was learned from response headers
   (`qwen/qwen3.8-27b`: 1000 req/day, 8000 tok/min).
8. **289 tests pass offline** with the network blocked and no `.env`; ruff clean; `llm/` at 90%
   and `catalog/` at ~97% coverage, both above the section 10 targets.
9. **Six of my own checks were wrong and were caught.** Three in the bake-off scorer alone were
   the same class as the K11 defect this project exists to fix. One — `make test` — had never
   actually run with sockets blocked. Details in §6.
10. **Not done:** 16 baseline questions (hardware), `test_setup.py` removal (deferred, §7), and
    the owner gate items.

---

## 2. What changed

22 commits on `phase-1-foundation`. Grouped by task:

| Task | Commits | Modules |
|---|---|---|
| spec v1.3 | `6a76adf` | `CLAUDE.md`, `docs/PROJECT_SPEC.md` |
| P1-01 Step 0 closure | `f5514fb`, `073a3c6` | `reports/phase0/`, `.gitignore` |
| P1-02 tooling | `a7c5656` | `requirements*.txt`, `Makefile`, `pyproject.toml`, `Dockerfile` |
| P1-03 lazy settings | `4a3cf7e` | `config.py` + 6 call sites |
| lint sweep | `5f6f9a7` | 15 v1 files (behaviour-preserving, 2 deliberate fixes) |
| P1-07 citations | `5d54a4e` | `generation/citations.py`, `synthesizer.py` |
| P1-08 years | `3afafe3` | `routing/classifier.py` |
| P1-06 security | `49b84d3` | `api/auth.py`, `api/ratelimit.py`, `api/app.py`, `api/chat.py` |
| P1-13 hygiene | `7a01027` | `scripts/check_repo_hygiene.py` |
| P1-04 llm client | `6f2c5ec` | `llm/` (client, errors, budget, cache, fake, providers.yaml) |
| P1-05 fail-loud | `4267931` | `query.py`, `models.py`, `routing/`, `generation/`, `llm/health.py`, `ui/` |
| P1-11 smoke | `38e2530` | `scripts/smoke.py` |
| P1-09 catalog | `8809b3b` | `catalog/` |
| P1-10 qdrant check | `1a0edb5`, `7c420a6` | `scripts/check_qdrant_filter.py`, `retrieval/vector_store.py` |
| P1-12 explainer | `1ad9c87` | `docs/explainers/phase1.md` |
| P1-00 indexer | `4c563ba` | `eval/phase1/index_per_ticker.py` |
| P1-04 bake-off | `4a8f697` | `eval/phase1/bakeoff.py`, `docs/DECISIONS.md` |
| P1-00/05 empty output | `1996b1a` | `llm/errors.py`, `llm/client.py`, `routing/classifier.py` |
| test guarantee | `39591bb` | `Makefile`, `tests/unit/test_offline_guarantee.py` |

---

## 3. Tests

**Command:** `make test` → `pytest tests/unit tests/integration -m "not live and not slow"
--disable-socket --allow-unix-socket --cov=. --junitxml=... --cov-report=xml:...`

**Result: 289 passed, 0 skipped, 0 failed, 22.5 s.** Artifacts:
`reports/phase1/tests/junit.xml`, `reports/phase1/tests/coverage.xml`.

| ID | Test | Count | Result |
|---|---|---|---|
| T1-01 | citation remap (offsets 0–5 × 1–12 citations, `[10]` vs `[1]`, non-citation brackets, idempotence, composition) | 87 + 7 integration | PASS |
| T1-02 | router failure paths → `status=error` with the right `error_code`, never the clarification | 7 | PASS |
| T1-03 | auth: route inventory, 503 unset, 403 wrong/missing, correct token passes, no token in query string, removed routes absent, constant-time compare | 24 | PASS |
| T1-04 | rate limit and question cap, per-client budgets, `Retry-After`, non-wildcard CORS | 9 | PASS |
| T1-05 | import with no env: `config`, `query`, `api.app`; typed error at use; section 8 defaults | 15 | PASS |
| T1-06 | catalog from recorded EDGAR fixtures: dual CIK, 10-K/A, dates, `as_of`, D14 | 38 | PASS |
| T1-07 | years from collections; 2026 survives; implausible dropped; prompt names indexed years | 20 | PASS |
| T1-08 | LLM client: cache, error mapping, `Retry-After`, budgets, JSON repair | 43 | PASS |
| T1-09 | `/health` LLM states; probe cached, not per request | 6 | PASS |
| T1-10 | chat DB untracked and ignored (incl. `-wal`/`-shm`) | 5 | PASS |
| T1-11 | failover on 429/5xx/auth/empty output; pinned mode; capability registry | 10 | PASS |
| T1-12 | repo hygiene: LICENSE/NOTICE, no third-party URLs, `.env` untracked | 8 | PASS |
| T1-13 | hygiene checks each catch a planted violation | 7 | PASS |
| — | offline guarantee (negative control: TCP/DNS/HTTP blocked, AF_UNIX allowed) | 5 | PASS |

**Coverage** (section 10 targets: ≥85% on `llm/`, `catalog/`; ≥70% on all new code):

| Module | Covered | Target | Status |
|---|---|---|---|
| `llm/` package | **90%** | ≥85 | OK |
| `llm/client.py` | 93% | | |
| `llm/errors.py`, `llm/cache.py` | 100% | | |
| `catalog/` package | **~97%** | ≥85 | OK |
| `catalog/store.py` | 100% | | |
| `catalog/build.py` | 96% | | |
| `api/auth.py` | 88% | ≥70 | OK |
| `api/ratelimit.py` | 88% | ≥70 | OK |
| `generation/citations.py` | 100% | ≥70 | OK |
| `llm/__init__.py` | 50% | ≥70 | **below** — an 8-line lazy re-export shim; its two functions are not called because tests import `llm.client` directly |
| Repo total | 60% | — | v1 modules not touched this phase are largely uncovered |

---

## 4. Metrics

### 4.1 P1-00 baseline, v1 on substituted models

**Provider/model:** Groq free tier. `ROUTING_MODEL=openai/gpt-oss-20b`,
`GENERATION_MODEL=openai/gpt-oss-120b`. **No compatibility patch was needed** — v1's unmodified
prompts and parsing work on the available models — so there is no `v1-baseline-compat` branch.
Run 2026-10-02 09:38–09:46 UTC. Artifacts under `reports/phase1/baseline_v1_local/`.

| ID | Category | Expected | Verdict | Failure stage |
|---|---|---|---|---|
| N1 | numeric | 391,035 M | **PASS** | — |
| N5 | numeric | 36,852 M | **PASS** | — |
| C1 | computed | 31.51% | FAIL | **classification** (F1) |
| M2 | trend | 3 values | FAIL | attribution unclear |
| R3 | narrative | sections | MANUAL | **classification** (F1) |
| T2 | ambiguity | — | MANUAL | **classification** (F1) |
| U1 | unanswerable | abstain | FAIL | **classification** (F1) |
| U2 | unanswerable | abstain | **PASS** | — |
| U3 | unanswerable | abstain | **PASS** | — |

**Per category:** numeric 2/2 · computed 0/1 · trend 0/1 · unanswerable 2/3 · narrative 0/1 manual ·
ambiguity 0/1 manual. **`not_run`: 16 of 25.**

**Latency:** p50 **1.1 s**, p95 **44.4 s**, max **268.4 s**. The p50 is low *because* four
questions failed in under a second — a fast failure, not a fast answer. U2's 268 s is auto-ingest
firing for Tesla.

**Tokens:** 21,610 across 7 recorded generator calls, ~2,400 per query. Rate-limit events: **0**.

**Retrieval diagnostics (3.4):** for both numeric passes the expected value was present in the
retrieved chunks (N1 at rank 2, N5 at rank 1) — retrieval worked, and the LLM read it correctly.
*Note:* the Phase 0 scorer labels these `retrieval_found_llm_misread` even when the answer is
right; the label is only meaningful for failures. Minor scorer defect, listed in §6.

### 4.2 Why 16 questions are `not_run`

Measured indexing throughput on this machine:

| Ticker | Chunks | Time | Rate |
|---|---|---|---|
| AAPL | 589 | 957.8 s | 1.63 s/chunk |
| AMZN | 713 | 787.2 s | 1.10 s/chunk |

The bundled 12 are **35,838 chunks** → **~16 hours**. Worse, v1's `index_chunks` embeds *every*
chunk across *every* collection before upserting a single point:

```python
all_texts = [c.text for _, col_chunks in needs_indexing for c in col_chunks]
```

On 8 GB that reached 2.4 GB RSS and drove swap to 9.6 GB of 10 GB with ~200 MB RAM free, after
68 minutes with **no collection written**. It was stopped rather than OOM-killed.
`eval/phase1/index_per_ticker.py` calls the same function per ticker, bounding memory and writing
incrementally — which is also what v1's own on-demand path does.

Collections indexed: **AAPL_2023/24/25, AMZN_2023/24/25**. Excluded questions are listed with the
exact missing collection in `reports/phase1/baseline_v1_local/plan.json`. They are excluded rather
than attempted because a question naming an unindexed filer triggers auto-ingest *inside the
measurement* — U2 shows the cost: 268 seconds.

### 4.3 P1-04 bake-off (24 calls, cap 40)

| entry | JSON | cites correctly | usable | style | refusal | median |
|---|---|---|---|---|---|---|
| gemini:gemini-3.5-flash-lite | 2/2 | 1/1 | 1/1 | ascii | 1/1 | 0.74 s |
| gemini:gemini-3.1-flash-lite | 2/2 | 1/1 | 1/1 | ascii | 1/1 | 1.69 s |
| groq:qwen/qwen3.8-27b | 2/2 | 1/1 | 1/1 | ascii | 1/1 | 0.66 s |
| groq:openai/gpt-oss-120b | 2/2 | 1/1 | **0/1** | **fullwidth** | 1/1 | 0.45 s |
| groq:openai/gpt-oss-20b | 2/2 | 1/1 | **0/1** | **fullwidth** | 1/1 | 0.51 s |
| gemini:gemma-4-26b-a4b-it | **0/2** | 1/1 | 1/1 | ascii | **0/1** | **81 s** |

Chosen orders (D1-04b): router `groq:gpt-oss-20b → gemini-3.5-flash-lite → gemini-3.1-flash-lite`;
generator `gemini-3.5-flash-lite → gemini-3.1-flash-lite → groq:qwen` (requires `ascii_citations`,
which excludes both gpt-oss); judge `groq:gpt-oss-120b → groq:qwen` (never the generator, rule 10).

Gemma 4 IDs discovered from the models endpoint as P1-04 asked: `gemma-4-26b-a4b-it`,
`gemma-4-31b-it`. Gemini exposes 61 models to this key (`reports/phase1/gemini_models.json`).

### 4.4 P1-10 Qdrant filter check

`AAPL_2024`, qdrant-client **1.19.1**: unfiltered `{text: 2, table: 7, footnote: 1}`, filtered
`{table: 10}`. **K9 REFUTED on a real collection.** The `scroll_by_section` comment claiming
filters are "silently ignored in local Qdrant" was wrong and is corrected in place.

### 4.5 Catalog

**365** annual filings, **29** amendments, **13** tickers, built from cache with **0** network
requests. BlackRock merges to 20 filings across both registrants (FY2023 under CIK 1364742,
FY2024–25 under 2012383). Microsoft shows FY2026. Resolving JPM FY2005 at successive `as_of`
dates walks the original 10-K → first 10-K/A → second 10-K/A as each becomes public.

---

## 5. Deviations from the spec

| # | Deviation | Reason |
|---|---|---|
| 1 | **16 of 25 baseline questions `not_run`** (P1-00 is MUST) | Measured ~16 h indexing time on 8 GB (§4.2). Every exclusion is recorded with its missing collection. The spec anticipates partial runs for quota; this is the same situation with compute. |
| 2 | Baseline indexed **per ticker**, not by `run_ingestion.py` in one pass | v1's one-pass embedding exhausts 8 GB. Same v1 function, same output, bounded memory. |
| 3 | `make test` uses `--allow-unix-socket` alongside `--disable-socket` | The TestClient's event loop needs an AF_UNIX self-pipe. A negative control asserts the network is still blocked. |
| 4 | Installed the **serving subset**, not all of `requirements.txt`, in the v1 worktree | The baseline imports nothing from ragas/langchain, and P1-02 splits them out anyway. |
| 5 | `python-multipart` and the `groq` SDK dropped from `requirements.txt` | Both became genuinely unused after P1-06 removed the upload route and P1-05 migrated the call sites. Narrower than P1-02 asked for. |
| 6 | Ruff rule families deferred (`UP`, `BLE001`) | Documented with reasons in `pyproject.toml` and `docs/DECISIONS.md` D1-02. |
| 7 | Router `max_tokens` 200 → **1600**, not the 700 I first chose | 700 was measured insufficient (§6.1). |
| 8 | `test_setup.py` still tracked (P1-01 says remove once `make test` exists) | `make test` now exists, so this is removable — left for the owner's call since it is the last v1 smoke script. Flagged in §7. |

Decisions logged: `docs/DECISIONS.md` D1-02, D1-04b, D1-04c (plus the Step 0 entries).

---

## 6. Defects found and fixed

### 6.1 In v1 (the point of the phase)

| ID | Defect | Fix | Evidence |
|---|---|---|---|
| F1 | Classifier swallowed every exception → outage looked like a clarification request | typed errors, `status=error` + `error_code`, 503, `/health` LLM state | 4/9 baseline questions reproduced it |
| **F1b** | **Reasoning models' chain of thought consumed `max_tokens=200`, returning empty content** | `LLMEmptyOutput` + **failover**, router budget 1600 | 3/4 questions at 200; 1/4 still failing at 700; 4/4 parse at 1600 (C1 used 1093 completion tokens) |
| K1 | Citation remap collisions | single-pass regex remap | offset 1 → `[4][4][4]`; offset 2 → `[5][4][5]` |
| K4/N1 | No `filing_date` at query time; BlackRock dual CIK | `catalog/` | 365 filings; BLK across 2 CIKs |
| K5 | Hard-coded `VALID_YEARS` | derived from collections + sanity range | MSFT FY2026 reachable |
| K7/N5 | Fail-open admin guard; unauthenticated chat deletes | `api/auth.py`, D13, fail-closed | 24 auth tests |
| K8 | Chat DB tracked | ignore rule widened to `db*` | negative control |
| N4 | Import-time `Settings()` | lazy settings + `require_*` | 15 tests |
| N2/N3 | Docker 3.10 vs local 3.12; eval deps in serving image | pinned 3.12; requirements split | — |
| K9 | Local-mode filter behaviour | **REFUTED**; stale comment corrected | real collection |

### 6.2 In my own work (all caught before shipping)

Six, listed because the pattern matters more than any one of them:

1. **Route inventory missed every `include_router` route.** `app.routes` is not flat in this
   FastAPI version; a plain loop found zero `/chat` paths — exactly the unguarded routes (N5) the
   test existed to catch. Now recursive, with a negative control.
2. **Public-route allow-list keyed by path alone**, which whitelisted the destructive `DELETE`
   sharing a path with a public `GET`. Now keyed by `(method, path)`.
3. **`str.format()` on the classifier prompt** broke on the prompt's literal JSON braces, sending
   every classification into the fallback — reproducing F1 itself.
4. **Three bake-off scorer bugs, all the K11 pattern** (a hand-written matcher under-matching real
   output): "2024" counted as a financial claim; "isn't included" missed because the hint list had
   "doesn't" but not "isn't"; then still missed because models write a typographic apostrophe.
   Before the fixes the table read 0/1 refusal for all six models — implausible enough to look at.
5. **`make test` had never run with sockets blocked.** Adding the flag produced 35 failures.
6. **The offline-guarantee control silently skipped.** `skipif` is evaluated at collection time,
   before `pytest-socket` patches, so all four assertions never ran.

Open, not fixed: the Phase 0 scorer labels a *passing* numeric answer
`retrieval_found_llm_misread`; the label only makes sense for failures. P4-03 rewrites the scorers.

---

## 7. Owner actions and questions

1. **Decide the baseline's scope.** The honest options: (a) accept the 9-question baseline and
   proceed, (b) run the indexer overnight (`eval/phase1/index_per_ticker.py`, resumable) to get
   all 25, or (c) run it on a larger machine. This affects Phase 4's V0 comparison, not Phases 2–3.
2. **Remove `test_setup.py`?** P1-01 says remove it once `make test` exists. It does. Left tracked
   pending your word, since it is the last v1-era smoke script.
3. **The old repository's name/URL**, so `scripts/check_repo_hygiene.py` can target it. It
   currently matches deployment hostnames only; `Financial_RAG` is deliberately *not* matched
   because it is this project's own working name.
4. **`llm/limits.local.yaml` is gitignored and now holds one measured limit.** Keep adding to it as
   limits are observed; O1 stays PARTIALLY RESOLVED.
5. **Gate items:** `make up` from a clean checkout, then
   `python scripts/smoke.py --base-url http://localhost:8000`. Note that smoke will report `503
   llm_auth`-class failures honestly if the provider is unreachable — that is the intended behaviour.
6. **Should the generator tolerate fullwidth citation brackets?** Currently no: `ascii_citations`
   is required, which excludes both gpt-oss models from generating. Normalising would widen the
   model pool but hide format drift (D1-04b).

---

## 8. Gate checklist

- [x] All T1 tests pass offline (289 passed, network blocked, no `.env`)
- [x] `make lint` clean (ruff, pinned rule set)
- [x] Baseline report exists with per-category results — **partial: 9 of 25, 16 `not_run` with reasons**
- [x] Qdrant filter check recorded (K9 REFUTED on a real collection)
- [x] Provider bake-off recorded (`docs/DECISIONS.md` D1-04b, `reports/phase1/bakeoff.json`)
- [x] Local release checklist written (§9)
- [x] `docs/explainers/phase1.md` written
- [x] Coverage targets met for `llm/` and `catalog/`
- [ ] **Owner:** review this report and the pull request, merge on GitHub
- [ ] **Owner:** `make up` + `scripts/smoke.py --base-url http://localhost:8000`
- [ ] **Owner:** decide items 1–3 and 6 in §7

---

## 9. Local release checklist

From a clean clone, with `.env` holding `groq_api`/`GROQ_API_KEY`, `GEMINI_API_KEY`, `edgar_email`:

```bash
make setup                    # venv + serving and dev dependencies
make test                     # 289 offline tests, network blocked
make lint                     # ruff
make hygiene                  # secrets, large files, third-party references
python scripts/check_repo_hygiene.py --self-test   # prove the checks can fail
make catalog                  # 365 filings from EDGAR (cached; add --offline to skip the network)
make ingest                   # SEE §4.2: ~16 h on 8 GB. Prefer:
#   cd <v1 worktree> && python eval/phase1/index_per_ticker.py --only AAPL,AMZN
make up                       # serve on http://localhost:8000
make smoke                    # read-only, at most 8 POST /query
```

`make facts`, `make eval-smoke`, `make eval-full` exit non-zero naming the phase that builds them
(2 and 4) rather than appearing to work.

---

## 10. Proposals (out of scope, not built)

1. **Stream the embedding step.** v1 materialises all chunk texts and vectors before upserting.
   Embedding and upserting per collection would bound memory and give progress output; the current
   step prints nothing for 68 minutes. Relevant to P5-01's memory sizing.
2. **Normalise citation markers at the generator boundary**, so a model's bracket style cannot
   silently zero out citations. Deliberately not done now (D1-04b).
3. **Record classifier LLM calls in the baseline trace.** The Phase 0 runner instruments only the
   generator's client, which is why the 4 classifier failures showed no error — the cause had to be
   reproduced separately. P4-04's runner should instrument every role.

---

## 11. Appendix — commands

Full transcript with timestamps and exit codes: `logs/phase1/commands.log`.
Long-run logs: `logs/phase1/ingest_v1.log`, `index_per_ticker.log`, `baseline_v1.log`,
`ingest_v1_attempt1_degraded.log`.

| Command | Exit |
|---|---|
| `git worktree add … v1-baseline` | 0 |
| Groq model listing (`/openai/v1/models`) | 0 — 11 models; **neither v1 default present** |
| v1 prompt probe on 4 candidate models | 0 — all parse v1's unmodified output |
| `run_ingestion.py` (attempt 1, no models set) | 143 — stopped; 38 model-404 warnings |
| `run_ingestion.py --skip-download` (models set) | 144 — stopped at 68 min, swap exhausted |
| `eval/phase1/index_per_ticker.py` | 143 — stopped after AAPL+AMZN (6 collections) |
| `eval/phase0/run_baseline.py --only …` | 0 — 9 questions, 0 rate limits |
| `eval/phase0/score_baseline.py` | 0 |
| `eval/phase1/plan_baseline.py` | 0 — 9 runnable, 16 `not_run` |
| `eval/phase1/bakeoff.py` | 0 — 24 calls; re-run fully cached, 0 calls |
| Gemini model listing | 0 — 61 models; Gemma 4 IDs found |
| `scripts/check_qdrant_filter.py --collection AAPL_2024` | 0 — K9 REFUTED |
| `python -m catalog.build --offline` | 0 — 365 filings, 0 requests |
| `scripts/check_repo_hygiene.py --self-test` | 0 — every check catches a planted violation |
| `scripts/check_repo_hygiene.py` | 0 — clean |
| `make lint` | 0 |
| `make test` | 0 — **289 passed** |
