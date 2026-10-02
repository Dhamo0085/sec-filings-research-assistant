# STATE — living handoff for Claude Code sessions

Seeded from `reports/phase1/REPORT.md` and the Phase 0/Step 0 reports at the end of Phase 1 (2026-10-02);
carried into Phase 2 on 2026-10-02 against spec v1.4.
**Read this at the start of every session and after any `/compact`. Update it at every commit batch, at every gate, and *before* running `/compact`.** If this file and the code disagree, trust the code, then fix this file. Facts here that you have not re-verified are marked (seed).

## 1. Where we are
| Item | State |
|---|---|
| Repository | private, `github.com/Dhamo0085/sec-filings-research-assistant`; branch model: `main` + one `phase-N-<slug>` branch per phase, merged by the owner via PR |
| Step 0 | COMPLETE (PR #1 merged) |
| Phase 1 | COMPLETE; PR #2 merged into `main` as `6be4200` |
| Phase 2 | COMPLETE at the gate; PR open from `phase-2-facts-engine` (9 commits). Two items open: T2-10's exact threshold and P2-00(e) indexing |
| Current task | none — awaiting "Approved: start Phase 3" |
| Tests | `make test` → **511 passed, 1 documented skip**; `facts/` 88.7%, `catalog/` 96.4%; ruff and hygiene clean |

## 1a. Phase 2 facts (measured, 2026-10-02)
- **Facts store built**: `data/derived/facts.sqlite`, **26,041 facts**, 66 submissions, 13 tickers, 3,040 distinct concepts, from the newest 5 originals per ticker plus the GS FY2023 10-K/A. `make facts` is idempotent: a second run reports all 66 `unchanged` and `--rebuild` reproduces the byte-identical content hash (T2-08).
- **Oracle cross-check (P2-09)**: 20,892 comparable (accn, concept, start, end, unit) pairs. **99.11% bit-exact overall; 100.00% on the 1,330 pairs for the 23 concepts the metric registry uses; 100% agree within the filer's declared precision. Zero scale errors, zero sign errors, zero unclassified mismatches.** The 181 `rounding` rows are filings tagging a concept twice — rounded in prose (`decimals="-8"`) and exact in a table; the oracle keeps the precise one, we keep the other. None touches a registry concept.
- **Coverage (P2-11)**: 634 of 780 (ticker, metric, year) resolutions = 81.3%, **zero ambiguous**. 146 `metric_not_found_in_filing`, all plausible (banks: no gross profit, no R&D, no OperatingIncomeLoss; AMZN tags no `us-gaap:Liabilities`). 51 `conflict` validations remain and are mostly v1 parser section-boundary defects, not extraction errors.
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
- **Indexed collections: AAPL_2023/24/25 and AMZN_2023/24/25 only.** Raw, parsed, and chunk files exist for 35 filings.
- Embedding throughput was **1.1–1.6 s/chunk** while the machine was swapping (swap hit 9.6 of 10 GB). v1's `index_chunks` embeds every chunk of every collection before the first upsert; `eval/phase1/index_per_ticker.py` (resumable, per ticker) is the workaround. The slowness is probably memory pressure, not the model: confirm in P2-00 before concluding anything.
- K9 (Qdrant local filter) is REFUTED on a real collection (qdrant-client 1.19.1).

## 5. Baseline (v1 on gpt-oss substitutes): 9 of 25 questions run
Numeric 2/2 pass (N1, N5); computed 0/1 (C1: classification failure); trend 0/1 (M2); unanswerable 2/3 (U2, U3 pass; U1 classification failure); narrative R3 and ambiguity T2 need manual review. 16 `not_run` (corpus not indexed). Latency p50 1.1 s (fast failures), p95 44 s, max 268 s (auto-ingest). Retrieval found the expected number for both numeric passes.

## 6. Decisions in force (see `docs/DECISIONS.md` and spec section 4)
D1 facts from iXBRL · D2 `as_of` via catalog · D5 headline total net revenue · D6 company's own fiscal label (DEI) · D7 typed errors · D13 admin fail-closed · D14 restatement rule · D17 portfolio repo · D18 no paid services · D19 `Co-Authored-By: Claude` · D20 normalize citation brackets (counted) · D21 partial baseline accepted, evaluation-core tickers indexed before Phase 3.

## 6a. Phase 2 decisions
D2-00 keep bge-base at 512 tokens, batch 8 (the >=4 chunks/s bar was a proxy; 4.2 h for the full corpus is still an overnight job) · D2-02 tiered candidate concepts plus "candidates that agree are not ambiguous" (took coverage from 69.7% to 81.3% and ambiguity from 90 rows to 0).

## 7. Open items
1. **P2-00(e) indexing is unfinished.** AAPL, AMZN, MSFT, JPM indexed; GOOGL, BLK, GS still to go at a measured ~1.6 chunks/s. Resume with `make index ONLY=GS` (resumable by point id, so re-running costs nothing for what is done). NFLX has no chunk files at all — it needs download/parse/chunk first.
2. **T2-10's exact threshold is not met**: 99.13% corpus-wide vs the 99.5% bar. The whole shortfall is 233 values in filings that tag one concept twice (rounded in prose, exact in a table); they agree within the declared precision and none is a registry concept. Fix proposed in the report section 9 item 1.
3. **v1's parsed statement sections are unreliable** and Phase 3's text path reads the same ones: the section titled "Consolidated Statements of Operations" is 106 chars of heading for AMZN, empty for JPM in all three years, over a megabyte for GS, and GOOGL's "Balance Sheets" section holds the auditors' report. This is why 51 validations are `conflict`.
4. `.hygiene_local` is still EMPTY, so the old project's names are not being scanned for (the check works; it has nothing to look for).
5. Owner spot-check sheet `reports/phase2/owner_spotcheck.csv` is the Phase 2 gate: 20 rows to mark OK/WRONG.
6. Old Phase 1 items that remain: the Phase 0 scorer mislabels passing numeric answers (P4-03 rewrites it); the runner instruments only the generator (P4-04).
2. Phase 0 scorer mislabels passing numeric answers `retrieval_found_llm_misread` (rewritten in P4-03).
3. Runner instruments only the generator; P4-04 must instrument every LLM role.
4. `llm/__init__.py` coverage 50% (shim); ignore unless it grows.
5. Rate limiter is per-process, in memory (fine for local-first).
6. Old project's handle/repo name belongs in the gitignored `.hygiene_local` patterns file only, never in a committed file.

## 8. Gotchas that already cost time (do not repeat)
- `app.routes` is nested under `include_router` in this FastAPI version: inventory routes recursively, key allow-lists by `(method, path)`.
- `str.format()` on a prompt containing JSON braces breaks silently into the fallback path.
- `pytest.mark.skipif` is evaluated at collection time, before `pytest-socket` patches; use runtime checks.
- Hand-written text matchers under-match real model output (typographic apostrophes, `isn't`, fullwidth brackets, years counted as claims). Test matchers on real outputs.
- A check that has never failed has not been tested: add a negative control. Prefer small Python scripts over nested shell pipelines.
- Never let a runner overwrite committed artifacts (the Phase 0 scorer's hard-coded output path did).

## 9. Resume commands
`make setup` · `make test` · `make lint` · `make hygiene` · `python scripts/check_repo_hygiene.py --self-test` · `make catalog` · `make up` · `python scripts/smoke.py --base-url http://localhost:8000` · resumable indexing: `python eval/phase1/index_per_ticker.py --only AAPL,AMZN` (run from the v1 worktree).
