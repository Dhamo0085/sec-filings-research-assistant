# STATE — living handoff for Claude Code sessions

Seeded from `reports/phase1/REPORT.md` and the Phase 0/Step 0 reports at the end of Phase 1 (2026-10-02);
carried into Phase 2 on 2026-10-02 against spec v1.4; updated at P2-13 closure, at P3-00, and at the
Phase 3 gate (2026-10-02) against spec v1.5; carried into Phase 4 and updated at the Phase 4 gate
(2026-10-03) against spec v1.6.
**Read this at the start of every session and after any `/compact`. Update it at every commit batch, at every gate, and *before* running `/compact`.** If this file and the code disagree, trust the code, then fix this file. Facts here that you have not re-verified are marked (seed).

## 1. Where we are
| Item | State |
|---|---|
| Repository | private, `github.com/Dhamo0085/sec-filings-research-assistant`; branch model: `main` + one `phase-N-<slug>` branch per phase, merged by the owner via PR |
| Step 0 | COMPLETE (PR #1 merged) |
| Phase 1 | COMPLETE; PR #2 merged into `main` as `6be4200` |
| Phase 2 | COMPLETE. PR #3 (12 pre-closure commits) and PR #4 (the P2-13 closure) are both merged into `main`; `main` is at `4013664`. All T2 tests pass and all T2-10 thresholds are met |
| Phase 3 | COMPLETE. PR #5 merged into `main` as `8ce9ae3` (`reports/phase3/REPORT.md`). All 12 MUST tasks done; P3-10 is OPTIONAL, moved to Phase 5 as P5-11 (D26) |
| Phase 4 | **REOPENED by the owner against spec v1.8** (2026-10-03). P4-00 to P4-10 are done (`reports/phase4/REPORT.md`); P4-02b and P4-11 are now done too; **P4-12 to P4-15 remain**. PR #6 is open on `phase-4-evaluation` and must not be merged |
| Current task | **P4-12 tooling is built, rehearsed and committed; the full overnight run is the owner's to start** (see section 1d). P4-13, P4-14 and P4-15 wait for the owner's go-ahead |
| Tests | `make test` → **1,327 passed, 0 failed, 0 skipped** (1,277 before the P4-12 tooling) (and the same under CI's isolation: no `.env`, fresh `HOME`); ruff clean; `python -m eval.mini_eval` 9/9 with 0 look-ahead. Artifacts in `reports/phase4/tests/` (`PHASE` in the Makefile was still `phase3` at the Phase 4 start and overwrote Phase 3's committed junit/coverage once — bumped in `63516cb`) |

## 1b. Phase 3 facts (measured, 2026-10-02)
- **The answer pipeline is `query.ask()` → `Outcome`.** `route()` (rules first; the model only for genuine ambiguity) → facts path (resolve → calc → deterministic template) or text path (catalog-scoped retrieval → structured `{found, answer}`) → `answering/abstain.py`. Dependencies are injected via `query.Deps`, so tests drive the real dispatcher.
- **G1–G4 are validators on `answering/outcome.py`, not conventions.** An Outcome with an untraceable number, a citation newer than `as_of`, a reasonless or citation-carrying refusal, or an outage shaped like a clarification raises on construction. Citation indices must be exactly 1..n (kills K1).
- **Routing costs nothing.** All 30 smoke questions route with **zero** LLM calls (`eval/phase3/smoke.py --dry-run`). In the live run only the 6 narrative questions called a model.
- **Smoke eval (P3-11): 30/30 expected status.** 0 look-ahead violations, 0 answered rows missing a fact citation or definition note, 0 refusals carrying a citation or figure. Latency p50 0.03 s (facts, no model) / p95 7.59 s (narrative generation). Transcripts: `reports/phase3/smoke/`.
- **Section quality (P3-00, D3-00): 238 of 429 audited slices usable; Item 1A missing from 17 of 39 filings.** Three candidate parser fixes ablated over 13 filings moved the corpus +1 of 143, so the parser is **unchanged** (byte-identical to `main`) and the two fixable classes are pinned as `xfail(strict=True)` in `tests/unit/test_parser_sections.py`.
- **Indexed collections: 25** (BLK_2023 added — 1,037 chunks, 286.5 s, 3.62 chunks/s). **`catalog.collection_name` is now populated for all 25**; before P3-06 it was null on all 483 rows, which would have left the text path's `as_of` scope empty on real data. Re-run `python -m ingestion.catalog_ingest --link` after any indexing.
- **D24 proved**: BLK's filing list spans CIKs [1364742, 2012383]; with `as_of=2025-01-01` the BLK scope is `[BLK_2023]` alone.
- Coverage: facts 88.8%, catalog 95.5%, llm 90.5%, answering 97.2%, routing/entities 95.5%, routing/periods 98.8%; lowest new file 73.6%.

## 1d. Phase 4 reopening: spec v1.8, P4-02b and P4-11 (2026-10-03)
- **Spec v1.8** adds **D27** (the reranker decision rule, pre-registered before the data is
  seen), **D28** (the one fix-and-rerun cycle goes to router defects A–C; the deferred re-index is
  scheduled as P4-12), **D29** (owner-only fields), tasks **P4-02b** and **P4-11 to P4-15**, and
  tests **T4-08 to T4-12**. The owner gate now requires the narrative rating to come from the
  **post-re-index** run. **CLAUDE.md rule 17** carries D29 into the standing rules.
- **P4-02b DONE.** `scripts/make_gold_verification_assist.py` →
  `reports/phase4/gold_verification_assisted.csv` (committed). **37 of 37 rows located, all
  `found_exact`, 25 of 25 core rows**; re-running produces a byte-identical file. **This is the
  sheet to sign**, not `gold_verification.csv` — D4-01 explains why it is a second file, and the
  script refuses `--out == --in`. `verdict`, `owner_note` and `verified_by` are untouched and
  `assert_unchanged()` proves it per row before writing.
  - Four real defects were found by *reading* the first output: Microsoft sets statement headings
    with letter-spacing ("INC OME STATE MENTS"), Bank of America's Consolidated Balance Sheet
    carries its title inside the table, Amazon prints capex as "( 82,999 )", and computed operands
    had no statement hint so Netflix's revenue landed on the MD&A "Streaming revenues" line.
  - The spot-check sample (seed 20261003) is `N-NFLX-OPERATINGINCOME-2024`,
    `C-AAPL-DEBTEQUITY-2024`, `C-AAPL-NETMARGIN-2024`, `C-MSFT-REVENUECAGR-2025`,
    `N-GOOGL-RDEXPENSE-2024`.
  - **Known limit:** the located line is the best-scoring printed occurrence. For a few rows an
    MD&A summary table and the statement itself both carry the figure under the same heading
    words; `statement_to_check` and `assist_link` remain the owner's check.
- **P4-11 DONE** (D4-02). All three defects fixed, each from a failing test; **12 of 12 computed
  gold items and 5 of 5 facts-path abstention items now score correct** against the real stores.
  The ratio defect was *not* in the router — `query._compute` paired two metrics only for a
  margin, so a two-metric ratio answered with the numerator alone. Longest-match aliasing was
  already in place; the cash-flow phrase was simply missing from `facts/concepts.yaml`.
  `period_not_covered` is now decided from `FactsStore.has_facts()`, because `facts_built_at`
  records that the build *ran* (four flagged submissions hold no facts).
- **Still open from P4-11's scope:** defect D (the MSFT segments over-refusal) is deferred to
  after the re-index by D28.
- **Carried into P4-12 and after:** the three remaining owner-facing gaps are unchanged — sign the
  assisted sheet, rate 15 narrative answers **from the post-re-index run** (P4-14 builds the
  sheet), and the re-index itself.

### Pre-P4-12 baseline, measured 2026-10-03 (P4-12 requires the before/after)
- `data/qdrant` — **25 collections, 14,557 points**. `data/qdrant_v1_backup` — **25 collections,
  14,557 points** (identical). Both must still read 14,557 after the re-index; the new index goes
  to a **new directory**.
- On disk: `data/parsed` 40 filings, `data/chunks` **36** files / **36,875 chunks** —
  **NFLX_2023..2025 and TSLA_2025 have no chunk file** although NFLX is indexed (it arrived
  through auto-ingest). A re-chunk from a fresh parse fixes that.
- `PARSED_DIR`, `CHUNKS_DIR` and `QDRANT_PATH` are all honoured as environment variables
  (verified), so the whole re-index is a configuration change with no code change.
- **`run_ingestion.py --skip-download` is NOT a safe parse step for P4-12.** It parses
  `data/raw/manifest.json`, which has **39** entries and is missing **BLK FY2023** — the filing
  D24 exists to cover. `scripts/reparse_corpus.py --parsed-dir data/parsed` works from the
  existing parses and covers all **40**.
- **P4-12 has no chunk-step CLI.** `chunk_all_documents()` is a library function with no
  `__main__`, and `run_ingestion.py` only reaches it through the manifest path above.
  **CLOSED 2026-10-03** — `scripts/rechunk_corpus.py` is that CLI, and
  `scripts/reindex_v2.py` is the one resumable command that drives all three steps.
- Disk: 16 GiB free (92% full; 19.3 GiB when re-measured 2026-10-03). The run needs roughly 0.5 GB.

### P4-12 tooling, built and rehearsed 2026-10-03 (the owner runs the full job)
- **The one command** (run it in a terminal, plugged in, with no server up):
  `caffeinate -i .venv/bin/python scripts/reindex_v2.py`
  It writes `data/parsed_v2`, `data/chunks_v2`, `data/qdrant_v2`,
  `reports/phase4/reindex_run.json` and `reports/phase4/reindex_run.log`, then runs T4-09.
  **Re-run the identical command to resume**; `--dry-run` is a seconds-long preflight.
- **Scope is 39 filings / 13 tickers** (the 12 bundled + NFLX; BLK_2023 included, TSLA_2025
  excluded). Preflight refuses if BLK_2023 or any NFLX year is missing from `data/parsed`.
- **Measured on the rehearsal** (AAPL_2025 + AAPL_2024, scratch dirs, real embedder):
  **2.2–2.4 chunks/s, peak RSS 1.0–1.1 GB**. Extrapolating to ~37–38k chunks: **about 4.5–5
  hours**. A resumed run that has nothing to do finishes in **0.7 s**.
  **Not measured at full scale**: Qdrant local mode keeps every existing collection in RAM
  (P2-00d), so RSS will climb above the rehearsal's 1.1 GB as the 39th collection is written.
  P2-00d measured 1.34 GB at batch 8 against a full existing index, so there should be room on
  8 GB — but the real number only comes from the real run, and the report records it.
- **Refuses to start** on: an output path equal to, inside, or containing any of
  `data/parsed`, `data/chunks`, `data/qdrant`, `data/qdrant_v1_backup`; a held Qdrant lock on
  the target, the live store or the backup; less than 2 GB free; a missing required filing.
  Each refusal was demonstrated with a planted fault (CLAUDE.md rule 15).
- **Resume is read from disk, not a state file.** Parse: readable with sections. Chunk: right
  `doc_id` **and** not older than the parse — `doc_id` is `f"{ticker}_{fiscal_year}"`, so it is
  unchanged by a re-parse and cannot witness one on its own (D4-03, point 2). Index: the
  indexer's own point-id skip, after dropping any collection holding points today's chunk files
  do not claim (`Chunk.chunk_id` is a fresh uuid4 per chunking run).
  Verified by `kill -9` at 96 of 192 points: the resumed run re-embedded 96, not 192.
- **`data/qdrant` and `data/qdrant_v1_backup` are never opened with a client.**
  `count_points_readonly()` reads `meta.json` plus each collection's `storage.sqlite` over a
  read-only URI; cross-checked against the client on the backup — 25 collections / 14,557
  points and AAPL_2024 = 197, both ways. All four protected directories are fingerprinted
  before and after and the run fails if one moved.
- **The catalog relink is dry-run inside the re-index** and is the activation script's last
  step instead (D4-03): writing `collection_name` while the new store is staged would point the
  live `as_of` scope at collections the live store does not hold.
- **Activation is separate and not run**: `.venv/bin/python scripts/activate_reindex.py`
  prints the plan; `--yes` performs six renames (the three live dirs → `data/<name>_retired_<stamp>`,
  then the three `_v2` dirs → live) and relinks the catalog; **undo is one command**,
  `.venv/bin/python scripts/activate_reindex.py --undo`, driven by a journal written before and
  after every rename so a crash mid-swap is still reversible. Nothing is ever deleted.
  It refuses unless `reindex_run.json` reports `status=ok` with T4-09 passed.
- **Tests**: `tests/unit/test_reindex_v2.py` (35) and `tests/unit/test_activate_reindex.py` (15),
  every guard paired with a planted-fault control. Suite is **1,327 passed, 0 failed**; ruff clean.

## 1c. Phase 4 facts (measured, 2026-10-03)
- **Variant results, D21 paired subset (69 of 80 items)**: V3 **60/69 (87.0%, CI 77.0–93.0)** ·
  V2 50/69 (72.5%) · V1 41/69 (59.4%) · V0 **19/69 (27.5%)**. Look-ahead violations: **V3 = 0, V2 = 4**.
  Numeric: V3 17/18, V1 12/18, V0 11/18. Abstain: V3 9/10, **V0 0/10** (v1 cannot refuse).
  **36 of V0's answers carry citations with no accession and no filing date.**
- **The 11 excluded items** name BAC, IVZ, STT, TROW or WFC filings, or JPM FY2022 — in the facts
  store, not in the text index.
- **Rate limits**: 13 V1 items and 2 each in V2/V3 remain `error` after a paced re-run
  (`--retry-rate-limited --sleep 25`). V1's 59.4% is a floor.
- **Cost/latency**: V3 16 model calls over 80 questions (all narrative; the facts path calls none),
  p50 **0.03 s**, p95 9.0 s. V1 60 calls, p50 5.2 s. V0's token counts are **not captured** (it runs
  in a worktree subprocess and v1's client is not instrumented).
- **Retrieval ablations** (no generation tokens, 15 narrative + 22 numeric): dense-only 12/15,
  MRR **0.747**, **1.6 s**; the shipped `hybrid_rerank_focus` 12/15, MRR 0.717, **206 s**;
  `hybrid_rerank` *loses* a hit (11/15). **Parent context off drops number-in-context 95% → 82%**
  with section hit unchanged. n=15, so the intervals are wide — recorded, not acted on.
- **`number_in_context` is 95% on every arm**: when the text path gets a number wrong it is almost
  never because retrieval missed the figure.
- **P4-00**: usable pairs 245 → **298 of 440**; Item 1 and Item 1A **40/40**; Item 7 25/40 (target 33,
  miss accepted, D4-00). Both strict `xfail` tests now pass as ordinary tests.
- **D25 old-vs-new index arm**: both stores hold the same pre-rewrite parse (re-index deferred), and
  the arms agree — a check on the backup, not a result about the parser. One unexplained difference:
  `hybrid` MRR 0.578 vs 0.544 on byte-identical content, most likely RRF tie-break order.
- **Open product defects** — **three of the four are now fixed by P4-11 (see section 1d); the
  figures below describe the run before that fix and will change in P4-12's re-run**: the router does not
  route "ratio of X to Y" or "A less B" though `facts/calc.py` implements both; "cash flow from
  operating activities" resolves to `cash_and_equivalents`; `X-PERIOD-NOT-COVERED` refuses with the
  wrong reason; one over-refusal on MSFT segments.
- **CI's first run failed 15 tests that pass locally** — and all three causes were real:
  `generation/generator.py` called `tiktoken.get_encoding` **at import**, which downloads the BPE
  file on a cold machine; seven `test_facts_documents.py` tests needed `edgar_email` from the
  developer's `.env` (T1-05 quietly did not hold); and `test_cli_runs_offline` was reading the real
  `.cache/edgar`, because `EdgarFetcher`'s `cache_dir` default was bound at import so
  `monkeypatch.setattr("catalog.build._CACHE_DIR", ...)` did nothing. All fixed, with a negative
  control on the last. **`./scripts/ci_local.sh` cannot catch this class** — it runs in the repo with
  `.env` present and caches warm. Reproduce CI's isolation with:
  `cd "$(mktemp -d)" && PYTHONPATH=<repo> HOME="$(mktemp -d)" <repo>/.venv/bin/python -m pytest <repo>/tests/unit <repo>/tests/integration -m "not live and not slow" --disable-socket --allow-unix-socket`
- **`eval/mini_eval.py`** is what CI enforces: real gold items, real pipeline, committed fixtures,
  no network — numeric+computed 9/9, look-ahead 0. The BAC fixture gained `us-gaap:Liabilities` so
  the 100% threshold did not have to be weakened around a hole in the test data.


### P4-00 section-boundary rewrite, in detail
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
**D4-00** the boundary rewrite is adopted although Item 7 misses D25's target (owner-confirmed
2026-10-03, so D25's exit rule was **not** applied). **The audit's size thresholds stay as committed** —
12 of the 15 Item 7 misses are cap-bound, not boundary defects, and that is reported rather than fixed.
P4-00 step 4 (the overnight re-index) is **deferred** until P4-01–P4-04 exist; until it runs,
`data/parsed/`, `data/chunks/` and `data/qdrant/` are the Phase 3 artifacts and Phase 3's retrieval
numbers still hold.

Spec v1.5 also carries **D23** (v1's parsed statement sections are not trusted; `validation_status` is informational only and must never affect an answer — guarded by two tests in `test_facts_resolve.py`; P3-00 audits section quality) and **D24** (ingestion resolves filings through the catalog across every CIK a ticker has filed under; P3-06 uses BLK FY2023 as the proof case).

## 7. Open items
Closed at P2-13: P2-00(e) indexing (all 8 core tickers), T2-10's exact threshold (now met), the owner spot-check (20/20 OK), and the empty `.hygiene_local` (now filled; the T2-12 skip is gone).

Closed in Phase 3: the section-quality audit (now measured and decided as D3-00), the SEC error-page cache guard, and the BLK FY2023 text-index hole (D24, now indexed).

Closed in Phase 4: the section-boundary defects (rewritten in P4-00; both strict `xfail` tests now
pass — D3-00 is superseded by D4-00), the Phase 0 scorer (replaced by `eval/scorers.py`), and
generator-only instrumentation (`RecordingLLM` records every role).

Carried into Phase 5:
1. **Two owner gates are outstanding and every headline figure is provisional until they close**:
   sign `reports/phase4/gold_verification.csv` (37 rows, 25 core), and rate 15 sampled narrative
   answers. A rating-sheet generator does not exist yet — proposed in the Phase 4 report section 9.
2. **The overnight re-index (P4-00 step 4) is deferred by the owner.** Until it runs, `data/parsed/`,
   `data/chunks/` and `data/qdrant/` hold the **pre-rewrite** parse, so every narrative number in
   Phase 4 describes the old parser and D25's old-vs-new comparison cannot be made. It also closes
   O-3: WFC, STT, TROW and IVZ would gain text coverage.
3. **Open product defects, none fixed** (P4-07's one fix-and-rerun cycle was not spent; each is 1–2
   items of 80): the router does not route "ratio of X to Y" or "A less B" although `facts/calc.py`
   implements both; "cash flow from operating activities" resolves to `cash_and_equivalents`
   (`wrong_metric`); `X-PERIOD-NOT-COVERED` refuses with `metric_not_found_in_filing`; and MSFT
   segments is an over-refusal. Defects 1 and 2 are the right first work for Phase 5.
4. **Free-tier limits bound what was measured**: 13 V1 items and 2 each in V2/V3 are still `error`
   after a paced re-run. V1's 59.4% is a floor, not its capability.
5. **The ablation says the cross-encoder earns nothing** (12/15 → 11/15 section hit, 200 s) and that
   dense-only beats the shipped default on MRR. n=15 narrative items, so the intervals are too wide
   to act on — a larger narrative set is proposed before touching the pipeline.
6. **Text coverage is still far narrower than facts coverage**: 25 indexed collections against 483
   catalogued filings. 11 of 80 gold items are outside the D21 paired subset for this reason.
7. **V0's token accounting is absent**: it runs as a worktree subprocess and v1's client is not
   instrumented. Its latencies are comparable, its token counts are not available.
8. **Four v1 modules are off the answer path but kept**: `routing/classifier.py`,
   `routing/resolver.py`, `generation/generator.py`, `generation/synthesizer.py`. `eval/phase4/run_v0.py`
   drives them from the `v1-baseline` tag, and the V0 baseline must stay reproducible. P5-12 deletes
   them after V0 is frozen in `reports/final/`.
9. **Follow-up questions do not resolve** ("and last year?"). History reaches the text generator as
   context only, deliberately. P5-10.
10. `.cache/company_tickers.json` still holds the SEC error page on disk; inert (both fetchers refuse
    to write or trust such a page) but the owner may delete it.
11. `llm/__init__.py` coverage 50% (shim); ignore unless it grows.
12. Rate limiter is per-process, in memory (fine for local-first). Qdrant local mode takes an
    exclusive lock, so one evaluation at a time — this cost a run once (section 6 defect 7).
13. The old project's handle/repo name belongs in the gitignored `.hygiene_local` only, never in a
    committed file.

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
`make test` · `make lint` · `./scripts/ci_local.sh` (the CI mirror) · `python -m eval.mini_eval` ·
`python -m eval.gold.build_gold --check` · `python -m eval.runner --variant V3 --report-only` ·
`python -m eval.ablations` · `python scripts/make_gold_verification_assist.py` (P4-02b; offline, ~30 s) · `python eval/phase4/run_v0.py --worktree /Users/dhamo_85/Downloads/FinancialRAG_v1_baseline` (an existing v1-baseline worktree; the driver symlinks `.env` into it and never reads the key) ·
P4-12: `caffeinate -i .venv/bin/python scripts/reindex_v2.py` (the overnight re-index; re-run verbatim to resume, `--dry-run` for a seconds-long preflight) · `.venv/bin/python scripts/activate_reindex.py` (plan only; `--yes` swaps, `--undo` reverses) ·
`make setup` · `make hygiene` · `python scripts/check_repo_hygiene.py --self-test` · `make catalog` · `make facts` · `make up` · `python scripts/smoke.py --base-url http://localhost:8000` · resumable indexing: `python eval/phase1/index_per_ticker.py --only AAPL,AMZN` (run from the v1 worktree) · section audit: `python scripts/audit_sections.py` then `python scripts/compare_section_audits.py <before> <after>`.
