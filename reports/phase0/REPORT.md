# Phase 0 — Audit and baseline report

**Repository:** `Financial_RAG-main` (source archive, no `.git`)
**Run date:** 2026-10-01 · **Host:** macOS 26.6.2, arm64 (Apple Silicon), Python 3.12.14
**Scope:** read-only measurement. No existing source file was modified.

---

## 1. Executive summary

1. **The live demo is down.** All five `POST /query` calls returned the generic
   *"Which company are you asking about?"* message in ~0.2 s — far too fast for an LLM call.
   `routing/classifier.py:180` catches every exception and returns `single_doc` with no tickers,
   which `query.py:101` converts into that message. A Groq auth/quota/model failure therefore
   disables every query with no error surfaced. **Baseline pass rate is 0/5.** Fix before the interview.
2. **`ADMIN_TOKEN` is unset in production.** `GET /admin/disk-usage` with no token returned
   **400, not 403**. Per the guard order in `api/app.py:163-166` that proves the token check
   did not fire. Every `/admin/*` route is open to the internet, including
   `DELETE /admin/collection/{name}`, `DELETE /admin/data-path` and `POST /admin/restore-data`
   (tar upload). The guard is fail-open: `if settings.admin_token and token != settings.admin_token`.
3. **iXBRL extraction works, and is exact.** 105 of 105 extracted (filing, concept) values matched
   SEC `companyfacts` — zero scale errors, zero sign errors, zero mismatches. Six values also match
   the repo's own test set exactly. **D1 is a GO** (with the caveats in §4).
4. **But concept *selection* is the real risk, not extraction.** BlackRock tags both
   `Revenues` = $12,794 M and `RevenueFromContractWithCustomerExcludingAssessedTax` = $20,407 M.
   4 of 11 filings (all financials) tag 2–4 competing revenue concepts differing by up to 44%.
   A single global priority list is unsafe.
5. **Wells Fargo's EX-13 split is solved and specified.** The 10-K wrapper holds 1,892 XBRL
   *contexts* but only 18 facts; the EX-13 exhibit holds 7,285 *facts* but 0 contexts. Neither
   resolves alone. Pooling both gives 7/12 coverage — identical to JPM and BAC.
6. **K5 is live and biting.** `VALID_YEARS = {2023, 2024, 2025}` is hardcoded and silently drops
   any other year. Microsoft's FY2026 10-K (filed 2026-07-29) is unreachable, and the production
   instance already has an `NVDA_2026` collection indexed that no user can query by year.
7. **K1 reproduced in the real `synthesize()`.** With offset=1, citations [1][2][3] all collapse to
   **[4]**; with offset=2, [1][2][3] become [5][4][5]. The `citations` list renumbers correctly, so
   the answer text and the source list disagree — every multi-company and trend answer is affected.
8. **K2 confirmed and quantified.** The generator prompt states *"All numbers are in MILLIONS
   unless the table header says otherwise."* Netflix reports in **thousands** (raw tag 39,000,966,
   scale=3, true value $39.0 B). Under that rule the LLM reports $39.0 **trillion** — a 1000× error.
9. **K4 confirmed: `as_of` is impossible today.** Stored point payloads are exactly the `Chunk`
   fields; `filing_date` and `accession_number` exist on `ParsedDocument` but are dropped at chunk
   construction, and `period_end` exists nowhere. This supports D2 (filter at filing-selection time).
10. **K9 REFUTED.** Qdrant local mode *did* honour the `chunk_type` payload filter (10/10 table
    chunks). Note the live deployment runs `qdrant_mode: remote` anyway.
11. **This checkout cannot run the pipeline.** No `.git`, no `.env`, no installed dependencies,
    and `data/raw`, `data/parsed`, `data/chunks`, `data/qdrant` are all absent. Step 3.2's 25-question
    local baseline could not run; 20 of 25 questions are `not_run`.
12. **Deliverables are in place for Phase 1.** 25-question gold set with values sourced from the
    verified extraction, a resumable instrumented runner, a deterministic scorer with its own
    passing self-test, and a 353-row filing manifest with `filing_date` for look-ahead scoring.

---

## 2. Environment and inventory

### 2.1 Platform and packages (Step 0.1)

| Item | Value |
|---|---|
| OS | macOS 26.6.2 (Darwin 25.6.0), arm64 |
| Python | 3.12.14 |
| Project dependencies installed | **none** |

Every package in `requirements.txt` — `qdrant-client`, `fastembed`, `groq`, `ragas`, `pydantic`,
`lxml`, `beautifulsoup4` — is **MISSING** from the project interpreter. A Phase 0-only virtualenv
(`.venv-phase0/`, git-ignorable, not the project runtime) was created to run the audits:
pydantic 2.13, pydantic-settings, groq, beautifulsoup4, lxml 6.0.2, pytest 9.1.1, requests 2.34.2,
qdrant-client (installed later, for K9 only). Full detail: `reports/phase0/environment.json`.

### 2.2 Git and authorship (Step 0.2) — **NOT AVAILABLE**

```
$ git status
fatal: not a git repository (or any of the parent directories): .git
```

This is an unpacked source archive. `git log`, `git shortlog` and the per-directory
`git blame` authorship table **cannot be produced**. See `docs/DECISIONS.md` (D0.5) for why
Phase 0 did not `git init`, and §8 Q1.

### 2.3 Secrets (Step 0.3) — values never read or printed

| Variable | Status |
|---|---|
| `groq_api` | **NOT SET** (no `.env` file) |
| `edgar_email` | **NOT SET** |
| `admin_token` | **NOT SET** locally; evidence in §7 shows it is also unset in production |

### 2.4 Data inventory (Step 0.3)

| Path | State |
|---|---|
| `data/raw/` | **absent** |
| `data/parsed/` | **absent** |
| `data/chunks/` | **absent** |
| `data/qdrant/` | **absent** |
| `data/test_sets/` | **absent** |
| `data/chat_history.db` | present, 65,536 bytes |
| Repo total | 500 KB |

Local Qdrant collections: **none** (`list_collections()` cannot even be called — see 2.5).
The **live** instance has 41 collections (§7).

### 2.5 Import and warm-up (Step 0.4)

`import query` **fails in 0.37 s**:

```
ValidationError: 2 validation errors for Settings
groq_api    Field required
edgar_email Field required
```

`config.py` constructs `settings = Settings()` at module import, so a missing `.env` makes the
entire package unimportable. Model load time (dense, sparse, reranker) could not be measured.

### 2.6 Filing manifest (Step 0.3 / 1.3)

Built from SEC EDGAR submissions (read-only, ~1 req/s): **353 10-K filings** across 13 tickers,
each with `ticker, cik, accession_number, period_end, filing_date, primary_doc_url`.
→ `reports/phase0/filing_manifest.{json,csv}`

| Ticker | Latest FY on EDGAR | Period end | Filed | Classifier can reach? |
|---|---|---|---|---|
| AAPL | 2025 | 2025-09-27 | 2025-10-31 | yes |
| **MSFT** | **2026** | **2026-06-30** | **2026-07-29** | **NO — unreachable** |
| GOOGL | 2025 | 2025-12-31 | 2026-02-05 | yes |
| AMZN | 2025 | 2025-12-31 | 2026-02-06 | yes |
| JPM | 2025 | 2025-12-31 | 2026-02-13 | yes |
| WFC | 2025 | 2025-12-31 | 2026-02-24 | yes |
| BAC | 2025 | 2025-12-31 | 2026-02-25 | yes |
| GS | 2025 | 2025-12-31 | 2026-02-25 | yes |
| BLK | 2025 | 2025-12-31 | 2026-02-25 | yes |
| STT | 2025 | 2025-12-31 | 2026-02-19 | yes |
| TROW | 2025 | 2025-12-31 | 2026-02-13 | yes |
| IVZ | 2025 | 2025-12-31 | 2026-02-24 | yes |
| NFLX | 2025 | 2025-12-31 | 2026-01-23 | yes |

Nothing is indexed locally, so **every** filing above is newer than what this checkout holds.

**BlackRock has two registrants.** `BlackRock Finance, Inc.` (CIK 1364742, the pre-2024 entity)
and `BlackRock, Inc.` (CIK 2012383, post-reorg). FY2024 and FY2025 filings are under the **new**
CIK. A single hardcoded CIK per ticker will silently return the wrong entity's history.

---

## 3. Known-issues register

| ID | Verdict | Evidence |
|---|---|---|
| **K1** citation remap collides | **CONFIRMED** | `tests/phase0/test_k1_citation_remap.py` (2 failing assertions, by design). offset=1 → `B [4] C [4] D [4]`; offset=2 → `B [5] C [4] D [5]`. `reports/phase0/k1_citation_remap.json` |
| **K2** LLM reads noisy tables; "millions" assumption | **CONFIRMED** | `generation/generator.py:57` verbatim: *"All numbers are in MILLIONS of dollars unless the table header says otherwise."* Netflix FY2024 tags scale=3 (thousands), raw 39,000,966 → this rule yields $39.0 trillion vs the true $39.0 billion |
| **K3** iXBRL discarded at parse time | **CONFIRMED** | `ingestion/parser.py:162-168` `_strip_ixbrl()` regex-removes all `ix:`, `xbrli:`, `xbrldi:` tags. Numbers survive as bare text; concept, context, scale, sign and unit are destroyed |
| **K4** no `filing_date` on `Chunk` | **CONFIRMED** | `retrieval/vector_store.py:272` stores `chunk.model_dump(exclude={'chunk_id'})`. `filing_date`/`accession_number` exist on `ParsedDocument`, not `Chunk`; `period_end` exists nowhere. `reports/phase0/k4_payload.json` |
| **K5** hardcoded `VALID_YEARS` | **CONFIRMED, worse than stated** | `routing/classifier.py:23` and `:153` (silent drop). MSFT FY2026 filed 2026-07-29 is unrequestable; production already holds an `NVDA_2026` collection no year-qualified query can reach |
| **K6** hand-tuned focus boosts | **CONFIRMED (exists as described)** | `retrieval/retriever.py:45` `_FOCUS_SECTION_PASS`, boost branches at `:198-306`. Not measured — needs the ablations planned for Phase 4 |
| **K7** `/admin/*` unauthenticated when token unset | **CONFIRMED, and live** | Fail-open guard `if settings.admin_token and token != ...` repeated at `api/app.py:163,418,482,529,566,596,616,634`. Live probe returned **400 not 403** (§7). `reports/phase0/k7_routes.{csv,json}` |
| **K8** chat DB shipped with the code | **CONFIRMED (substance)** | `data/chat_history.db` present, 64 KB: `sessions` 5 rows, `turns` 19 rows (`question`, `answer`, `citations`, `is_correct`). **All 19 `is_correct` are NULL** — no review labels, so it carries privacy cost and zero eval value. Tracked-in-git status unverifiable (no `.git`) |
| **K9** local-mode payload filters ignored | **REFUTED** | `tests/phase0/test_k9_local_filter.py`: `chunk_type_filter="table"` returned `{"table": 10}`, unfiltered returned `{"text":3,"table":4,"footnote":3}` — filter honoured, 0 leaks. Verified against the **real** qdrant-client (see note below). Caveat in §8 Q4 |
| **K10** weak eval | **CONFIRMED** | `evaluation/ragas_eval.py`: 8 questions, judge is Groq (same family as the generator), no numeric/abstention/look-ahead scorer. Phase 0 ships deterministic replacements |
| **K11** refusal regex | **CONFIRMED** | `tests/phase0/test_k11_refusal_regex.py`: 17 cases → **4 false negatives, 3 false positives**. `reports/phase0/k11_refusal_regex.csv` |

**K9 verification note.** This result was initially order-dependent: `tests/phase0/conftest.py`
stubs heavy imports so `query.py` can be imported without the full stack, and that stub shadowed
the real `qdrant_client`, so the K9 test silently exercised the stub when it ran after other
tests. `conftest.py` now prefers the real package whenever it is installed, and the suite was
re-run to confirm the stub is never used (0 occurrences). The REFUTED verdict comes from the real
client in both isolated and full-suite runs.

### K11 detail

False **negatives** (real refusal not detected → the broadened retry never fires, a wrong
"not found" ships):
`"I'm unable to answer that…"` · `"The context provided is insufficient to determine…"` ·
`"That detail does not appear anywhere…"` · `"Sorry, the excerpts don't contain…"`

False **positives** (good answer treated as refusal → one wasted retrieve+generate, 2 Groq calls):
`"Apple's 10-K does not mention cryptocurrency holdings, but total net sales were $391,035 million."` ·
`"Net income cannot be found by simply adding the segments; the consolidated figure is $93,736 million."` ·
`"The company does not discuss dividends in Item 7, but Item 5 reports $15.2 billion…"`

Recall on genuine refusals: **0.556**. The code comment claims false negatives are the dangerous
case; they are also the majority case.

---

## 4. iXBRL feasibility verdict (decision D1)

### 4.1 What was measured

`data/raw` is empty, so the same primary documents the pipeline downloads were fetched into
`.cache/filings/` from the accessions in the manifest (see `docs/DECISIONS.md` D0.4).
**11 filings, 4 sectors.** Parsed with `lxml` in a standalone script; `ingestion/parser.py`
untouched.

A note on method: lxml's `HTMLParser` does no namespace processing and lowercases tags, so
`<ix:nonFraction>` arrives as the literal string `ix:nonfraction`. A first run that matched on
`QName(...).localname` reported 0 facts everywhere — a bug in the audit, not an absence of tags.
Corrected matching is in `eval/phase0/ixbrl_extract.py`.

### 4.2 Coverage (Step 2.4)

| Filing | Sector | Concepts found / 12 | Coverage | ix facts | contexts |
|---|---|---|---|---|---|
| AAPL_2024 | Technology | 12/12 | **100%** | 963 | 193 |
| MSFT_2024 | Technology | 12/12 | **100%** | 1,578 | 465 |
| GOOGL_2024 | Technology | 11/12 | 92% | 1,526 | 344 |
| GOOGL_2023 | Technology | 11/12 | 92% | 1,415 | 347 |
| NFLX_2024 | Media | 11/12 | 92% | 1,213 | 312 |
| BLK_2024 | Asset Mgmt | 10/12 | 83% | 2,445 | 803 |
| GS_2024 | Banking | 9/12 | 75% | 6,123 | 2,169 |
| AMZN_2023 | Technology | 8/12 | 67% | 1,287 | 362 |
| JPM_2024 | Banking | 7/12 | 58% | 7,841 | 2,203 |
| BAC_2024 | Banking | 7/12 | 58% | 7,892 | 2,437 |
| WFC_2024 | Banking | 7/12 | 58% | 7,303 | 1,892 (pooled, see 4.5) |

**Coverage below 100% is mostly correct behaviour, not failure.** Banks do not report
`GrossProfit`, `OperatingIncomeLoss`, `ResearchAndDevelopmentExpense` or
`PaymentsToAcquirePropertyPlantAndEquipment`. Of the applicable concepts, essentially all were found.
Every filing yielded revenue, net income, total assets and diluted EPS.

### 4.3 Cross-check (Steps 2.5 / 2.6) — the headline result

**105 of 105 (filing, concept) pairs matched SEC `companyfacts` exactly.**

```
MATCH  105
SCALE_ERROR 0 · SIGN_ERROR 0 · MISMATCH 0 · NOT_IN_COMPANYFACTS 0
```

Independently, six values match the repo's own test set exactly: AAPL revenue 391,035 and
operating income 123,216; MSFT R&D 29,510; JPM net income 58,471; GOOGL R&D 49,326 (FY2024) and
45,427 (FY2023). → `reports/phase0/ixbrl_crosscheck.csv`

**Scale handling is correct**, including the non-millions case: Netflix FY2024 carries `scale=3`
with raw text `39,000,966`, correctly yielding $39,000.966 M. This is precisely what the
generator's "assume millions" prompt (K2) gets wrong.

### 4.4 The real risk: concept selection, not extraction (Step 2.7)

| Filing | Candidates | Values |
|---|---|---|
| AAPL / MSFT / GOOGL / AMZN / NFLX | **1** | unambiguous |
| JPM_2024 | 4 | `RevenuesNetOfInterestExpense` 177,556 · `Revenues` 177,556 · `InterestIncomeExpenseNet` 92,583 · `NoninterestIncome` 84,973 |
| BAC_2024 | 4 | `InterestAndDividendIncomeOperating` **146,607** · `Revenues` **101,887 ← correct** · `InterestIncomeExpenseNet` 56,060 · `NoninterestIncome` 45,827 |
| GS_2024 | 4 | `InterestAndDividendIncomeOperating` 81,397 · `RevenuesNetOfInterestExpense` **53,512 ← correct** · `NoninterestIncome` 45,456 · `InterestIncomeExpenseNet` 8,056 |
| BLK_2024 | 2 | `RevenueFromContractWithCustomerExcludingAssessedTax` **20,407 ← correct** · `Revenues` **12,794** |
| WFC_2024 | 0 in the wrapper | see 4.5 |

**4 of 11 filings tag competing consolidated revenue concepts; all four are financials.** Picking
the wrong one understates BlackRock by 37% or overstates Bank of America by 44% — while the
extraction itself is perfectly faithful (companyfacts agrees with every value above). A global
priority list is not safe. → `reports/phase0/revenue_ambiguity.json`, `docs/DECISIONS.md` D0.3

### 4.5 Wells Fargo / EX-13 (Step 2.7) — solved

| Document | ix facts | contexts |
|---|---|---|
| `wfc-20241231.htm` (10-K wrapper, 1.6 MB) | 18 | **1,892** |
| `wfc-20241231_d2.htm` (EX-13, 10.9 MB) | **7,285** | 0 |
| pooled | 7,303 | 1,892 |

The instance is **split across two documents**: contexts in the wrapper, facts in the exhibit,
with `contextRef` pointing across the boundary. Each document alone yields **0 usable facts**.
Pooled, WFC reaches 7/12 — identical to JPM and BAC — with revenue $82,296 M and net income
$19,722 M, both matching companyfacts. `ingestion/downloader.py` already merges EX-13 for text
(`_EXHIBIT_TYPES`), so the plumbing exists. A simpler alternative is the complete instance
document `wfc-20241231_htm.xml` (14.6 MB), which the submission already contains.

### 4.6 Verdict: **GO** for D1, with three required conditions

Extraction from locally stored primary documents is accurate (105/105), offline, reproducible,
and pins to an accession — everything `as_of` correctness needs. Conditions:

1. **Per-filer concept resolution**, validated against the rendered income-statement total, not a
   single global priority list (§4.4).
2. **Pool all documents of a submission** before resolving contexts (§4.5).
3. **Never infer scale.** Always read the `scale` and `sign` attributes (§4.3).

Keep `companyfacts` as an offline validation oracle only (`docs/DECISIONS.md` D0.2).

---

## 5. Baseline results

### 5.1 What could and could not run

**Step 3.2 (local, 25 questions) did not run.** This checkout has no dependencies, no `.env` and
no indexed corpus; `import query` fails in 0.37 s (§2.5). The runner
(`eval/phase0/run_baseline.py`) is written, instrumented and resumable, and exits non-zero with a
clear message rather than producing misleading output.

**Step 4.2 (live, 5 questions) ran** and is the only real baseline available.
**20 of 25 questions are `not_run`.**

### 5.2 Live results — 0/5

| ID | Category | Expected | Answer | Verdict | Failure stage |
|---|---|---|---|---|---|
| N1 | numeric | 391,035 M | *"Which company are you asking about?"* | **FAIL** | classification |
| C1 | computed | 31.51% | *(same)* | **FAIL** | classification |
| M3 | compare | MSFT 29,510 / GOOGL 49,326 | *(same)* | **FAIL** | classification |
| T3 | lookahead | only FY2023 eligible | *(same)* | NO_CITATIONS | classification |
| U3 | unanswerable | abstain | *(same)* | **FAIL** | classification |

Latency: p50 **0.24 s**, p95 **0.30 s**, max 0.30 s — all five below 0.31 s.
Tokens per query: **0** (no Groq call completed). Rate-limit events: **0**.

Every failure is at the **same** stage. This is not five bugs; it is one.

U3 is scored FAIL rather than PASS because *"Which company are you asking about?"* is a
clarification, not an abstention — the system does not know it cannot answer. It is flagged for
manual review. T3 is NO_CITATIONS: with no citations there is no look-ahead violation to detect,
but also no answer.

### 5.3 Retrieval diagnostics (Step 3.4)

Not applicable: the live API exposes no chunk-level trace and retrieval never ran. The scorer's
`retrieval_diagnostic()` is implemented and will separate *"retrieval missed it"* from
*"retrieval found it, the LLM misread it"* as soon as Step 3.2 runs locally.

### 5.4 Scorer self-test

The scorer is itself an instrument, so it has its own evidence:
`tests/phase0/test_scorer_selftest.py` — **7 passed**. It covers `$391,035 million`,
`$391.0 billion`, `391.0B`, `45,183,036 thousand`, percentages, scale-error detection,
per-company attribution (including a swapped-values case that must fail), and abstention.

Two scorer bugs were found and fixed during the run: `10-K` was parsed as the number 10, and
attribution matched only ticker symbols, not company names ("Microsoft" vs "MSFT").

---

## 6. Top 5 failures, analysed

### F1 — The live classifier fails open, and takes every query with it

*Classification:* none — `classify_query()` threw. *Retrieved:* nothing. *Answer:* the generic
clarification string, in 0.22 s.

`routing/classifier.py:180-187` catches **every** `Exception` and returns
`ClassifiedQuery(query_type="single_doc", tickers=[], ...)`. `query.py:101` then sees
`single_doc` with no tickers and returns *"Which company are you asking about?"*.

The 0.2 s latency rules out a timeout: the Groq call failed immediately — an auth error, an
exhausted quota, or a decommissioned model (`llama-3.1-8b-instant` / `llama-3.3-70b-versatile`).
**Diagnosis:** a recoverable outage is rendered indistinguishable from a legitimate "please name
a company", for every query, with nothing in the response or `/health` to reveal it. A user sees
a system that has forgotten what Apple is.

### F2 — Admin routes are unauthenticated in production

`GET /admin/disk-usage` with no token returned **400**, not 403. The handler checks the token
first (`:163`) and only then rejects on `qdrant_url` (`:166`), so reaching the 400 proves the
token check did not fire — i.e. `settings.admin_token` is falsy in production.

The pattern `if settings.admin_token and token != settings.admin_token` is **fail-open**: an
unset token disables authentication entirely rather than denying access. It is repeated verbatim
at eight call sites, guarding `DELETE /admin/collection/{name}`, `DELETE /admin/data-path`,
`POST /admin/restore-data` (tar extraction into `data_dir`), `POST /admin/migrate-to-remote` and
`POST /ingest`. Per Phase 0 rules no destructive route was called; the 400-vs-403 inference needs
no such call.

Separately, `api/chat.py` has **no token check at all**, not even the fail-open one:
`DELETE /sessions/{sid}` and `PATCH /sessions/{sid}/turns/{tid}` are open and destructive.

### F3 — Citation remapping corrupts every multi-document answer (K1)

`generation/synthesizer.py:127-134` rewrites sub-answer text with successive `str.replace()`
calls. Each replacement can be re-matched by a later iteration:

| offset | sub-answer 2 in | out | correct |
|---|---|---|---|
| 1 | `B [1] C [2] D [3].` | `B [4] C [4] D [4].` | `B [2] C [3] D [4].` |
| 2 | `B [1] C [2] D [3].` | `B [5] C [4] D [5].` | `B [3] C [4] D [5].` |

The `citations` list is renumbered correctly (1,2,3,4), so **the text and the source list
disagree** — the answer attributes a claim to [4] while the list says that claim's source is [2].
Fix direction: a single-pass regex substitution with a function replacement, or map onto
placeholders before writing final indices.

### F4 — Concept selection, not extraction, is what will produce wrong numbers (D1)

BlackRock FY2024 tags `Revenues` = $12,794 M *and*
`RevenueFromContractWithCustomerExcludingAssessedTax` = $20,407 M. A fixed priority list that
tries `Revenues` first returns the component — **37% low** — with perfect confidence and a valid
citation. The extractor is not wrong; `companyfacts` confirms both tags. The *policy* is wrong.
Bank of America is the mirror image: `InterestAndDividendIncomeOperating` = $146,607 M against the
correct $101,887 M, **44% high**. Phase 2 must resolve per filer and validate against the
income-statement total.

### F5 — The "assume millions" prompt breaks on thousands-reporting filers (K2)

`generation/generator.py:57`: *"All numbers are in MILLIONS of dollars unless the table header
says otherwise."* Netflix's income statement is in thousands; the iXBRL fact carries `scale=3`
and raw text `39,000,966`. The markdown table the LLM sees retains the digits but not the scale
attribute (destroyed by `_strip_ixbrl`, K3), so the only signal left is a header the chunk may
not contain. The instructed behaviour yields **$39.0 trillion** instead of $39.0 billion. The
Facts path makes this class of error structurally impossible.

---

## 7. Live vs local, and the security finding

### 7.1 Live deployment (9 requests total, limit 12)

`GET /health` → 200. `qdrant_mode: **remote**`, `collections_loaded: 41`.

```
AAPL_2023/24/25  AMZN_2023/24/25  BAC_2023/24/25  BLK_2024/25  COST_2025
GOOGL_2023/24/25 GS_2023/24/25    IVZ_2023/24/25  JPM_2023/24/25  META_2025
MSFT_2023/24/25  NFLX_2025  NVDA_2026  STT_2023/24/25  TROW_2023/24/25
TSLA_2025  V_2025  WFC_2023/24/25
```

`GET /collections` → 200, identical list. `GET /ingest/status` → 200, `running: false`,
`exit_code: 0`, last ingest completed cleanly.

**Local comparison:** local has **zero** collections — `data/qdrant` does not exist and
`list_collections()` cannot be called. The two environments are not comparable; the live
instance is the only working one.

Observations from the live list:
- **`NVDA_2026` is indexed but unreachable.** `VALID_YEARS = {2023,2024,2025}` drops 2026, so no
  year-qualified query can reach it (K5, end to end).
- **`MSFT_2026` is missing** although that 10-K was filed 2026-07-29.
- `BLK_2023`, `NFLX_2024` absent; `COST`, `META`, `NVDA`, `TSLA`, `V` present from on-demand ingest.
- Live is 41 collections against the ingest log's expected 35 — on-demand ingest has grown it.

### 7.2 Security finding (Step 4.3) — **ADMIN_TOKEN IS UNSET IN PRODUCTION**

```
GET /admin/disk-usage   (no token)
→ HTTP 400, 95 bytes
  {"detail":"Not applicable — QDRANT_URL is set (remote Qdrant mode); data/qdrant isn't used."}
```

**400, not 403.** The token check at `api/app.py:163` runs *before* the `qdrant_url` check at
`:166`. Reaching the second check proves the first did not reject an unauthenticated request —
`settings.admin_token` is falsy on the deployment.

**Consequence:** every `/admin/*` route is reachable by anyone with the URL, including
`DELETE /admin/collection/{name}` (delete any collection), `DELETE /admin/data-path` (delete
paths under `data_dir`), `POST /admin/restore-data` (upload a tar extracted into `data_dir`),
`POST /admin/migrate-to-remote` and `POST /ingest`. The 41 indexed collections can be destroyed
by an unauthenticated request. **Set `ADMIN_TOKEN` in the Railway environment now**, and change
the guard from fail-open to fail-closed. Per Phase 0 rules none of these routes was called.

---

## 8. Open questions for the owner

1. **Where is the real git repository?** This is an unpacked archive. Steps 0.2 (authorship) and
   half of 1.7 need it, and Phase 1 needs somewhere to branch. Should Phase 1 work in the clone?
2. **Is the live demo's Groq key valid?** §6 F1 shows classification failing in ~0.2 s. Please
   check the key, the quota, and whether `llama-3.1-8b-instant` / `llama-3.3-70b-versatile` are
   still served. This needs credentials Phase 0 does not have, and it is the top priority.
3. **Set `ADMIN_TOKEN` in Railway** (§7.2). Also decide whether `api/chat.py`'s
   `DELETE /sessions/{sid}` should stay open.
4. **K9 caveat.** Local-mode filtering was tested with the qdrant-client installed for this audit,
   against a synthetic collection. `retrieval/vector_store.py:342` carries a comment asserting the
   opposite for `scroll_by_section`. Please confirm against your pinned version and a real
   collection before relying on this; the result may be version-specific.
5. **Gold-set verification (D4).** 25 questions are in `eval/phase0/baseline_questions.jsonl`.
   Values marked `ixbrl_unverified` come from the Phase 0 extraction (cross-checked against
   `companyfacts`, but that is not independent enough for D4). **Please hand-verify at least 15
   against the filings.** Priority: N4, N5, N6, C3, M1, M2, S1.
6. **Which revenue definition should a bank question return?** For JPMorgan, "revenue" could be
   total net revenue ($177,556 M), net interest income ($92,583 M) or noninterest income
   ($84,973 M). This is a product decision, not a technical one, and it determines §4.4's design.
7. **`fiscal_year` for Microsoft.** MSFT's FY2026 ended 2026-06-30. v1 would label it 2026, but
   users may say "2025". Should the system answer in the company's fiscal labelling, the calendar
   year, or both, and say which?
8. **Delete `data/chat_history.db` from the repo?** 5 sessions / 19 turns of real questions and
   answers, all `is_correct` NULL. It has no eval value and should probably be removed and
   gitignored.
9. **Set `edgar_email`.** SEC fair-access requires a contact-shaped User-Agent;
   `www.sec.gov/Archives` returns **403** without one. Phase 0 used a clearly-marked placeholder.
10. **Scope check for Phase 1.** Given that the live demo is down and admin routes are open,
    should Phase 5 hardening items be pulled forward ahead of the gold set?

---

## 9. Appendix — artifacts and commands

### 9.1 Artifacts produced

| Path | Contents |
|---|---|
| `reports/phase0/REPORT.md` | this report |
| `reports/phase0/environment.json` | Step 0.1/0.3/0.4 |
| `reports/phase0/filing_manifest.{json,csv}` | 353 10-K filings with `filing_date` |
| `reports/phase0/ixbrl_coverage.csv` | 132 (filing, concept) rows |
| `reports/phase0/ixbrl_crosscheck.csv` | 105 rows vs `companyfacts`, all MATCH |
| `reports/phase0/ixbrl_tagstats.json` | 2.1/2.2 tag and context statistics |
| `reports/phase0/revenue_ambiguity.json` | §4.4 concept-collision evidence |
| `reports/phase0/k1_citation_remap.json` | K1 reproduction |
| `reports/phase0/k4_payload.json` | K4 payload audit |
| `reports/phase0/k7_routes.{csv,json}` | 20 routes with auth status |
| `reports/phase0/k8_chat_db.json` | schema + row counts only |
| `reports/phase0/k9_local_filter.json` | K9 refutation |
| `reports/phase0/k11_refusal_regex.{json,csv}` | 17 refusal cases |
| `reports/phase0/baseline_results_live.jsonl` | 5 live answers |
| `reports/phase0/baseline_scores.json` | deterministic scores |
| `reports/phase0/live/*.json` | raw live probe responses |
| `eval/phase0/baseline_questions.jsonl` | 25-question gold set |
| `eval/phase0/{run_baseline,score_baseline,run_live}.py` | runner, scorer, live driver |
| `eval/phase0/{build_manifest,fetch_filings,ixbrl_extract,crosscheck_companyfacts,revenue_ambiguity}.py` | evidence scripts |
| `tests/phase0/*` | pytest suite + audit scripts |
| `docs/DECISIONS.md` | five decision entries |
| `.cache/` | fetched filings, EDGAR JSON (regenerable; already gitignored) |

**One gitignore gap, not fixed here.** `.gitignore` already covers `.cache/`, but not
`.venv-phase0/` (it lists `.venv/`, not this name). Phase 0 may not edit existing files, so the
owner should add `.venv-phase0/` — or simply delete the directory, which is disposable.

### 9.2 Commands and exit codes

| Command | Exit |
|---|---|
| `uname -a` / `sw_vers` | 0 |
| `git status` | **128** — not a git repository |
| `python3 -c "import …"` (project interpreter) | 0 — all project packages MISSING |
| `python3 -m venv .venv-phase0 && pip install …` | 0 |
| `pytest tests/phase0/test_k1_citation_remap.py` | **1** — 2 failed (intended: K1 evidence) |
| `pytest tests/phase0/test_k11_refusal_regex.py` | 0 — 1 passed, 4 FN / 3 FP reported |
| `pytest tests/phase0/test_k9_local_filter.py` | 0 — 1 passed (K9 refuted) |
| `pytest tests/phase0/test_scorer_selftest.py` | 0 — 6 passed |
| `pytest tests/phase0/` (all) | 1 — **9 passed, 2 failed** (the 2 are the intended K1 evidence) |
| `python tests/phase0/audit_routes.py` | 0 — 20 routes |
| `python tests/phase0/audit_k8_chat_db.py` | 0 |
| `python tests/phase0/audit_k4_payload.py` | 0 |
| `python tests/phase0/audit_env.py` | 0 |
| `python eval/phase0/build_manifest.py` | 0 — 353 filings |
| `python eval/phase0/fetch_filings.py` | 0 — 11 documents (first attempt 403: UA) |
| `python eval/phase0/ixbrl_extract.py` | 0 (first run reported 0 facts — audit bug, §4.1) |
| `python eval/phase0/crosscheck_companyfacts.py` | 0 — 105/105 MATCH |
| `python eval/phase0/revenue_ambiguity.py` | 0 |
| `python eval/phase0/run_live.py` | 0 — 5 queries |
| `python eval/phase0/score_baseline.py` (local) | **1** — no results file, Step 3.2 not run |
| `python eval/phase0/score_baseline.py --results …live.jsonl` | 0 |
| `curl GET /health`, `/collections`, `/ingest/status` | 200, 200, 200 |
| `curl GET /admin/disk-usage` (no token) | **400** — see §7.2 |
| `curl POST /query` × 5 | 200 × 5 |

Live request count: **9** (limit 12). `POST /query` calls: **5** (limit 6).
No `POST`/`DELETE` to any admin route. `data/raw` never written.
