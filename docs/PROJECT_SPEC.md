# Financial_RAG v2 — Project Specification

Version 1.9 · Authoritative for Phases 1–5 · Changes require owner approval and a `docs/DECISIONS.md` entry.

---

## 0. How to use this document

- `CLAUDE.md` holds the standing rules; this file holds the design, contracts, tests, and phase tasks.
- Every task has an ID (`P2-05`), every test an ID (`T2-05`). Reports reference these IDs.
- Status words: **MUST** (required for the gate), **SHOULD** (do unless blocked; explain if skipped), **OPTIONAL** (only with owner approval).

---

## 1. Product definition

**One line:** a research assistant over SEC 10-K filings whose numbers are traceable to filing facts, whose answers respect "what was public on date X", and which says "I can't answer that" (with a reason) instead of guessing.

**Primary use case:** retail-trading research questions: "What was X's net income in FY2024?", "How did X's operating margin change 2022→2024?", "Compare X and Y R&D spend", "What risks does X disclose?", "As of March 1, 2024, what was X's latest annual revenue?"

### 1.1 Guarantees (each is tested)
- **G1 Traceable numbers.** Every number in a `status=answered` response comes from a stored Fact or the Calculator, and cites filing, period, concept.
- **G2 No look-ahead.** With `as_of=D`, no cited filing has `filing_date > D`.
- **G3 Abstain, don't guess.** Missing evidence, ambiguity, or unsupported scope produces `status=abstained` with a reason code.
- **G4 Errors are visible.** Dependency failures (LLM, DB) produce `status=error`, never a misleading "clarification" or empty answer.
- **G5 Reproducible evaluation.** Every reported metric traces to a file under `reports/` produced by a command in the repo.

### 1.2 Scope
- Annual reports (10-K, and 10-K/A amendments) of US SEC filers, USD reporting.
- Bundled tickers: AAPL, MSFT, GOOGL, AMZN, JPM, WFC, BAC, GS, BLK, STT, TROW, IVZ, NFLX. Other filers via on-demand ingestion (text path always; facts path when extraction succeeds; ambiguity means abstain).
- **Delivery:** local-first, free services only (D17, D18).
- **Non-goals:** investment advice, forecasts, prices, real-time data, trade execution, quarterly filings (10-Q). A quarterly or non-annual period request abstains with `unsupported_period_type`.

---

## 2. Engineering principles
1. Correctness before coverage. 2. Deterministic where possible; LLM only where language understanding is needed.
3. Everything testable offline; the live LLM is an integration concern, not a unit-test dependency.
4. Fail closed (auth) and fail loud (errors). 5. Small modules with typed interfaces. 6. Every claim in a README is backed by a log file.

---

## 3. Current state (from Phase 0; evidence in `reports/phase0/REPORT.md`)

**Provenance.** The code started from an earlier MIT-licensed prototype and now lives in this new repository with fresh history (D17). The original hosted deployment belongs to a third party: it was probed read-only in Phase 0 and is out of scope (not measured further, not touched, not linked).

Existing, working: EDGAR download, iXBRL-aware HTML parser with section detection and EX-13 merge, chunker, ONNX embeddings (dense bge-base + BM25), Qdrant (one collection per `{TICKER}_{fiscal_year}`), RRF fusion, cross-encoder rerank, per-focus boosts, parent-section context, LLM classifier + decomposer + synthesizer (Groq only), FastAPI + chat UI, Dockerfile, on-demand ingestion, chat history.

Measured defects: see section 17.2. Highlights: classifier swallows all exceptions (fail-open); admin guard is fail-open (the original third-party deployment ran with no token); citation renumbering collides; years hard-coded to 2023–2025; "assume millions" prompt breaks thousands-reporting filers; iXBRL facts are discarded at parse time; `filing_date` is not available at query time; the refusal regex has recall 0.556.

Validated for reuse: iXBRL extraction matched SEC `companyfacts` on 105/105 (filing, concept) pairs; concept **selection** (not extraction) is the risk; Wells Fargo's contexts and facts are split across the 10-K wrapper and the EX-13 exhibit and must be pooled.

---

## 4. Decisions register (see `docs/DECISIONS.md` for rationale; IDs are stable)

| ID | Decision | Status |
|---|---|---|
| D1 | Facts come from iXBRL in locally stored filings, pooled per submission. SEC `companyfacts` is an offline validation oracle only. | DECIDED |
| D2 | `as_of` is applied at filing-selection time via the catalog (no re-indexing, no Qdrant payload change). | DECIDED |
| D3 | Keep the v1 text path; its components are ablated in Phase 4, not rewritten. | DECIDED |
| D4 | Gold numeric answers come from an oracle independent of the code under test; the owner hand-verifies a stratified sample; verification level is recorded per item. | DECIDED |
| D5 | "Revenue" returns the filer's own headline total net revenue (e.g. JPM total net revenue; BAC total revenue, net of interest expense; GS total net revenues), always naming the definition used, with alternative definitions available on request. Per-filer overrides are curated and validated. | DECIDED (owner may override) |
| D6 | Fiscal labeling uses the company's own label (`dei:DocumentFiscalYearFocus`, verified on the corpus; fallback `period_end.year`) and always shows the period-end date. A calendar-year phrasing is mapped via `period_end`. | DECIDED (owner may override) |
| D7 | Dependency failures are typed errors (`status=error`, HTTP 503). No fallback masks them. | DECIDED |
| D8 | Facts-path answers are produced by deterministic templates. LLM paraphrase is optional and off by default. | DECIDED |
| D9 | Status taxonomy: `answered` (verified facts), `answered_text` (text-path answer, not fact-verified), `abstained`, `clarification_needed`, `error`. | DECIDED |
| D10 | Annual filings only in v2. | DECIDED |
| D11 | LLM access uses **free tiers only** (no paid keys): an OpenAI-compatible, multi-provider client with ordered failover, per-role model choice, and a capability registry. Embeddings and reranking stay local (ONNX). Provider and model are pinned per evaluation run and recorded. | DECIDED |
| D12 | SQLite for catalog and facts; Qdrant for text (local and remote supported). | DECIDED |
| D13 | Admin auth: `X-Admin-Token` header, constant-time compare, fail-closed (unset token disables admin routes). | DECIDED |
| D14 | Restatements: for a requested period, use the most recent filing eligible at `as_of` that contains it; flag `restated=true` when it differs from the original filing's value. | DECIDED |
| D15 | Facts coverage ≥ text coverage: facts for up to 5 most recent 10-Ks per bundled ticker (`FACTS_FILINGS_PER_COMPANY`), text index per existing `filings_per_company`. | DECIDED |
| D16 | Chat history is runtime data: untracked by git, per-session secret, retention limit. | DECIDED |
| D17 | Provenance: v2 is a **new, personal portfolio repository** with fresh history, created by Step 0 (`docs/BOOTSTRAP.md`) through the GitHub CLI. The code started from an earlier MIT-licensed prototype; `LICENSE` and a one-paragraph `NOTICE` acknowledge that. No data, deployments, secrets, or links from the old project are carried over. | DECIDED |
| D18 | **No paid services.** Delivery is local-first (`make up`, Docker Compose) plus a recorded demo. A hosted demo is optional and only on a verified-free platform (O2). | DECIDED |
| D19 | Commits made by Claude Code carry a `Co-Authored-By: Claude` trailer: the repository states openly that development is AI-assisted and owner-supervised, and the owner reviews and merges every phase PR. | DECIDED |
| D20 | Citation markers are normalized at the generator boundary: fullwidth/ideographic brackets (for example `【1】`) become ASCII `[1]`. Every normalization is counted and recorded in the trace, so format drift stays visible. With normalization in place, gpt-oss models may serve in the tail of the generator failover list; the primary generator stays a Gemini Flash-Lite entry. | DECIDED |
| D21 | Baseline scope: the partial 9-of-25 v1 baseline is accepted for now. The evaluation-core tickers (AAPL, AMZN indexed; MSFT, JPM, GOOGL, NFLX, BLK, GS next) are indexed with v1's embedding model before Phase 3. V0 in Phase 4 runs on the gold items whose corpus is indexed, with N reported explicitly. The embedding model is changed only if P2-00 proves it infeasible, and then the change is declared in every results table. | DECIDED |
| D22 | Phase 2 closure: implement the precision-preference fix (when a filing tags one concept twice and the values agree within the coarser declared `decimals`, keep the instance with the larger `decimals`), rebuild the facts store, re-run the oracle cross-check, and report **before and after** corpus-wide exactness. Values that disagree beyond the coarser precision are never merged. T2-10 stays "not met" in the record if the re-measurement still falls short. | DECIDED |
| D23 | v1's parsed statement sections are unreliable (one filer's income-statement section is 106 characters of heading, another's is empty, another's is over 1 MB). Nothing user-visible may depend on them. `validation_status` is informational only and never affects an answer or its confidence wording. Phase 3 first measures section quality for the narrative sections the text path uses (P3-00), then fixes or documents. | DECIDED |
| D24 | Ingestion resolves a ticker's filings through the catalog, covering every CIK the ticker has filed under. This closes the BlackRock FY2023 hole (filed under the old CIK; in the facts store, absent from the text index). | DECIDED |
| D25 | Phase 3 outcome (D3-00 revisited): the local parser patches were rightly rejected, but the narrative text path cannot be demonstrated on a parser that loses Item 1A in 17 of 39 filings. Phase 4 therefore opens with a **time-boxed rewrite of section-boundary selection** (P4-00): candidate scoring plus a global assignment instead of one-occurrence-per-id with a greedy filter. The audit tool and the two strict `xfail` tests are the acceptance criteria. If the targets are not met within the time box, revert to the current parser and keep D3-00 as the final position. A backup of the v1 index is kept so V0 stays a true v1 measurement. | DECIDED |
| D26 | Product-polish items move to Phase 5: the deterministic follow-up rewriter (SHOULD) and mixed numeric + narrative answers (OPTIONAL, formerly P3-10). Retired v1 modules are deleted only after V0 is frozen. | DECIDED |
| D27 | Reranker decision rule, **pre-registered before the data is seen**: after the re-index and the expanded narrative set (at least 40 items), make the cross-encoder **off by default** if, for hybrid without rerank versus hybrid with rerank, (a) section hit@5 is within 5 percentage points or higher, (b) MRR is not lower by more than 0.03, and (c) retrieval time per query is at least 10 times lower. Otherwise keep it on. Report n/N with Wilson intervals either way; the switch stays available. | DECIDED |
| D28 | Phase 4's one fix-and-rerun cycle (P4-07) is used on the router defects A–C from the Phase 4 report. Defect B ("cash flow from operating activities" resolving to cash and equivalents) produces a confidently wrong, traceable number, which is high-impact by severity even at 1 of 80 items. Defect D (over-refusal on `R-MSFT-SEGMENTS-2025`) is re-examined after the re-index. The deferred P4-00 step 4 (full re-index) is scheduled now as P4-12 because Phase 5 builds on the final parser. | DECIDED |
| D29 | **Owner-only fields.** Claude Code never fills the `verdict` or `notes` columns of the verification and rating sheets, and never sets `verified_by` to `owner`. Assistance goes in separate `assist_*` columns, and the owner spot-checks the assistance itself on a seeded random sample of at least 5 rows. Verification tiers stay `owner` > `companyfacts` > `auto`. A sheet the owner has not signed leaves the headline metrics provisional. | DECIDED |
| D30 | **Second fix cycle, authorized (defect E).** The text path passed each retrieved chunk's whole parent section to the generator with no budget (one JPM prompt reached about 831,000 tokens; 30 of 704 sections exceed 100,000 tokens). Every provider refused it, and the failure was reported as `llm_rate_limited`. This is a regression introduced by v2's own answer path and it invalidates narrative results for banks, so it is fixed now and V1, V2, and V3 are re-run on the full 80 items. The pre-fix results are kept unmodified as measured "before" evidence in the report. | DECIDED |
| O1 | Free-tier keys: **created** (Groq, Gemini). Gemini limits are recorded in Appendix A. Groq free-plan limits are measured in P1-04 (response headers) and may be added to `llm/limits.local.yaml`. | PARTIALLY RESOLVED |
| O2 | Whether and where to host a free public demo (verify current free terms at signup), and how derived DBs and Qdrant data reach it. | OPEN (decide at P5-02) |

---

## 5. Target architecture

```
Client (UI / API)  ──  POST /query {question, as_of?}
        │
  API layer (api/app.py): rate limit · size cap · request id · error mapping · CORS allow-list
        │
  ask()  (query.py)
        │
  [1] ENTITY & PERIOD RESOLUTION   deterministic: tickers (SEC registry + aliases), fiscal periods, as_of, catalog lookups
        │
  [2] ROUTER   rules first → LLM only when needed → strict JSON schema validation → typed failure
        │ intent
        ├── numeric_fact | computed | compare | trend ──► FACTS PATH
        │       facts.resolve(ticker, metric, period, as_of) → Fact → calc → deterministic template
        ├── narrative ───────────────────────────────────► TEXT PATH
        │       catalog-eligible collections → hybrid retrieve → rerank → generate (structured {found, answer})
        └── unsupported / unanswerable ──────────────────► ABSTAIN
        │
  [3] ABSTENTION GATE   single module; reason enum; user-facing message templates
        │
  [4] Outcome(status, answer, citations[fact|text], definition_note, as_of, trace)
```

**Stores:** Qdrant (text chunks) · `data/derived/catalog.sqlite` · `data/derived/facts.sqlite` · chat DB (runtime, untracked) · LLM cache (`.cache/llm_cache.sqlite`).

**Degradation rule (P5):** if the LLM is unavailable, numeric questions that the deterministic router can parse still answer from facts; narrative questions return `status=error`/`llm_unavailable`.

---

## 6. Data contracts

### 6.1 Filing (catalog row)
`accession` (PK) · `ticker` · `cik` · `entity_name` · `form_type` (`10-K`|`10-K/A`) · `period_end` (date) · `fiscal_label` (int) · `fiscal_label_source` (`dei`|`period_end`) · `filing_date` (date) · `primary_doc` · `exhibit_docs` (json list) · `collection_name` (nullable) · `facts_built_at` (nullable) · `amends` (accession, nullable)
Rules: a ticker may map to several CIKs (BlackRock: `BlackRock Finance, Inc.` CIK 1364742 → `BlackRock, Inc.` CIK 2012383); the mapping lives in `catalog/cik_overrides.yaml`.

### 6.2 Fact (facts.sqlite)
`id` · `accession` (FK) · `ticker` · `concept` (`us-gaap:Revenues`) · `value` (decimal string, fully scaled and signed) · `value_num` (REAL, for sorting only) · `unit` (`USD`, `USD/shares`, `shares`, `pure`) · `decimals` · `scale_raw` · `sign_raw` · `period_type` (`duration`|`instant`) · `start_date` · `end_date` · `context_id` · `source_doc` · `element_id`
Rules: only **non-dimensional** (consolidated) facts are stored in v2. Calculations use `Decimal`. Never infer scale; read `scale`, `sign`, `format`, `xsi:nil`.

### 6.3 Metric registry (`facts/concepts.yaml`)
```yaml
revenue:
  label: "Total net revenue"
  statement: income
  period_type: duration
  candidates:                # ordered per sector; the order is a hint, NOT the policy (see 6.4)
    default: [us-gaap:Revenues, us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, us-gaap:SalesRevenueNet]
    bank:    [us-gaap:Revenues, us-gaap:RevenuesNetOfInterestExpense]
  aliases: [revenue, revenues, sales, net sales, top line, total revenue]
  overrides:                 # per ticker (and optionally per fiscal range), each with provenance
    BLK: {concept: us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, evidence: "accession …, income statement line 'Total revenue'"}
```
Initial metrics: `revenue`, `net_income`, `operating_income`, `gross_profit`, `rd_expense`, `total_assets`, `total_liabilities`, `stockholders_equity`, `cash_and_equivalents`, `operating_cash_flow`, `capex`, `eps_diluted`.

### 6.4 Resolution policy (from the Phase 0 findings; MUST)
1. Pool **all documents of a submission** (wrapper + exhibits, or the complete instance XML) before resolving contexts.
2. Duration facts: annual means 350–380 days (52/53-week years). Instant facts: at `period_end`. Exclude dimensional contexts.
3. Candidate selection per (filer, metric) (D2-02): an evidenced per-filer override wins; otherwise take the metric's **tiered preference list**, where each tier states a required `preference` reason. Within the first tier that has any candidate: if all candidates carry the same value, that value is the result; if they differ, return **`ambiguous`** with all candidates. Never silently choose between differing values.
4. Validation: the chosen value should appear as the matching line in the rendered income statement (parsed section `fs_income_stmt`). Record `validation_status` ∈ `validated|unvalidated|conflict`. Conflicts are listed in the phase report.
5. Restatements (D14) and `as_of` (D2) are applied after candidate selection.

### 6.5 Outcome (API response)
```json
{
  "status": "answered | answered_text | abstained | clarification_needed | error",
  "answer": "string",
  "abstain_reason": null,
  "error_code": null,
  "query_type": "numeric_fact | computed | compare | trend | narrative | unsupported",
  "as_of": "YYYY-MM-DD or null",
  "definition_note": "e.g. 'Revenue = total net revenue (us-gaap:Revenues), fiscal year ended 2024-09-28'",
  "citations": [
    {"kind": "fact", "index": 1, "ticker": "AAPL", "metric": "revenue", "concept": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
     "period_end": "2024-09-28", "fiscal_label": 2024, "value": "391035000000", "unit": "USD",
     "accession": "…", "filing_date": "2024-11-01", "restated": false},
    {"kind": "text", "index": 2, "ticker": "JPM", "fiscal_label": 2024, "section": "Item 1A: Risk Factors", "accession": "…", "filing_date": "…"}
  ],
  "trace": null
}
```
`abstain_reason` enum: `company_not_found`, `no_filing_for_company`, `period_not_covered`, `period_not_filed_as_of`, `future_period`, `metric_not_supported`, `metric_not_found_in_filing`, `ambiguous_concept`, `unsupported_period_type`, `out_of_scope`, `insufficient_evidence`.
`error_code` enum: `llm_auth`, `llm_rate_limited`, `llm_unavailable`, `llm_bad_output`, `data_unavailable`, `internal`.
`trace` is returned only to an admin (`X-Admin-Token`) with `?debug=1`.

### 6.6 Gold item (`eval/gold/gold_v1.jsonl`)
```json
{"id": "N-AAPL-REV-2024", "category": "numeric", "question": "What were Apple's total net sales in fiscal 2024?", "as_of": null,
 "expected": {"type": "numeric", "ticker": "AAPL", "metric": "revenue", "period_end": "2024-09-28", "value": "391035000000", "unit": "USD", "tolerance_rel": 0.0},
 "source": {"accession": "…", "concept": "us-gaap:…", "oracle": "sec_companyfacts"},
 "verified_by": "owner | companyfacts | auto", "notes": ""}
```
Other `expected.type` values: `computed` (value + tolerance_abs), `multi` (list of numeric), `abstain` (`abstain_reason`), `text` (`expected_sections`, optional `must_contain_numbers`).

---

## 7. Module layout (target; deviations need a DECISIONS entry)

```
api/            app.py (routes, middleware), chat.py, auth.py (new), ratelimit.py (new)
catalog/        build.py, store.py, cik_overrides.yaml            (new)
facts/          extract.py, concepts.yaml, concepts.py, store.py, resolve.py, calc.py, format.py   (new)
llm/            client.py, errors.py, fake.py, providers.yaml, prompts/ (versioned prompt files)                    (new)
routing/        entities.py (new), periods.py (new), router.py (new; classifier.py kept until replaced), decomposer.py, resolver.py
answering/      outcome.py, abstain.py, facts_answer.py, text_answer.py                              (new)
generation/     generator.py, synthesizer.py, citations.py (new)
retrieval/      (existing; modes behind config flags for ablation)
ingestion/      (existing; extended to build catalog + facts on ingest)
evaluation/     ragas_eval.py (kept, optional)
eval/           gold/, scorers.py, runner.py, ablations.py, variants.yaml, phase0/ (kept as history)
scripts/        smoke.py, build_all.sh, crosscheck_companyfacts.py, check_qdrant_filter.py
tests/          unit/, integration/, regression/, fixtures/, phase0/ (kept)
docs/           PROJECT_SPEC.md, DECISIONS.md, CHANGELOG.md, EVAL.md, LIMITATIONS.md, DEMO.md, RUNBOOK.md, PRIVACY.md, explainers/
reports/        phase0/ … phase5/, final/
logs/           phase1/ … phase5/
LICENSE, NOTICE (MIT; provenance, see D17)
```

---

## 8. Configuration (env vars; lazy-loaded, no import-time failure)

| Var | Purpose | Default |
|---|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` (alias `groq_api`), optional third-provider key | free-tier provider keys (owner-created) | required at runtime for LLM features, not at import |
| `edgar_email` | SEC User-Agent contact | required at runtime, not at import |
| `ADMIN_TOKEN` | admin auth; unset means admin routes are disabled | unset |
| `LLM_PROVIDERS` | default ordered failover list of `provider:model` entries, e.g. `gemini:gemini-3.5-flash-lite,gemini:gemini-3.1-flash-lite,groq:openai/gpt-oss-120b` | set by the P1-04 bake-off |
| `ROUTER_PROVIDERS`, `GENERATOR_PROVIDERS`, `JUDGE_PROVIDERS` | per-role ordered `provider:model` lists (override `LLM_PROVIDERS`); legacy `GENERATION_MODEL`/`ROUTING_MODEL` accepted on the Groq path | from the bake-off |
| `CORS_ORIGINS` | comma-separated allow-list | empty (same-origin only) |
| `RATE_LIMIT_PER_MIN`, `MAX_QUESTION_CHARS` | `/query` limits | 20, 500 |
| `LLM_CACHE_PATH`, `LLM_DAILY_TOKEN_BUDGET` | cache location, hard stop | `.cache/llm_cache.sqlite`, limits from `llm/limits.local.yaml` (owner-entered, O1) |
| `QDRANT_URL`, `QDRANT_API_KEY` | optional remote Qdrant (not needed for local-first delivery) | unset (local mode) |
| `FACTS_DB_PATH`, `CATALOG_PATH` | derived DBs | `data/derived/…` |
| `FACTS_FILINGS_PER_COMPANY` | facts coverage (D15) | 5 |
| `ENABLE_FACTS`, `ENABLE_ASOF`, `ENABLE_ABSTAIN_GATE` | ablation switches | all `true` |
| `RETRIEVAL_MODE` (`hybrid`,`dense`,`bm25`), `ENABLE_FOCUS_BOOST`, `ENABLE_RERANK` | retrieval ablations | `hybrid`, `true`, `true` |
| `FACTS_LLM_PHRASING` | optional paraphrase (D8) | `false` |

---

## 9. LLM policy
1. One client (`llm/client.py`); no other module imports a provider SDK. It speaks the OpenAI-compatible chat API against configurable `base_url`s, so providers are configuration (`llm/providers.yaml`: base URL, key env var, models per role, supported features such as JSON output).
2. Disk cache keyed by hash of (model, messages, temperature, prompt_version). Cache hits cost zero tokens.
3. Typed errors: `LLMAuthError`, `LLMRateLimited(retry_after)`, `LLMUnavailable`, `LLMBadOutput`. Respect `Retry-After`; max 3 attempts; never loop.
4. Budgets are tracked per (provider, model): requests per minute/day and tokens per minute (and per day where a limit exists), seeded from Appendix A and `llm/limits.local.yaml`, and corrected at runtime from rate-limit response headers when a provider sends them (for example `x-ratelimit-*`) and from `Retry-After`. Failover walks the ordered `provider:model` list for the role; when all entries are exhausted raise `LLMRateLimited`; runners mark remaining items `not_run`.
5. Structured outputs are validated against a pydantic schema; an invalid output raises `LLMBadOutput` (one repair retry allowed).
6. Router temperature 0. Prompts live in `llm/prompts/` with a version id that is logged in traces.
7. Filing text is untrusted data: prompts must instruct the model to treat context as data, not instructions.
8. A `FakeLLM` (deterministic, fixture-driven) is used by all offline tests.
9. Never log secrets or full prompts at INFO; log token counts, provider, model, latency, cache hit.
10. Failover is for the app. **Evaluation runs pin one provider/model** (no failover) and record provider, model, and date in every result row. A judge, if used, comes from a different provider than the generator.
11. Free tiers change without notice. Never hard-code limits or model names in code; read them from config and record the values used in each report.
12. Free tiers may use submitted content to improve provider models. This project sends only public SEC text and user questions; private data (for example emails) must never be sent to a free-tier provider.

---

## 10. Testing strategy

| Layer | What | Network/LLM | Marker |
|---|---|---|---|
| Unit | pure functions: calc, scale/sign, concept resolution, period parsing, eligibility, citation remap, scorers | none | `unit` |
| Integration | pipeline with fixture filings/facts + `FakeLLM`; API contract tests; fixture Qdrant (tiny) | none | `integration` |
| Regression | gold-set subsets on the real stack | LLM and/or network | `live`, `slow` |
| Property | calculator and `as_of` invariants (Hypothesis) | none | `unit` |

- `make test` runs `unit` + `integration` with sockets blocked (`pytest-socket`); it MUST pass with no `.env` present.
- Coverage targets: ≥ 85% on `facts/`, `catalog/`, `llm/`, `answering/`, `routing/entities.py`, `routing/periods.py`; ≥ 70% on all new code. Coverage XML is saved per phase.
- Fixtures: small trimmed real iXBRL excerpts in `tests/fixtures/ixbrl/` (AAPL, NFLX thousands, BLK dual revenue, BAC, WFC split wrapper/EX-13, a 52/53-week filer, a 10-K/A, a negative-value case), each ≤ 200 KB.
- A phase gate requires: all phase tests green, `make lint` clean (ruff), no skipped test without a documented reason, and the phase report.

---

## 11. Evaluation specification (built in Phase 4; scorers evolve from Phase 0's)

**Gold set v1 (target 80):** numeric 25 · computed 12 · compare/trend 10 · narrative 15 · `as_of`/look-ahead 8 · abstain 10. Cover all four sectors and at least one thousands-reporting filer, one split-document filer, one multi-concept filer.
**Oracle (D4):** numeric values come from SEC `companyfacts` (independent of our extractor); `verified_by` ∈ `owner` > `companyfacts` > `auto`. Headline metrics use `owner` + `companyfacts` items; `auto` items are reported separately.
**Metrics:** numeric accuracy · computed accuracy · abstention precision/recall (overall and per reason) · false-abstain rate · look-ahead violation count (target 0) · citation validity · scale errors · wrong-period errors · narrative section hit@k and MRR · latency p50/p95 · tokens/query · cost/query. Report counts as n/N with 95% Wilson intervals. No significance claims unless a test was computed.
**Variants:** V0 `v1-baseline` tag (same gold; `as_of` passed in question text) · V1 v2 with `ENABLE_FACTS=false` · V2 `ENABLE_FACTS=true`, `ENABLE_ASOF=false`, `ENABLE_ABSTAIN_GATE=false` · V3 full v2. Each run pins one provider/model (section 9); identical prompts across variants are served from the LLM cache, so ablations cost few new tokens.
**Retrieval ablations (no generation tokens):** BM25-only, dense-only, hybrid, hybrid+rerank, hybrid+rerank+focus-boost, plus parent-context on/off; metrics: section hit@k, MRR, expected-number-in-context rate.

---

## 12. Phases

Each phase ends with the report protocol in section 13. Owner gates are listed per phase.

### Phase 1 — Foundation, hardening, catalog, true baseline

**Goal:** a trustworthy, testable base; hardened code that runs locally; a real "before" measurement exists.

| ID | Task | Level |
|---|---|---|
| P1-00 | **Baseline first, on unmodified v1 code.** Create a git worktree of tag `v1-baseline`; share one local data directory with the main checkout (symlink or env). Set up a venv and run `run_ingestion.py` for the bundled 12 + NFLX. The v1 code only speaks to Groq with the owner's Groq key. First list the models available to the key (`GET https://api.groq.com/openai/v1/models`): the Groq models page marks `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` (v1's defaults) as Enterprise, which may explain the old deployment's failures (unverified). Use the nearest available models via env (`GENERATION_MODEL`/`ROUTING_MODEL`: configuration, not code) and record exactly what was used. If no available model works with v1's unmodified prompts and parsing, apply the smallest possible compatibility patch (model names and response parsing only) on a branch `v1-baseline-compat`, report the diff, and label the baseline "v1 + compat patch". Run the Phase 0 runner on the 25 questions (cache on, resumable; stop cleanly at the free-tier limit and continue the next day). Score with the Phase 0 scorer. Save `reports/phase1/baseline_v1_local/` with provider, model names, dates, and `not_run` counts. Record ingestion time, sizes, and failures. Reuse valid SEC responses already in `.cache/` (EDGAR submissions, `companyfacts`, 11 primary documents) before fetching again; the cache is never committed. | MUST |
| P1-01 | Verify the Step 0 result (`reports/bootstrap/REPORT.md`): repository exists and is private; tags present; ignore rules active; README stub and `.env.example` in place. Close the Step 0 items: move `PHASE0_INSTRUCTIONS.md` to `reports/phase0/`; change the ignore rule to `data/chat_history.db*`; remove `test_setup.py` once `make test` exists; switch to `main`, fast-forward, and delete the stale local `v2-dev` branch; create `phase-1-foundation` for the phase work. No repository creation work here. | MUST |
| P1-02 | Dependencies and tooling: split `requirements.txt` (serving), `requirements-dev.txt` (pytest, pytest-cov, pytest-socket, hypothesis, ruff), `requirements-eval.txt` (ragas, datasets, langchain-*); verify the pinned Python version works for fastembed/onnxruntime on macOS arm64 and align `Dockerfile`; create `Makefile` targets from CLAUDE.md. | MUST |
| P1-03 | Lazy settings: no import-time failure without `.env`; add env vars from section 8; keep `groq_api`/`edgar_email` names. | MUST |
| P1-04 | `llm/` package per section 9: OpenAI-compatible multi-provider client with ordered `provider:model` failover across **free-tier** entries (Appendix A), capability registry (`llm/providers.yaml`), disk cache, per-(provider, model) request/token budgets with header-based correction, typed errors; migrate classifier, decomposer, generator, synthesizer to it. Confirm each provider's OpenAI-compatible base URL from its docs and list available model IDs via the provider's models endpoint (discover the Gemma 4 IDs this way). Include a **provider bake-off** (≤ 40 calls total, cached; at most 5 calls on any 20-requests/day model): JSON-validity rate, latency, and citation-format compliance on a fixed prompt set; record in `docs/DECISIONS.md` and choose ordered lists per role (router, generator, judge) following the role guidance in Appendix A. | MUST |
| P1-05 | Fail-loud routing: classifier/decomposer errors raise typed errors; `ask()` returns `status=error` with `error_code`; API returns 503; UI shows a clear message; `/health` reports `llm: ok|auth_error|rate_limited|unreachable` using a cached no-token probe (e.g. model listing, TTL 60 s), never one probe per request. | MUST |
| P1-06 | Security: `api/auth.py` with D13; protect every remaining `/admin/*`, `/ingest`, and the destructive chat routes (`DELETE /sessions/{sid}`, `PATCH …/turns/{tid}`) via admin token or session secret; **remove** the admin routes that only served the old hosted deployment (`/admin/restore-data`, `/admin/migrate-to-remote*`, `/admin/data-path` delete) unless a DECISIONS entry justifies keeping them; remove wildcard CORS; `/query` rate limit and `MAX_QUESTION_CHARS`. | MUST |
| P1-07 | Citation remap fix in `generation/citations.py` (single-pass, function replacement; handles `[10]` vs `[1]`; ignores non-citation brackets); wire into synthesizer. | MUST |
| P1-08 | Years: remove hard-coded `VALID_YEARS`; derive from available collections (interim; the catalog replaces this in P3). Unknown year returns the existing `year_not_available` path. | MUST |
| P1-09 | Catalog (`catalog/`): build from EDGAR submissions (seed from Phase 0's `build_manifest.py`), all 10-K and 10-K/A, with the fields in 6.1 (`fiscal_label` = `period_end.year` for now), BlackRock dual-CIK handling via overrides, 10-K/A `amends` link, mapping to local collection names. CLI `python -m catalog.build`; SEC-polite (descriptive User-Agent with `edgar_email`, ≤ 5 req/s, cached raw JSON). | MUST |
| P1-10 | Verify Qdrant local-mode filtering against a **real** ingested collection (`scripts/check_qdrant_filter.py`) to settle the Phase 0 K9 caveat; record the pinned `qdrant-client` version. | MUST |
| P1-11 | `scripts/smoke.py --base-url URL`: read-only check usable against a local or hosted instance (GET `/health`, `/collections`; at most 8 `POST /query`; asserts status and latency; prints a pass/fail table). | MUST |
| P1-12 | `docs/explainers/phase1.md` (owner briefing, section 13) and the phase report including a **local release checklist** (`make up` from a clean checkout, then `smoke.py` against it). | MUST |
| P1-13 | `scripts/check_repo_hygiene.py` (+ `make hygiene`): one reliable command for the key-pattern scan (file:line only), the > 5 MB check, and the T1-12 reference grep. Each check must include a **negative control** (a planted match it must catch) so a silent "no matches" cannot be a false pass. Reusable by CI (P4-08). | SHOULD |

**Tests**

| ID | Test |
|---|---|
| T1-01 | Citation remap table tests: offsets 0–5, 1–12 citations, `[10]` vs `[1]`, text with non-citation brackets, idempotence. |
| T1-02 | Router failure paths: fake LLM raising each typed error → `status=error` with the right `error_code`; never the "Which company…" clarification. |
| T1-03 | Auth: enumerate all routes from `app.routes`; every admin/destructive route returns 503 when `ADMIN_TOKEN` unset, 403 with a wrong token, passes the guard with the right one. Fails if a new unguarded route appears. |
| T1-04 | Rate limit and question-length cap. |
| T1-05 | Importing the app with no environment variables succeeds. |
| T1-06 | Catalog from recorded EDGAR fixtures: BlackRock dual CIK, a 10-K/A, period_end/filing_date correctness. |
| T1-07 | A year with an existing collection (e.g. 2026) is accepted; an absent year yields `year_not_available`. |
| T1-08 | LLM client: cache hit avoids a network call; 429 honors `Retry-After`; budget stop; error mapping; `FakeLLM` determinism. |
| T1-09 | `/health` LLM states and no per-request probe. |
| T1-10 | `data/chat_history.db` is untracked and gitignored. |
| T1-11 | Provider failover: first provider 429/5xx → second provider used; an auth error on one provider is reported distinctly; evaluation-pinned runs never fail over; the capability registry rejects a provider lacking a feature required for a role. |
| T1-13 | Hygiene script: planted key-like string, planted large file, and planted third-party URL are each detected; a clean tree passes. |
| T1-12 | Repo hygiene: `LICENSE` and `NOTICE` exist; no tracked file outside `docs/BOOTSTRAP.md`, `reports/`, `eval/phase0/`, and `tests/phase0/` (historical records) references third-party deployment URLs or repository names (grep test); `.env` and the old chat DB are untracked. |

**Exit criteria:** all T1 tests pass offline; ruff clean; baseline report exists with per-category results; Qdrant filter check recorded; provider bake-off recorded; local release checklist written.
**Owner gates:** review the report; review the pull request Claude Code opens (`gh pr create`, `phase-1-foundation` → `main`) and merge it on GitHub; run `make up` and `scripts/smoke.py --base-url http://localhost:8000`.

---

### Phase 2 — Facts engine

**Goal:** an accurate, validated, offline facts store with a resolver and calculator.

| ID | Task | Level |
|---|---|---|
| P2-00 | **Housekeeping and indexing throughput (run first; it can overlap with P2-01 to P2-08).** (a) Remove `test_setup.py` (`make test` exists). (b) `scripts/check_repo_hygiene.py` also reads a gitignored `.hygiene_local` file of extra patterns (the old project's handle, repository name, and hostnames, supplied by the owner); the file is never committed and no committed file names the old project. (c) Replace v1's one-pass embedding with a **streaming indexer**: embed and upsert per collection with a bounded batch size and visible progress output (chunks/s, RSS); keep `eval/phase1/index_per_ticker.py` until the new indexer is verified. (d) **Throughput profile** on 200 real chunks: batch size {8, 16, 32, 64} × max sequence length {512, 256}, measuring chunks/s and peak RSS with other heavy apps closed. Keep the v1 dense model (bge-base) if the tuned setting reaches ≥ 4 chunks/s (the full set then takes about 2.5 hours; run it overnight with `caffeinate -i`). Otherwise, in this order: embed text and footnote chunks only for non-core tickers; then, as a last resort, switch to a smaller model, declared per D21. Fallback if local indexing stays infeasible: build the index on a free Colab GPU runtime (the removed v1 notebook is recoverable with `git show v1-baseline:colab.ipynb`) and copy `data/qdrant` back. (e) Index the evaluation-core tickers in the order MSFT, JPM, GOOGL, NFLX, BLK, GS. (f) Create or refresh `docs/STATE.md` and keep it current (CLAUDE.md rule 16). | MUST |
| P2-01 | Fixtures: trimmed iXBRL excerpts listed in section 10. | MUST |
| P2-02 | `facts/extract.py`: pooled per-submission parsing; contexts (period, dimensions), units; `ix:nonFraction` with `scale`, `sign`, `format` transforms (collect every distinct `format` value in the corpus, handle all, **fail loudly on unknown**; include zero-dash and fixed-zero styles), `xsi:nil`, nested/hidden facts (`ix:header/ix:hidden` carries the cover-page `dei:` facts). | MUST |
| P2-03 | DEI extraction: `DocumentFiscalYearFocus`, `DocumentPeriodEndDate`, `DocumentType`, `AmendmentFlag`, `EntityRegistrantName`, `EntityCentralIndexKey`. Verify DEI fiscal labels on all 13 tickers (report mismatches vs `period_end.year`); update catalog `fiscal_label` and `fiscal_label_source`. | MUST |
| P2-04 | `facts/store.py` (SQLite, indexes on ticker/concept/end_date/accession); `make facts` is idempotent per accession (hash of source docs); facts for up to `FACTS_FILINGS_PER_COMPANY` filings per ticker (download extra filings if needed; raw stays read-only). | MUST |
| P2-05 | `facts/concepts.yaml` and `concepts.py` per 6.3; aliases; per-filer overrides with provenance. | MUST |
| P2-06 | Resolution policy per 6.4, including `validation_status` against the rendered income statement. | MUST |
| P2-07 | `facts/resolve.py`: `resolve(ticker, metric, period_selector, as_of)`; selectors: `fiscal_label`, `period_end`, `latest`, `calendar_year`; returns `Fact` + filing ref or a typed `Abstain(reason)`; applies D14 and `restated` flag. | MUST |
| P2-08 | `facts/calc.py` (Decimal): `growth_pct`, `margin_pct`, `ratio`, `difference`, `cagr_pct`, `sum`; guards for zero/negative base, unit mismatch, period mismatch; returns value, formula string, and input facts. `facts/format.py`: money/percent formatting. | MUST |
| P2-09 | Oracle cross-check (`scripts/crosscheck_companyfacts.py`): all catalog 10-K filings for bundled tickers plus NVDA, TSLA, V, COST, META; **compare by accession** (`accn`) so restatements do not create false mismatches; output `reports/phase2/crosscheck.csv`. | MUST |
| P2-10 | Owner spot-check sheet `reports/phase2/owner_spotcheck.csv`: ≥ 15 rows (every sector, includes BLK, BAC, GS, WFC, NFLX), each with filing URL, statement/page hint, concept, extracted value. | MUST |
| P2-11 | Coverage and ambiguity report; list every filer/metric returned as `ambiguous` or `conflict`. | MUST |
| P2-12 | `docs/explainers/phase2.md` and the phase report. | MUST |
| P2-13 | **Closure (D22).** After the owner completes the spot-check sheet: commit it; fix and re-check every WRONG row; implement the precision-preference fix in `facts/extract.py`; rebuild the facts store (identical-hash rules from T2-08 still apply); re-run the oracle cross-check; update the Phase 2 report with before/after exactness (corpus-wide and registry concepts); update `docs/STATE.md`, `docs/DECISIONS.md`, and the CLAUDE.md status table; push to the existing PR `phase-2-facts-engine`. | MUST |

**Tests**

| ID | Test |
|---|---|
| T2-01 | Scale/sign/format transforms (thousands, millions, `sign="-"`, zero-dash, nil facts); unknown `format` fails loudly. |
| T2-02 | Dimensional facts excluded; annual duration window incl. 52/53-week years; transition/short periods excluded; instant at `period_end`. |
| T2-03 | Pooled submission (WFC wrapper + EX-13 fixture) resolves; each document alone does not. |
| T2-04 | BLK and BAC fixtures: override honored; without override → `ambiguous`; never a silent pick. |
| T2-05 | `as_of` eligibility: resolving before vs after `filing_date`; 10-K/A selection; `restated` flag (D14). |
| T2-06 | Calculator: unit tests plus property tests (growth inverse, sign, zero base → typed error, unit/period mismatch → error). |
| T2-07 | Formatting: thousands/millions/billions boundaries; negative values; percent rounding. |
| T2-08 | Idempotent rebuild: running `make facts` twice yields identical DB content hashes. |
| T2-09 | Golden values: ≥ 20 (ticker, metric, fiscal_label) tuples, owner/oracle-verified, resolved exactly. |
| T2-10 | Cross-check thresholds (live/network): ≥ 99.5% exact on comparable pairs; 0 scale errors; 0 sign errors; every discrepancy classified. |
| T2-11 | Streaming indexer: with a fake embedder, collections are embedded and upserted one at a time; no batch exceeds the configured size; an interrupted run resumes without re-embedding finished collections. |
| T2-12 | Hygiene local patterns: a planted pattern in a temporary `.hygiene_local` is detected; the file is gitignored; the committed tree contains no old-project names. |
| T2-13 | Precision preference (D22): two instances of one concept that agree within the coarser `decimals` resolve to the larger-`decimals` instance; two that disagree beyond it are not merged; negative control: reverting the rule makes the test fail; the rebuild after the change is deterministic. |

**Exit criteria:** T2 tests pass; cross-check thresholds met; DEI fiscal-label verification reported; spot-check sheet produced.
**Owner gate:** complete the spot-check sheet (mark each row OK/WRONG); any WRONG row must be fixed and re-checked before Phase 3.

---

### Phase 3 — Routing, answers, `as_of`, abstention, UI

**Goal:** the user-facing system implements D5–D9 and G1–G4.

| ID | Task | Level |
|---|---|---|
| P3-00 | **Section-quality audit and housekeeping (D23).** (a) `scripts/audit_sections.py`: for every parsed filing, report each narrative section the text path uses (Items 1, 1A, 1C, 3, 7, 7A and the financial-statement notes) as present/empty/too small/too large with character counts, plus the same for statement sections; include a **negative control** (planted empty and oversized sections must be flagged). Decide per defect class whether to fix the parser or document the limit, and record it. (b) Make the SEC response cache refuse to store non-JSON error pages (`.cache/company_tickers.json` currently holds one). (c) Refresh `docs/STATE.md`. | MUST |
| P3-01 | `answering/outcome.py`: typed `Outcome`, `Citation` (fact|text), enums from 6.5; backward-compatible fields for the existing UI. | MUST |
| P3-02 | `routing/entities.py`: deterministic company/ticker resolution (SEC registry cache + aliases + bundled names). `routing/periods.py`: fiscal-year phrases, "fiscal 2024", bare years, "last year", "latest", ranges ("2022 to 2024"), explicit dates → `as_of`, quarterly phrases → `unsupported_period_type`; injectable clock. | MUST |
| P3-03 | `routing/router.py`: rules first; LLM only to disambiguate intent/metric/narrative focus; schema-validated; metric aliases from the registry; unknown metric → `metric_not_supported` or text path with `answered_text` (document the rule). Replace `classifier.py` usage. | MUST |
| P3-04 | `answering/facts_answer.py`: deterministic templates for numeric/computed/compare/trend, including period end, definition note (D5), source filing link, `restated` note. | MUST |
| P3-05 | Text path integration: eligible collections from the catalog with `as_of` (D2); generator returns structured `{found, answer}` (replaces the refusal regex, K11); citation markers are normalized per D20 (counted in the trace); citations become typed with `accession` and `filing_date`; result status `answered_text`. | MUST |
| P3-06 | On-demand ingestion builds catalog + facts for the new filer; on facts failure → text path only, flagged. Ingestion resolves filings through the catalog and covers **every CIK** a ticker has filed under (D24); index BLK FY2023 as the proof case. | MUST |
| P3-07 | `answering/abstain.py`: single mapping from conditions to reasons and message templates. | MUST |
| P3-08 | API: `/query` accepts `as_of` (ISO date) and returns 6.5; request IDs; admin-only `?debug=1` trace. | MUST |
| P3-09 | UI: `as_of` date input; status badges; fact citation chips linking to the EDGAR filing; "definition used" line; abstain-reason and error states. | MUST |
| P3-10 | Mixed numeric + narrative questions: answer both parts in labeled sections (one of each). | OPTIONAL |
| P3-11 | Smoke evaluation: 30 gold-style questions with the real LLM; transcripts saved. | MUST |
| P3-12 | `docs/explainers/phase3.md` and the phase report. | MUST |

**Tests**

| ID | Test |
|---|---|
| T3-01 | Entity and period parsing: ≥ 60 phrase cases (fiscal vs calendar, ranges, "last year" with injected clock, quarterly → unsupported). |
| T3-02 | Router with `FakeLLM` fixtures; deterministic-only cases never call the LLM; invalid LLM output → `llm_bad_output` error. |
| T3-03 | **`as_of` invariant (property test):** random dates × tickers × intents; no cited filing has `filing_date > as_of` (facts and text citations). |
| T3-04 | Every abstain reason is reachable and returns the right message and no numeric claim. |
| T3-05 | Template golden tests for numeric/computed/compare/trend answers (stable string output). |
| T3-06 | K11 regression: the 17 Phase 0 refusal cases become structured outputs; `found:false` abstains with `insufficient_evidence`; false-positive phrases no longer trigger a retry. |
| T3-07 | API contract/snapshot tests for `/query` request and response schemas. |
| T3-08 | Offline end-to-end: fixture facts DB + `FakeLLM` + tiny Qdrant fixture; one case per intent. |
| T3-09 | Netflix thousands case end-to-end → correct magnitude and wording. |
| T3-10 | Citation normalization (D20): `【1】` and other fullwidth/ideographic variants become `[1]` and are counted; ASCII text is untouched; non-citation brackets are untouched; a model that mixes styles is handled in one pass. |
| T3-11 | Dual-CIK ingestion (D24): with a recorded BlackRock catalog fixture, the filing list for BLK includes the FY2023 filing from the old CIK; negative control: restricting to the current CIK drops it. |
| T3-12 | Section audit: planted empty, tiny, and oversized sections are each flagged; a clean fixture passes; the audit output is deterministic. |

**Exit criteria:** T3 tests pass; smoke eval completes with no crashes; transcripts show correct statuses; owner UI walkthrough done.
**Owner gate:** try the 10 sample questions in the UI (listed in the report) and report anything surprising.

---

### Phase 4 — Evaluation, ablations, documentation

**Goal:** a defensible, reproducible results table.

| ID | Task | Level |
|---|---|---|
| P4-00 | **Section-boundary rewrite (D25), time-boxed.** (1) Back up the current index to `data/qdrant_v1_backup/` and keep the current parsed/chunk files, so V0 can run against true v1 chunks (point V0 at the backup by configuration, not by code change). (2) Replace one-occurrence-per-id plus greedy monotonic filtering in `ingestion/parser.py` with candidate scoring (heading-shaped line; followed by prose; sentinel points at it; Item priority) and a global assignment over all occurrences (for example a longest-increasing-subsequence on line order and Item priority). Also address the two other diagnosed classes: the TOC skip zone as a fraction, and headings destroyed inside table rows. (3) Measure with `scripts/audit_sections.py` on all 39 parsed filings before re-indexing anything. **Targets:** Item 1A ok in at least 33 of 39 (from 19); Items 1 and 7 ok in at least 33 of 39 each; no filing loses a section that was ok before, except cases listed and explained one by one; every remaining Item 1A miss diagnosed per filing (some filers legitimately place risk factors in an annual-report exhibit); the two strict `xfail` tests pass and become ordinary tests. (4) Only if the targets are met: re-parse, re-chunk, and re-index **all 13 bundled tickers and NFLX once** (overnight; closes O-3: WFC, STT, TROW, IVZ get text coverage), refresh the catalog link, and re-run the Phase 3 smoke evaluation. (5) **Exit rule:** if the targets are not met after one focused working session, revert `ingestion/parser.py` to the committed version, record the attempt and numbers in `docs/DECISIONS.md` (D3-00 stands), and continue with P4-01. | MUST |
| P4-01 | Build `eval/gold/gold_v1.jsonl` (section 11): a generator script for oracle-sourced numeric/computed/multi items; `as_of` items from catalog filing dates; hand-drafted narrative and abstain items (owner approves). Schema validator. | MUST |
| P4-02 | Owner verification sheet `reports/phase4/gold_verification.csv` (≥ 25 numeric/computed items, stratified by sector and category). Headline metrics are published only after the gate. | MUST |
| P4-02b | **Assisted verification sheet (D29).** `scripts/make_gold_verification_assist.py` adds, for every row of `reports/phase4/gold_verification.csv`, **separate** columns: `assist_printed_line` (the table row or sentence from the filing's raw primary document that contains the value as printed, found by searching the raw HTML text, not the facts store), `assist_row_label`, `assist_column_header`, `assist_units_header` (for example "in millions"), `assist_value_as_printed`, `assist_match` (`found_exact`, `found_scaled`, `not_found`), `assist_link` (EDGAR), and `assist_spotcheck` (a seeded random selection of at least 5 rows the owner must open in the real filing). Rows with `assist_match=not_found` sort first. The script never writes `verdict` or `notes`, never changes any existing column, and never sets `verified_by` to `owner`. | MUST |
| P4-03 | `eval/scorers.py` (successor to Phase 0's): numeric (displayed-precision-aware), computed, multi-value attribution, abstention (by reason), look-ahead, citation validity, scale/period errors, narrative section hit@k. | MUST |
| P4-04 | `eval/runner.py` + `eval/variants.yaml`: resumable, cached, token/latency/cost accounting; **instruments every LLM role** (router, decomposer, generator, judge) in the trace, not only the generator; outputs JSONL, CSV summary, Markdown table. | MUST |
| P4-05 | Run V0–V3 on the gold set (LLM budget per O1; resumable across days). V0 covers the gold items whose corpus is indexed with v1's embedding (D21) and reports its N explicitly; V1–V3 are also reported on that same subset so the comparison is paired. | MUST |
| P4-06 | Retrieval ablations (flags in `retrieval/`, defaults unchanged); no generation tokens. When P4-00 succeeded, add one more arm: the same narrative questions against the **v1 backup index** versus the rewritten index, which isolates the parser effect from every other change. | MUST |
| P4-07 | Failure analysis: categorize every failure (router, retrieval, reading, resolution, abstention, scoring); at most one fix-and-rerun cycle for high-impact bugs. | MUST |
| P4-08 | CI (`.github/workflows/ci.yml`): lint + offline tests + offline mini-eval (facts-path gold subset on committed fixtures with recorded LLM cache); thresholds: numeric subset 100%, look-ahead violations 0. | MUST |
| P4-09 | Docs: README rewrite (what/why, architecture, results table linked to logs, reproduction commands, honest limitations), `docs/EVAL.md`, `docs/LIMITATIONS.md`. | MUST |
| P4-10 | `docs/explainers/phase4.md` and the phase report. | MUST |
| P4-11 | **Router defect fixes (D28).** (A) Route "ratio of X to Y" and "A less B" to the calculator's `ratio` and `difference`. (B) Metric aliases resolve by **longest match** so "cash flow from operating activities" cannot match "cash" first; add a table-driven test of every multi-word alias against its shorter prefixes. (C) A period outside the facts coverage window returns `period_not_covered`, not `metric_not_found_in_filing`. Each fix starts with a failing test taken from the failing gold item. | MUST |
| P4-12 | **Re-index and re-run (P4-00 step 4, D28).** Overnight, with the machine plugged in and no other process holding the local index: re-parse, re-chunk, and re-index all 13 bundled tickers plus NFLX into a **new** index directory; keep `data/qdrant_v1_backup/` untouched (record its point counts before and after); relink the catalog (orphan count must be 0); re-run the Phase 3 smoke evaluation; then re-run V1, V2, and V3 on the full 80 items (the text index now covers BAC, IVZ, STT, TROW, WFC) and on the 69-item paired subset, retrying rate-limited items across days with pacing. V0 stays at its 69 paired items. Re-run the D25 arm (old index versus new index) and report the parser's measured effect. Re-examine defect D. | MUST |
| P4-13 | **Narrative retrieval set and the reranker decision (D27).** Expand the narrative gold items from 15 to at least 40 with expected section ids only. Claude Code drafts them from the filings and validates each against the section audit (the expected section must exist and be usable in every filing in scope); the owner reviews a sample of 10 questions. Re-run the retrieval ablations on the re-indexed corpus and apply D27's rule exactly as written. If the rule says off, change the default in `config.py`, re-measure end-to-end narrative latency, and record it in `docs/DECISIONS.md`. | MUST |
| P4-14 | **Narrative rating sheet.** `scripts/make_rating_sheet.py` generates, from the post-re-index, post-P4-16 V3 run, a sheet for 15 sampled narrative answers with: question, answer text, each cited section with the **retrieved passage text** (about 1,500 characters) and an EDGAR link, and the columns `verdict` (`supported`, `partially`, `unsupported`), `issue` (`none`, `wrong_section`, `missing_info`, `hallucination`, `other`), `notes`. Rubric at the top of the sheet: *supported* means every claim in the answer is stated or clearly implied by the cited text. | MUST |
| P4-16 | **Context budget fix and re-run (D30).** Test-first, each starting from the reproduced failure (`N-JPM-REVENUE-2024` sends 3.3 MB). (1) Bound the generator context to a token budget set below the smallest TPM limit of any provider in the generator failover list, taking v1's `MAX_CTX_TOKS` semantics from `git show v1-baseline:generation/generator.py` as the reference. (2) Send the retrieved chunk plus a **window of its parent section around the chunk**, not the head of the section; collapse several chunks of one section into one source with merged windows. (3) A prompt whose estimated size exceeds every candidate provider's limit raises a typed error before any request (`llm_prompt_too_large`), is never labeled `llm_rate_limited`, and does not burn failover attempts. (4) Add the error code to the spec's `error_code` enum and the API contract. (5) Re-run the Phase 3 smoke, then V1, V2, and V3 on the full 80 and the live paired 79; keep the pre-fix result files untouched under a `before_fix` name; report pre-fix and post-fix side by side. | MUST |
| P4-15 | **Finalize.** After the owner gates: replace "provisional" with final in the README, `docs/EVAL.md`, and the Phase 4 report addendum; every number regenerable by one command (T4-05); update `docs/STATE.md`, `docs/DECISIONS.md`, the CLAUDE.md status table; push to the open PR and stop. | MUST |

**Tests**

| ID | Test |
|---|---|
| T4-01 | Scorer adversarial cases: swapped company values, scale error, wrong fiscal year, negatives, abstention containing numbers, percent vs fraction. |
| T4-02 | Gold schema validator: every item has `source` and `verified_by`; no duplicate IDs; `as_of` items consistent with catalog dates; abstain items name a reason. |
| T4-03 | Runner determinism with `FakeLLM`; resume after interruption yields identical results. |
| T4-04 | CI workflow executes locally (script mirror) and enforces thresholds. |
| T4-05 | Table renderer: every number in `reports/phase4/results.md` is regenerable from raw JSONL by one command. |
| T4-06 | Boundary selection (P4-00): the two strict `xfail` cases now pass; on planted multi-heading fixtures the global assignment keeps every section while the old greedy rule loses one (negative control); selection is deterministic; the audit's per-filing numbers are reproduced by one command. |
| T4-07 | V0 isolation: with the backup index configured, the V0 runner reads v1 chunks (a known v1-only chunk id is retrievable); the rewritten index and the backup never share a storage path. |
| T4-08 | Router fixes (P4-11): the gold items `C-AAPL-DEBTEQUITY-2024` and `C-AMZN-FCF-2024` route to `ratio` and `difference`; every multi-word metric alias beats its shorter prefix (table-driven, negative control: the old first-match rule fails it); a period beyond coverage yields `period_not_covered`. |
| T4-09 | Re-index integrity (P4-12): catalog orphan count is 0; every collection has a catalog row; the v1 backup index's point counts are unchanged; the new and backup stores never share a path. |
| T4-10 | Narrative gold validator (P4-13): every expected section exists and is usable in each filing in scope per the audit; a planted item pointing at an unusable section is rejected. |
| T4-11 | Rating sheet (P4-14): deterministic for a given run; every row has passage text and a link; a row whose answer has no citation is flagged; negative control: a planted answer with no citation appears flagged in the output. |
| T4-12 | Assisted sheet (P4-02b): the original columns, including `verdict` and `notes`, are byte-identical before and after; a value planted in a fixture filing is found with its row label and header; a value absent from the fixture yields `not_found`; output is deterministic; the spot-check selection is seeded and recorded in the sheet. |
| T4-13 | Context budget (P4-16): for `N-JPM-REVENUE-2024` the assembled context is within the budget (negative control: the old unbounded builder exceeds it by orders of magnitude); two chunks from one section yield one source; the retrieved chunk text is always inside the window; the budget is below the smallest provider TPM in the failover list. |
| T4-14 | Oversize prompts (P4-16): a prompt larger than every candidate provider's limit raises `llm_prompt_too_large` before any request is made; no failover attempt is consumed; the API contract lists the new code; a genuine 429 is still labeled `llm_rate_limited` (negative control). |

**Exit criteria:** results table with n/N and Wilson intervals; ablation tables; failure analysis; CI green; docs written.
**Owner gate:** sign the gold verification sheet (at least 25 core items) and rate the 15 sampled narrative answers from the **post-re-index, post-P4-16** run (sheet from P4-14); approve the final results. Headline numbers stay marked provisional until both are done.

---

### Phase 5 — Productization and release

**Goal:** a documented, secure, one-command-runnable `v2.0.0` (optionally hosted for free).

| ID | Task | Level |
|---|---|---|
| P5-01 | Slim, non-root image (serving deps only; no ragas/langchain); `docker-compose.yml`; `make up` for a one-command local run from a clean clone (verify in a fresh directory); document memory and disk needs. | MUST |
| P5-02 | Hosting decision (O2): research and report the current free options with the owner (eligibility, RAM/disk, sleep behavior; a 512 MB instance is too small for the ONNX models plus local Qdrant). If the owner picks one, implement delivery of derived DBs and Qdrant data (measured sizes; bake into the image vs download at start); otherwise skip hosting. `scripts/build_all.sh` (ingest → catalog → facts) is required either way. | MUST |
| P5-03 | Production hardening: structured JSON logs with request IDs, response cache for repeated queries, LLM-down degradation rule (section 5), graceful shutdown. | MUST |
| P5-04 | Privacy: per-session secret for chat history, retention purge (default 30 days), log redaction; `docs/PRIVACY.md`. | MUST |
| P5-05 | Security review: `pip-audit`, secret scan of the repo and history (report only), CORS/headers, input limits, admin disabled by default, SSRF/path-traversal review of on-demand ingestion and any remaining upload/restore code. | MUST |
| P5-06 | Demo kit `docs/DEMO.md`: 8 scripted queries (numeric, computed, compare, trend, narrative, `as_of` look-ahead, abstention, ambiguity), expected outputs, a 3-minute walkthrough, honest limitations talking points, a screen-recording script for a demo video the owner records, and README screenshots. | MUST |
| P5-07 | `docs/RUNBOOK.md`: env vars, run/deploy steps, key rotation, data rebuild, failure modes mapped to `/health` states. | MUST |
| P5-08 | Final regression: full tests, full gold eval frozen into `reports/final/`, `scripts/smoke.py` against the local stack (and the hosted instance, if any), tag `v2.0.0`. | MUST |
| P5-09 | `docs/explainers/phase5.md` (interview Q&A pack: design choices, failures found, results, limits) and the final report. | MUST |
| P5-10 | **Follow-up rewriter (D26).** Deterministic: resolve "and last year?", "what about Microsoft?", "same for 2023" by rewriting against the previous turn's *resolved* entities, metric, period and `as_of` **before** routing. Never hand history prose to the router. When the previous turn has no resolved entity, or the follow-up is ambiguous, ask for clarification instead of guessing. | SHOULD |
| P5-11 | Mixed numeric + narrative questions in labeled sections, one of each (formerly P3-10). | OPTIONAL |
| P5-12 | Delete the retired v1 modules (`routing/classifier.py`, `routing/resolver.py`, `generation/generator.py`, `generation/synthesizer.py`) after V0 is frozen in `reports/final/`; keep the Phase 0/1 baseline artifacts reproducible from tag `v1-baseline`. | SHOULD |
| — | Stretch (owner approval required, not part of Definition of Done): portfolio/trade-review demo on synthetic data; 10-Q support; segment facts; price data tool. | OPTIONAL |

**Tests**

| ID | Test |
|---|---|
| T5-01 | `docker build` succeeds; container starts with fixture data; `/health` healthy. |
| T5-02 | LLM-down degradation: fake LLM raising `LLMUnavailable` → parseable numeric question still answers; narrative → `error`/`llm_unavailable`. |
| T5-03 | Chat privacy: session A cannot read or delete session B; retention purge works. |
| T5-04 | Response cache correctness (key includes `as_of`); cache never returns across different `as_of`. |
| T5-05 | `pip-audit` clean or exceptions documented; no secrets detected. |
| T5-06 | Compose e2e: `make up`, ten demo queries, expected statuses. |
| T5-07 | Smoke: `scripts/smoke.py --base-url` passes against the local stack (and the hosted demo, if one exists). |
| T5-08 | Follow-up rewriter: at least 25 phrase cases; a follow-up with no resolved previous entity asks for clarification; a rewrite never changes the previous turn's `as_of` unless the user gives a new one; the rewritten question is shown to the user. |
| T5-09 | After P5-12 the suite is green, no import of a removed module remains, and `git show v1-baseline:<path>` still reproduces the V0 runner inputs. |

**Exit criteria:** all tests green; image builds; runs from a clean clone; smoke-tested; documents complete; `v2.0.0` tagged.
**Owner gates:** choose O2 (host or not); if hosting, deploy per the runbook; rotate keys; record the demo video; run the demo end to end once.

---

## 13. Logging and reporting protocol (every phase)

Files Claude Code MUST produce at each phase end:
- `reports/phaseN/REPORT.md` — sections: **1** Summary (≤ 10 bullets) · **2** What changed (modules, commit hashes) · **3** Tests (table: ID, name, result; coverage %; runtime; exact command) · **4** Metrics (phase-specific) · **5** Deviations from spec and decisions (link to DECISIONS entries) · **6** Defects found/fixed/open · **7** Owner actions and questions · **8** Gate checklist (checked/unchecked) · **9** Proposals (out-of-scope ideas) · **10** Appendix (commands with exit codes).
- `reports/phaseN/tests/junit.xml` and `coverage.xml`.
- `logs/phaseN/commands.log` — every non-trivial command, timestamp, exit code; plus raw logs for long runs.
- `docs/explainers/phaseN.md` — a plain-language one-pager for the owner: what was built, why, how data flows, 5 likely interview questions with answers, 3 known weaknesses.
- Updates to `docs/DECISIONS.md`, `docs/CHANGELOG.md`, `docs/STATE.md`, and the phase status table in `CLAUDE.md`.
- Final terminal output: report path, ≤ 12-line summary, then `PHASE N COMPLETE — awaiting owner approval`.

Failure handling: if a test fails after two fix iterations, stop, record it as an open defect, and ask in the report. Never weaken a test to pass it.

---

## 14. Git and change management
- Branches: `main` (stable, integration) · `phase-N-<slug>` (one per phase). Each phase gate is **one pull request** `phase-N-<slug>` → `main`, opened by Claude Code with `gh pr create` and merged by the owner on GitHub. Tags: `v1-baseline` (the initial import commit, placed during Step 0) and `phaseN-complete` on `main` after each merge. The earlier `v2-dev` branch was merged in Step 0 and is retired; delete the stale local copy.
- One concern per commit; conventional prefixes (`fix:`, `feat:`, `test:`, `docs:`, `chore:`). Commits made by Claude Code carry a `Co-Authored-By: Claude` trailer (D19). Direct commits to `main` are not allowed.
- Claude Code pushes phase branches and opens pull requests with `gh`; merging into `main` is the owner's action on GitHub. Forbidden: force-push, repository deletion or rename, visibility or settings changes, any remote other than `origin`.
- Never rewrite published history. If a secret is ever committed, stop and tell the owner.

## 15. Security and privacy checklist (verified in Phase 5, enforced from Phase 1)
Admin fail-closed (T1-03) · no wildcard CORS · rate limit and size cap · constant-time token compare · tokens only in headers · no secrets in logs/repo · filings treated as untrusted prompt data · on-demand ingestion resolves companies only through the SEC registry (no arbitrary URL fetch) · upload/restore routes removed (path-traversal-guarded if any are kept) · chat history untracked, per-session secret, retention limit · dependency audit.

## 16. Definition of Done (project)
1. All tests green offline (`make test`); live regression passes; CI green.
2. Gold results table published with n/N, intervals, and links to raw logs; owner-verified sample documented.
3. G1–G5 each demonstrated by a named test or report section.
4. `v2.0.0` runs from a clean clone with one command (`make up`); admin routes are disabled by default; no paid service is required; if a hosted demo exists, `smoke.py` passes against it; no known high-severity issues.
5. README, EVAL, LIMITATIONS, DEMO, RUNBOOK, PRIVACY, DECISIONS, and phase explainers exist and match the code.

## 17. Registers

### 17.1 Open questions (owner)
O1 Groq free-plan limits (measured in P1-04). O2 whether and where to host a free demo.

### 17.2 Known-issues register (from Phase 0) → resolving phase
| ID | Issue | Phase |
|---|---|---|
| F1 | Classifier swallows all exceptions; outage looks like "Which company?" | P1 |
| K1 | Citation remap collisions | P1 |
| K2 | "Assume millions" prompt; LLM reads noisy tables | P2/P3 |
| K3 | iXBRL facts discarded at parse | P2 |
| K4 | No `filing_date`/`period_end` at query time | P1 (catalog) / P3 |
| K5 | Hard-coded `VALID_YEARS`; MSFT FY2026 unreachable; `NVDA_2026` unreachable | P1 interim / P3 final |
| K6 | Hand-tuned focus boosts | P4 (ablation) |
| K7 | Admin routes open when token unset (original deployment) | P1 |
| K8 | Chat DB committed | P1 |
| K9 | Local-mode filter behavior (refuted in synthetic test; verify on real collection) | P1 |
| K10 | Weak evaluation (8 questions, same-family judge) | P4 |
| K11 | Refusal regex (recall 0.556; 3 false positives) | P3 |
| N1 | BlackRock dual CIK | P1/P2 |
| N2 | Python 3.10 in Docker vs 3.12 locally | P1 |
| N3 | Heavy eval dependencies in production image | P1 split / P5 slim |
| N4 | Import-time `Settings()` breaks import without `.env` | P1 |
| N5 | `api/chat.py` destructive routes unauthenticated | P1 |
| N6 | iXBRL `format` transforms (zero-dash etc.) unhandled | P2 |
| N7 | 52/53-week fiscal years | P2 |

### 17.3 Changelog
- v1.0 — initial specification after Phase 0.
- v1.1 — new repository (D17); free-tier-only multi-provider LLM client (D11); no paid services, local-first delivery (D18); hosted-platform dependency removed.
- v1.9 — D30 (second fix cycle for the unbounded parent-context defect E); P4-16, T4-13, T4-14; narrative rating waits for the post-fix run.
- v1.8 — D29 (owner-only fields); P4-02b assisted verification sheet; T4-12.
- v1.7 — Phase 4 reviewed; D27 (pre-registered reranker decision rule), D28 (fix cycle on router defects A–C; full re-index now); P4-11 to P4-15, T4-08 to T4-11; owner narrative rating moves to the post-re-index run.
- v1.6 — Phase 3 reviewed; D25 (time-boxed section-boundary rewrite as P4-00 with an exit rule, v1 index backup for V0, full re-index once; T4-06, T4-07), D26 (follow-up rewriter, mixed questions, retired-module removal move to Phase 5; P5-10 to P5-12, T5-08, T5-09); P4-06 gains the old-index versus new-index arm.
- v1.5 — Phase 2 reviewed; D22 (precision-preference closure, P2-13, T2-13), D23 (v1 statement sections not trusted; P3-00 audit, T3-12), D24 (catalog-driven, multi-CIK ingestion; P3-06, T3-11); 6.4 rule 3 aligned with D2-02.
- v1.4 — Phase 1 reviewed; D20 (citation normalization), D21 (baseline scope); P2-00 (housekeeping, streaming indexer, throughput profile, core-ticker indexing, `docs/STATE.md`); T2-11, T2-12, T3-10; P4-04 instruments every role; P4-05 paired V0 subset.
- v1.3 — Step 0 closed; `v2-dev` retired, one PR per phase into `main` (section 14); D19 commit trailer; P1-01 closes the Step 0 items; P1-13 hygiene script; `.cache/` reuse.
- v1.2 — personal portfolio framing; Step 0 bootstrap through `gh` (D17); declared Gemini free-tier limits recorded (Appendix A); P1-00/P1-01/P1-04 updated.


---

## 18. Appendix A — declared free-tier limits (read from the owner's consoles on 2026-10-02)

Limits change without notice (section 9, rule 11). Treat these as seeds; the client corrects them at runtime. Quotas are per model, so each `provider:model` entry has its own budget and failover can walk across models of the same provider.

**Gemini API (AI Studio, free tier).**

| Model | RPM | TPM | RPD | Planned use |
|---|---|---|---|---|
| Gemini 3.5 Flash-Lite (`gemini-3.5-flash-lite`) | 15 | 250K | 500 | primary generator and router |
| Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite`) | 15 | 250K | 500 | failover generator/router (separate quota) |
| Gemma 4 26B / 31B (discover IDs via the models endpoint) | 30 | 16K | 14.4K | router, decomposer, judge (short prompts only: the 16K TPM cap allows about one long generation per minute) |
| Gemini 3.8 / 3.7 / 3.6 / 3.5 Flash, Gemini 3 Flash | 5 | 250K | 20 | reserve: spot checks only (at most 20 calls per day) |
| Gemini 2.5 Flash / Flash-Lite | 5 / 10 | 250K | 20 | not used (access limited to prior users per Google's models page) |
| Gemini Embedding 1 / 2 | 100 | 30K | 1K | not used (embeddings stay local) |
| Gemini 2.5 Pro, 3.1 Pro, 2.0 Flash / Flash-Lite | 0 | 0 | 0 | unavailable on the free tier |

**Groq (models page supplied by the owner).** Listed chat models: `openai/gpt-oss-120b`, `openai/gpt-oss-20b` (developer-plan limits shown: 250K TPM, 1K RPM; **free-plan limits are not on that page**), `qwen/qwen3.8-27b` (preview), `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` (both marked Enterprise). Base URL: `https://api.groq.com/openai/v1`. Free-plan limits are measured from response headers in P1-04.
Note: `openai/gpt-oss-*` are reasoning models; the client must handle their reasoning output separately from the answer text and validate JSON strictly.

**Planning consequences.**
- **Requests per day, not tokens, are the binding limit.** One evaluation pass (about 80 questions, router plus 1–5 generation calls each, variants sharing cached prompts) is roughly 300–400 requests, so it fits in one or two days across the two Flash-Lite entries.
- Rules-first routing and the facts path make no LLM call for most numeric questions, which also saves quota.
- Judge and generator come from different models (and, where possible, different providers); never judge with the same model that generated.
- No Pro-class model is available, so quality comes from deterministic components (facts, calculator, `as_of`, abstention) rather than from model size.
