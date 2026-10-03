# Phase 4 — Evaluation, ablations, documentation

Branch `phase-4-evaluation`, against spec v1.6. Every number below was produced
by a command named beside it; the raw per-item rows are in
`reports/phase4/runs/*.jsonl` and scoring is reproducible from them without
spending any budget.

**Headline metrics are provisional.** Spec section 11 publishes them only after
the owner has verified at least 25 gold items against the filings themselves.
`reports/phase4/gold_verification.csv` is generated and unsigned.

---

## 1. Summary

* **The shipped system answers 60 of 69 gold items (87.0%, 95% CI 77.0–93.0);
  the v1 prototype answers 19 (27.5%, 18.4–39.0)** on the same items with the
  same scorers.
* **Zero look-ahead violations in V3; four in V2**, the variant with
  point-in-time scope switched off. The guard is not decorative, and the
  ablation can produce the failure it prevents.
* **v1 cannot refuse at all** — 0 of 10 abstention items, 14 scored
  `answered_wrongly` — and **36 of its answers carry citations with no
  accession and no filing date**, so a reader cannot reach the document.
* **The facts engine is worth 29 points** on the paired subset (V1 59.4% → V3
  87.0%) and **takes numeric accuracy from 12/18 to 17/18**. The facts path
  makes **no model call at all**: V3 spent 16 generation calls over 80
  questions, all on narrative items.
* **P4-00's boundary rewrite was adopted.** Usable (filing, section) pairs went
  from 245 to 298 of 440; Items 1 and 1A from 33 and 20 to **40 of 40**. Item 7
  reached 25 of 40 against a target of 33 and the owner accepted the miss
  (D4-00).
* **The cross-encoder earns nothing on these metrics and costs 200 seconds.**
  Dense-only retrieval matches the shipped default's section hit rate (12/15),
  beats its MRR (0.747 vs 0.717), and runs in 1.6s against 206s.
* **Parent-section context is what puts figures in front of the model**:
  removing it drops expected-number-in-context from 95% to 82% while leaving
  section hit unchanged.
* Three defects were found by the evaluation and fixed inside it; two product
  defects and one capability gap are left open and named in section 6.
* 1,172 offline tests pass. CI runs lint, hygiene, the suite and an offline
  mini-evaluation with two thresholds, all with no network and no key.

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

`make test` — 1,172 passed, 0 failed, 0 skipped, 32.0s. Artifacts in
`reports/phase4/tests/`.

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

Three findings, and one of them is uncomfortable:

1. **The cross-encoder costs a section hit and 200 seconds.** `hybrid` → `+rerank`
   moves section hit from 12/15 to 11/15 and MRR from 0.578 to 0.556, for 65×
   the retrieval time. The focus boost recovers the hit but not the time.
2. **Dense alone is the best arm on these metrics** — 12/15, MRR 0.747, 1.6s. On
   15 narrative items the 95% interval around 12/15 is roughly 55–93%, so this
   is a reason to measure properly on a larger set, not a reason to rip the
   pipeline out today. It is recorded rather than buried.
3. **Parent-section context is what gets figures in front of the model**: it
   does not change which section is retrieved, but removing it drops
   expected-number-in-context from 95% to 82%.

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

The re-index of P4-00 step 4 was deferred by the owner, so both stores hold the
same pre-rewrite parse and the two columns are expected to agree — which they
do. This is a check on the backup, not a result about the parser: the comparison
D25 actually asks for becomes possible only after the re-index.

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

### Open

| # | what | impact |
|---|---|---|
| A | **The router does not route "ratio of X to Y" or "A less B".** `facts/calc.py` implements both; the router answers them as a plain numeric fact for the first operand. `C-AAPL-DEBTEQUITY-2024` returns total liabilities; `C-AMZN-FCF-2024` returns operating cash flow. | 2 of 80 items. A capability gap, not a wrong answer — the figure returned is correct for the wrong question. |
| B | **"Cash flow from operating activities" resolves to `cash_and_equivalents`.** A metric-alias defect: the router matches "cash" before the longer phrase. Scored `wrong_metric`, which is the verdict added to name exactly this. | 1 of 80 items, and the one failure that produces a confidently wrong traceable number. |
| C | **`X-PERIOD-NOT-COVERED` refuses with `metric_not_found_in_filing`** instead of `period_not_covered`. The refusal is correct; the reason is not true. | 1 of 80. Abstention is reported per reason, so a wrong reason is its own defect. |
| D | **Over-refusal on `R-MSFT-SEGMENTS-2025`** — `insufficient_evidence` for a question Item 1 answers. Retrieval searched three MSFT collections and found nothing it would use. | 1 of 80. |
| E | **13 V1 items and 2 each in V2/V3 remain rate-limited** after a paced re-run. V1's number is a floor, not its capability. | reported as `error`, never as a wrong answer. |

Defects A–D were not fixed: P4-07 allows one fix-and-rerun cycle for
high-impact bugs, and none of these is high-impact at 1–2 items each. A and B
are the right first work for Phase 5.

---

## 7. Owner actions and questions

1. **Sign `reports/phase4/gold_verification.csv`** — 37 rows, 25 marked `core`,
   covering all four sectors and NFLX, WFC and BLK. Headline metrics stay
   provisional until this is done.
2. **Rate 15 sampled narrative answers** (spec's owner gate). The rating sheet
   is not yet generated — see Proposals.
3. **Decide when to run the overnight re-index** (P4-00 step 4). Until then,
   every narrative number describes the pre-rewrite parse and D25's
   old-versus-new comparison cannot be made.
4. **Question:** the ablation says the cross-encoder earns nothing on these
   metrics and costs 200 seconds. On 15 narrative items that is not conclusive.
   Worth a larger narrative set in Phase 5, or worth acting on now?

---

## 8. Gate checklist

- [x] Gold set built, schema-validated, byte-identical on rebuild (80 items)
- [x] Expected values confirmed against an independent oracle (47 of 47 generated items)
- [x] Owner verification sheet generated (37 rows, 25 core)
- [ ] **Owner verification sheet signed** — outstanding
- [x] Scorers with adversarial cases (T4-01)
- [x] Runner: resumable, deterministic, every LLM role instrumented (T4-03)
- [x] V0–V3 run on the gold set; N reported explicitly; paired subset reported
- [x] Retrieval ablations, no generation tokens
- [x] Failure analysis: every failure categorised (section 6)
- [x] CI green locally through the script mirror (T4-04)
- [x] `docs/EVAL.md`, `docs/LIMITATIONS.md`, README rewritten
- [x] Results table with n/N and Wilson intervals
- [ ] **Owner narrative rating** — outstanding
- [x] All phase tests pass (1,172, 0 failed, 0 skipped)

Every number in this report is regenerable (T4-05):

```bash
for v in V0 V1 V2 V3; do python -m eval.runner --variant $v --report-only; done
python -m eval.ablations
python scripts/compare_section_audits.py \
    reports/phase4/section_audit_before.json reports/phase4/section_audit_after.json
```

---

## 9. Proposals (not built)

1. **A narrative rating sheet generator**, so the owner's second gate is as
   mechanical as the first. Small; it belongs with P4-02's sheet.
2. **Route `ratio` and `difference`** (defect A). The calculator already does
   the work.
3. **A larger narrative gold set.** Every narrative conclusion in section 4.6
   rests on 15 items, and the intervals are too wide to act on.
4. **Instrument v1's client in the V0 child**, so the baseline's token cost is
   measured rather than absent.
5. **Reconsider the audit's narrative cap** once a bank's MD&A length is argued
   from the documents rather than assumed — currently 12 Item 7 misses are
   cap-bound.

---

## 10. Appendix — commands

| command | exit |
|---|---|
| `make test` | 0 — 1,172 passed |
| `make lint` | 0 |
| `./scripts/ci_local.sh` | 0 |
| `python -m eval.gold.build_gold --check` | 0 — matches a fresh build |
| `python -m eval.mini_eval` | 0 — numeric+computed 9/9, look-ahead 0 |
| `python -m eval.runner --variant V3` | 0 |
| `python -m eval.runner --variant V2` | 0 |
| `python -m eval.runner --variant V1 --retry-rate-limited --sleep 25` | 0 |
| `python eval/phase4/run_v0.py --worktree <v1-baseline checkout>` | 0 — 69 rows over two invocations (3 in a smoke run, 66 in the full one) |
| `python -m eval.ablations` | 0 |
| `python -m eval.ablations --index data/qdrant_v1_backup --label "v1 backup index"` | 0 |
| `python scripts/audit_sections.py --parsed-dir <re-parse>` | 1 — findings, as designed |
| `python scripts/compare_section_audits.py --self-test` | 0 |
| `python scripts/make_gold_verification_sheet.py` | 0 |

Raw logs: `logs/phase4/` (gitignored).
