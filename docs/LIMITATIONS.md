# Limitations

What this system does not do, what the published numbers do not cover, and where
a reader should be sceptical. Nothing here is a plan; the plan is in
`docs/PROJECT_SPEC.md`.

---

## The numbers

**37 of the 80 gold answers are hand-checked; 43 are not.** A person read each
numeric and computed item against the printed line in the filing and signed
`reports/phase4/gold_verification_signed.csv` (37 rows OK, 25 of 25 `core`), so
those carry `verified_by=owner`. The other 43 — compare/trend, `as_of`,
narrative and abstain — stay `companyfacts` (machine-cross-checked against SEC
data) or `auto` (no oracle exists for prose, a refusal or a date). Any claim
that *all* the expected answers were hand-checked would be false.

**The narrative human-rating gate was NOT completed, and no narrative figure in
this project is human-verified.** The sheet was built and is blind by
construction, but the ratings that came back were unusable: the columns were
offset by one, and the refusal rows carried no link, so the `none` entries were
defaults rather than judgements. The consequence is stated rather than worked
around — every narrative number here is the **automated scorer's**, with its
wide n=15 intervals, and there is **no scorer-versus-owner agreement figure**.
`D31` (the pre-registered rule for what to do about narrative, keyed to the
owner's ratings) **was not applied: its precondition was never met.**

**The resolver and the oracle can be wrong together.** Expected values are
confirmed against SEC `companyfacts`, which is an independent pipeline but reads
the same iXBRL facts. If a concept is the wrong line for a metric, both agree on
a number that answers a different question. Only a person reading the statement
catches that, which is exactly what the gate above is for.

**80 items is a small set.** Every rate is reported with a Wilson 95% interval
for that reason, and some of those intervals are 30 points wide. A difference
between two variants that is smaller than its interval is not a result.

**11 of 80 items were outside the paired subset when V0 was frozen; 1 is
today.** The re-index indexed BAC, IVZ, STT, TROW and WFC, so the live paired
subset is 79 of 80 (only a JPM FY2022 trend has no text). The **frozen 69** is
still reported wherever V0 appears, because V0 runs against the 25-collection
backup and a "paired" column computed today is not paired with it.

The original wording, which still describes why the subset exists: The facts store covers
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

**~~The text index holds the pre-rewrite parse.~~ SUPERSEDED — the re-index ran
(P4-12).** The live store is **39 collections / 37,882 points** and holds the
rewritten parse; D25's comparison was made on the 24 filings both stores hold
and the shipped default moved **30/40 (75.0%) → 37/40 (92.5%)**. The previous
artifacts are retained as `data/{parsed,chunks,qdrant}_retired_20261003T163939Z`
and the swap is reversible with one command.

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

**~~Five of thirteen bundled tickers have no text coverage.~~ CLOSED by the
re-index** — WFC, STT, TROW, IVZ and BAC are now indexed. TSLA lost its
leftover Phase 1 collection in the same swap, which was never a bundled ticker.

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

**The narrative path over-refuses, and that is the clearest open defect.**
Under the automated scorer V3 answers 8 of 15 narrative gold items; **5 of the
15 refuse with `insufficient_evidence` although the section audit confirms the
expected section exists and carries the topic**, and 2 cite the wrong section.
Retrieval is not the cause — section hit@5 is 97.8% on a 45-item set — so the
gap is in context assembly or in scorer strictness, and with no human rating
there is no way to tell which from here.

**The context budget is sized to the weakest provider, and that costs
accuracy.** `TOTAL_CTX_BUDGET=6000` is derived from the 8,000 tokens/minute the
smallest member of the generator failover list serves. The V1-generous arm
measured the price: the same text-only variant at 30,000 tokens scores 50/80
against 37/80. The shipped budget is what the system actually runs on, so it is
what the headline uses — but 13 of the 36-item V3-versus-V1 gap is this, not
architecture.

**No conversation memory.** Follow-ups like "and last year?" are not resolved
against the previous turn; that is P5-10. History reaches the text generator as
context and nothing else, deliberately — v1 prepended prior turns to the
question and a previous answer naming three years turned a plain figure request
into a trend.

**Local-first, single process.** The rate limiter is in-memory and per-process.
Qdrant runs in local mode, which takes an exclusive lock on its storage folder,
so one evaluation at a time.

---

## Deferred, and why

Named here so that "not built" is a decision on the record rather than a gap
someone has to discover. None of these is a blocker for what the system claims.

| | what it is | why it is deferred |
|---|---|---|
| **P5-13 — narrative generation improvement** | A 45-item generation set, then a comparison of context-assembly variants (current parent windows vs top-k chunks vs chunks plus one neighbour) under one pinned model, with the adoption rule pre-registered before the run. | **The top item of future work.** Its trigger rule (D31) was keyed to the owner's narrative ratings, which were never usable — so the rule did not fire, and the case for doing it rests on the automated evidence instead: **5 of 15 narrative items refuse although the section audit confirms the expected sections exist**, with retrieval at 97.8% hit@5. That is a strong enough signal to act on without the gate. |
| **P5-10 — follow-up rewriter** | Resolve "and last year?", "what about Microsoft?" deterministically against the previous turn's *resolved* entities, before routing. | Needs its own design to avoid v1's failure, where prepending history turned a plain figure request into a trend. Out of scope for this release. |
| **P5-11 — mixed numeric + narrative answers** | One question answered with a labelled numeric section and a labelled narrative section. | Optional in the spec; neither path's quality is currently limited by the absence of the other. |
| **P5-12 — delete the retired v1 modules** | Remove `routing/classifier.py`, `routing/resolver.py`, `generation/generator.py`, `generation/synthesizer.py` now that V0 is frozen. | Housekeeping. Deliberately after the release, so the V0 baseline stays trivially reproducible from the `v1-baseline` tag through this release. |
| **Docker image slimming** | The image carries the full model stack. | No deployment exists, so the image size costs nothing today. |
| **Response caching** | Cache whole answers, not just LLM prompts. | The facts path already answers in ~0.03 s with no model call; a response cache would mostly cache things that are already free, and would add a staleness question to a system whose whole point is point-in-time correctness. |
| **Hosting** | A public instance. | **No paid services (D18)**, and a hosted instance would share the owner's free-tier quota with every visitor. If one is ever created it is checked read-only with `scripts/smoke.py --base-url <url>`, never `/admin/*` or `/ingest`. |

**A fresh clone cannot be demoed in one command.** `make setup`, `make catalog`
and `make facts` are quick, but building the text index means embedding ~38,000
chunks, measured at **about 5 hours on an 8 GB laptop**. The facts path works
without it; narrative does not. The README says so rather than implying a
one-command start.

**One accepted dependency advisory.** `PYSEC-2026-3740` (nltk 3.10.3) has no
released fix. This project's only nltk calls pass hard-coded resource names and
filing text, never a caller-supplied path. Recorded with the reason, and what
would end the acceptance, in `reports/security/accepted_vulnerabilities.txt`;
`make security` fails on any advisory **not** listed there.

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
