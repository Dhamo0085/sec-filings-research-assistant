# Limitations

What this system does not do, what the published numbers do not cover, and where
a reader should be sceptical. Nothing here is a plan; the plan is in
`docs/PROJECT_SPEC.md`.

---

## The numbers

**The owner's verification gate is not signed yet.** Spec section 11 publishes
headline metrics only after a person has checked at least 25 gold items against
the filings themselves. `reports/phase4/gold_verification.csv` is generated and
waiting. Until it is signed, every figure in `reports/phase4/` is provisional,
and the `verified_by` column says `companyfacts` or `auto`, never `owner`.

**The resolver and the oracle can be wrong together.** Expected values are
confirmed against SEC `companyfacts`, which is an independent pipeline but reads
the same iXBRL facts. If a concept is the wrong line for a metric, both agree on
a number that answers a different question. Only a person reading the statement
catches that, which is exactly what the gate above is for.

**80 items is a small set.** Every rate is reported with a Wilson 95% interval
for that reason, and some of those intervals are 30 points wide. A difference
between two variants that is smaller than its interval is not a result.

**11 of 80 items are outside the paired subset.** The facts store covers
eighteen filers, the text index eight. Items naming BAC, IVZ, STT, TROW or WFC,
and one JPM FY2022 trend, cannot be answered by any variant that reads text, so
they are excluded from the variant comparison and reported separately. That is
an indexing backlog, not a property of the system.

**Narrative answers are scored by the section they came from, not by whether
they are right.** `section_hit@k` and MRR measure retrieval. Nothing here
checks that the prose is faithful to the section it cites. Scoring wording would
need an LLM judge, which would make the headline numbers depend on the judge's
taste and stop them being reproducible; the judge is scheduled for Phase 5 and
for *rating*, never for pass or fail.

**Two abstention reasons are never exercised.** `ambiguous_concept` and
`no_filing_for_company` have no gold item, because the corpus cannot produce
them: P2-11 measured zero ambiguous resolutions across 780 resolutions, and
every ticker the system knows has filings. Their messages are unit-tested; their
behaviour end to end is not measured.

**A swapped comparison is not detected.** If an answer states both companies'
revenues but attributes each to the other, every expected number is present and
the scorer passes it. This is stated in the test rather than papered over.
Catching it needs the answer to attribute values structurally, which the facts
path does and the text path does not.

---

## The corpus

**The text index holds the pre-rewrite parse.** P4-00 rewrote section-boundary
selection and measured a large improvement offline (245 → 298 usable
(filing, section) pairs; Item 1 and Item 1A from 33 and 20 to 40 of 40). The
re-parse, re-chunk and re-index that would carry it into retrieval is an
overnight job and has not been run. So every narrative number currently
describes the **old** parse, and D25's old-index-versus-new-index comparison is
pending.

**Item 7 (MD&A) is usable in 25 of 40 filings, against a target of 33.** All
fifteen misses are five banks across three years. Three of them (BAC, GS, STT)
are slices the parser now places correctly that exceed the audit's
250,000-character narrative cap — a large bank's MD&A really is that long, and
the cap's stated premise ("even a bank's run well under that") is refuted by the
measurement. The cap was deliberately left alone rather than re-argued
mid-evaluation. The other two (JPM, WFC) are filings whose Item 7 genuinely is a
one-line cross-reference, with the MD&A printed elsewhere under the filer's own
heading. See D4-00.

**Item 3 (Legal Proceedings) is usable in 6 of 40.** Most filers satisfy it by
pointing at a note. That is the filers' choice, not a parser defect, but it
means the section is close to useless as retrieval context.

**Five of thirteen bundled tickers have no text coverage.** WFC, STT, TROW, IVZ
and BAC are in the facts store and the catalog but not the index.

**The corpus is 10-K annual filings only.** No 10-Q, no 8-K, no proxy
statements, no segment-level facts, no market data.

---

## The system

**Twelve metrics.** `facts/concepts.yaml` defines revenue, net income, operating
income, gross profit, R&D, total assets, total liabilities, stockholders'
equity, cash and equivalents, operating cash flow, capex and diluted EPS.
Anything else is refused with `metric_not_supported` rather than guessed —
guessing a concept is the defect that made BlackRock's revenue read 37% low
(D0.3).

**v1's parsed statement sections are still not trusted.** D23 forbids anything
user-visible depending on `validation_status`, and that still holds: it is
informational, and two tests enforce that it cannot affect an answer.

**Free tier only (D18).** Rate limits are real and shape what can be measured:
the V1 run hit a per-minute token limit on 23 of 80 items and had to be re-run
paced. There is no cost-per-query figure anywhere in the reports because there
is no price to multiply by, and inventing one would put a fabricated number in
a results table.

**No conversation memory.** Follow-ups like "and last year?" are not resolved
against the previous turn; that is P5-10. History reaches the text generator as
context and nothing else, deliberately — v1 prepended prior turns to the
question and a previous answer naming three years turned a plain figure request
into a trend.

**Local-first, single process.** The rate limiter is in-memory and per-process.
Qdrant runs in local mode, which takes an exclusive lock on its storage folder,
so one evaluation at a time.

---

## Reading the V0 baseline

V0 is v1 as it shipped, with two necessary deviations, both stated wherever it
is reported:

* its own Groq models (`llama-3.1-8b-instant`, `llama-3.3-70b-versatile`) are
  Enterprise-only on this key and returned 404 to every request, so the Phase 1
  gpt-oss substitutes are used;
* its on-demand ingestion is disabled, because fetching and indexing filings
  during an evaluation would rewrite the corpus mid-measurement.

Without the first, V0 would measure an outage. Without the second, it would
measure an accidental download. With them, it is still v1's routing, retrieval
and generation — but it is not literally the binary that shipped.

---

## Not investment advice

This is a research tool over public filings. It does not give investment advice,
it has no market data, and it can be wrong. Every number it states carries the
concept, the period and the filing it came from so that a reader can check it —
that is the point of the design, and it is also the reason not to take any
single answer on trust.
