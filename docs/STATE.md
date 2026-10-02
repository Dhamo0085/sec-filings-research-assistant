# STATE — living handoff for Claude Code sessions

Seeded from `reports/phase1/REPORT.md` and the Phase 0/Step 0 reports at the end of Phase 1 (2026-10-02);
carried into Phase 2 on 2026-10-02 against spec v1.4; updated at P2-13 closure against spec v1.5.
**Read this at the start of every session and after any `/compact`. Update it at every commit batch, at every gate, and *before* running `/compact`.** If this file and the code disagree, trust the code, then fix this file. Facts here that you have not re-verified are marked (seed).

## 1. Where we are
| Item | State |
|---|---|
| Repository | private, `github.com/Dhamo0085/sec-filings-research-assistant`; branch model: `main` + one `phase-N-<slug>` branch per phase, merged by the owner via PR |
| Step 0 | COMPLETE (PR #1 merged) |
| Phase 1 | COMPLETE; PR #2 merged into `main` as `6be4200` |
| Phase 2 | COMPLETE through P2-13 closure. **PR #3 was merged mid-session with the 12 pre-closure commits** (`21566ee`); the two P2-13 commits are on `phase-2-facts-engine` and need a follow-up PR to reach `main`. All T2 tests pass and all T2-10 thresholds are met |
| Current task | none — awaiting "Approved: start Phase 3" |
| Tests | `make test` → **547 passed, 0 skipped**; `facts/` 88.7%, `catalog/` 95.4%; ruff and hygiene clean |

## 1a. Phase 2 facts (measured, 2026-10-02)
- **Facts store built**: `data/derived/facts.sqlite`, **29,002 facts**, 94 submissions, 18 tickers (13 bundled + the 5 cross-check-only filers), 3,492 distinct concepts, newest 5 originals per ticker plus amendments. `make facts` is idempotent: a second run reports all 94 `unchanged` and `--rebuild` reproduces the byte-identical content hash (T2-08) — re-verified after the D22 change.
- **Oracle cross-check (P2-09, after P2-13)**: 23,429 comparable (accn, concept, start, end, unit) pairs. **99.9787% bit-exact; 100.00% on the 1,047 pairs for the 23 registry concepts; 100% agree within the filer's declared precision. Zero scale errors, zero sign errors, zero unclassified mismatches, zero `rounding`.** All T2-10 thresholds met. Before P2-13 it was 99.1347% with 233 `rounding` rows; the before/after is in the report section 4.1a.
- **Precision preference (D22 / D2-03)**: when a filing tags one concept twice in one context at different precisions, keep the larger-`@decimals` instance — only when the two agree within the coarser declared tolerance (half a unit each, summed). Disagreements beyond it are never merged; both are kept and the resolver abstains. Undeclared `@decimals` is never merged. 272 values changed, 4,510 duplicate instances collapsed. The owner-signed spot-check values all re-resolve unchanged.
- **Coverage (P2-11)**: 634 of 780 (ticker, metric, year) resolutions = 81.3%, **zero ambiguous** — unchanged by D22. 146 `metric_not_found_in_filing`, all plausible (banks: no gross profit, no R&D, no OperatingIncomeLoss; AMZN tags no `us-gaap:Liabilities`). Validation after D22: validated 256 (was 223), unvalidated 327, `conflict` 51 — the conflicts are v1 parser section-boundary defects, not extraction errors (D23).
- **Owner gate**: spot-check sheet signed **20 of 20 OK, zero WRONG**, each row noted against the statement line it was checked on.
- **Indexing throughput (P2-00d)**: batch size is worth ~9% at 512 tokens (2.15-2.35 chunks/s); sequence length is worth 2x (4.8 chunks/s at 256) but changes what a vector means, so it was rejected (D2-00). Batch 8 in a real run against an existing index: **1.34 GB peak RSS vs 3.05 GB at batch 64, and faster** — Qdrant local mode holds every existing collection in RAM, which the isolated grid did not capture. `config.index_batch_size = 8`.
- **iXBRL formats**: across all 250 cached documents (65 filings) there are exactly **7** distinct `ix:nonFraction/@format` values, all implemented. `ixt-sec:numwordsen` uses 16 distinct texts including `nil` and `three million`.
- **DEI labels (P2-03)**: 65 of 65 match `period_end.year`; catalog now records `fiscal_label_source='dei'` for those 65. **29 of 65 filings are multi-document submissions** (not just WFC) and `exhibit_docs` is populated.
- `.cache/filings/` now holds ~500 MB (one directory per accession). Reuse it; the offline re-runs make zero requests.

## 2. Machine and environment (seed)
macOS arm64, **8 GB RAM**, CPython 3.12.14, `git` 2.51, `gh` authenticated. Keys live in `.env` (`groq_api`/`GROQ_API_KEY`, `GEMINI_API_KEY`, `edgar_email`); never read or print it. No paid services (D18). `.cache/` (~187 MB of SEC responses) and the downloaded raw filings are preserved and gitignored; reuse them before fetching again.

## 3. LLM facts (measured in Phase 1)
- The Groq key sees 11 models. `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` (v1's defaults) are Enterprise-only → every v1 request returned 404.
- Available Groq models are **reasoning models** (gpt-oss) that spend `max_tokens` on chain of thought *before* the answer; v1's `max_tokens=200` returned empty content. Router budget is now 1600 (700 still failed once).
- gpt-oss models cite with **fullwidth brackets `【1】`**; the pipeline parses only `[1]`.
- Gemma 4 (`gemma-4-26b-a4b-it`, `gemma-4-31b-it`): 0/2 valid JSON, ~81 s. Not used.
- Chosen failover orders (D1-04b): router `groq:openai/gpt-oss-20b → gemini:gemini-3.5-flash-lite → gemini:gemini-3.1-flash-lite`; generator `gemini-3.5-flash-lite → gemini-3.1-flash-lite → groq:qwen/qwen3.8-27b` (requires ASCII citations until D20 is implemented); judge `groq:openai/gpt-oss-120b → groq:qwen/qwen3.8-27b` (never the generator model).
- Measured limit: `groq:qwen/qwen3.8-27b` 1000 requests/day, 8000 tokens/min (kept in gitignored `llm/limits.local.yaml`). Gemini limits: spec Appendix A (Flash-Lite 15 RPM / 500 RPD; 3.x Flash 20 RPD).
- Evaluation runs pin one provider/model; the cache makes repeated prompts free.

## 4. Data facts (seed)
- Catalog (`catalog/`): **365** annual filings, **29** amendments, **13** tickers, built offline from cache. BlackRock = two CIKs (1364742 for FY2023, 2012383 for FY2024–25). MSFT FY2026 present. `fiscal_label` is still `period_end.year` (P2-03 replaces it with the DEI label).
- Bundled tickers: AAPL, MSFT, GOOGL, AMZN, JPM, WFC, BAC, GS, BLK, STT, TROW, IVZ (+ NFLX on demand). Full text index = **35,838 chunks**.
- **Indexed collections (after P2-00e): 24, 13,520 points.** AAPL, AMZN, MSFT, JPM, GOOGL, NFLX, GS at 3 fiscal years each; BLK at 2 (FY2023 absent — its 10-K is under BlackRock's old CIK 1364742 and has no chunk files); TSLA_2025 left over from a Phase 1 auto-ingest. 10,848 chunks embedded in 1.6 h at 1.86 chunks/s, peak RSS <= 1.37 GB.
- Embedding throughput was **1.1–1.6 s/chunk** while the machine was swapping (swap hit 9.6 of 10 GB). v1's `index_chunks` embeds every chunk of every collection before the first upsert; `eval/phase1/index_per_ticker.py` (resumable, per ticker) is the workaround. The slowness is probably memory pressure, not the model: confirm in P2-00 before concluding anything.
- K9 (Qdrant local filter) is REFUTED on a real collection (qdrant-client 1.19.1).

## 5. Baseline (v1 on gpt-oss substitutes): 9 of 25 questions run
Numeric 2/2 pass (N1, N5); computed 0/1 (C1: classification failure); trend 0/1 (M2); unanswerable 2/3 (U2, U3 pass; U1 classification failure); narrative R3 and ambiguity T2 need manual review. 16 `not_run` (corpus not indexed). Latency p50 1.1 s (fast failures), p95 44 s, max 268 s (auto-ingest). Retrieval found the expected number for both numeric passes.

## 6. Decisions in force (see `docs/DECISIONS.md` and spec section 4)
D1 facts from iXBRL · D2 `as_of` via catalog · D5 headline total net revenue · D6 company's own fiscal label (DEI) · D7 typed errors · D13 admin fail-closed · D14 restatement rule · D17 portfolio repo · D18 no paid services · D19 `Co-Authored-By: Claude` · D20 normalize citation brackets (counted) · D21 partial baseline accepted, evaluation-core tickers indexed before Phase 3.

## 6a. Phase 2 decisions
D2-00 keep bge-base at 512 tokens, batch 8 (the >=4 chunks/s bar was a proxy; 4.2 h for the full corpus is still an overnight job) · D2-02 tiered candidate concepts plus "candidates that agree are not ambiguous" (took coverage from 69.7% to 81.3% and ambiguity from 90 rows to 0; **now part of spec 6.4 rule 3 rather than a deviation from it**) · D2-03 precision preference (D22), which took corpus-wide exactness from 99.1347% to 99.9787%.

Spec v1.5 also carries **D23** (v1's parsed statement sections are not trusted; `validation_status` is informational only and must never affect an answer — guarded by two tests in `test_facts_resolve.py`; P3-00 audits section quality) and **D24** (ingestion resolves filings through the catalog across every CIK a ticker has filed under; P3-06 uses BLK FY2023 as the proof case).

## 7. Open items
Closed at P2-13: P2-00(e) indexing (all 8 core tickers), T2-10's exact threshold (now met), the owner spot-check (20/20 OK), and the empty `.hygiene_local` (now filled; the T2-12 skip is gone).

Carried into Phase 3:
1. **v1's parsed statement sections are unreliable** and the text path reads the same ones: "Consolidated Statements of Operations" is 106 chars of heading for AMZN, empty for JPM in all three years, over a megabyte for GS, and GOOGL's "Balance Sheets" section holds the auditors' report. This is why 51 validations are `conflict`. **P3-00** audits it; **D23** forbids anything user-visible depending on it in the meantime.
2. **BLK FY2023 is absent from the text index** (its 10-K is under the old CIK 1364742 and was never chunked; the catalog and facts store both have it). **D24 / P3-06** make ingestion catalog-driven across every CIK, with this as the proof case.
3. `.cache/company_tickers.json` holds a cached SEC **error page** rather than JSON. Nothing reads it today (v1 uses `data/company_tickers.json`, which is valid), but a cache that stores failures as data is a trap. **P3-00(b)**.
4. Phase 0 scorer mislabels passing numeric answers `retrieval_found_llm_misread` (rewritten in P4-03).
5. Runner instruments only the generator; P4-04 must instrument every LLM role.
6. `llm/__init__.py` coverage 50% (shim); ignore unless it grows.
7. Rate limiter is per-process, in memory (fine for local-first).
8. The old project's handle/repo name belongs in the gitignored `.hygiene_local` only, never in a committed file.

## 8. Gotchas that already cost time (do not repeat)
- `app.routes` is nested under `include_router` in this FastAPI version: inventory routes recursively, key allow-lists by `(method, path)`.
- `str.format()` on a prompt containing JSON braces breaks silently into the fallback path.
- `pytest.mark.skipif` is evaluated at collection time, before `pytest-socket` patches; use runtime checks.
- Hand-written text matchers under-match real model output (typographic apostrophes, `isn't`, fullwidth brackets, years counted as claims). Test matchers on real outputs.
- A check that has never failed has not been tested: add a negative control. Prefer small Python scripts over nested shell pipelines.
- Never let a runner overwrite committed artifacts (the Phase 0 scorer's hard-coded output path did).
- `None` as a constructor default meaning "use the default" rather than "disabled" makes tests read real on-disk data without saying so. `FactsResolver(statements=...)` uses an explicit sentinel for this reason.
- A test that asserts a COUNT of extracted facts goes stale the moment extraction changes. Assert the values and the per-concept cardinality instead.

## 9. Resume commands
`make setup` · `make test` · `make lint` · `make hygiene` · `python scripts/check_repo_hygiene.py --self-test` · `make catalog` · `make up` · `python scripts/smoke.py --base-url http://localhost:8000` · resumable indexing: `python eval/phase1/index_per_ticker.py --only AAPL,AMZN` (run from the v1 worktree).
