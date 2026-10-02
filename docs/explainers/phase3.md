# Phase 3 in plain language — routing, answers, `as_of`, abstention, UI

A one-pager for the owner. What was built, why, how a question now flows through
it, five questions an interviewer is likely to ask, and three things that are
genuinely weak.

---

## 1. What Phase 3 built

Phase 2 produced an accurate store of numbers. Phase 3 is the part a person
touches: deciding what a question is asking, answering it from the right place,
refusing clearly when it cannot be answered, and never using a filing that was
not public yet.

Seven new modules, each small enough to explain on its own:

| Module | Job |
|---|---|
| `routing/entities.py` | which company, from tickers, aliases and the catalog |
| `routing/periods.py` | which years, and whether a date is a cutoff or a period |
| `routing/router.py` | which intent and which path — rules first, model last |
| `answering/outcome.py` | the one response type, with the guarantees built in |
| `answering/facts_answer.py` | the sentences for numeric, computed, compare, trend |
| `answering/text_answer.py` | the narrative path, scoped by date and cited |
| `answering/abstain.py` | one table from "why not" to what the user reads |

`query.py` became a dispatcher that wires them together, and `api/app.py` now
returns the specification's response shape with an `as_of` field and an
admin-only trace.

---

## 2. How a question flows

Take **"As of March 1 2025, what was Apple's latest annual revenue?"**

1. **Entities.** `Apple` → `AAPL`. No model call: it is a lookup in an alias
   table, the bundled names, and the catalog's own tickers.
2. **Periods.** `As of March 1 2025` is pulled out as a **cutoff** and removed
   from the sentence before years are read — otherwise "2025" would also be
   taken as the year being asked about. `latest` is left unresolved on purpose,
   because what "latest" means depends on the cutoff.
3. **Router.** The sentence contains `revenue`, which is an alias in
   `facts/concepts.yaml`, one company and no comparison or trend words, so the
   intent is `numeric_fact` and the path is the facts engine. **No model call.**
4. **Resolve.** The catalog is asked for Apple's filings that were public on
   2025-03-01. The FY2024 10-K (filed 2024-11-01) qualifies; the FY2025 one
   (filed 2025-10-31) does not exist yet. The resolver reads revenue out of the
   FY2024 filing's facts.
5. **Answer.** A template writes the sentence. Nothing is paraphrased by a
   model, so the number in the text is the number in the citation, always.
6. **Outcome.** The response type refuses to be built if the answer has no fact
   citation, no definition note, or a citation newer than the cutoff.

A narrative question ("what risks does Apple disclose?") takes the same first
three steps and then diverges: the catalog decides which year's collections may
be searched, retrieval runs only over those, and the model is asked for
`{"found": true/false, "answer": "..."}` rather than prose we have to interpret.

---

## 3. Why rules before the model

Three reasons, in order of how much they mattered:

1. **It is more correct.** A regex that sees "fiscal 2024" is right every time.
   A model asked the same thing is right most of the time, and the failures are
   invisible.
2. **It is reproducible.** An evaluation re-run in six months routes every
   question identically, with no provider pinning and no cache dependency.
3. **It is free.** The binding free-tier limit is requests per day. In the
   30-question smoke run, **every question routed without a model call**, and
   only the 6 narrative ones used the model at all — for generation, not routing.

The model is still there for the questions rules cannot decide; `used_llm` in
the trace records which happened, so a Phase 4 failure analysis can separate a
routing mistake from a retrieval one.

---

## 4. The one idea worth remembering: the guarantees are a type

The four promises in the specification are all of the form "this combination of
fields must never be produced":

- a number without a traceable source (**G1**)
- a citation from a filing published after the as-of date (**G2**)
- a refusal with no reason, or one that quietly cites sources (**G3**)
- an outage dressed up as a question back to the user (**G4**)

Phase 0 found v1 producing two of those live. So rather than relying on every
code path to remember, `answering/outcome.py` makes them **unconstructible**: an
`Outcome` that breaks any of them raises on creation. The answer templates, the
text path and the abstention gate all go through it, so the guarantee is checked
in one place instead of hoped for in twenty.

The citation numbering is in the same category — indices must be exactly 1..n in
order, which makes the Phase 0 renumbering bug (K1) impossible to express.

---

## 5. Five questions an interviewer might ask

**"Why not just let the LLM do the routing? It's one call."**
It is one call per question, every day, against a limit measured in requests per
day — and it makes the system non-reproducible, because the same question can
route differently next month. The deeper reason is that routing failures are
silent: a model that reads "Q3 2024" as an annual question returns a confident
wrong number, while a rule that doesn't recognise something returns an
abstention. I kept the model for genuine ambiguity and recorded in the trace
when it was used. In the 30-question smoke run it was needed for routing zero
times.

**"How do you know the as-of logic actually works?"**
Three layers, because one would not convince me either. The scope is decided by
the catalog before any search runs, so an ineligible filing is never retrieved.
The `Outcome` type refuses a citation newer than the cutoff, so even if the
scope were wrong the answer could not ship. And a property test runs random
dates against random filers and intents and asserts no cited filing post-dates
the cutoff, covering both the facts path and the text path — which use different
mechanisms and so could drift apart. The live smoke run shows zero violations.

**"What happens when the language model is down?"**
A numeric question still answers, because nothing on that path calls a model.
A narrative question returns `status=error` with `llm_unavailable` and HTTP 503 —
visibly broken, not quietly wrong. That distinction is the whole of defect F1:
v1 returned "Which company are you asking about?" for every failure, including a
total outage, and nobody could tell.

**"Your section parser is broken. Why didn't you fix it?"**
I measured it first. The audit found 238 of 429 section slices usable and Item 1A
Risk Factors missing from 17 of 39 filings. Then I implemented three targeted
fixes and ablated them over 13 filings: the best combination moved the corpus by
**+1 usable slice out of 143**, with individual filings improving and regressing
in roughly equal measure. The cause is structural — the boundary selector keeps
one candidate per section and then filters greedily, so one new early candidate
both misplaces its own section and blocks every later one. Shipping a change
inside the noise would have cost a full re-index and made every retrieval number
measured so far incomparable, for no measured gain. So I recorded the numbers,
pinned the two fixable defect classes as strict `xfail` tests so a future fix
cannot land silently, and scoped the real fix.

**"What's the hardest bug you found in this phase?"**
The chat endpoint prepended the previous turns to the question before routing.
v1 did that so its LLM classifier could resolve "compare to last year", and it
looked harmless. With a rules-first router it is poison: asking the same
question twice made the second one a *trend over every year the first answer had
mentioned*, because the period parser read the years out of the quoted history.
It only showed up when I drove the real UI by hand — no unit test would have
caught it, because every test called `ask()` directly. History now goes to the
text generator as its own labelled section and never near the router.

---

## 6. Three things that are genuinely weak

1. **The text path rests on section boundaries that are known to be bad.**
   The facts path is solid; the narrative path retrieves from slices that are
   missing, truncated or merged for roughly 45% of the filings audited. Nothing
   user-visible claims a section is complete, and a narrative answer is marked
   `answered_text` rather than `answered` for exactly this reason — but a
   question about Goldman's risk factors is answering from a slice that does not
   cleanly contain them. Phase 4's retrieval numbers will show the cost.

2. **Follow-up questions no longer resolve.** The fix above was the right trade
   — a wrong answer is worse than a clarification — but "and what about last
   year?" now asks which company you mean. A proper fix is a deterministic
   follow-up rewriter, which is not built.

3. **Text coverage is much narrower than facts coverage, and unevenly so.**
   25 collections are indexed against 483 catalogued filings. Wells Fargo has
   five years of facts and no indexed text at all, so it answers numbers and
   refuses narrative. The refusal now says which capability is missing, but the
   gap itself is real and the bundled set is not uniformly usable.

---

## 7. If you read one file

`answering/outcome.py`. It is the shortest statement of what this project
promises, and every other module in the phase is arranged around satisfying it.
