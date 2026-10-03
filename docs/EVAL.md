# How this system is evaluated

Everything in `reports/phase4/` comes from the pieces described here. The aim is
that a reader can disagree with a number by re-running the command that produced
it, and that no number depends on a model's opinion.

---

## 1. The gold set

`eval/gold/gold_v1.jsonl` — 80 items, in the shape spec section 11 fixes:

| category | n | what it tests |
|---|---:|---|
| numeric | 25 | one figure from one filing |
| computed | 12 | a margin, growth rate, CAGR, ratio or difference |
| compare/trend | 10 | several figures, each attributed to a company and a year |
| narrative | 15 | prose, scored by the section it came from |
| `as_of` | 8 | point-in-time correctness, half answerable and half look-ahead |
| abstain | 10 | questions the system must refuse, one per reason where the corpus allows |

**The items are planned, not sampled.** `eval/gold/plan.py` names every
generated item with the reason it is in the set, because a random sample can
meet those counts and still miss the cases the system was built for — and
because the owner's verification gate is a line-by-line read, which needs each
row to say why it exists. The plan covers all four sectors, all twelve registry
metrics, and the three filer properties the spec names: Netflix (reports in
thousands), Wells Fargo (incorporates Items 1A/7/8 into an EX-13 exhibit) and
BlackRock (tags two revenue concepts 37% apart).

**Expected values are not ours.** `eval/gold/build_gold.py` resolves each item
through the product's own resolver and then checks it against SEC
`companyfacts`, a different pipeline over the same filings (D4). A value the
oracle holds and disagrees with is **rejected and the build fails** — it is not
downgraded and shipped, because that would bake an extractor defect into every
number reported. All 47 generated numeric, computed and compare items are
confirmed by the oracle.

`verified_by` is `owner` > `companyfacts` > `auto`. Headline metrics are
published over the first two. The 29 `auto` items are the narrative, abstain and
look-ahead items, which have no oracle by nature — there is none for prose, for
a refusal, or for a date.

**Point-in-time dates are derived, never typed.** A look-ahead item's `as_of` is
the day before the catalog says the filing was filed, so it is a real test by
construction; the schema validator re-checks every one against the catalog.

Rebuild and verify:

```bash
python -m eval.gold.build_gold --check     # byte-identical to the committed file?
python -m pytest tests/unit/test_gold_schema.py
```

---

## 2. The scorers

`eval/scorers.py`. No model is involved. An LLM judge would make the headline
numbers depend on the judge's taste and stop them being reproducible; a judge is
used in Phase 5 for narrative *rating*, never for pass or fail.

**A number is compared as a number, at the precision it was displayed.**
"$391.0 billion" matches 391,035,000,000 because the expected value falls inside
half of the last digit shown; "$391.1 billion" does not. The Phase 0 scorer
compared strings and therefore recorded correct answers as the model misreading
its own context — in the direction that flattered the replacement system.

**A miss is classified, never just counted:**

| verdict | what it means |
|---|---|
| `scale_error` | right digits, wrong power of ten — the K2 failure |
| `sign_error` | right magnitude, wrong sign |
| `unit_error` | a percentage answered as a fraction |
| `wrong_period` / `wrong_entity` | the right metric for a different year or company |
| `wrong_metric` | a traceable figure for a different line — a routing fault, not a reading one |
| `partial_multi` | some values of a comparison right, not all |
| `wrong_abstain_reason` | refused correctly, for a reason that is not true |
| `abstained_wrongly` / `answered_wrongly` | refused the answerable, or answered the unanswerable |

Flags are independent of the verdict, because a correct answer can still cite a
filing published after the `as_of` date (`look_ahead`), state a number no
citation carries (`uncited_number`), or renumber its citations
(`citation_invalid`).

Rates are reported as n/N with a **Wilson** 95% interval — the normal
approximation gives a zero-width interval at 0/N and N/N, which is exactly where
a gold set of 80 lands.

---

## 3. The variants

`eval/variants.yaml`. Each pins one provider and model, so a silent failover
would not turn a variant into a different experiment with the same name.

| id | what it is |
|---|---|
| V0 | v1's own pipeline, from the `v1-baseline` tag, against the frozen pre-rewrite index |
| V1 | v2 with the facts engine off — every number read from retrieved text |
| V2 | v2 with point-in-time scope and the abstention gate off |
| V3 | the shipped system |

V0 runs as a **subprocess** inside a git worktree at the tag: v1 and v2 share
module names (`query`, `config`, `retrieval`), so importing both into one
interpreter would quietly measure the wrong system. Two deviations from "v1 as
it shipped" are necessary and are stated wherever V0 is reported: its own Groq
models are Enterprise-only on this key and returned 404 to every request, so the
Phase 1 gpt-oss substitutes are used; and its on-demand ingestion is disabled,
because fetching filings during an evaluation would rewrite the corpus
mid-measurement.

**The paired subset (D21).** The facts store covers eighteen filers, the text
index eight. A variant that answers from text has no corpus for a Wells Fargo
question, and scoring that as wrong would measure the indexing backlog rather
than the ablation. `eval/subsets.py` computes the subset from the catalog's own
`collection_name`: 69 of 80 items. Every table reports both numbers and names
the missing filings.

```bash
python -m eval.runner --variant V3                 # resumable; appends as it goes
python -m eval.runner --variant V3 --report-only   # re-score without re-asking
python -m eval.runner --variant V1 --retry-rate-limited --sleep 25
python eval/phase4/run_v0.py --worktree /path/to/v1-baseline-checkout
```

The runner is resumable because the budget is a free tier: a run stopped by a
rate limit or a closed laptop continues instead of restarting, an item that
raised is recorded with its error rather than lost, and an item that never ran
scores `not_run` so n/N always states its own N.

Every LLM **role** is instrumented — router, generator, judge — not only the
generator, which is all the Phase 0 and Phase 1 runners timed.

---

## 4. Retrieval ablations

`eval/ablations.py`. Six arms, each the previous one plus exactly one stage:
`bm25`, `dense`, `hybrid`, `+rerank`, `+focus boost` (the shipped default), and
the default with parent-section context off.

No generation tokens: each arm calls `retrieve()` directly and is scored on what
it put in front of the model, never on what a model then said about it. A
generated answer mixes retrieval quality with the generator's, and the
difference between two arms would be buried in model variance.

Routing happens once per question, with `llm=None`, and is reused by every arm,
so the arms differ only in the retrieval switches.

Metrics: `section_hit@k`, MRR, and `number_in_context` — whether the expected
figure is physically present in the retrieved text, in any rendering the filing
might print it in. That last one is the ceiling on what the text path could
answer correctly, and it separates a retrieval failure from a reading failure.

```bash
python -m eval.ablations
python -m eval.ablations --index data/qdrant_v1_backup --label v1_index
```

An arm that scores no items fails loudly rather than printing `0/0 (0%)`. That
fired on the first run: Qdrant's local mode takes an exclusive lock on its
storage folder, an evaluation run held it, every retrieval raised, and the table
still looked like a measurement.

---

## 5. What CI enforces

`.github/workflows/ci.yml`, mirrored by `scripts/ci_local.sh`. No network, no
API key, no `data/` directory.

That isolation is the point, and it paid for itself on the first run: 15 tests
that pass locally failed, because seven needed `edgar_email` from a developer's
`.env`, seven imported a module that downloads a tokenizer at import time, and
one was reading the developer's real `.cache/edgar` because a monkeypatch had no
effect against a default bound at import. The local mirror cannot catch any of
those — it runs in the repository, where `.env` exists and the caches are warm —
so a green mirror means "probably green in CI", not a guarantee. The script says
so, and gives the command that does reproduce CI's isolation.

`eval/mini_eval.py` answers real gold items with the real pipeline against the
committed iXBRL fixtures. The facts path makes no model call, so this is a
genuine end-to-end check of routing, resolution, calculation, the abstention
gate and point-in-time scope at zero budget. Two thresholds:

* **numeric and computed: 100%.** These are the answers the system states as
  fact; a regression is a wrong number with a citation attached.
* **look-ahead violations: 0.** G2 is a guarantee, and that is the only
  threshold a guarantee can have.

Thresholds rather than a golden file: a golden file records what the system
does, and gets updated by whoever broke it.

---

## 6. Reproducing a published number

Every table in `reports/phase4/REPORT.md` names the command that produced it.
The raw per-item rows are in `reports/phase4/runs/*.jsonl`, scoring is pure, and
`--report-only` re-scores a recorded run without spending any budget — so a
scorer fix does not mean re-running the evaluation.
