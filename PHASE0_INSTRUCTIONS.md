# Phase 0: Audit and baseline (read-only measurement)

**Read `CLAUDE.md` first.** This phase measures; it does not fix. Its output is a report the owner
will paste to the project architect, who will use it to decide Phases 1–5.

## Rules for this phase

- Work on a new branch: `git checkout -b phase0-audit`.
- **Do not edit any existing source file.** Only add files under `eval/phase0/`, `tests/phase0/`, `reports/phase0/`, `docs/`, `.cache/`. If a fix seems necessary, describe it in the report; do not apply it.
- If a step fails, record the error (command, exit code, last 20 lines of output) and continue with the next step. Never loop on a failure.
- Never print secret values. Only report whether `groq_api`, `edgar_email`, `admin_token` are set.
- **Never call any `POST` or `DELETE` route on the live site**, except `POST /query` (max 6 calls).
- Every LLM call goes through a disk cache (`.cache/llm_phase0.jsonl`, key = hash of model + messages) so reruns are free.
- Stop cleanly on a Groq daily-limit error: write partial results, mark remaining questions `not_run`, and say so in the report.
- Detect the OS first and adapt commands. Read and write files as UTF-8.

---

## Step 0: Environment and inventory

0.1 Print OS, Python version, and versions of: qdrant-client, fastembed, groq, ragas, pydantic, lxml, beautifulsoup4.

0.2 `git status`, `git log --oneline | head -20`, `git shortlog -sn --all`. Also compute, for each top-level
directory (`api`, `ingestion`, `retrieval`, `routing`, `generation`, `evaluation`, `ui`), the share of lines in HEAD
attributed to each author via `git blame --line-porcelain` (aggregate by author name). This is for the owner's
interview preparation, so keep it factual.

0.3 Inventory (names and counts only, no content dumps):
- Are `groq_api` / `edgar_email` set in `.env`? Is `admin_token` set?
- `data/raw/<TICKER>/<YEAR>/` present? How many primary documents? Total size.
- `data/parsed`, `data/chunks`: file counts.
- Qdrant collections from `retrieval.vector_store.list_collections()`, with point counts (`get_collection_stats`).
- Where is the filing manifest (the records with `ticker, fiscal_year, accession_number, filing_date`)? Print it as a table.

0.4 Import and warm-up test: `python -c "from query import ask"`; measure the time to load models (dense, sparse, reranker). Report any errors.

## Step 1: Static and unit checks (no network, no LLM)

Write tests under `tests/phase0/`. Use `pytest` and monkeypatching so existing source stays untouched.

1.1 **K1, citation remap.** Call `generation.synthesizer.synthesize()` with these patched: `decompose_query` and `decompose_temporal` (return two sub-questions), `retrieve` (return empty list), `generate_answer` (return sub-result 1 with 1 citation and text "A [1]."; sub-result 2 with 3 citations and text "B [1] C [2] D [3]."), and `_get_client` (a fake whose `chat.completions.create` captures `messages` and returns a dummy). Assert that the captured synthesis input contains distinct markers for the second sub-answer (`[2]`, `[3]`, `[4]`). Report PASS or FAIL and print the captured text. Also test a case with offset=2 and 3 citations. Do not fix.

1.2 **K11, refusal regex.** Run `query._is_refusal` over at least 12 strings: 6 real refusals in varied phrasing, and 6 legitimate answers that happen to contain words like "mention", "state", "provide". Report false positives and false negatives.

1.3 **K5, fiscal-year audit.** For every filing in the manifest/parsed set: ticker, `fiscal_year`, the period-end date from the SGML header if available, `filing_date`, and which extraction method produced `fiscal_year` (SGML, `reportDate`, or the accession-number fallback; call the downloader's helper functions read-only). Flag rows where `fiscal_year` differs from the year of `period_end`, where the fallback was used, or where `filing_date` is missing. Also list, for each of the 12 bundled tickers, the latest fiscal year present locally. If network is available, query EDGAR submissions for the latest 10-K per ticker (use `edgar_email` as the User-Agent, at most 1 request per second) and flag any filing newer than what is indexed.

1.4 **K9, Qdrant local filter behavior.** In a throwaway script, open one collection (e.g. `AAPL_2024`). Run `hybrid_search(..., chunk_type_filter="table")` and an unfiltered search with the same vectors. Report the `chunk_type` distribution of each. If the filtered result contains non-table chunks, K9 is CONFIRMED.

1.5 **K4, payload audit.** Print the payload keys of one stored point and one `Chunk` model dump. State whether `filing_date`, `period_end`, `accession_number` are present.

1.6 **K7, route exposure (static only).** Parse `api/app.py` and list every route, its method, and whether the handler checks `settings.admin_token`. State which routes are unauthenticated when the token is unset. Do not call them.

1.7 **K8.** Run `git ls-files data | head`. State whether `data/chat_history.db` is tracked, and whether it appears in git history (`git log --oneline -- data/chat_history.db`). Report table names and row counts only. Do not print any chat content.

## Step 2: iXBRL feasibility (decision D1)

Goal: decide whether numeric facts can be extracted reliably from the **already downloaded** primary documents.
This is read-only on `data/raw`. If `data/raw` is empty, say so and skip to Step 2.6.

2.1 For each `primary-document.html` (or equivalent), parse with lxml/BeautifulSoup in a **separate script**
(do not change `ingestion/parser.py`). Count `ix:nonFraction` and `ix:nonNumeric` elements. Record the distinct `name` attributes.

2.2 Build the context map: for every `xbrli:context`, record id, period (instant, or start/end), and whether it has
dimensional members (`xbrldi:explicitMember` / `typedMember`). Non-dimensional means consolidated total.

2.3 For each filing, find consolidated (non-dimensional) facts for the fiscal-year duration (about 365 days, ±10) ending at
the fiscal period end (instants for balance-sheet items) for these concepts, trying the listed alternates:
- Revenue: `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`; for banks/asset managers also `InterestAndDividendIncomeOperating`, `NoninterestIncome`, `RevenuesNetOfInterestExpense` and any "net revenue" custom concepts
- `NetIncomeLoss`, `OperatingIncomeLoss`, `GrossProfit`, `ResearchAndDevelopmentExpense`
- `Assets`, `Liabilities`, `StockholdersEquity`, `CashAndCashEquivalentsAtCarryingValue`
- `NetCashProvidedByUsedInOperatingActivities`, `PaymentsToAcquirePropertyPlantAndEquipment`, `EarningsPerShareDiluted`

Apply `scale` and `sign` attributes to get `value_usd` (fully scaled, signed). Record `unitRef` and `decimals`.

2.4 Output `reports/phase0/ixbrl_coverage.csv`: one row per (filing, concept) with found/not-found, the concept name that matched, `value_usd`, scale, and context id. Also print a coverage matrix summary: per ticker, the share of key concepts found.

2.5 **Cross-check.** For at least 10 (filing, concept) pairs across different sectors, compare the extracted value with the number shown in the rendered financial-statement table (use the parsed markdown). Report mismatches, and inspect scale/sign errors in particular.

2.6 **Independent check (if network).** For 3 filings, fetch SEC `companyfacts` for the CIK (User-Agent from `edgar_email`, 1 request per second) and compare the same concepts. If network is unavailable, state it plainly.

2.7 Special cases to report explicitly: Wells Fargo (financials are incorporated from an EX-13 exhibit; do the ix tags exist in the merged document?), banks' revenue definitions, and any filing where `ix:` tags are absent.

## Step 3: Baseline run of the current pipeline

3.1 Create `eval/phase0/baseline_questions.jsonl` with the 25 questions below (fields: `id`, `category`, `question`, `as_of` (nullable), `notes`, `expected_value_usd_millions` where given, `expected_section_ids` where given). Where the table says "from iXBRL", fill the expected value from your Step 2 extraction and mark it `expected_source: "ixbrl_unverified"`. The owner will verify these later. Values marked "existing test set" come from `evaluation/ragas_eval.py` and are marked `expected_source: "repo_test_set"`.

| ID | Category | Question | Expected / notes |
|---|---|---|---|
| N1 | numeric | What were Apple total net sales in fiscal year 2024? | 391,035 (existing test set) |
| N2 | numeric | What were Microsoft research and development expenses in fiscal year 2024? | 29,510 (existing test set) |
| N3 | numeric | What was JPMorgan Chase net income in fiscal year 2024? | 58,471 (existing test set) |
| N4 | numeric | What was Alphabet net income in fiscal year 2024? | from iXBRL |
| N5 | numeric | What was Amazon operating income in fiscal year 2023? | from iXBRL |
| N6 | numeric | What were BlackRock total assets at the end of fiscal year 2024? | from iXBRL (balance sheet, asset manager) |
| C1 | computed | What was Apple operating margin in fiscal year 2024? | 123,216 / 391,035 = 31.5% (existing test set) |
| C2 | computed | By what percentage did Alphabet research and development expense grow from fiscal year 2023 to fiscal year 2024? | (49,326 − 45,427) / 45,427 = 8.58% (existing test set) |
| C3 | computed | What was Goldman Sachs net income as a percentage of total assets in fiscal year 2024? | from iXBRL |
| M1 | compare | Compare JPMorgan Chase and Bank of America net income in fiscal year 2024. | both from iXBRL |
| M2 | trend | How did Amazon operating income trend from fiscal year 2023 to fiscal year 2025? | three values from iXBRL |
| M3 | compare | Compare Microsoft and Alphabet research and development spending in 2024. | 29,510 and 49,326 |
| R1 | narrative | What are the main risk factors JPMorgan Chase disclosed in its fiscal 2024 10-K? | `item_1a_risk_factors` |
| R2 | narrative | How does Microsoft describe its cybersecurity risk management and governance? | `item_1c_cyber` |
| R3 | narrative | What are Apple's reportable segments? | `item_1_business` or notes |
| R4 | narrative | Summarize the legal proceedings Wells Fargo disclosed for fiscal 2024. | `item_3_legal` (may be incorporated by reference) |
| T1 | ambiguity | What was Microsoft's revenue last year? | record which fiscal year is used; does the answer say so? |
| T2 | ambiguity | What is the latest annual revenue for Apple? | record the year; compare with the latest filing from Step 1.3 |
| T3 | lookahead | As of March 1, 2024, what was Microsoft's most recent annual revenue? | `as_of: 2024-03-01`; only the FY2023 10-K was public; citing FY2024 is a violation |
| T4 | lookahead | As of January 15, 2025, what was Alphabet's total revenue for fiscal year 2024? | `as_of: 2025-01-15`; the FY2024 10-K was not yet filed; should abstain |
| U1 | unanswerable | What will Apple's revenue be in fiscal year 2027? | should abstain |
| U2 | unanswerable | What was Tesla's closing stock price yesterday? | should abstain / out of scope |
| U3 | unanswerable | What was Acme Quantum Holdings' revenue in fiscal year 2024? | fictional company; should say no filings found |
| U4 | unanswerable | Which BlackRock executive was paid the most in 2024, and how much? | proxy-statement data, not in a 10-K; should abstain |
| S1 | units (optional) | What was Netflix's revenue in fiscal year 2024? | on-demand ingest; Netflix reports in thousands; check scale handling. Skip if ingest fails or takes over 10 minutes. |

For lookahead questions, v1 has no `as_of` parameter. Pass the date inside the question text as-is and record that the system ignores it.

3.2 Write `eval/phase0/run_baseline.py`. It runs questions in the priority order below, calling `query.ask()`, and records the
following **without modifying source files**, by wrapping functions at runtime (`functools.wraps` and monkeypatching
`query.classify_and_ensure`, `query.retrieve`, `query.generate_answer`, `query.synthesize`, and the Groq client's
`chat.completions.create`):
- classification output (type, tickers, years, focus, failed lookups);
- collections searched;
- for each retrieved chunk: id, ticker, fiscal year, section id, chunk type, score;
- final answer, citations;
- stage latency (classify, retrieve, rerank if separable, generate, synthesize) and total;
- for every Groq call: model, prompt tokens, completion tokens, retries, rate-limit/413 errors;
- any exception (type and message).

Priority order if the budget runs out: N1, N2, N3, C1, C2, M3, T3, T4, U1, U3, R1, T1, then the rest.
Sleep at least 4 seconds between questions. On `RateLimitError`, wait for the `retry-after` header (max 3 attempts), then
stop cleanly. Save after every question (`reports/phase0/baseline_results.jsonl`) so the run can resume with `--resume`.

3.3 **Deterministic scoring (no LLM judge).** Write `eval/phase0/score_baseline.py`:
- *Numeric/computed:* extract numbers from the answer (handle `$391,035 million`, `$391 billion`, `391.0B`, `45,183,036 thousand`, percentages); normalize to a common unit; PASS if within 0.5% of the expected value. Also report separately when the value is off by exactly a power of 1000 (scale error) or belongs to a different fiscal year than asked.
- *Compare/trend:* PASS only if every expected value appears and is attributed to the right company/year (best effort; flag for manual review when uncertain).
- *Unanswerable:* PASS if the answer contains an abstention marker (e.g. "cannot", "not available", "no filings", "outside the scope", "don't have") and contains no numeric claim; flag for manual review either way.
- *Lookahead:* for each cited (ticker, fiscal year), look up `filing_date` in the manifest. VIOLATION if any cited filing date is after `as_of`.
- *Narrative:* no auto-score. Report whether any cited section id is in `expected_section_ids` and list the cited sections.

3.4 **Retrieval diagnostics.** For every numeric question with an expected value, report whether any retrieved chunk text contains the expected number in common formats (`391,035`, `391035`, `$391.0 billion`) and the rank of the first hit. This separates "retrieval missed it" from "retrieval found it but the LLM misread it".

## Step 4: Live deployment check (read-only)

Live URL: `https://financialrag-production-420e.up.railway.app/`

4.1 `GET /health`, `GET /collections`, `GET /ingest/status`. Compare the collection list with the local one.

4.2 `POST /query` for only these 5 questions: N1, C1, M3, T3, U3. Record the answer, latency, and any difference from the local result.

4.3 **Security probe (read-only):** `GET /admin/disk-usage` with no token. Report only the HTTP status code and whether the response had content. If it is 200, the live deployment has no admin token set, and the report must state that prominently. Do **not** call any other admin route, and never `POST`/`DELETE` anything except the five `/query` calls above.

Keep the total live request count under 12.

## Step 5: Report

Write `reports/phase0/REPORT.md` with these sections, and also print the executive summary to the terminal:

1. **Executive summary** (max 12 bullets, plain language).
2. **Environment and inventory** (Step 0), including the authorship table.
3. **Known-issues register:** K1–K11, each marked CONFIRMED / REFUTED / INCONCLUSIVE, with a one-line evidence pointer (file, test, or output).
4. **iXBRL feasibility verdict:** coverage numbers per ticker, cross-check mismatches, banks/asset-manager caveats, EX-13 findings, and a GO / NO-GO / PARTIAL recommendation for decision D1 with reasons.
5. **Baseline results:** pass rate per category; a row per question (expected, answer value, PASS/FAIL, failure stage: classification, retrieval, reading, scale, year, or abstention); latency p50/p95 per stage; tokens per query; any rate-limit events; how many questions were `not_run`.
6. **Top 5 failures, analyzed:** for each, show the classification, what was retrieved, what the answer said, and your diagnosis.
7. **Live vs local:** differences, plus the security finding from 4.3.
8. **Open questions for the owner** (things only a human can decide or verify).
9. **Appendix:** every command run with its exit code.

When finished, print: the report path, the executive summary, and the line
`PHASE 0 COMPLETE — not starting Phase 1 until the owner approves.` Then stop.
