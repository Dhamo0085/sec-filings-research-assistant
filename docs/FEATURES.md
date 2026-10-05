# Features

Nine features, each with: what it is in one sentence for a non-technical
reader; how it works in three sentences for an engineer; how to show it live;
one real example with **real captured output**; and its limitation.

Every example below is real output from the live store, captured 2026-10-05.
The full transcripts are in [`docs/demo/TRANSCRIPTS.md`](demo/TRANSCRIPTS.md).

---

## 1. The facts engine and the calculator

**Plain.** When the assistant states a number, that number is read from the
machine-readable tags the company itself attached to its annual report — not
from a model reading the page.

**Engineer.** `facts/extract.py` parses inline-XBRL (`ix:nonFraction` and
friends, all seven `@format` values observed across 250 cached documents) into
`data/derived/facts.sqlite` — **29,002 facts, 94 submissions, 18 filers,
3,492 distinct concepts**, keyed by `(accession, concept, context, unit)`.
`facts/resolve.py` maps one of **12 registry metrics** onto a tiered list of
candidate US-GAAP concepts per filer and returns exactly one fact or abstains,
and `facts/calc.py` then does the arithmetic — margin, growth, CAGR, ratio,
difference — in `Decimal`, never in a prompt. **No model is called on this
path at all**, so a numeric answer costs zero generator tokens and is
reproducible to the digit.

**Show it live.** `python query.py "What was Apple's operating margin in fiscal 2024?"`
— point at the `Computed as …` clause and at the two separate citations.

**Real output.**

```text
Apple Inc.'s operating income margin for fiscal 2024 was 31.51%. Computed as
123216000000 / 391035000000 x 100 from fiscal 2024 (year ended 2024-09-28)
operating income $123.22 billion; fiscal 2024 (year ended 2024-09-28) total
net revenue $391.04 billion. [1] [2]

Definition used: Operating income = us-gaap:OperatingIncomeLoss, fiscal year
ended 2024-09-28; Total net revenue =
us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, …
```

**Limitation.** **12 metrics, and anything else is refused rather than
guessed.** Coverage is **634 of 780 (ticker, metric, year) resolutions =
81.3%**; the 146 misses are genuine (banks tag no gross profit and no R&D;
Amazon tags no `us-gaap:Liabilities`). Two known defects remain in the
*router*, not the calculator: "cash flow from operating activities" resolves
to the wrong concept on one item, and "ratio of X to Y" is routed as a plain
fact on two — see [`LIMITATIONS.md`](LIMITATIONS.md).

---

## 2. Point in time — `as_of`

**Plain.** You can ask what the system could have known on a given date, and
it will refuse to use a filing that was not public yet.

**Engineer.** `catalog/` holds every annual filing the SEC lists for each
ticker — **365 filings, 29 amendments, 13 tickers** — with its real
`filing_date`, and it is the *only* thing that decides scope. Every path
(facts and text) filters its candidate filings through that date, and
`answering/outcome.py` **validator G2 refuses to construct** an answered
outcome carrying a citation whose filing date is after `as_of`, so a
look-ahead answer is not a bug that slips through review, it is an exception.
BlackRock is the proof case: it files under two CIKs, and the catalog resolves
a ticker across every CIK it has filed under (D24).

**Show it live.** Ask the same question twice, once with `--as-of 2024-06-30`.

**Real output.**

```text
$ python query.py --as-of 2024-06-30 "What was Apple's revenue in fiscal 2024?"
STATUS: abstained (period_not_filed_as_of)

As of 2024-06-30, Apple Inc had not yet filed its annual report for fiscal
2024 — that filing became public on 2024-11-01. Answering from it would be
using information that did not exist on the date you asked about. I do have
fiscal 2021 to 2023.
```

**Limitation.** `as_of` is **filing-date** correctness, not restatement
correctness: it answers "was this document public then", not "is this the
figure a reader would have seen then" when a later filing restates it. The
restatement rule (D14) picks the newest original filing for a period and marks
amendments, which is a defensible choice, not the only one. Measured: V3
scores **8/8** on the `as_of` items and **0 look-ahead violations**; with the
guard ablated (V2) the same system commits **4**.

---

## 3. Abstention and typed errors

**Plain.** It says "I don't know, and here is exactly why" instead of making
something up — and a technical failure is reported as a failure, not as a
question back to you.

**Engineer.** `answering/abstain.py` is a single gate mapping **11 named
reasons** (`company_not_found`, `period_not_filed_as_of`, `future_period`,
`metric_not_supported`, `out_of_scope`, `insufficient_evidence`, …) to one
message each. Dependency failures are a separate axis: **7 typed error codes**
(`llm_rate_limited`, `llm_prompt_too_large`, `llm_unavailable`,
`data_unavailable`, …) that surface as HTTP **503**, never as a 200 carrying
an apology. Validators **G3** (a refusal may not carry citations and may not
lack a reason) and **G4** (a dependency failure may not be built as a
clarification) make both properties unrepresentable rather than conventional.

**Show it live.** Ask for a private company, then ask for advice.

**Real output.**

```text
$ python query.py "Should I buy Apple stock?"
STATUS: abstained (out_of_scope)

That's outside what I do. I answer questions about what US companies reported
in their annual filings — not investment advice, price or market data,
forecasts, or recommendations.
```

**Limitation.** **2 of the 11 reasons are never exercised end to end.**
`ambiguous_concept` and `no_filing_for_company` have no gold item because the
corpus cannot produce them (zero ambiguous resolutions across 780; every
known ticker has filings). Their messages are unit-tested; their behaviour in
a live request is not measured. And one wrong-reason defect is open:
`X-PERIOD-NOT-COVERED` refuses correctly but names
`metric_not_found_in_filing` instead of `period_not_covered`.

---

## 4. Citations

**Plain.** Every number and every quoted passage names the filing it came
from, by accession number and filing date, so you can check it yourself.

**Engineer.** A `Citation` carries ticker, company, fiscal label, accession,
filing date, form type and either the XBRL concept (facts path) or the
section (text path). **Validator G1** refuses to construct an answered outcome
without a citation *and* a definition note naming the concept and period;
citation indices must be exactly `1..n`, which is what kills the v1 defect
where renumbering left `[3]` pointing at nothing. `generation/citations.py`
parses only ASCII `[N]`, and the generator failover order therefore *requires*
an `ascii_citations` capability — two otherwise-good models emit fullwidth
`【1】` and were excluded for it (D1-04b).

**Show it live.** Any answer; point at `SOURCES:` and read the accession out.

**Real output.**

```text
SOURCES:
  [1] NETFLIX INC FY2024 — revenue (us-gaap:Revenues)
       0001065280-25-000044, filed 2025-01-27
```

**Limitation.** On the text path a citation names the **section the retrieved
chunk came from**, and D23/D3-00 forbid it from claiming that section slice is
complete — the parser's boundaries are audited (**290 of 429 slices usable**)
and not trusted. So a narrative citation means "this passage is from Apple's
FY2024 Item 1A", not "this is all of Apple's FY2024 Item 1A". Nothing checks
that the prose is faithful to the passage; that is the gap in section 9.

---

## 5. The rules-first router

**Plain.** Working out what you asked — which company, which year, which
figure — is done with ordinary code, and a language model is consulted only
when the question is genuinely ambiguous.

**Engineer.** `routing/` resolves entities (ticker aliases, longest-match
first), periods (fiscal labels, `as_of` phrases, quarter detection), the
metric, the focus and the intent with deterministic rules; the model is
reached only when the rules leave real ambiguity. Measured on the 30-question
Phase 3 smoke set: **all 30 route with zero LLM calls**. That is the
difference between a system whose comprehension degrades when a free tier
rate-limits and one whose comprehension does not.

**Show it live.** `python eval/phase3/smoke.py --dry-run` — 30 routes, no
model, in about a second.

**Real output.** `make demo-check` shows the consequence: seven of the eight
demo queries return in **0.02–0.04 s** because nothing reached a model.

**Limitation.** Rules are brittle at the edges by construction. The two open
routing defects above (ratio/difference phrasing, the "cash" alias) are
exactly that class, and neither is a wrong number — the first returns the
right figure for the wrong question, the second the wrong concept. There is
also **no follow-up rewriting**: "and last year?" is not resolved against the
previous turn (deferred as P5-10).

---

## 6. The text path (retrieval + generation)

**Plain.** For questions about what a company *says* rather than what it
reports as a number, it searches the filing text and answers with the passage
it found, cited.

**Engineer.** `retrieval/` runs dense (bge-base, 512 tokens) and BM25 in
parallel, fuses them, reranks with a cross-encoder and applies a focus boost
toward the section the question implies — scoped to the collections the
catalog says are in `as_of` range. `answering/text_answer.py` assembles a
bounded context window **centred on each retrieved chunk**, collapsing chunks
from one section into one window, under `TOTAL_CTX_BUDGET=6000` /
`MAX_SOURCE_TOKENS=1500` tokens, and asks the generator for a structured
`{found, answer}` so "I could not find it" is a value rather than a sentence
to parse. On the 45-item retrieval set the shipped default reaches **44/45
(97.8%) section hit@5, MRR 0.870**.

**Show it live.** `python query.py "What risks does Apple disclose about its supply chain?"`

**Real output.** (abridged — the full answer is in the transcripts)

```text
STATUS: answered_text

Apple discloses that any failure of manufacturing or logistics partners can
negatively impact component or finished goods costs and supply … [1]

SOURCES:
  [1] Apple Inc. FY2024 — Item 1A: Risk Factors
       0000320193-24-000123, filed 2024-11-01
```

**Limitation.** **This is the weakest part of the system and the numbers say
so.** Under the automated scorer V3 answers **8 of 15** narrative gold items;
**5 of the 15 are refusals** (`insufficient_evidence`) even though the section
audit confirms the expected section exists and carries the topic, and 2 cite
the wrong section. Retrieval is not the bottleneck — 97.8% hit@5 — so the gap
is in context assembly or in scorer strictness, and **it has not been
human-rated**, so nobody has checked whether those 8 are *good* answers or
merely well-sourced ones. Bounding the context was also not free: it is what
took the text-only variant V1 from 48/80 to 37/80.

---

## 7. The free-tier LLM client

**Plain.** It uses only free model providers, switches to another when one is
rate-limited, remembers answers it has already paid for, and stops instead of
spending a budget it does not have.

**Engineer.** Every model call in the project goes through `llm/client.py`:
an **ordered failover list per role** (router, generator, judge — chosen by a
measured 24-call bake-off, not by published limits), an on-disk prompt cache,
bounded retry, a request/token budget, and **typed exceptions** that the
answer layer turns into the 7 error codes above. A prompt too large for the
list's smallest member raises `llm_prompt_too_large` **before** the failover
loop, because walking the whole list with an oversized prompt looks like a
rate limit and was mis-diagnosed as one for a day (D4-07). Pinned mode
(`failover=False`) is what makes an evaluation run attributable to one model.

**Show it live.** `curl -s localhost:8000/health | jq .llm`, then point at the
`[LLM]` tag on the single demo row that needs it.

**Real output.** `GET /health` → `"llm": "ok"`, and `make demo-check` reports
**7 of 8 demo queries needing no model**.

**Limitation.** Free tiers are the constraint the whole design bends around:
the smallest member of the generator order serves **8,000 tokens/minute**,
which is where the 6,000-token context budget comes from — so the system is
sized to its weakest fallback. The V1-generous arm measured what that costs:
the same text-only variant at 30,000 tokens scores **50/80 against 37/80**,
so **13 of the 36-item V3-versus-V1 gap is budget, not architecture**. Also:
free-tier rate limits are the single most likely cause of a failed demo, which
is why `make demo-check` prints the LLM state first.

---

## 8. The section catalog and the parser audit

**Plain.** The system knows which part of each annual report it is reading —
the business description, the risk factors, the management discussion — and it
has measured how often it gets those boundaries right rather than assuming.

**Engineer.** `ingestion/parser.py` scores every candidate heading and picks
the maximum-weight subsequence with strictly increasing line *and* Item
priority, which replaced "first heading past a 15% table-of-contents zone"
plus a greedy monotonic filter. `scripts/audit_sections.py` then measures the
result per (filing, section): **290 of 429 slices usable on the live parse,
with Item 1 and Item 1A at 39/39**, up from 245/440 and 33/40 and 20/40 before
the rewrite. The catalog's `collection_name` link is what gives the text path
its `as_of` scope, and a relink now *clears* a link the store cannot honour
rather than leaving a dangling pointer (D4-04).

**Show it live.** `curl -s localhost:8000/health | jq .collections_loaded` →
**39**; `python scripts/audit_sections.py --parsed-dir data/parsed`.

**Real output.** The re-index that carried the rewrite into retrieval moved
the shipped default's section hit from **30/40 (75.0%) to 37/40 (92.5%)** on
the 24 filings both stores hold — the single biggest retrieval improvement in
the project.

**Limitation.** **Item 7 (MD&A) is usable in 25 of 40 filings against a target
of 33**, and all fifteen misses are five banks across three years — three of
them are slices the parser now places *correctly* that exceed the audit's
250,000-character cap, which the measurement refutes and which was
deliberately left alone rather than re-argued mid-evaluation. **Item 3 (Legal
Proceedings) is usable in 6 of 40** because most filers satisfy it by pointing
at a note. And nothing user-visible may assert a slice is complete (D23).

---

## 9. The evaluation harness

**Plain.** The claims this project makes about itself are produced by code you
can re-run, over a fixed set of 80 questions whose answers were checked
against an independent source, with no model acting as judge.

**Engineer.** `eval/gold/` builds 80 planned (not sampled) items through the
product's own resolver and **rejects any item SEC `companyfacts` disagrees
with** rather than shipping it; the file rebuilds byte-identically.
`eval/scorers.py` is deterministic — numeric tolerance, scale and sign checks,
section-id matching, flags for uncited numbers and look-ahead citations — so
no headline depends on a judge's taste. `eval/runner.py` runs five variants
(V0 the v1 prototype, V1 no facts engine, V2 no `as_of`/no gate, V3 shipped,
plus the V1-generous budget arm) resumably with every LLM role instrumented,
and `eval/ablations.py` measures retrieval with **zero** generation tokens.

**Show it live.** `python -m eval.mini_eval` — real gold items, real pipeline,
committed fixtures, no network, in seconds. That is what CI enforces.

**Real output.**

```text
V3 (shipped)       73/80 (91.2%)   0 flags
V2 (no as_of)      63/80 (78.8%)   4 look-ahead violations
V1 (no facts)      37/80 (46.2%)   19 uncited numbers
```

**Limitation.** **80 items is a small set** — every rate carries a Wilson 95%
interval and several are 30 points wide, so a gap smaller than its interval is
not a result; the project has one pre-registered decision rule (D27) precisely
because an n=15 ablation once pointed the opposite way to the n=45 one. **37
of the 80 expected answers are owner-verified against filing snippets; the
other 43 are `companyfacts` or `auto`.** The narrative items are **scored by
the section they came from, not by whether the prose is right**, and the human
rating gate for narrative **was not completed**, so every narrative figure
here is automated-scorer-only and labelled that way.

---

See also [`EVAL.md`](EVAL.md) for the method, [`LIMITATIONS.md`](LIMITATIONS.md)
for what to be sceptical about, and [`DECISIONS.md`](DECISIONS.md) for why each
of these is built the way it is.
