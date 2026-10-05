# Phase 4 — Evaluation, ablations, documentation

Branch `phase-4-evaluation`. Every number below was produced by a command named
beside it; the raw per-item rows are in `reports/phase4/runs*/ *.jsonl` and
scoring is reproducible from them without spending any budget.

> ## How to read this report
>
> It was written in two passes and reconciled end to end at **P4-15
> (2026-10-05, spec v1.11)**.
>
> **Sections 1 to 10 are the pre-re-index, pre-context-fix body**, written
> against spec v1.6. **The addendum (A1 onward) is current and takes
> precedence.** Every statement in the body that the addendum changed is now
> either corrected in place or marked `SUPERSEDED` with a pointer to the
> addendum section that replaces it. **No number appears twice with two values
> and no note of which one is current** — where both are kept, the pre-fix one
> is labelled as "before" evidence.
>
> **The headline is post-fix: V3 73/80 (91.2%), zero flags** (A1).
>
> **Verification status, final.** The gold sheet is signed and applied: **37 of
> 80 expected answers carry `verified_by=owner`** (every numeric and computed
> item, hand-checked against the printed line in the filing); **14 are
> `companyfacts`** and **29 are `auto`**. Nothing is "provisional" any more.
> **The narrative rating gate was NOT completed** — narrative results are the
> automated scorer's, labelled "not human-verified" everywhere, and **D31 was
> not applied because its precondition was never met**. See
> [A14](#a14-p4-15--closing-the-phase).

---

## 1. Summary

> **SUPERSEDED where it conflicts with [A1](#a1-headline--pre-fix-versus-post-fix).**
> This summary was written before the re-index (P4-12) and the context-budget
> fix (P4-16). The current headline is **V3 73/80 (91.2%) full set, 62/69
> (89.9%) on the frozen paired subset, zero flags**. The bullets below are the
> measurement as it stood then; three of them are explicitly corrected.

* **The shipped system answers 60 of 69 gold items (87.0%, 95% CI 77.0–93.0);
  the v1 prototype answers 19 (27.5%, 18.4–39.0)** on the same items with the
  same scorers. → **Current: V3 62/69 (89.9%, 80.5–95.0); V0 unchanged at
  19/69** (A1).
* **Zero look-ahead violations in V3; four in V2**, the variant with
  point-in-time scope switched off. The guard is not decorative, and the
  ablation can produce the failure it prevents.
* **v1 cannot refuse at all** — 0 of 10 abstention items, 14 scored
  `answered_wrongly` — and **36 of its answers carry citations with no
  accession and no filing date**, so a reader cannot reach the document.
* **The facts engine is worth 29 points** on the paired subset (V1 59.4% → V3
  87.0%) and **takes numeric accuracy from 12/18 to 17/18**. The facts path
  makes **no model call at all**: V3 spent 16 generation calls over 80
  questions, all on narrative items. → **Current: 42 points on the frozen
  subset (V1 47.8% → V3 89.9%) and numeric 11/18 → 18/18** (A1) — but see
  [A12](#a12-p4-17--the-v1-generous-arm-d32): **13 of the 36-item full-set gap
  is context budget, not the facts engine.** The zero-model-call property is
  unchanged and is measured at **54,047 generator tokens for all 80 questions**
  (A3).
* **P4-00's boundary rewrite was adopted.** Usable (filing, section) pairs went
  from 245 to 298 of 440; Items 1 and 1A from 33 and 20 to **40 of 40**. Item 7
  reached 25 of 40 against a target of 33 and the owner accepted the miss
  (D4-00).
* ~~**The cross-encoder earns nothing on these metrics and costs 200
  seconds.**~~ **REFUTED at n=45** (A7). On 45 items the cross-encoder is
  **+13.3 pp hit@5 and +0.110 MRR**, and the shipped default reaches **44/45
  (97.8%), MRR 0.870** against dense-only's 31/45. D27's rule, pre-registered
  before the data was seen, is what stopped this bullet being acted on.
* **Parent-section context is what puts figures in front of the model**:
  removing it drops expected-number-in-context from 95% to 82% while leaving
  section hit unchanged.
* Three defects were found by the evaluation and fixed inside it; two product
  defects and one capability gap were left open and named in section 6. →
  **All five of section 6's open defects are now closed** (P4-11, P4-16); the
  one genuinely open product defect is the **narrative over-refusal rate**,
  5 of 15.
* **1,428** offline tests pass. CI runs lint, hygiene, the suite and an offline
  mini-evaluation with two thresholds, all with no network and no key — **and
  its first run failed 15 tests that pass locally**, all three causes being
  tests or code that silently depended on the developer's machine (defects
  10–12). That is the clearest argument for the workflow existing.

---

## 2. What changed

| commit | what |
|---|---|
| `d6885c2` | spec v1.6 (owner's), carried onto the branch |
| `63516cb` | P4-00: global section-boundary assignment replaces the greedy filter |
| `cf1c49d` | P4-00 measurement, `scripts/compare_section_audits.py`, D4-00 |
| `1f47af0` | owner closes the P4-00 gate |
| `c43319d` | P4-01: the gold set, its plan, generator and schema validator |
| `d7b6d98` | P4-02: the owner verification sheet |
| `89dd1d0` | P4-03: `eval/scorers.py` |
| `58d665a` | P4-04: `eval/runner.py`, `eval/variants.yaml`, the ablation switches |
| `2fb1729` | P4-05: the D21 paired subset |
| `a50960c` | P4-06: retrieval switches, `eval/ablations.py`; the `as_of` ablation fix |
| `d24a8d2` | P4-08: CI, its local mirror, `eval/mini_eval.py` |
| `9ead4b8` | V1/V2 runs, free-tier pacing, `docs/EVAL.md`, `docs/LIMITATIONS.md` |

New modules: `eval/gold/` (plan, builder, schema, seeds), `eval/scorers.py`,
`eval/runner.py`, `eval/subsets.py`, `eval/ablations.py`, `eval/mini_eval.py`,
`eval/phase4/run_v0.py`, `scripts/compare_section_audits.py`,
`scripts/make_gold_verification_sheet.py`, `scripts/ci_local.sh`,
`.github/workflows/ci.yml`.

---

## 3. Tests

`make test` — **1,428 passed, 0 failed, 0 skipped**, 36.8s, at P4-15. Artifacts
in `reports/phase4/tests/`. (This section's per-test table below lists the suite
as it stood at the first pass, 1,173; A11 lists what the addendum added, and the
1,428 above is the current total, which includes P4-15's own
`test_gold_owner_verification.py`.)

| id | what it covers | result |
|---|---|---|
| T4-01 | scorer adversarial cases: swapped values, scale error, wrong year, negatives, abstention containing numbers, percent vs fraction | pass (40 tests) |
| T4-02 | gold schema validator: provenance, duplicate ids, `as_of` against the catalog, abstain reasons | pass (27 tests) |
| T4-03 | runner determinism with a stub pipeline; resume after interruption; rate-limit recovery | pass (19 tests) |
| T4-04 | CI workflow and its local mirror run the same steps; no secret; network blocked | pass (11 tests) |
| T4-05 | every number in this report regenerable from the raw JSONL by one command | pass — see section 8 |
| T4-06 | boundary selection: both strict `xfail` cases now pass; negative control; determinism; audit reproduced by one command | pass (12 tests) |
| T4-07 | V0 index isolation: the backup is selectable by configuration, never shares a path, is not the default | pass (3 tests) |

Coverage of the new modules: `eval/scorers.py` and `eval/gold/schema.py` are
exercised by their adversarial suites; the overall figure is in
`reports/phase4/tests/coverage.xml`.

---

## 4. Metrics

### 4.1 Variants, on the D21 paired subset

69 of 80 items. The 11 excluded name BAC, IVZ, STT, TROW or WFC filings, or JPM
FY2022, which are in the facts store but not the text index — a variant that
reads text has no corpus for them, and scoring that as wrong would measure the
indexing backlog rather than the ablation.

| variant | | paired | 95% CI | look-ahead | rate-limited |
|---|---|---:|---|---:|---:|
| **V3** | the shipped system | **60/69 (87.0%)** | 77.0–93.0 | **0** | 2 |
| V2 | `as_of` + abstention gate off | 50/69 (72.5%) | 61.0–81.6 | **4** | 2 |
| V1 | facts engine off | 41/69 (59.4%) | 47.6–70.2 | 0 | 13 |
| V0 | the v1 prototype | 19/69 (27.5%) | 18.4–39.0 | 0 | 0 |

```bash
python -m eval.runner --variant V3 --report-only
```

### 4.2 By category, paired

| category | V0 | V1 | V2 | V3 |
|---|---:|---:|---:|---:|
| numeric | 11/18 | 12/18 | 17/18 | **17/18** |
| computed | 2/12 | 3/12 | 10/12 | **10/12** |
| compare / trend | 0/6 | 4/6 | 6/6 | **6/6** |
| narrative | 4/15 | 10/15 | 10/15 | **10/15** |
| `as_of` | 2/8 | 3/8 | 4/8 | **8/8** |
| abstain | 0/10 | 9/10 | 3/10 | **9/10** |

Read the two ablation columns against V3 rather than against each other:

* **V1 (no facts engine)** loses numeric (17→12), computed (10→3) and
  compare/trend (6→4). Those are the questions the facts engine exists for.
  Narrative is unchanged, as it should be — V1 does not touch the text path.
* **V2 (no `as_of`, no abstention gate)** loses `as_of` (8→4) and abstain
  (9→3) and nothing else. The four lost `as_of` items are the four look-ahead
  violations: with scope off, the system answers from filings that were not
  public on the date asked.

### 4.3 Verdicts

| verdict | V0 | V1 | V2 | V3 |
|---|---:|---:|---:|---:|
| `correct` | 6 | 17 | 58 | 58 |
| `correct_text_only` | 13 | 15 | – | – |
| `correct_abstain` | – | 9 | 3 | 13 |
| `wrong_value` | 31 | 7 | 4 | 4 |
| `answered_wrongly` | 14 | – | 5 | – |
| `abstained_wrongly` | – | 15 | 1 | 1 |
| `wrong_abstain_reason` | – | 4 | 6 | 1 |
| `wrong_metric` | – | – | 1 | 1 |
| `partial_multi` | 4 | – | – | – |
| `scale_error` | **1** | – | – | – |
| `error` (rate limit) | – | 13 | 2 | 2 |
| `not_run` | 11 | – | – | – |

`correct` versus `correct_text_only` is the traceability distinction: V3's
answers carry a fact citation holding the exact value, v1's have the right
number in the prose and nothing to check it against.

### 4.4 Flags

| flag | V0 | V1 | V2 | V3 |
|---|---:|---:|---:|---:|
| `look_ahead` | 0 | 0 | **4** | **0** |
| `uncited_number` | 15 | 18 | 0 | 0 |
| `citation_invalid` | **36** | 0 | 0 | 0 |

v1's citations carry a ticker, a fiscal year and a section title, and no
accession and no filing date — there is no way to reach the document from one.
That is what G1 was written against, and the count is the measurement of it.

### 4.5 Cost and latency

| variant | model calls | tokens | p50 | p95 |
|---|---:|---:|---:|---:|
| V3 | 16 | 1,057,394 | **0.03s** | 9.0s |
| V2 | 21 | 1,412,039 | 0.03s | 11.2s |
| V1 | 60 | 3,954,213 | 5.2s | 14.6s |
| V0 | not captured | not captured | 5.4s | 57.3s |

V3's p50 of 0.03s is the facts path answering without a model. V1's 60 calls
over the same 80 questions is what happens when every number has to be read out
of retrieved text — and it is why V1 hit the free-tier token limit.

**V0's per-role accounting is not captured**: it runs inside a worktree at the
`v1-baseline` tag as a subprocess, and v1's client is not instrumented. Its
latencies are wall-clock per item and are comparable; its token counts are not
available and are reported as absent rather than estimated.

No cost figure appears anywhere: free-tier providers only (D18), so there is no
price to multiply by, and inventing one would put a fabricated number in a
results table.

### 4.6 Retrieval ablations

> **SUPERSEDED on the conclusions, kept for the measurement.** This table is
> the **pre-re-index** run at **n=15 narrative items**. Every *conclusion*
> drawn from it below was reversed at n=45 on the live index — see
> **[A7](#a7-p4-13-and-d27--the-cross-encoder-stays-on)**, which is current.
> The numbers in this table are correct for the artifacts and the n they were
> measured on; they are not the system's retrieval quality today.
>
> | | here (n=15, old index) | **current (A7, n=45, live index)** |
> |---|---|---|
> | shipped default | 12/15 (80%), MRR 0.717 | **44/45 (97.8%), MRR 0.870** |
> | cross-encoder | appeared to *lose* a hit | **+13.3 pp, +0.110 MRR — stays on (D27)** |
> | dense-only | appeared to be the best arm | 31/45 (68.9%), clearly behind the default |

No generation tokens. Each arm calls `retrieve()` directly and is scored on what
it put in front of the model. 15 narrative and 22 numeric items.

| arm | section hit@5 | MRR | number in context | retrieval s |
|---|---:|---:|---:|---:|
| `bm25` | 9/15 (60%) | 0.433 | 21/22 (95%) | 5.9 |
| `dense` | 12/15 (80%) | **0.747** | 21/22 (95%) | **1.6** |
| `hybrid` | 12/15 (80%) | 0.578 | 21/22 (95%) | 3.1 |
| `hybrid_rerank` | 11/15 (73%) | 0.556 | 21/22 (95%) | 203.2 |
| `hybrid_rerank_focus` *(shipped)* | 12/15 (80%) | 0.717 | 21/22 (95%) | 205.9 |
| `…focus, parent context off` | 12/15 (80%) | 0.717 | **18/22 (82%)** | 187.8 |

```bash
python -m eval.ablations
```

Three findings as written at the time. **Findings 1 and 2 are now known to be
noise and were refuted at n=45 (A7); finding 3 stands.** They are left in place
because the sequence is the result: a 15-item ablation pointed confidently in
the wrong direction, and only a pre-registered decision rule (D27, written
before the n=45 data was seen) prevented acting on it.

1. ~~**The cross-encoder costs a section hit and 200 seconds.**~~ **REFUTED at
   n=45.** `hybrid` → `+rerank` is **+13.3 pp and +0.110 MRR** on 45 items
   (A7). The n=15 reading — 12/15 → 11/15 — was one item.
2. ~~**Dense alone is the best arm on these metrics.**~~ **REFUTED at n=45.**
   Dense is 31/45 (68.9%, MRR 0.544) against the shipped default's 44/45
   (97.8%, MRR 0.870). The note in the original text — that the interval around
   12/15 is roughly 55–93% and this is a reason to measure on a larger set —
   was the correct reading of it.
3. **Parent-section context is what gets figures in front of the model**: it
   does not change which section is retrieved, but removing it drops
   expected-number-in-context from 95% to 82%. **Still true**, and A5 gives it
   its boundary: *unbounded* parent context is what caused defect E, so the
   shipped path keeps the parent window and bounds it.

`number_in_context` at 95% across every arm is a ceiling result: when the text
path gets a number wrong, it is almost never because retrieval failed to put the
figure in the context.

### 4.7 D25's old-index-versus-new-index arm

| arm | current index | v1 backup index |
|---|---|---|
| `bm25` | 9/15, MRR 0.433 | 9/15, MRR 0.433 |
| `dense` | 12/15, MRR 0.747 | 12/15, MRR 0.747 |
| `hybrid` | 12/15, MRR 0.578 | 12/15, MRR 0.544 |
| `hybrid_rerank_focus` | 12/15, MRR 0.717 | 12/15, MRR 0.717 |

> **SUPERSEDED by [A8](#a8-d25--what-the-parser-rewrite-was-worth).** The
> re-index has since run, and A8 is the comparison D25 actually asked for:
> on the 24 filings both stores hold, the shipped default moves **30/40
> (75.0%) → 37/40 (92.5%), +17.5 pp**. The table above is the *control* that
> preceded it.

The re-index of P4-00 step 4 was deferred by the owner **at the time this
section was written**, so both stores held the same pre-rewrite parse and the
two columns were expected to agree — which they do. This is a check on the
backup, not a result about the parser.

One unexplained difference: `hybrid` MRR is 0.578 against 0.544 on byte-identical
content. Both runs made zero errors and every other arm agrees exactly, so this
is most likely tie-breaking order inside Qdrant's RRF fusion rather than a
difference in the data. It is recorded because an unexplained difference in a
control is worth more as a note than as a rounding.

### 4.8 P4-00 section-boundary rewrite

Measured offline over all 40 parsed filings, before anything was re-indexed.

| | before | after | D25's target |
|---|---:|---:|---|
| usable (filing, section) pairs | 245/440 | **298/440** | — |
| Item 1 Business | 33 | **40/40** | ≥ 33 of 39 ✓ |
| Item 1A Risk Factors | 20 | **40/40** | ≥ 33 of 39 ✓ |
| Item 7 MD&A | 20 | 25/40 | ≥ 33 of 39 ✗ |

```bash
python scripts/reparse_corpus.py --out-dir /tmp/after
python scripts/audit_sections.py --parsed-dir /tmp/after --out-dir /tmp/after_audit
python scripts/compare_section_audits.py \
    reports/phase4/section_audit_before.json /tmp/after_audit/section_audit.json
```

65 pairs gained, 12 lost. The 12 are four classes × three years, each read
first-hand, and three of the four are the audit penalising a *more* correct
parse — BAC's Item 7A really is a 149-character cross-reference, and the old
22 kB slice was that sentence plus the next section's tables. Full detail in
D4-00. The owner accepted the Item 7 miss and left the audit's thresholds alone.

---

## 5. Deviations and decisions

| | |
|---|---|
| **D4-00** | the boundary rewrite is adopted although Item 7 misses D25's target; D25's exit rule was **not** applied. Owner-confirmed at the gate. |
| **Audit thresholds unchanged** | 12 of the 15 Item 7 misses are cap-bound, not boundary defects. Reported, not fixed by moving the measure. |
| **Re-index deferred** | P4-00 step 4 was not run. Every narrative number in this report therefore describes the **pre-rewrite** parse. |
| **V0 deviations** | v1's own Groq models are Enterprise-only on this key and returned 404 to every request, so the Phase 1 gpt-oss substitutes are used; and v1's on-demand ingestion is disabled, because fetching filings mid-evaluation would rewrite the corpus. Without the first, V0 measures an outage; without the second, an accidental download. |
| **One gold item corrected** | `X-NO-ENTITY` expected `company_not_found`; the system returns `clarification_needed`, which is better and is what spec 6.5's clarification path is for. The expectation was wrong, not the system. The item now accepts either, and says so. |
| **`logs/phase4/`** | raw run logs are on disk and gitignored, as in every earlier phase. |
| **V0 worktree** | a `v1-baseline` worktree already exists at `/Users/dhamo_85/Downloads/FinancialRAG_v1_baseline`; this phase created a temporary one in the scratch directory and removed it afterwards. A future V0 run can point `--worktree` at the existing checkout. |

---

## 6. Defects

### Found and fixed inside this phase

| # | what | how it was found |
|---|---|---|
| 1 | **The `as_of` ablation ablated nothing.** V2 dropped the `as_of` parameter but the gold questions carry the date in the sentence and the router parses it from there. V2 was identical to V3 on every point-in-time item, and would have been published as "the guard makes no difference". | the first V2 run |
| 2 | **Narrative scored 0 of 15 live.** A `Citation` carries `section` as the display title; the gold set names sections by id. Retrieval was correct — Apple's supply-chain question cited Item 1A three times. | the first live V3 run |
| 3 | **`[1]` read as the quantity 1.** 1 shifted eleven places lands near 115,877,000,000, so an answer about an entirely different metric was labelled `scale_error`, pointing at the extractor and hiding a routing defect. | the first FakeLLM run |
| 4 | **The scale test shifted its tolerance with the value**, so a larger exponent bought a looser test. | fixing 3 |
| 5 | **A date read as a quantity**: "nearest number stated was '-09'" for a ratio question, from "year ended 2024-09-28". | V3 failure analysis |
| 6 | **`RecordingLLM` passed `role` twice**, raising `TypeError` on every instrumented call. | T4-03 |
| 7 | **An ablation arm that scored nothing printed `0/0 (0%)`** as though it were a measurement. Qdrant's local mode takes an exclusive lock, an evaluation held it, and every retrieval raised. | the first ablation run |
| 8 | **The Makefile still wrote test artifacts to `reports/phase3/`**, overwriting Phase 3's committed junit and coverage — the exact hazard the Makefile's own comment warns about. | `make test` at the Phase 4 start |
| 9 | **CIKs keyed by ticker** sent BlackRock's FY2024 oracle lookup to the wrong companyfacts document (BLK files under two CIKs, D24), so the item was silently written as `auto` — a verification skipped rather than failed. | the gold builder |
| 10 | **`generation/generator.py` downloaded a tokenizer at import.** `tiktoken.get_encoding("cl100k_base")` at module scope fetches the BPE file when it is not cached, so *importing* the module was a network call. Invisible on any machine that has the file. Now lazy. | the first CI run |
| 11 | **Seven tests needed `edgar_email` from the developer's `.env`.** `DocumentFetcher` builds its User-Agent even when the session is faked and no request is made, so T1-05 ("the suite passes with no `.env`") quietly did not hold. | the first CI run |
| 12 | **`test_cli_runs_offline` was reading the developer's real `.cache/edgar`.** `EdgarFetcher`'s `cache_dir` default was bound at import, so `monkeypatch.setattr("catalog.build._CACHE_DIR", ...)` had no effect. The test passed for three phases for the wrong reason. It now has a negative control: with an empty patched cache the build must find nothing. | the first CI run |

### Open at the time this section was written — ALL FIVE ARE NOW CLOSED

> **SUPERSEDED.** Every row below was closed by P4-11 (defects A–C), P4-16
> (defects D and E). The closures are in [A5](#a5-p4-16-d30--defect-e-and-the-two-bugs-inside-its-own-fix)
> and in D4-02 / D4-07. The paragraph that followed this table — "Defects A–D
> were not fixed … A and B are the right first work for Phase 5" — **is no
> longer true and is withdrawn.**

| # | what | status now |
|---|---|---|
| A | **The router does not route "ratio of X to Y" or "A less B".** `facts/calc.py` implements both; the router answered them as a plain numeric fact for the first operand. | **CLOSED (P4-11).** The defect was not in the router: `query._compute` paired two metrics only for a *margin*, so a two-metric ratio answered with the numerator alone. **12 of 12 computed gold items now score correct.** |
| B | **"Cash flow from operating activities" resolves to `cash_and_equivalents`.** | **CLOSED (P4-11).** Longest-match aliasing was already in place; the cash-flow phrase was simply missing from `facts/concepts.yaml`. |
| C | **`X-PERIOD-NOT-COVERED` refuses with `metric_not_found_in_filing`** instead of `period_not_covered`. | **CLOSED (P4-11).** `period_not_covered` is now decided from `FactsStore.has_facts()`; `facts_built_at` only records that the build *ran*, and four flagged submissions hold no facts. **5 of 5 facts-path abstention items now score correct.** |
| D | **Over-refusal on `R-MSFT-SEGMENTS-2025`.** | **CLOSED (P4-16).** It was a symptom of defect E, not a retrieval defect: the question's context was unservable. It scores `correct` post-fix, at section rank 2. |
| E | **13 V1 items and 2 each in V2/V3 remain rate-limited** after a paced re-run. | **CLOSED, and it was misdiagnosed.** It was never a rate limit: the text path sent each chunk's whole parent section, once per chunk, and the last refusal was `LLMBudgetExceeded`, a subclass of `LLMRateLimited`. Post-fix **V1 runs 80/80 with zero errors.** The new `llm_prompt_too_large` code exists so this cannot be mislabelled again. Full account in [A5](#a5-p4-16-d30--defect-e-and-the-two-bugs-inside-its-own-fix). |

**The one genuinely open product defect is the narrative over-refusal rate** —
5 of 15 narrative gold items refuse with `insufficient_evidence` although the
section audit says the expected section exists and carries the topic, with
retrieval at 97.8% hit@5. That is the top item of future work (P5-13), and the
evidence for it is automated, not rated.

---

## 7. Owner actions and questions — resolved

> **SUPERSEDED.** All four items below have an outcome. Current status is in
> [A14](#a14-p4-15--closing-the-phase).

| asked | outcome |
|---|---|
| 1. **Sign the gold verification sheet** | **DONE.** `gold_verification_signed.csv`, 37 rows OK, 25 of 25 `core`. Applied at P4-15 (D4-06): **37 items now carry `verified_by=owner`; the other 43 stay `companyfacts` (14) or `auto` (29)** and are described that way everywhere. |
| 2. **Rate 15 sampled narrative answers** | **NOT COMPLETED.** The sheet was built (P4-14, A9) and a blind reading view with it (P4-14b), but the ratings returned were unusable — columns offset by one, and the refusal rows carried no link so the `none` entries were defaults rather than judgements. **The narrative gate is recorded as not completed**, narrative results are automated-scorer-only and labelled "not human-verified", and **D31 was not applied because its precondition was never met** (A14). |
| 3. **Decide when to run the overnight re-index** | **DONE.** P4-12 ran and was activated: live store **39 collections / 37,882 points**, reversible with one command (A6). D25's comparison followed (A8). |
| 4. **Question: is the cross-encoder worth 200 seconds?** | **ANSWERED by measurement, and the n=15 reading was wrong.** At n=45 the cross-encoder is **+13.3 pp hit@5 and +0.110 MRR**; D27's pre-registered rule says keep it on, and `config.py` is unchanged (A7). |

---

## 8. Gate checklist

Current as of P4-15 (2026-10-05). Rows that were outstanding when this section
was first written are now resolved; one is resolved as **not done**, and says so.

- [x] Gold set built, schema-validated, byte-identical on rebuild (80 items)
- [x] Expected values confirmed against an independent oracle (47 of 47 generated items)
- [x] Owner verification sheet generated (37 rows, 25 core)
- [x] **Owner verification sheet signed** — 37 OK, 25 of 25 core; **applied at P4-15**: 37 `owner`, 14 `companyfacts`, 29 `auto`
- [x] Scorers with adversarial cases (T4-01)
- [x] Runner: resumable, deterministic, every LLM role instrumented (T4-03)
- [x] V0–V3 run on the gold set; N reported explicitly; paired subset reported
- [x] V1-generous arm run and reported beside the shipped-budget rows (D32 / P4-17)
- [x] Retrieval ablations, no generation tokens — **at n=45 on the live index** (A7); the n=15 pre-re-index run is kept in 4.6 and marked superseded
- [x] Failure analysis: every failure categorised (section 6, reconciled)
- [x] Re-index run, verified independently, activated, reversible (P4-12, T4-09, A6)
- [x] CI green locally through the script mirror (T4-04)
- [x] `docs/EVAL.md`, `docs/LIMITATIONS.md`, README rewritten — **and again at P4-15** for the final numbers
- [x] Results table with n/N and Wilson intervals
- [ ] **Owner narrative rating — NOT COMPLETED.** The sheet and a blind reading
      view were built (A9, P4-14b); the ratings returned were unusable (columns
      offset by one; refusal rows carried no link, so `none` entries were
      defaults, not judgements). **Narrative results are automated-scorer-only
      and are labelled "not human-verified" everywhere.** No
      scorer-versus-owner agreement figure exists. **D31 was not applied — its
      precondition was never met** (A14).
- [x] All phase tests pass — **1,428 passed, 0 failed, 0 skipped**, locally and under CI's isolation (no `.env`, no warm caches)

Every number in this report is regenerable (T4-05):

```bash
python -m eval.gold.build_gold --check        # gold set, incl. verified_by tiers
for v in V0 V1 V2 V3; do python -m eval.runner --variant $v --report-only; done
python -m eval.runner --variant V1-generous --report-only
python scripts/score_d21_subset.py            # the frozen 69-item paired subset
python -m eval.ablations                      # n=45, live index
python scripts/compare_section_audits.py \
    reports/phase4/section_audit_before.json reports/phase4/section_audit_after.json
python scripts/compare_before_after.py        # per-item, both directions
```

---

## 9. Proposals (not built)

1. ~~**A narrative rating sheet generator.**~~ **BUILT** as P4-14
   (`scripts/make_rating_sheet.py`, A9) and P4-14b (the blind reading view).
2. ~~**Route `ratio` and `difference`** (defect A).~~ **DONE** in P4-11; the
   defect turned out to be in `query._compute`, not the router.
3. ~~**A larger narrative gold set.**~~ **BUILT** as
   `eval/gold/narrative_retrieval_v1.jsonl`, 45 items (P4-13, A7) — and it
   reversed two of section 4.6's three findings.
4. **Instrument v1's client in the V0 child**, so the baseline's token cost is
   measured rather than absent.
5. **Reconsider the audit's narrative cap** once a bank's MD&A length is argued
   from the documents rather than assumed — currently 12 Item 7 misses are
   cap-bound.

---

## 10. Appendix — commands

| command | exit |
|---|---|
| `make test` | 0 — **1,428 passed**, 0 failed, 0 skipped |
| `make lint` | 0 |
| `./scripts/ci_local.sh` | 0 |
| `python -m eval.gold.build_gold --check` | 0 — matches a fresh build |
| `python -m eval.mini_eval` | 0 — numeric+computed 9/9, look-ahead 0 |
| `python -m eval.runner --variant V3` | 0 |
| `python -m eval.runner --variant V2` | 0 |
| `python -m eval.runner --variant V1 --retry-rate-limited --sleep 25` | 0 |
| `python eval/phase4/run_v0.py --worktree <v1-baseline checkout>` | 0 — 69 rows over two invocations (3 in a smoke run, 66 in the full one) |
| `python -m eval.ablations` | 0 — n=15 pre-re-index (section 4.6, superseded) and n=45 on the live index (A7, current) |
| `python -m eval.ablations --index data/qdrant_v1_backup --label "v1 backup index"` | 0 |
| `python scripts/audit_sections.py --parsed-dir <re-parse>` | 1 — findings, as designed |
| `python scripts/compare_section_audits.py --self-test` | 0 |
| `python scripts/make_gold_verification_sheet.py` | 0 |
| the suite under CI's isolation (empty cwd, `PYTHONPATH`, fresh `HOME`) | 0 — same suite, 0 failed |

Raw logs: `logs/phase4/` (gitignored).

---

# Addendum — P4-12, P4-13, P4-16 (spec v1.10, written 2026-10-04)

Everything above describes the **pre-re-index** system. This addendum covers the
re-index (P4-12), the expanded narrative set and the reranker decision (P4-13,
D27), the D25 arm, and the context-budget fix (P4-16, D30).

**Post-fix numbers are the headline.** The pre-fix numbers are kept because the
owner asked for the comparison, and because one of them is misleading in a way
worth stating: pre-fix, 14 V1 items and 1 V3 item were *unanswerable* — prompts
no provider would accept — while every other item received unlimited context.
Post-fix, every item is answerable inside a budget a real provider serves. Only
the post-fix column describes a system that can actually run.

**Verification status (updated at P4-15).** The gold sheet is signed and **now
applied**: 37 items carry `verified_by=owner`, 14 `companyfacts`, 29 `auto`.
**The narrative rating was not completed**, so every narrative figure below is
the automated scorer's, not a person's, and is labelled that way. Nothing here
is "provisional" — it is final, with its verification tier stated.

## A1. Headline — pre-fix versus post-fix

| variant | full 80, pre-fix | **full 80, post-fix** | paired 79, post-fix | flags, post-fix |
|---|---|---|---|---|
| V0 (v1 baseline, frozen) | — | — | — | 15 uncited, 36 invalid citations |
| V1 (no facts engine) | 48/80 (60.0%) | **37/80 (46.2%)** | 37/79 | 19 uncited numbers, **0 errors** |
| V2 (no as_of, no gate) | 64/80 (80.0%) | **63/80 (78.8%)** | 62/79 | 4 look-ahead (by design) |
| **V3 (shipped)** | 74/80 (92.5%) | **73/80 (91.2%)** | **72/79** | **none** |

On the **frozen D21 69** — the only set on which V0 is a fair control, since V0
runs against the 25-collection backup (`scripts/score_d21_subset.py`):

| | V0 | V1 | V2 | **V3** |
|---|---|---|---|---|
| overall | 19/69 (27.5%) | 33/69 (47.8%) | 52/69 (75.4%) | **62/69 (89.9%)** |
| 95% CI | 18.4–39.0% | 36.5–59.4% | 64.0–84.0% | **80.5–95.0%** |
| numeric | 11/18 | 11/18 | 18/18 | **18/18** |
| computed | 2/12 | 4/12 | 12/12 | **12/12** |
| compare/trend | 0/6 | 1/6 | 6/6 | **6/6** |
| as_of | 2/8 | 3/8 | 4/8 | **8/8** |
| narrative | 4/15 | 8/15 | 8/15 | 8/15 |
| abstain | 0/10 | 9/10 | 4/10 | **10/10** |

The **live** paired subset is now 79 of 80 (only JPM FY2022 is unindexed), up
from 69, because P4-12 indexed BAC, IVZ, STT, TROW and WFC. Both are reported:
a "paired" column computed today is no longer paired with the frozen V0.

## A2. By category, pre-fix → post-fix (full 80)

| category | V1 | V2 | V3 |
|---|---|---|---|
| numeric (25) | 17 → **12** | 25 → **25** | 25 → **25** |
| computed (12) | 4 → **4** | 12 → **12** | 12 → **12** |
| compare/trend (10) | 6 → **1** | 10 → **10** | 10 → **10** |
| as_of (8) | 3 → **3** | 4 → **4** | 8 → **8** |
| narrative (15) | 9 → **8** | 9 → **8** | 9 → **8** |
| abstain (10) | 9 → **9** | 4 → **4** | 10 → **10** |

## A3. Cost — the context budget cut tokens ~95%

Same questions, same number of model calls, same models.

| variant | calls | tokens pre-fix | **tokens post-fix** | change |
|---|---:|---:|---:|---:|
| V1 | 68 | 4,682,750 | **244,074** | **−94.8%** |
| V2 | 21 | 1,419,465 | **68,749** | **−95.2%** |
| V3 | 16 | 927,519 | **54,047** | **−94.2%** |

V3 answers 80 questions on **54,047 generator tokens** because the facts path
makes no generation call at all; V1 needs 4.5× that to score half as well.

## A4. What the V1 drop actually means

V1 fell 48 → 37 while its errors went 14 → 0. Neither figure is "V1's
capability", and the per-item diff (`scripts/compare_before_after.py`) shows
why: 6 items that previously *errored* now answer, and 17 that previously
answered now fail — almost all numeric, computed or compare/trend.

The mechanism was verified, not assumed, by tracing retrieval with no model
call. For `N-AAPL-REVENUE-2024` retrieval returns Notes and MD&A chunks, all
located correctly; with the full 89,572-character section the model could scan
for revenue, with a ~4,000-character window it cannot, and it abstains honestly
rather than guessing.

**This is the clearest result in the evaluation.** V1 answers numeric questions
from retrieved text, and bounding context to what a free-tier provider actually
serves costs it 11 items. V3 loses one, because its numerics never touch the
generator. The facts engine is what makes the system robust to a constraint
that cripples the text-only approach — and that constraint is not hypothetical,
it is the provider limit this project runs under.

## A5. P4-16 (D30) — defect E, and the two bugs inside its own fix

**Defect E.** `answering/text_answer.py` passed each retrieved chunk's whole
parent section to the generator, once per chunk. `N-JPM-REVENUE-2024` assembled
**3,323,116 characters (~831,000 tokens) from 4,653 characters of retrieved
chunk**, because `JPM_2024/fs_income_stmt` is 1,526,355 characters and was
emitted twice. 30 of 704 live sections exceed 100,000 tokens. Every provider
refused on size; the last refusal was `LLMBudgetExceeded`, a subclass of
`LLMRateLimited`, so it was recorded as `llm_rate_limited`. **A paced retry
looked like the fix and recovered 1 item of 15.** All 14 remaining V1 errors
were banks.

**The fix.** `TOTAL_CTX_BUDGET` 6,000 / `MAX_SOURCE_TOKENS` 1,500, derived from
the smallest TPM in the generator failover order (8,000) less the 900-token
response and the system prompt — a test asserts the relationship, not the
constant. A window centred on the chunk rather than the head of the section.
Several chunks of one section collapsed into one merged window. And
`llm_prompt_too_large`, deliberately not a `LLMRateLimited` subclass, raised
before the failover loop so no attempt is consumed.

**Two bugs in that fix, both found by measurement:**

1. **The locator failed 70% of the time.** `ingestion/chunker.py` prefixes every
   chunk with a header line that is not in the parent section, so probing with
   the chunk's opening missed and the window fell back to the section head —
   the exact behaviour P4-16 removes. Fixed: **1,167/1,170 located (99.74%)**.
2. **The token estimate was wrong in both directions.** A flat 3 chars/token
   *under*-counted table tokens (the unsafe direction) and over-counted prose by
   ~78%. The chunker's own `token_count` over 35,700 chunks says tables run
   **2.68** and prose **5.33**. `estimate_tokens` is now content-aware.

One of the new tests was itself wrong first: it inferred "located" from
`window != parent[:4000]`, false for any chunk genuinely at the head of its
section, and reported 53/150 while the locator was finding 150/150.

**Defect D is closed.** `R-MSFT-SEGMENTS-2025`, the over-refusal D28 deferred,
was a symptom of defect E and now answers correctly.

**The budget was swept and not changed** (`reports/phase4/budget_sweep/`):
3k → 9/15, 6k → 8/15, 12k → 11/15, 30k → 8/15. Non-monotonic, all intervals
overlapping; n=15 cannot separate them. An earlier reading of 8 versus 11 as
"the budget costs three narrative items" was noise read as signal — the same
error D4-08 documents. The negative result is kept, with its reproduction
command, rather than discarded.

**A rejected improvement, recorded.** Three V1 items became `scale_error`, and
the hypothesis was that the window cut off "(in thousands)" units headers.
Measured: only **19.4%** of statement sections carry the units phrase in their
first 400 characters (52.4% deeper in, 28.2% not at all). Weakly supported, so
not built.

## A6. P4-12 — the re-index

- Live store **39 collections / 37,882 points**; `data/qdrant_v1_backup`
  untouched at 25 / 14,557. Retired artifacts kept; undo is one command.
- **T4-09 verified independently** by `scripts/verify_reindex_integrity.py`,
  which re-derives every number from each collection's `storage.sqlite` over a
  read-only immutable URI rather than trusting the runner's own integrity
  block. `--self-test` plants a corpus and fails 9 checks, including all three
  it must. The guard also fired for real: an activation attempt was refused
  because `make test` held the Qdrant lock.
- **D4-04**: the relink now *clears* a link the store cannot honour. TSLA FY2025
  survived the swap pointing at a collection the new store does not hold.
  Retrieval was never at risk, but `query._years_with()` reads
  `collection_name`, so a refusal would have offered a year nothing can search.
- Section audit on the new parse: **290 of 429 pairs ok**; Item 1 and Item 1A
  usable in **39 of 39** filings.
- Phase 3 smoke re-ran twice — post-re-index and post-fix — **30/30 both times**.

## A7. P4-13 and D27 — the cross-encoder stays on

45 items, 13 filers, 4 fiscal years, 5 sections
(`eval/gold/narrative_retrieval_v1.jsonl`), each drafted only where the audit
says the section is `ok` **and** the section text carries the topic. A separate
file from the gold set by **D4-05**: appending would change every denominator
already measured and break comparability with the frozen V0.

| arm | section hit@5 | MRR | s/query |
|---|---|---|---|
| bm25 | 17/45 (37.8%) | 0.334 | 0.25 |
| dense | 31/45 (68.9%) | 0.544 | 0.08 |
| hybrid, no rerank | 29/45 (64.4%) | 0.494 | 0.17 |
| hybrid + rerank | 35/45 (77.8%) | 0.604 | 5.96 |
| **hybrid + rerank + focus (shipped)** | **44/45 (97.8%)** | **0.870** | 6.20 |

D27's three conditions for switching the cross-encoder **off**: (a) hit@5 within
5 pp or higher → **−13.3 pp FAIL**; (b) MRR not lower by more than 0.03 →
**−0.110 FAIL**; (c) ≥10× faster → 34.9× PASS. **Only (c) holds, so it stays on
and `config.py` is unchanged.**

At n=15 the same ablation said the cross-encoder *lost* a hit and that
dense-only beat the shipped default on MRR. At n=45 the sign reverses. Acting on
the n=15 reading would have made retrieval substantially worse; pre-registering
the rule is what made that impossible.

## A8. D25 — what the parser rewrite was worth

Identical questions over the **24 filings both stores hold** (62 scorable items;
`scripts/make_d25_itemset.py`). Running unfiltered would have asked the old arm
about five filers it never held and credited the rewrite for coverage.

| arm | old index | new index | Δ hit@5 | Δ MRR |
|---|---|---|---|---|
| bm25 | 20/40 | 22/40 | +5.0 pp | +0.065 |
| dense | 25/40 | 32/40 | +17.5 pp | +0.093 |
| hybrid | 28/40 | 31/40 | +7.5 pp | +0.105 |
| hybrid + rerank | 27/40 | 33/40 | +15.0 pp | +0.107 |
| **shipped default** | **30/40 (75.0%)** | **37/40 (92.5%)** | **+17.5 pp** | **+0.109** |

Every arm improves. `number_in_context` is 21/22 on both stores, repeating the
earlier finding that when the text path gets a number wrong, retrieval had the
figure anyway.

## A9. P4-14 — the rating sheet

`reports/phase4/narrative_rating_sheet.csv`, from the **post-re-index, post-fix**
V3 run: 15 questions, 23 rows, 18 with passage text and an EDGAR link, 5 refusal
rows flagged `refused:insufficient_evidence`, `verdict`/`issue`/`notes` written
empty (D29), byte-identical across builds.

**A bug caught before it reached the owner.** The first build matched passages to
citations *by position*, but `prune_to_cited` renumbers citations 1..n over only
the sources an answer used. An AAPL FY2024 citation carried text footed "2025
Form 10-K". Rating an answer against a passage it never cited manufactures both
false hallucination reports and false clean bills — on the artifact that decides
whether narrative numbers stop being provisional. Now matched by
`(ticker, fiscal_label, section)`, with a test over the committed sheet that
failed against the version as shipped.

A refusal row is flagged as a decision, not as an uncited answer: five of the
fifteen are refusals, and marking them `no_citation` would have had the owner
rating five working refusals as hallucinations.

## A10. Spec discrepancy — raised, and since resolved

Spec v1.10 dropped `llm_prompt_too_large` from the `error_code` enum (section
6.5) while P4-16 item (4) and T4-14 both still required it listed. It was
committed as the owner wrote it and raised rather than edited back, since the
spec is theirs; the implementation kept the code.

**Resolved in v1.11**: the owner confirmed it was dropped by mistake and the
line is restored. Nothing in the implementation changed.

## A11. Tests

`make test` **1,394 passed, 0 failed, 0 skipped**; ruff clean; `eval.mini_eval`
9/9 with 0 look-ahead. New in this addendum: T4-09 (re-index integrity, with a
self-test), T4-10 (narrative gold validator, 11), T4-11 (rating sheet, 18),
T4-13 (context budget, 21), T4-14 (oversize prompts, 7), plus the D25 item
filter (5).

## A12. P4-17 — the V1-generous arm (D32)

V1 again, facts engine off, but with a **30,000-token** context budget instead
of the shipped 6,000, pinned to **one** Gemini Flash-Lite model with failover
structurally impossible (the generator role resolved to a single candidate).
80 of 80 items ran, **zero errors**. Results in
`reports/phase4/runs_v1_generous/`; the shipped-budget runs were not touched.

This arm exists because V1's shipped-budget score is conditional on a number
derived from the **weakest** member of the failover list (groq:qwen, 8,000 TPM)
while Flash-Lite allows 250,000. Without it, "the facts engine is worth 36
items" could equally have meant "the fallback provider's token limit is worth
36 items".

| arm | overall | numeric | computed | cmp/trend | as_of | narrative | abstain | generator tokens | flags |
|---|---|---|---|---|---|---|---|---:|---|
| V1, shipped 6k | 37/80 (46.2%) | 12/25 | 4/12 | 1/10 | 3/8 | 8/15 | 9/10 | 244,074 | 19 uncited |
| **V1-generous, 30k** | **50/80 (62.5%)** | **20/25** | 5/12 | 5/10 | 3/8 | 8/15 | 9/10 | **1,024,250** | **28 uncited** |
| **V3, shipped 6k** | **73/80 (91.2%)** | **25/25** | **12/12** | **10/10** | **8/8** | 8/15 | **10/10** | **54,047** | **none** |

**The budget was worth 13 items to V1** (37 → 50). So part of what looked like
the facts engine's contribution was the fallback provider's token ceiling, and
the honest decomposition of the 36-item gap between V3 and V1 is roughly
**13 items of context budget and 23 of the facts engine** at comparable context.
This is exactly what D32 was pre-registered to find out, and it changes the
claim the README is entitled to make.

### D32's condition, evaluated

> *"If it scores close to V3 on numeric items…"*

| | numeric | 95% CI |
|---|---|---|
| V1-generous | 20/25 (80.0%) | 60.9–91.1% |
| V3 | 25/25 (100.0%) | 86.7–100.0% |

A gap of **5 items / 20.0 pp**. The intervals do overlap, but only over
86.7–91.1%, a sliver produced by V3's lower bound rather than by the arms being
alike. **Called: not close.** D32 did not fix a numeric threshold the way D27
did, so this is a judgement, stated here so the owner can overrule it — the
rule is theirs.

**The qualitative difference is more decisive than the count.** All 20 of
V1-generous's numeric passes are `correct_text_only` — the right value, read out
of retrieved prose, with no fact citation behind it. V3's 25 are `correct`:
every figure traced to a tagged XBRL fact. And V1-generous carries **28 uncited
numbers** against V3's **zero flags of any kind**, while spending **19× the
generator tokens** (1,024,250 against 54,047) to score 23 items worse.

### What the README may claim

Even though the conditional is called "not close", the three properties D32
names are independently measured here and should be stated plainly, because
they are the part of the comparison that does not depend on where the budget
is set:

- **Robustness under free-tier limits.** V1-generous needs a 30,000-token
  prompt, which **no member of the shipped failover list below Flash-Lite can
  serve** — groq:qwen's ceiling is 8,000. It is not a configuration the system
  can fall back to.
- **Zero generator tokens on the facts path.** V3 answers all 25 numeric and
  12 computed items with **no generation call at all**; its 54,047 tokens are
  narrative only.
- **Exact traceability.** `correct` versus `correct_text_only` is the whole
  difference between a figure tied to a tagged fact and a figure a model read
  out of a page.

Accuracy remains part of the claim — 25/25 against 20/25, and 12/12 against
5/12 on computed — but it is no longer the *only* part, and the README must not
rest on the 36-item headline alone now that 13 of those items are known to be
budget rather than architecture.

---

## A13. For P4-15 — the plan, as written before it ran

*(Every item below is addressed in A14. Kept so the plan and the outcome can be read against each other.)*

1. Apply the owner's `OK` rows to set `verified_by=owner` on the 37 verified
   numeric and computed items; the other 43 stay `companyfacts` or `auto` and
   must be described that way everywhere.
2. Read the rating sheet by `gold_id`/row id only (D29).
3. **Report agreement between the automated narrative verdicts and the owner's
   ratings** — n/N, with every disagreement listed.
4. **Apply D31 as written** and record which branch it takes: 5 or more bad
   outcomes of 15 → P5-13; otherwise record the gap as scorer strictness, list
   the disagreements, and tighten the scorer without changing the pipeline.
5. README quotes post-fix numbers only, with pre-fix shown as "before" evidence.
6. **Reconcile this report end to end** (spec v1.11). Sections 4.6, 6, 7 and 8
   predate P4-11 to P4-17 and contradict the addendum: defects A–D are closed,
   the rating sheet exists, the re-index ran, D27 reversed the cross-encoder
   reading at n=45, and the test counts moved. Correct each stale statement in
   place or mark it superseded with a pointer; no number may appear twice with
   two values and no note of which is current.
7. **Carry A12's conclusion into the README** (D32): the facts engine's value is
   robustness under free-tier limits, zero generator tokens and exact
   traceability **as well as** accuracy — 13 of the 36-item V3-versus-V1 gap is
   context budget, not architecture, and the headline must not rest on 36 alone.
   A12 calls D32's "close to V3 on numeric items" condition **not met**
   (20/25 against 25/25); that call is a judgement on a rule the owner wrote
   without a numeric threshold, and is theirs to overrule.

---

## A14. P4-15 — closing the phase

Written 2026-10-05 against spec v1.11, with one owner override recorded as
**D4-09** in `docs/DECISIONS.md`.

### 1. The gold sheet is applied — 37 owner, 14 companyfacts, 29 auto

`eval/gold/build_gold.py` now reads `reports/phase4/gold_verification_signed.csv`
**by `gold_id` only** and promotes `verified_by` to `owner` for the rows the
owner marked `OK`. Only the strength tier moves, and only upwards: no value,
label or note from the sheet reaches a gold item, so a transcription slip in a
column the owner did not sign cannot change an expected answer. An unknown
`gold_id`, a `WRONG` verdict, or an unrecognised verdict **fails the build**
rather than being skipped.

| tier | n | which items |
|---|---:|---|
| `owner` | **37** | every numeric (25) and computed (12) item — read against the printed line in the filing |
| `companyfacts` | 14 | compare/trend (10) and the answerable `as_of` (4) items — confirmed by the oracle, not by a person |
| `auto` | 29 | narrative (15), abstain (10), look-ahead `as_of` (4) — no oracle exists for prose, a refusal or a date |

```bash
python -m eval.gold.build_gold --check   # matches a fresh build, tiers included
python -m pytest tests/unit/test_gold_owner_verification.py   # 8 tests
```

The tests include the negative controls: an unknown `gold_id` raises, a `WRONG`
verdict raises, and a sheet row carrying a different `expected_value` leaves the
gold item byte-identical.

### 2. The narrative gate was NOT completed, and D31 was NOT applied

The returned narrative ratings were **not usable**: the entries were one column
off, and the refusal rows carried no link, so the `none` entries were defaults
rather than judgements. The owner directed that the file not be read and the
gate be treated as not completed (D4-09).

The consequences, applied everywhere:

- **No scorer-versus-owner agreement figure exists.** Spec P4-15 item 3 asks
  for n/N agreement with every disagreement listed. It cannot be produced from
  an unusable rating, and inventing one would be the exact failure CLAUDE.md
  rule 11 exists to prevent. **Not run**, not estimated.
- **D31 is not applied. Its precondition was not met.** D31's branch condition
  counts "bad" outcomes *as the owner marked them*; with no usable marks there
  is no count, so neither branch fires. This is recorded as a precondition
  failure, not as the "fewer than 5 bad" branch — those are different
  statements and only one of them is true.
- **P5-13 (narrative generation improvement) stays unbuilt and is the top
  item of future work.** The case for it now rests on **automated** evidence,
  which is strong enough on its own: **5 of 15 narrative items refuse with
  `insufficient_evidence` although the section audit confirms the expected
  section exists and carries the topic**, with retrieval at **97.8% hit@5**.
  Over-refusal with correct retrieval points at context assembly or scorer
  strictness; which of the two cannot be told apart from here.
- **No "owner-rated" language for narrative appears anywhere** in the README,
  `docs/EVAL.md`, `docs/LIMITATIONS.md`, `docs/FEATURES.md` or this report.
  Narrative is labelled **"not human-verified"** at every mention.
- The signed narrative file stays **untracked and unread**, and is gitignored
  so it cannot be committed by accident.

The automated narrative verdicts, for the record (post-fix V3, full 15):

| verdict | n | items |
|---|---:|---|
| `correct` | 8 | AAPL-SUPPLY-2024, AAPL-CYBER-2024, AMZN-COMPETE-2024, MSFT-SEGMENTS-2025, GOOGL-REGULATION-2024, GS-SEGMENTS-2024, MSFT-AI-2026, NFLX-COMPETE-2025 |
| `abstained_wrongly` | 5 | JPM-CREDITRISK-2024, NFLX-CONTENT-2024, BLK-BUSINESS-2023, AAPL-RISK-GENERIC, GS-MARKETRISK-2024 |
| `wrong_value` | 2 | AMZN-AWS-2024, JPM-CAPITAL-2025 (no top-5 citation from the expected sections) |

8/15 = 53.3%, Wilson 95% CI **30.1–75.2%**. That interval is 45 points wide,
which is the other reason not to draw a fine conclusion from it.

### 3. README, EVAL and this report quote post-fix numbers only

"Provisional" is gone from the README, `docs/EVAL.md` and this report. The
README's results table is the **post-fix** full-80 figures with Wilson
intervals, plus the frozen-69 comparison against V0; the pre-fix numbers stay
in A1 as measured "before" evidence and are never quoted as the system's
performance.

**A12's conclusion is carried into the README** (D32): the facts engine's value
is stated as **robustness under free-tier limits, zero generator tokens and
exact traceability — as well as accuracy, not instead of it** — and the README
says plainly that **13 of the 36-item V3-versus-V1 gap is context budget**, so
the headline does not rest on 36 alone.

### 4. The report is reconciled end to end

| section | was | now |
|---|---|---|
| header | "headline metrics are provisional" | final, with the verification tiers stated |
| 1. Summary | pre-re-index figures; "the cross-encoder earns nothing" | marked superseded by A1; the cross-encoder bullet marked **refuted at n=45**; the facts-engine bullet carries A12's qualification |
| 3. Tests | 1,173 | **1,428**, with the first-pass count explained rather than silently replaced |
| 4.6 Ablations | n=15, conclusions favouring dense-only | marked superseded by A7; findings 1 and 2 struck as refuted, finding 3 kept with A5's boundary |
| 4.7 D25 arm | "the re-index was deferred" | marked superseded by A8; relabelled as the control it was |
| 6. Open defects | A–E open | **all five closed** (P4-11, P4-16), each with what the fix turned out to be; the withdrawn "not fixed" paragraph is named as withdrawn |
| 7. Owner actions | four outstanding | all four resolved, including **one resolved as not done** |
| 8. Gate checklist | two unchecked | one checked, one left **unchecked with the reason** |
| 9. Proposals | three proposed | three struck as built or done |
| 10. Appendix | 1,173 | current counts and the full regeneration command list |

### 5. Phase tests and checks at closure

| command | result |
|---|---|
| `make test` | **1,428 passed, 0 failed, 0 skipped** |
| `make lint` | clean |
| `python -m eval.gold.build_gold --check` | matches a fresh build |
| `python -m eval.mini_eval` | 9/9, 0 look-ahead |
| `make demo-check` | **12/12 PASS** in ~17 s (health, LLM state, 39 collections, 8 real queries, `scripts/smoke.py` 10/10) |
| `make security` | PASS — hygiene clean, no tracked secrets, admin fail-closed with a negative control, 1 accepted advisory |

### 6. What is deliberately not done

- The scorer was **not** tightened. D31's "otherwise" branch says to tighten it
  after listing the disagreements with the owner's ratings; with no usable
  ratings there are no disagreements to tighten *towards*, and changing a
  scorer against one's own intuition about which refusals are wrong is how a
  measurement stops being independent of the thing it measures.
- No new evaluation was run at P4-15. Every figure here comes from artifacts
  already committed, re-read rather than re-measured.
