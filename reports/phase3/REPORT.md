# Phase 3 report — routing, answers, `as_of`, abstention, UI

Branch `phase-3-answers` · spec v1.5 · 2026-10-02 · 14 commits off `main` at `4013664`.

---

## 1. Summary

- **All 12 MUST tasks (P3-00 … P3-09, P3-11, P3-12) are done. P3-10 (mixed numeric + narrative) is OPTIONAL and was not built** — section 5.
- **All 12 T3 tests exist and pass.** `make test` → **1,052 passed, 2 xfailed, 0 skipped** offline with sockets blocked; ruff and hygiene clean.
- **Coverage meets every spec target**: facts 88.8%, catalog 95.5%, llm 90.5%, answering 97.2%, `routing/entities.py` 95.5%, `routing/periods.py` 98.8% against the 85% bar; the lowest new file is 73.6% against the 70% bar.
- **Smoke evaluation (P3-11): 30 of 30 questions land on the expected status** on the real stack. Zero look-ahead violations, every answered row carries a fact citation and a definition note, no refusal carries a citation or a figure.
- **Routing needs no model**: all 30 smoke questions route correctly with zero LLM calls; only the 6 narrative questions call a model at all, for generation.
- **G1–G4 are enforced by a type**, not by convention. An `Outcome` that breaks one cannot be constructed.
- **The section-quality audit (P3-00) found the text path's foundation is worse than D23 recorded**: 238 of 429 audited section slices are usable and Item 1A Risk Factors is missing from 17 of 39 filings. Three candidate parser fixes were implemented and ablated; the best moved the corpus by +1 of 143. **Decision D3-00: document the limit, do not change the parser in Phase 3** — section 4.
- **A latent hole was found and closed**: `catalog.filings.collection_name` was written by nothing, so all 483 rows were null and the text path's `as_of` scoping would have found no collections on real data.
- **D24 proved end to end**: BlackRock FY2023, filed under the pre-reorganisation CIK 1364742, is now parsed, indexed (1,037 chunks) and answerable.
- **Nine defects found and fixed during the phase**, four of them only visible by driving the real UI — section 6.

---

## 2. What changed

| Commit | Task | What |
|---|---|---|
| `09b3d2b` | P3-00a, T3-12 | `scripts/audit_sections.py` (+ `--self-test`), `scripts/reparse_corpus.py`, D3-00 |
| `1b1adba` | P3-00b | `ingestion/sec_cache.py`; `catalog/build.py` and `facts/documents.py` validate before caching |
| `e281952` | P3-00c | D3-00 in `docs/DECISIONS.md`, `docs/STATE.md`, Makefile `PHASE=phase3` |
| `5107c3f` | P3-01 | `answering/outcome.py` — `Outcome`, `Citation`, the 6.5 enums, G1–G4 as validators |
| `23fe1b6` | P3-02, T3-01 | `routing/entities.py`, `routing/periods.py` |
| `c7c168f` | P3-03, T3-02 | `routing/router.py` |
| `b7b285e` | P3-04, T3-05, T3-09 | `answering/facts_answer.py` |
| `859911e` | P3-07, T3-04 | `answering/abstain.py` |
| `a467cd4` | P3-05, T3-06, T3-10 | `answering/text_answer.py`, `generation/normalize.py` |
| `77f56bc` | P3-03, P3-08, T3-03/07/08 | `query.py` rewritten as a dispatcher; `api/app.py` to 6.5; `retrieval/retriever.py` takes an explicit collection list |
| `d975563` | P3-06, T3-11 | `ingestion/catalog_ingest.py`; `api/chat.py` off the classifier |
| `360ac5b` | P3-09 | `ui/index.html` |
| `b3ca861` | P3-11 | `eval/phase3/` smoke evaluation and transcripts |
| `ed296ea` | P3-12 | coverage for the CLI, filter chips, audit and re-parse tool |

New modules: `answering/` (4 files), `routing/router.py`, `routing/entities.py`, `routing/periods.py`, `generation/normalize.py`, `ingestion/sec_cache.py`, `ingestion/catalog_ingest.py`, `scripts/audit_sections.py`, `scripts/reparse_corpus.py`, `eval/phase3/`, `tests/fixture_world.py`.

`routing/classifier.py`, `routing/resolver.py`, `generation/generator.py` and `generation/synthesizer.py` are no longer on the answer path. They are **kept, not deleted**: `eval/phase0/run_baseline.py` patches `classify_and_ensure` and the Phase 0/Phase 1 baseline artifacts must stay reproducible (D21). Noted as an open item in section 6.

---

## 3. Tests

Exact command: `make test` → `pytest tests/unit tests/integration -m "not live and not slow" --disable-socket --allow-unix-socket --cov=. --cov-report=xml:reports/phase3/tests/coverage.xml --junitxml=reports/phase3/tests/junit.xml`

**Result: 1,052 passed, 2 xfailed, 0 skipped, 26.76 s.** Artifacts: `reports/phase3/tests/junit.xml`, `reports/phase3/tests/coverage.xml`.

| ID | Test | Where | Result |
|---|---|---|---|
| T3-01 | Entity and period parsing, ≥ 60 phrase cases | `test_routing_periods.py` (84), `test_routing_entities.py` (59) | **143 cases, pass** |
| T3-02 | Router with FakeLLM; deterministic cases never call the LLM; invalid output → `llm_bad_output` | `test_router.py` (70) | pass |
| T3-03 | `as_of` invariant, property test over random dates × tickers × intents | `test_pipeline_end_to_end.py` | pass (180 examples) |
| T3-04 | Every abstain reason reachable, right message, no numeric claim | `test_abstain.py` (62) | pass |
| T3-05 | Template golden tests, stable strings | `test_facts_answer.py` (21) | pass |
| T3-06 | K11 regression: all 17 Phase 0 refusal cases | `test_text_answer.py` | pass |
| T3-07 | API contract/snapshot for `/query` | `test_api_contract.py` (21) | pass |
| T3-08 | Offline end to end, one case per intent | `test_pipeline_end_to_end.py` | pass |
| T3-09 | Netflix thousands case | `test_facts_answer.py` | pass |
| T3-10 | Citation normalization (D20) | `test_citation_normalize.py` (22) | pass |
| T3-11 | Dual-CIK ingestion + negative control | `test_catalog_ingest.py` (17) | pass |
| T3-12 | Section audit: planted defects flagged, clean fixture passes, deterministic | `test_audit_sections.py` (7) | pass |

**The two xfailed are deliberate and `strict=True`** (`test_parser_sections.py`): they record the two fixable section-boundary defect classes under D3-00. Strict means that if someone fixes the parser, the unexpected pass fails the suite and forces the decision to be revisited — the code and the record cannot diverge silently.

### Coverage

| Target (spec section 10) | Bar | Measured |
|---|---|---|
| `facts/` | 85% | **88.8%** |
| `catalog/` | 85% | **95.5%** |
| `llm/` | 85% | **90.5%** |
| `answering/` | 85% | **97.2%** |
| `routing/entities.py` | 85% | **95.5%** |
| `routing/periods.py` | 85% | **98.8%** |
| all new code | 70% | lowest **73.6%** (`scripts/reparse_corpus.py`) |

Per new file: `abstain.py` 100.0, `outcome.py` 98.9, `periods.py` 98.8, `catalog_ingest.py` 97.9, `router.py` 97.8, `normalize.py` 97.1, `sec_cache.py` 96.2, `facts_answer.py` 95.7, `entities.py` 95.5, `text_answer.py` 94.8, `query.py` 82.4, `audit_sections.py` 77.2, `reparse_corpus.py` 73.6.

**One stated exception:** `eval/phase3/smoke.py` has no unit test and is not measured. It is an evaluation runner invoked as a command, consistent with `eval/phase1/bakeoff.py` in Phase 1; its output is the artifact that is reviewed. Flagged rather than hidden.

---

## 4. Metrics

### 4.1 Section-quality audit (P3-00a) — the headline finding

`python scripts/audit_sections.py` over 39 parsed filings × 11 audited sections = 429 pairs. Raw rows: `reports/phase3/section_audit.{json,csv}`.

| verdict | count |
|---|---|
| ok | **238** (55.5%) |
| missing | 122 |
| too_small | 24 |
| too_large | 45 |
| empty | 0 |

Per section (ok / missing / too_small / too_large, out of 39):

| section | ok | missing | small | large |
|---|---|---|---|---|
| `item_1_business` | 32 | 0 | 0 | 7 |
| `item_1a_risk_factors` | **19** | **17** | 0 | 3 |
| `item_1c_cyber` | 18 | 19 | 2 | 0 |
| `item_3_legal` | **3** | 23 | 13 | 0 |
| `item_7_mda` | 19 | 17 | 0 | 3 |
| `item_7a_market_risk` | 15 | 18 | 6 | 0 |
| `fs_notes` | 34 | 2 | 0 | 3 |
| `fs_income_stmt` | 28 | 2 | 3 | 6 |
| `fs_balance_sheet` | 28 | 3 | 0 | 8 |
| `fs_cash_flow` | 26 | 3 | 0 | 10 |
| `fs_equity` | 16 | 18 | 0 | 5 |

**This is worse than D23 recorded.** D23 was about statement sections; the narrative sections the text path depends on are equally affected. Item 1A Risk Factors — the primary focus section of the text path — is missing from **17 of 39 filings**, including 4 of the 8 evaluation-core tickers (AMZN, GS, JPM, NFLX).

Three defect classes, each diagnosed to a line:

1. **The TOC skip zone is a fraction, not a bound.** `parse_filing` ignores the first 15% of lines. Filers whose primary document *is* the whole annual report start Part I well inside it: JPM FY2024's real `Item 1A. Risk Factors.` is at line 229 of 6,897 (3.3%), GS FY2024's at 490 of 5,370 (9.1%), STT FY2024's at 405 of 3,715 (10.9%). The preceding section then absorbs the text — GS FY2024's Item 1 is 295,735 characters; JPM FY2024's `fs_income_stmt` is 1,525,489.
2. **The heading is destroyed before any scan.** Amazon renders each item heading as a two-cell table row (`Item 1A.` | `Risk Factors`); `_extract_tables` decomposes it, and the audit found **zero** occurrences of the heading anywhere in AMZN's parsed text.
3. **One sentinel per table** (already in the parser's own comments): AAPL/GOOGL/MSFT list every statement title in one index table, the annotation loop takes the first match and stops.

### 4.1a The ablation behind D3-00

Three candidate fixes, all 8 subsets, 13 filings (143 pairs), re-parsed with `scripts/reparse_corpus.py` and scored with the audit. Raw counts: `reports/phase3/section_audit_ablation.json`.

| variant | usable pairs / 143 | usable text-path pairs / 65 |
|---|---|---|
| none (committed parser) | 80 | 35 |
| A — extend `_ITEM_RECOVERY_PATTERNS` | 81 | 36 |
| B — anchor the Item-N patterns | 79 | 34 |
| C — cap the TOC skip zone at 250 lines | 80 | 36 |
| A+B | 80 | 35 |
| **A+C** | **81** | **37** |
| B+C | 79 | 35 |
| A+B+C | 80 | 36 |

Best case **+1 of 143**; anchoring alone is negative. Individual filings move substantially in both directions — C recovers GS's real 74,825-character Risk Factors section and takes GS's Item 1 from 295,735 to 152,354 characters, and in the same run STT loses Item 1C and Item 3 while STT's MD&A grows from 534,742 to 738,937.

The cause is structural: `_select_and_validate` keeps one occurrence per section id and then applies a greedy monotonic priority filter, so one new early candidate both misplaces its own section and blocks every lower-priority section after it.

**The parser was reverted to byte-identical with `main`** (verified: `git diff main -- ingestion/parser.py` is empty). Adopting a change would require re-parsing, re-chunking and re-indexing (1.6 h at D2-00's measured 1.86 chunks/s) and would make every retrieval number measured so far incomparable — for +1 pair. Full reasoning in **D3-00**.

### 4.2 Smoke evaluation (P3-11)

`python eval/phase3/smoke.py`, 30 questions, real catalog + real facts store + real Qdrant + real LLM. Transcripts: `reports/phase3/smoke/transcripts.jsonl`; table: `reports/phase3/smoke/RESULTS.md`.

| | |
|---|---|
| expected status reached | **30 / 30** |
| answered (facts) | 16 |
| answered_text | 6 |
| abstained | 7 |
| clarification_needed | 1 |
| look-ahead violations (G2) | **0** |
| answered rows missing a fact citation or definition note (G1) | **0** |
| refusals carrying a citation or a figure (G3) | **0** |
| latency p50 / p95 / max | 0.03 s / 7.59 s / 9.30 s |

The p50/p95 split is the shape of the design: a facts answer is 0.02–0.08 s because no model is involved; a narrative answer is 5–9 s because it is a generation call.

`--dry-run` routes all 30 **with zero LLM calls**, which is the rules-first claim measured rather than asserted.

Cases worth naming:

- **S04 Netflix (tags in thousands, K2)** → "$39.00 billion ($39,000,966,000)". Not millions.
- **S19 BlackRock FY2023 (D24)** → answered from `0000950170-24-019271`, CIK **1364742**, the pre-reorganisation registrant.
- **S22 "latest annual revenue" with `as_of=2023-12-31`** → FY2023, not FY2024.
- **S20 `as_of=2024-06-30`** → abstains `period_not_filed_as_of`, naming both the cutoff and the real filing date.
- **S23 narrative with `as_of=2024-06-30`** → cites AAPL FY2023 only; the FY2024 collection was never searched.

### 4.3 Catalog / index join (P3-06)

| | before | after |
|---|---|---|
| catalog filings with a `collection_name` | **0 of 483** | 25 |
| orphan collections (indexed, no catalog row) | — | 0 |
| indexed collections | 24 | **25** (BLK_2023 added) |

BLK FY2023: 14 sections parsed, 1,037 chunks, indexed in 286.5 s at 3.62 chunks/s, peak RSS 1.22 GB. The BLK scope is now `[BLK_2025, BLK_2024, BLK_2023]` across CIKs `[1364742, 2012383]`, and with `as_of=2025-01-01` it is `[BLK_2023]` alone.

---

## 5. Deviations from the spec, and decisions

1. **D3-00 — the parser is not changed in Phase 3.** P3-00 offers "fix the parser or document the limit". The limit is documented, with the ablation above as evidence, the two fixable classes pinned as strict `xfail`, and the proper fix scoped in section 9. Full entry in `docs/DECISIONS.md`.

2. **P3-10 (mixed numeric + narrative) not built.** Marked OPTIONAL in the spec; it needs owner approval and the phase's MUST work came first. Nothing blocks it: the router already produces one intent and the two paths already return the same `Outcome` type, so a mixed answer is a composition, not a new mechanism.

3. **`Citation` carries one field beyond 6.5: `cik`.** EDGAR's filing-index URL is keyed by (CIK, accession) and there is no accession-only permalink, so P3-09's "citation chips linking to the EDGAR filing" is not implementable without it. Optional — a citation without a CIK simply has no link.

4. **The filter chips (`tickers`, `years`) are applied inside `routing.router.route`**, not as a post-hoc patch. The intent depends on how many companies and periods there are, so a chip adding a second company has to be able to make the question a comparison. They cannot override an out-of-scope or sub-annual refusal; asserted by tests.

5. **Conversation history is no longer part of the routed question.** v1's `/chat` prepended prior turns. See defect 6 below. Consequence: follow-up questions ("and what about last year?") no longer resolve and ask for a company instead. A wrong answer is worse than a clarification; a deterministic follow-up rewriter is proposed in section 9.

6. **`logs/phase3/commands.log` is produced locally but not committed**, because `logs/` is gitignored — consistent with Phases 1 and 2. Spec section 13 requires it to be produced, which it is.

7. **The v1 modules off the answer path are kept, not deleted** (`routing/classifier.py`, `routing/resolver.py`, `generation/generator.py`, `generation/synthesizer.py`). `eval/phase0/run_baseline.py` patches into them and the baseline must stay reproducible (D21). Proposed for removal in Phase 5.

---

## 6. Defects found, fixed, open

### Found and fixed in this phase

| # | Defect | How it was found | Fix |
|---|---|---|---|
| 1 | `catalog.filings.collection_name` written by nothing — all 483 rows null, so the text path's `as_of` scoping would have found no collections on real data | building P3-06 against the real catalog | `ingestion.catalog_ingest.link_collections`, reporting orphans rather than skipping them |
| 2 | `catalog/build.py` wrote `resp.text` to the cache **before** `resp.json()`, so SEC's rate-limit page (served with HTTP 200) was cached and the exception raised afterwards, uncaught | P3-00b | validate before writing; validate cache entries on read too |
| 3 | `facts/documents.py` would cache the same error page as a filing document, failing much later inside iXBRL extraction | P3-00b | same guard |
| 4 | `test_growth_sign_follows_the_direction` asserted a strict sign on a value quantized to 2 places | Hypothesis found 200.01 → 200.02 (a real 0.005% rise that correctly prints 0.00%) | property restated as "never contradicts", with the strict claim asserted above the display precision |
| 5 | A filer reachable by alias but absent from `config.COMPANIES` displayed as its ticker — "Apple Inc. … more than NFLX" | T3-08 | fall back to the catalog's registered entity name |
| 6 | **`/chat` prepended conversation history to the question before routing.** The same question asked twice became a *trend over every year the first answer mentioned*, because the period parser read years out of the quoted history | driving the real UI by hand | history goes to the text generator as its own labelled section, never to the router |
| 7 | Refusals offered every year in the catalog ("I do have fiscal 1994, 1995, … 2023") | UI walkthrough | offer only answerable years, and distinguish text coverage from facts coverage |
| 8 | "I don't have **no period named** for Wells Fargo & Company"; and "for Apple Inc.." | UI walkthrough | readable period phrase; trim the trailing period from registered names |
| 9 | The generator writes `[1, 2]`, which the single-marker parser cannot see — the second source was dropped from the citation list while the text still pointed at it | live narrative query | split and counted in the D20 normalizer, inside every bracket style; a range (`[1-3]`) is deliberately left alone |
| 10 | A trend question answered with a growth calculation reported `query_type=computed` | P3-11 smoke run | 6.5's `query_type` describes the query, so the template takes it from the route |
| 11 | The dynamic "N companies indexed" hint used `textContent` on the whole hint row, deleting the as-of control on the first poll tick | UI walkthrough | the sentence got its own span |
| 12 | `scripts/audit_sections.py --self-test` — the negative control for T3-12 — was never run by `make test` | the coverage review for this report | `tests/unit/test_audit_sections.py` calls it in-process |

**Four of these (6, 7, 8, 11) were only visible by driving the real UI.** Every unit and integration test called `ask()` directly, so none of them could have caught the history-prefix bug. That is the lesson of the phase and it is in the explainer.

### Open

| # | Issue | Disposition |
|---|---|---|
| O-1 | v1's section boundaries: 238 of 429 slices usable; Item 1A missing from 17 of 39 filings | **D3-00** — documented, pinned as strict `xfail`, fix scoped in section 9. D23 forbids anything user-visible depending on them |
| O-2 | Follow-up questions no longer resolve (consequence of defect 6) | proposal in section 9 |
| O-3 | Text coverage (25 collections) is far narrower than facts coverage (483 catalogued filings); WFC, STT, TROW, IVZ have facts and no indexed text | the refusal now says which capability is missing; indexing more is a Phase 4/5 decision |
| O-4 | `.cache/company_tickers.json` still holds the SEC error page on disk | inert — nothing reads it and both fetchers now refuse to write or trust such a page. The owner may delete it |
| O-5 | The four v1 modules off the answer path | kept for baseline reproducibility (D21); propose removal in Phase 5 |
| O-6 | `eval/phase3/smoke.py` has no unit test | stated exception, section 3 |

---

## 7. Owner actions and questions

1. **Review and merge the pull request** `phase-3-answers` → `main` on GitHub.
2. **UI walkthrough** (the Phase 3 owner gate). `make up`, then try these ten in the UI:
   1. `What was Apple's revenue in fiscal 2024?` → verified facts, definition note, EDGAR-linked chip
   2. `What was Netflix's revenue in fiscal 2024?` → **$39.00 billion**, not millions (K2)
   3. `What was Apple's operating margin in fiscal 2024?` → a percentage with its formula shown
   4. `Compare Apple and Microsoft revenue in fiscal 2024` → both, and a note that the fiscal years end on different dates
   5. `How has Apple's revenue moved from 2023 to 2025?` → three years, oldest first
   6. `What risks does Apple disclose about its supply chain?` → from filing text, cited
   7. `What does BlackRock say about its business in fiscal 2023?` → answers from the old-CIK filing (D24)
   8. Set **As of** to `2024-06-30`, then ask question 1 again → refuses, naming both dates
   9. `What was Apple's Q3 2024 revenue?` → refuses, annual reports only
   10. `Should I buy Apple stock?` → refuses, out of scope
3. **A question for you (D3-00).** The parser decision is the one judgement call in this phase I would most like checked. The measured gain from the local fixes was +1 of 143 slices; I judged that not worth a full re-index and the loss of comparability. If you would rather pay that cost for the chance of a larger gain, the proper fix in section 9 is the thing to schedule, not the local patches.
4. **Optional:** delete `.cache/company_tickers.json` (O-4).

---

## 8. Gate checklist

- [x] P3-00 section audit with a negative control, SEC cache guard, `docs/STATE.md` refreshed
- [x] P3-01 typed `Outcome` / `Citation` with the 6.5 enums
- [x] P3-02 `routing/entities.py`, `routing/periods.py`, injectable clock
- [x] P3-03 rules-first router; `classifier.py` off the answer path
- [x] P3-04 deterministic facts templates
- [x] P3-05 text path: catalog scope with `as_of`, structured `{found, answer}`, D20 normalization, typed citations
- [x] P3-06 catalog-driven multi-CIK ingestion; BLK FY2023 indexed as the proof case
- [x] P3-07 single abstention gate
- [x] P3-08 `/query` takes `as_of`, returns 6.5, request ids, admin-only trace
- [x] P3-09 UI: as-of input, status badges, fact chips linking to EDGAR, definition line, refusal and error states
- [ ] P3-10 mixed numeric + narrative — **OPTIONAL, not built** (section 5)
- [x] P3-11 smoke evaluation, 30 questions, transcripts saved
- [x] P3-12 explainer and this report
- [x] All T3 tests pass; ruff clean; hygiene clean; no skipped test
- [x] Coverage targets met
- [x] Smoke eval completes with no crashes; transcripts show correct statuses
- [ ] **Owner UI walkthrough** — section 7, item 2

---

## 9. Proposals (out of scope; not built)

1. **Rewrite boundary selection (the D3-00 fix).** Replace one-occurrence-per-id plus greedy monotonic filtering with candidate scoring (is the line heading-shaped; is it followed by prose; does a sentinel point at it) and a global assignment over all occurrences — a longest-increasing subsequence on (line, Item priority). Then re-parse, re-chunk and re-index once. Estimated: a day of work plus a 4-hour overnight re-index. The audit already exists to measure the "after", and the two strict `xfail` tests already encode two acceptance criteria.
2. **A deterministic follow-up rewriter** (O-2): resolve "and what about last year?" by rewriting it against the previous turn's resolved entities and period *before* routing, rather than handing the router prose. Deterministic, testable, and it restores the feature defect 6 removed.
3. **Index the remaining bundled tickers** (O-3). WFC, STT, TROW and IVZ answer numbers and refuse narrative. Four tickers × 3 years at the measured 3.62 chunks/s is roughly one overnight run.
4. **Per-row sentinel insertion in `_annotate_fs_header_tables`** — the parser's own comments name this as the fix for AAPL/GOOGL/MSFT's merged statement sections. Subsumed by proposal 1 but smaller if done alone.
5. **Delete the four retired v1 modules** once the Phase 0 baseline no longer needs to be re-runnable (O-5).
6. **A response cache keyed on (question, as_of)** — spec P5-03 already lists it; worth noting the smoke run's p95 of 7.6 s is entirely generation.

---

## 10. Appendix — commands with exit codes

Every non-trivial command is in `logs/phase3/commands.log` (produced locally; `logs/` is gitignored, as in Phases 1 and 2). The ones that produced the numbers above:

| Command | Exit | Result |
|---|---|---|
| `make test` | 0 | 1,052 passed, 2 xfailed, 26.76 s |
| `make lint` | 0 | all checks passed |
| `make hygiene` | 0 | all checks clean, 233 tracked files |
| `python scripts/check_repo_hygiene.py --self-test` | 0 | every check demonstrably fails on a planted violation |
| `python scripts/audit_sections.py --self-test` | 0 | all planted defects detected; clean fixture passed; deterministic |
| `python scripts/audit_sections.py` | 1 | 429 pairs, 238 ok (exit 1 means "findings", which is expected on real data) |
| `python scripts/reparse_corpus.py --out-dir <scratch> --only <13 filings>` | 0 | ×8, one per ablation variant |
| `python -m ingestion.catalog_ingest --link --dry-run` | 0 | linked 24, orphan collections 0 |
| `python -m ingestion.catalog_ingest --link` | 0 | applied; after BLK_2023, linked 25 |
| `python -m ingestion.indexer --only BLK` | 0 | BLK_2023: 1,037 chunks, 286.5 s, 3.62 chunks/s, peak RSS 1.22 GB |
| `python eval/phase3/smoke.py --dry-run` | 0 | 30 routed, zero LLM calls |
| `python eval/phase3/smoke.py` | 0 | 30/30 expected status |
| `uvicorn api.app:app --port 8077` + UI walkthrough | — | numeric, narrative, as-of refusal and the EDGAR link verified by hand |

One re-run worth reporting (CLAUDE.md rule 15): the **first** live smoke run returned `status=error` for all 6 narrative questions. The cause was a local dev server still holding the Qdrant local-mode storage lock, not a code defect. The runner records the failure per row and continues rather than aborting, which is why the cause was visible; the run was repeated with the server stopped.
