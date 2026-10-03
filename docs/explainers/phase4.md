# Phase 4 in plain language

*A one-pager for the owner: what was built, why, and the questions an interviewer
is likely to ask.*

---

## What Phase 4 was for

Phases 1–3 built the system. Phase 4 asks the only question that matters about
it: **is it actually better, and by how much?** Not "does it feel better" — a
number, on a fixed set of questions, with the same scorer applied to the old
system and the new one.

Everything in this phase exists to make that number believable.

---

## What was built

**A gold set of 80 questions** (`eval/gold/gold_v1.jsonl`). Each one has an
expected answer. The numeric answers were not written by hand and were not
taken on trust from our own extractor: the builder resolves each one through
the product's own code and then checks it against SEC `companyfacts`, which is
the SEC's own rendering of the same filings by a different pipeline. If the two
disagree, **the build fails** rather than shipping either number.

The questions are chosen deliberately, not sampled. The set covers all four
sectors, all twelve metrics, and the three filers that break naive systems:
Netflix (reports in thousands — the prototype's "assume millions" prompt made
its figures a thousand times too big), Wells Fargo (hides Items 1A, 7 and 8 in
a separate exhibit) and BlackRock (tags two different "revenue" concepts that
differ by 37%).

**Scorers that classify, not just count** (`eval/scorers.py`). A wrong number
is not just wrong — it is a scale error, or a sign error, or the right metric
for the wrong year, or a percentage written as a fraction. Knowing which turns
a failure list into a work list. They compare numbers *as numbers at the
precision they were shown*, so "$391.0 billion" matches 391,035,000,000. The
Phase 0 scorer compared strings and therefore recorded correct answers as
mistakes — in the direction that flattered the new system.

**A runner that survives a free tier** (`eval/runner.py`). It appends each
result as it finishes, so a run killed by a rate limit continues where it
stopped. It records every model call by role — router, generator, judge — not
just the generator, which is all the earlier runners measured.

**Four variants.** V3 is the shipped system. V2 turns off point-in-time scope
and the refusal gate. V1 turns off the facts engine. V0 is the original
prototype, run from its git tag in a separate process.

---

## What the numbers say

On the 69 questions every variant has a corpus for:

| | | |
|---|---|---|
| **V3** | the shipped system | **60/69 — 87%** |
| V2 | no date scope, no refusal gate | 50/69 — 72% |
| V1 | no facts engine | 41/69 — 59% |
| V0 | the original prototype | 19/69 — 27% |

Three things are worth saying out loud:

1. **The facts engine is the big win.** Numeric accuracy goes from 12/18
   without it to 17/18 with it — and the facts path calls no model at all, so
   those answers are also instant (0.03s) and free.
2. **The date guard is not decorative.** With it off, the system answered four
   questions using filings that were not public on the date asked. With it on,
   zero. The ablation proves the guard can fail, which is the only way to know
   it is doing something.
3. **The prototype could not say no.** It scored 0 out of 10 on questions that
   should be refused — it answered all of them. And 36 of its answers cite a
   "section" with no document reference at all, so there is nothing to check.

---

## How the data flows

```
question → router (rules first; a model only for genuine ambiguity)
             │
             ├─ a number?  → facts store → calculator → fixed template
             │                (no model involved anywhere here)
             │
             └─ prose?     → catalog decides which filings were public
                           → retrieve → model writes the answer with citations
             │
             └─ neither?   → one of eleven named refusals
```

The catalog is what makes dates work. It knows when each filing became public,
so "as of 31 October 2024" removes Apple's fiscal 2024 10-K from the search
scope entirely — before any search runs, rather than filtering afterwards.

---

## Five likely interview questions

**"How do you know your evaluation isn't just measuring your own bugs?"**
The expected values come from SEC `companyfacts`, a different pipeline over the
same filings. 47 of 47 generated items are confirmed by it, and a disagreement
fails the build. That is not complete protection — both read the same XBRL
tags, so a wrong *concept* would fool both — which is exactly why a human signs
off at least 25 items against the statements themselves before any headline
number is published.

**"Why not use an LLM to judge the answers?"**
Because then the headline numbers depend on the judge's taste, and they stop
being reproducible — re-running would give different results. Every scorer here
is deterministic. The one thing that genuinely needs judgement, rating the
quality of narrative prose, is a separate human gate.

**"Your retrieval ablation says the reranker makes things worse. Why is it
still there?"**
Because the measurement is on 15 narrative questions, and the confidence
interval around that is roughly 55–93%. The honest position is "this is a
strong signal that the reranker is not earning 200 seconds, and the set is too
small to act on yet". It is written up as a finding with its interval rather
than quietly dropped — or quietly ignored.

**"What's the worst failure still in the system?"**
Asking for "Amazon's cash flow from operating activities" returns cash and cash
equivalents. It is a confidently wrong number with a correct-looking citation,
which is the exact failure mode this project was built to eliminate. It is one
item in eighty, it is named in the report, and it is the first thing to fix.

**"What would you do differently?"**
Build the evaluation earlier. Four separate bugs in this phase were found by
running the evaluation, and two of them — a date read as a number, and the
section-title mismatch that scored narrative 0 out of 15 — would have produced
published numbers that were simply wrong.

---

## Three known weaknesses

1. **The text index holds the old parse.** The section-boundary rewrite in this
   phase was a large improvement when measured offline (Items 1 and 1A went
   from 33 and 20 of 40 to 40 of 40), but carrying it into retrieval needs an
   overnight re-index that has not been run. So every narrative number
   currently describes the *old* parser.

2. **Eighty items is a small set.** Several confidence intervals are 30 points
   wide. The V3-versus-V0 gap is far too large to be noise; a difference of a
   few points between V2 and V3 on any single category would not be.

3. **Narrative answers are scored by where they came from, not by whether they
   are right.** The scorer checks that the answer cited the correct section. It
   does not check that the prose is faithful to it. That needs a human, and it
   is the owner gate that is still outstanding.
