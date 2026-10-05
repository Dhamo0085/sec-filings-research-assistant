# Interview brief

Everything on one page. Numbers here are post-fix and come from
`reports/phase4/REPORT.md`; where a number is not human-verified, this page
says so.

---

## The 60-second pitch

> It's a research assistant over SEC 10-K filings. I started from a
> conventional RAG prototype — chunk, embed, retrieve, let the model read the
> answer out — and audited it. The defects weren't prompt problems. A prompt
> told the model to assume figures were in millions, so every Netflix number
> was a thousand times wrong and read perfectly fluently. Nothing knew when a
> filing became public, so asking "as of June" happily answered from a 10-K
> filed in November. There was no way to say no, and a model outage was
> rendered as "which company did you mean?"
>
> So v2 splits the problem. Numbers come from the filing's own inline-XBRL
> tags through a deterministic resolver and calculator — no model touches a
> numeric answer. Prose still goes through retrieval. A filing-date catalog
> decides what's in scope for any given date, and the result type's
> constructor refuses to build an answer with an untraceable number, a
> look-ahead citation, or a refusal dressed up as a question.
>
> Then I measured it: 80 planned gold questions whose expected values are
> confirmed against SEC companyfacts, deterministic scorers with no LLM judge,
> and four ablation variants. The shipped system scores 73 of 80 with zero
> flags; the same system with the facts engine removed scores 37. The honest
> caveat is that about a third of that gap is context budget rather than
> architecture — I measured that separately, and the narrative half of the
> system is the weak part and has never been human-rated.

---

## Architecture in six bullets

1. **Rules-first router.** Entities, periods, metric, focus, intent resolved
   in code; a model is consulted only for genuine ambiguity. All 30 smoke
   questions route with **zero** LLM calls.
2. **Catalog as the clock.** Every annual filing the SEC lists per ticker,
   with its real filing date — the only thing that decides `as_of` scope, and
   it resolves a ticker across every CIK it has filed under.
3. **Facts path (numbers).** inline-XBRL → `facts.sqlite` (29,002 facts) →
   per-filer tiered concept resolution → `Decimal` calculator → deterministic
   template. **No generation call at all.**
4. **Text path (prose).** Hybrid dense + BM25, cross-encoder rerank, focus
   boost, scoped to the `as_of` collections; a bounded context window centred
   on each retrieved chunk; a structured `{found, answer}` from the generator.
5. **One abstention gate, one typed result.** 11 named refusal reasons, 7
   typed error codes → HTTP 503. Four validators (G1–G4) on the `Outcome`
   constructor make an untraceable number, a look-ahead citation, a reasonless
   refusal and an outage-as-clarification **unrepresentable**.
6. **Everything free-tier.** All model calls go through one client with
   per-role ordered failover, a prompt cache, a budget and typed errors. The
   6,000-token context budget is derived from the smallest provider in the
   failover list — the system is sized to its weakest fallback on purpose.

---

## Six headline results, each with its honest caveat

| # | result | the caveat I volunteer |
|---|---|---|
| 1 | **V3 scores 73/80 (91.2%) with zero flags** — no uncited number, no invalid citation, no look-ahead. | 80 items is small; the Wilson interval on the frozen 69-item subset is **80.5–95.0%**. And **37 of the 80 expected answers are hand-checked against filing snippets by a person**; the other 43 are machine-cross-checked against SEC companyfacts or have no oracle by nature. |
| 2 | **Removing the facts engine costs 36 items** (V1: 37/80). | **13 of those 36 are context budget, not architecture.** I ran V1 again at a 30,000-token budget and it scored 50/80. I report both, because the first reading overstated the facts engine. What survives: all 20 of V1-generous's numeric passes are *right value, no fact citation*, against V3's 25 *correct*; 28 uncited numbers against zero; and 19× the tokens. |
| 3 | **Ablating `as_of` produces 4 look-ahead answers; the shipped system produces 0.** | The first version of that ablation **ablated nothing** — the gold questions carry the date in the sentence and the router parsed it from there, so V2 was identical to V3 and would have been published as "the guard makes no difference". I found it on the first V2 run. |
| 4 | **The facts extractor agrees with SEC companyfacts on 99.9787% of 23,429 comparable pairs**, 100% on the 1,047 pairs for the 23 registry concepts, zero scale and zero sign errors. | companyfacts is an independent pipeline but reads the **same** iXBRL facts. If a concept is the wrong line for a metric, both agree on a number that answers a different question — only a person reading the statement catches that, which is what the 37-row verification gate is for. |
| 5 | **Retrieval finds the right section 44/45 times (97.8%), MRR 0.870**, and the parser rewrite moved that from 75.0% to 92.5% on the filings both indexes hold. | At n=15 the same ablation said the **opposite** — that the cross-encoder lost a hit and dense-only was best. I had pre-registered the decision rule (D27) before seeing the data, which is the only reason I didn't act on noise. |
| 6 | **V3 answers all 80 questions on 54,047 generator tokens**; the text-only variant needs 4.5× that to score half as well. | Those tokens are 95% lower than they were a week ago, because the text path was sending each chunk's **whole parent section, once per chunk** — one question assembled 3.3 million characters from 4.6 KB of retrieved chunk. The bug had been masquerading as a rate limit. |

**Narrative, stated separately and plainly:** under the automated scorer V3
answers **8 of 15** narrative items; **5 of the 15 are over-refusals** even
though the section audit confirms the section exists and carries the topic.
These are **not human-verified** — the human rating gate was not completed,
so no one has checked whether the 8 passes are good answers or merely
well-sourced ones. n=15 intervals here are roughly 30 points wide.

---

## Ten questions I expect, and the answers

**1. Why not just use a bigger model, or better prompts?**
Because three of the four defects I found aren't prompt-shaped. "Assume
millions" is a scale bug a smarter model still can't fix without the filer's
own scale attribute; knowing a filing's publication date needs a catalog, not
a better reader; and refusing needs a path that exists. The one that *is*
prompt-shaped — reading a number out of prose — I measured: V1 does exactly
that and scores 37/80 against 73.

**2. Isn't the facts engine just hard-coding?**
It's 12 named metrics mapped to *tiered candidate concepts per filer*, not one
global priority list. A single list is how the prototype read BlackRock's
revenue 37% low — BlackRock tags two revenue concepts that far apart. The
tiering plus "candidates that agree are not ambiguous" took coverage from
69.7% to 81.3% and ambiguous resolutions from 90 to **zero**. Anything outside
the 12 is refused, not guessed.

**3. Why no LLM judge in the evaluation?**
Because then the headline number depends on the judge's taste and stops being
reproducible. Every scorer is deterministic: numeric tolerance, scale and sign
checks, section-id matching, flags for uncited numbers and look-ahead
citations. A judge is scheduled for Phase 5 and for *rating*, never for
pass/fail.

**4. 80 items is tiny. Why should I believe any of this?**
You shouldn't believe the small differences, and the report says so — every
rate carries a Wilson interval and several are 30 points wide. Believe the
large, directional ones: 73 vs 37 vs 19, and 4 look-ahead violations vs 0.
And note what I did when a small-n result was tempting: the 15-item retrieval
ablation said to turn the cross-encoder off; the pre-registered rule made me
re-measure at n=45, where the sign reversed.

**5. What's the worst bug you shipped and caught?**
The context one. The text path sent each retrieved chunk's whole parent
section, once per chunk — 3,323,116 characters from 4,653 characters of chunk
on one JPMorgan question. Every provider refused on size, and the *last*
refusal was a budget error that subclassed the rate-limit error, so it was
logged as `llm_rate_limited` and a paced retry looked like a partial fix. It
recovered 1 of 15 items. I lost a day to the wrong diagnosis. The fix made
the error its own type, raised before the failover loop, and cut tokens 95%.

**6. How do you know your fix didn't break something else?**
`scripts/compare_before_after.py` diffs **per item, both directions**. V3's
headline barely moved, 74 → 73, while underneath two correct items became
abstentions and one abstention became correct. A headline that doesn't move
is not evidence that nothing moved.

**7. Why is the narrative side so much worse?**
Retrieval isn't the problem — 97.8% section hit@5. The gap is downstream:
5 of 15 are refusals where the section demonstrably contains the topic, which
points at context assembly or at scorer strictness. I bounded the context
window to fit the smallest free-tier provider, and that bound is not free —
it's what took the text-only variant from 48 to 37. The next piece of work is
a 45-item generation set and a context-assembly comparison with the adoption
rule pre-registered.

**8. What would you do differently with production budget?**
Three things, in order. Index the whole corpus properly rather than 13
tickers. Size the context to the provider that will actually serve the request
instead of to the smallest fallback. Then get a human rating pass on
narrative, because that is the one claim I currently can't make.

**9. How much of this is reproducible?**
All of the numbers. `python -m eval.gold.build_gold --check` proves the gold
set rebuilds byte-identically; `python -m eval.runner --variant V3
--report-only` regenerates the tables; `python -m eval.ablations` regenerates
retrieval; `python -m eval.mini_eval` is the offline subset CI enforces on
every push. The test suite is 1,454 tests, no network, and passes with no
`.env` present.

**10. What's the weakest claim on your README?**
Anything narrative. It's automated-scorer-only, n=15, and the gate that would
have made it a human-verified claim was not completed. I'd rather label it
than average it into a headline.

---

## Known limitations

- **5 of 13 bundled tickers had no text coverage until the re-index; 1 item
  (JPM FY2022) still has none.** An indexing backlog, not a property of the
  design.
- **Narrative is automated-scorer-only and n=15.** No human rating. 5 of 15
  are over-refusals.
- **12 metrics.** Anything else is refused.
- **Two open routing defects**: "ratio of X to Y" / "A less B" route as a
  plain fact for the first operand (2 items — right figure, wrong question);
  "cash flow from operating activities" resolves to `cash_and_equivalents`
  (1 item — the one confidently wrong traceable number in the set).
- **One wrong refusal reason**: `period_not_covered` surfaces as
  `metric_not_found_in_filing`.
- **Item 7 (MD&A) parses usably in 25 of 40 filings; Item 3 in 6 of 40.**
- **A swapped comparison is not detected** — if an answer gives both
  companies' revenues but attributes each to the other, every expected number
  is present and the scorer passes it. Stated in the test rather than papered
  over.
- **No follow-up handling** ("and last year?"), no conversational memory
  beyond a turn.
- **A fresh clone cannot run in one command**: the index takes about 5 hours
  to build on this 8 GB laptop.
- **One accepted dependency advisory** (`PYSEC-2026-3740`, nltk, no fix
  released; our only nltk call passes hard-coded resource names) — recorded
  in `reports/security/accepted_vulnerabilities.txt`.

---

## What I would do next

1. **Narrative generation improvement** — the top item. Build a 45-item
   generation set from the retrieval set, compare context-assembly variants
   (current parent windows vs top-k chunks vs chunks plus one neighbour) under
   one pinned model, with the adoption rule pre-registered before running.
   The evidence that this is the right next thing is automated: 5 of 15
   narrative items refuse although the section audit confirms the expected
   sections exist.
2. **Human rating for narrative**, so the narrative claim stops being
   automated-only.
3. **Fix the two routing defects.** The calculator already implements ratio
   and difference; the router just doesn't reach them.
4. **Follow-up rewriting** — resolve "and last year?" against the previous
   turn's *resolved* entities deterministically, before routing, and ask for
   clarification rather than guessing.
5. **Index the full corpus**, and size context per serving provider.
6. **Delete the retired v1 modules** now that V0 is frozen, keeping the
   baseline reproducible from the `v1-baseline` tag.

Full list, with why each is deferred:
[`LIMITATIONS.md`](LIMITATIONS.md).
