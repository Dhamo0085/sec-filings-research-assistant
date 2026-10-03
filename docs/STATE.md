# STATE — living handoff for Claude Code sessions

Seeded from `reports/phase1/REPORT.md` and the Phase 0/Step 0 reports at the end of Phase 1 (2026-10-02);
carried into Phase 2 on 2026-10-02 against spec v1.4; updated at P2-13 closure, at P3-00, and at the
Phase 3 gate (2026-10-02) against spec v1.5; carried into Phase 4 on 2026-10-03 against spec v1.6.
**Read this at the start of every session and after any `/compact`. Update it at every commit batch, at every gate, and *before* running `/compact`.** If this file and the code disagree, trust the code, then fix this file. Facts here that you have not re-verified are marked (seed).

## 1. Where we are
| Item | State |
|---|---|
| Repository | private, `github.com/Dhamo0085/sec-filings-research-assistant`; branch model: `main` + one `phase-N-<slug>` branch per phase, merged by the owner via PR |
| Step 0 | COMPLETE (PR #1 merged) |
| Phase 1 | COMPLETE; PR #2 merged into `main` as `6be4200` |
| Phase 2 | COMPLETE. PR #3 (12 pre-closure commits) and PR #4 (the P2-13 closure) are both merged into `main`; `main` is at `4013664`. All T2 tests pass and all T2-10 thresholds are met |
| Phase 3 | COMPLETE. PR #5 merged into `main` as `8ce9ae3` (`reports/phase3/REPORT.md`). All 12 MUST tasks done; P3-10 is OPTIONAL, moved to Phase 5 as P5-11 (D26) |
| Phase 4 | IN PROGRESS on `phase-4-evaluation`, spec v1.6. P4-00 measured and committed (`63516cb`); awaiting the owner's decision on D25's exit rule and on the overnight re-index before P4-01 |
| Current task | P4-00 gate — owner decision pending (see D4-00) |
| Tests | `make test` → **1,060 passed, 0 xfailed, 0 skipped**; ruff clean. Artifacts now land in `reports/phase4/tests/` (`PHASE` in the Makefile was still `phase3` at the Phase 4 start and overwrote Phase 3's committed junit/coverage once — bumped in `63516cb`) |

## 1c. Phase 4 facts (measured, 2026-10-03)
- **P4-00 section-boundary rewrite (D25 / D4-00)**: `ingestion/parser.py` now scores every candidate
  heading and picks the maximum-weight subsequence with strictly increasing line AND Item priority,
  instead of "first past a 15 % TOC zone" plus a greedy monotonic filter. Measured offline over all
  **40** parsed filings: usable pairs **245 → 298 of 440**; **Item 1 33 → 40 of 40**, **Item 1A
  20 → 40 of 40**, **Item 7 20 → 25 of 40** (D25's target was ≥ 33, so Item 7 is MISSED). 65 pairs
  gained, 12 lost. Both strict `xfail` tests now pass and are ordinary tests.
- **The 12 regressions are 4 classes × 3 years and 3 of them are the audit penalising a *better*
  parse**: BAC Item 7A really is a 149-char cross-reference (the old 22 kB slice was that sentence
  plus the next section's tables); JPM Item 7 really is a 300-char pointer; GS/BAC/STT Item 7 are now
  the genuine MD&A (395–653 kB) and exceed the audit's 250 kB narrative cap; WFC `fs_notes` now
  resolves to the EX-13's real 859 kB notes instead of a 15 kB stub. **The audit thresholds were not
  touched** — that is an owner decision, raised at the gate.
- **Nothing is re-indexed.** The new parse exists only in a scratch dir; `data/parsed/`,
  `data/chunks/` and `data/qdrant/` are untouched, so Phase 3's retrieval numbers still describe the
  artifacts they were measured on. P4-00 step 4 (re-parse, re-chunk, re-index 13 tickers + NFLX,
  overnight) is gated on the owner.
- **`data/qdrant_v1_backup/`** exists: 163 MB, 25 collections, 14,557 points, verified equal to the
  live store. `QDRANT_PATH=<path>` repoints the store **by configuration, no code change** (pinned by
  T4-07 in `tests/unit/test_v0_index_isolation.py`). Gitignored.
- Reproduce the measurement: `python scripts/reparse_corpus.py --out-dir /tmp/after` →
  `python scripts/audit_sections.py --parsed-dir /tmp/after --out-dir /tmp/after_audit` →
  `python scripts/compare_section_audits.py reports/phase4/section_audit_before.json /tmp/after_audit/section_audit.json`.
  The re-parse is offline, makes zero LLM calls, and takes ~3 minutes for 40 filings.

## 1b. Phase 3 facts (measured, 2026-10-02)
- **The answer pipeline is `query.ask()` → `Outcome`.** `route()` (rules first; the model only for genuine ambiguity) → facts path (resolve → calc → deterministic template) or text path (catalog-scoped retrieval → structured `{found, answer}`) → `answering/abstain.py`. Dependencies are injected via `query.Deps`, so tests drive the real dispatcher.
- **G1–G4 are validators on `answering/outcome.py`, not conventions.** An Outcome with an untraceable number, a citation newer than `as_of`, a reasonless or citation-carrying refusal, or an outage shaped like a clarification raises on construction. Citation indices must be exactly 1..n (kills K1).
- **Routing costs nothing.** All 30 smoke questions route with **zero** LLM calls (`eval/phase3/smoke.py --dry-run`). In the live run only the 6 narrative questions called a model.
- **Smoke eval (P3-11): 30/30 expected status.** 0 look-ahead violations, 0 answered rows missing a fact citation or definition note, 0 refusals carrying a citation or figure. Latency p50 0.03 s (facts, no model) / p95 7.59 s (narrative generation). Transcripts: `reports/phase3/smoke/`.
- **Section quality (P3-00, D3-00): 238 of 429 audited slices usable; Item 1A missing from 17 of 39 filings.** Three candidate parser fixes ablated over 13 filings moved the corpus +1 of 143, so the parser is **unchanged** (byte-identical to `main`) and the two fixable classes are pinned as `xfail(strict=True)` in `tests/unit/test_parser_sections.py`.
- **Indexed collections: 25** (BLK_2023 added — 1,037 chunks, 286.5 s, 3.62 chunks/s). **`catalog.collection_name` is now populated for all 25**; before P3-06 it was null on all 483 rows, which would have left the text path's `as_of` scope empty on real data. Re-run `python -m ingestion.catalog_ingest --link` after any indexing.
- **D24 proved**: BLK's filing list spans CIKs [1364742, 2012383]; with `as_of=2025-01-01` the BLK scope is `[BLK_2023]` alone.
- Coverage: facts 88.8%, catalog 95.5%, llm 90.5%, answering 97.2%, routing/entities 95.5%, routing/periods 98.8%; lowest new file 73.6%.

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

## 6b. Phase 3 decisions
**D3-00** v1's section boundaries are documented, not fixed, in Phase 3: the ablation moved the corpus +1 of 143 slices and adopting a change would cost a re-index and the comparability of every retrieval number so far. D23's prohibition stands and widens — nothing user-visible may assert a section slice is complete, which constrains what a narrative citation may claim.

## 6a. Phase 2 decisions
D2-00 keep bge-base at 512 tokens, batch 8 (the >=4 chunks/s bar was a proxy; 4.2 h for the full corpus is still an overnight job) · D2-02 tiered candidate concepts plus "candidates that agree are not ambiguous" (took coverage from 69.7% to 81.3% and ambiguity from 90 rows to 0; **now part of spec 6.4 rule 3 rather than a deviation from it**) · D2-03 precision preference (D22), which took corpus-wide exactness from 99.1347% to 99.9787%.

## 6b. Phase 4 decisions
**D4-00** the boundary rewrite is adopted although Item 7 misses D25's target; the exit rule's literal
action (revert) is put to the owner with the numbers. **The audit's size thresholds stay as committed.**

Spec v1.5 also carries **D23** (v1's parsed statement sections are not trusted; `validation_status` is informational only and must never affect an answer — guarded by two tests in `test_facts_resolve.py`; P3-00 audits section quality) and **D24** (ingestion resolves filings through the catalog across every CIK a ticker has filed under; P3-06 uses BLK FY2023 as the proof case).

## 7. Open items
Closed at P2-13: P2-00(e) indexing (all 8 core tickers), T2-10's exact threshold (now met), the owner spot-check (20/20 OK), and the empty `.hygiene_local` (now filled; the T2-12 skip is gone).

Closed in Phase 3: the section-quality audit (now measured and decided as D3-00), the SEC error-page cache guard, and the BLK FY2023 text-index hole (D24, now indexed).

Carried into Phase 4:
1. **v1's section boundaries are broken for the text path too, and are staying that way in Phase 3 (D3-00).** Measured by `scripts/audit_sections.py` over 39 filings: 238 of 429 (filing, audited section) pairs usable, 122 missing, 24 heading-only, 45 oversized. **Item 1A Risk Factors is missing from 17 of 39 filings**, including AMZN, GS, JPM and NFLX; Item 3 Legal is usable in 3. Three candidate parser fixes were ablated and moved the corpus by +1 of 143 pairs, so the limit is documented instead (full reasoning and the ablation table in D3-00; raw counts in `reports/phase3/section_audit_ablation.json`). The two fixable defect classes are pinned as `xfail(strict=True)` in `tests/unit/test_parser_sections.py`, so a future fix cannot land silently. **Consequence for P3-05: a narrative citation names the filing and the section title only — nothing user-visible may assert that a section slice is complete (D23).**
2. **Follow-up questions no longer resolve.** `/chat` used to prepend conversation history to the question; with a rules-first router that turned a repeated question into a trend over every year the previous answer named. History now goes to the text generator only. A deterministic follow-up rewriter is proposed in the Phase 3 report section 9.
3. **Text coverage is far narrower than facts coverage**: 25 indexed collections against 483 catalogued filings. WFC, STT, TROW and IVZ have facts and **no** indexed text, so they answer numbers and refuse narrative (the refusal says which capability is missing). Four tickers x 3 years is about one overnight run at 3.62 chunks/s.
4. `.cache/company_tickers.json` still holds the SEC error page on disk. Nothing reads it (v1 uses `data/company_tickers.json`, which is valid) and both SEC fetchers now refuse to write or trust such a page (`ingestion/sec_cache.py`), so it is inert — but the owner may delete it.
5. **Four v1 modules are off the answer path but kept**: `routing/classifier.py`, `routing/resolver.py`, `generation/generator.py`, `generation/synthesizer.py`. `eval/phase0/run_baseline.py` patches into them and the baseline must stay reproducible (D21). Propose deleting in Phase 5.
4. Phase 0 scorer mislabels passing numeric answers `retrieval_found_llm_misread` (rewritten in P4-03).
5. Runner instruments only the generator; P4-04 must instrument every LLM role.
6. `llm/__init__.py` coverage 50% (shim); ignore unless it grows.
7. Rate limiter is per-process, in memory (fine for local-first).
8. The old project's handle/repo name belongs in the gitignored `.hygiene_local` only, never in a committed file.

## 8. Gotchas that already cost time (do not repeat)
- **Driving the real UI found four defects no test could.** Every unit and integration test called `ask()` directly, so none of them saw that `/chat` prepended conversation history to the routed question. Walk the UI by hand before claiming a phase is done.
- **A local dev server holds the Qdrant local-mode storage lock.** The first live smoke run returned `status=error` for all 6 narrative questions for that reason alone. Stop `uvicorn` before running anything that opens Qdrant.
- A long-running `make test` writes to `reports/$(PHASE)/tests/`; bump `PHASE` in the Makefile at the start of each phase or the previous phase's committed artifacts get overwritten.
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
