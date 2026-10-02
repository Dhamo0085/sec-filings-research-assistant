# Phase 2 in plain language — the facts engine

*A one-pager for the owner. What was built, why, how the data flows, five
questions an interviewer is likely to ask, and three things that are still
weak.*

---

## What Phase 2 built

A **facts engine**: the part of the system that answers "what was Apple's
revenue in fiscal 2024?" by reading the number **the company itself tagged in
its filing**, rather than by asking a language model to read a table.

Five pieces, in the order data moves through them:

| Piece | File | What it does |
|---|---|---|
| Extractor | `facts/extract.py` | Reads the inline-XBRL tags out of a 10-K and turns them into typed facts |
| Store | `facts/store.py` | Keeps them in SQLite, one rebuild-safe transaction per filing |
| Registry | `facts/concepts.yaml` | Says which XBRL tags count as "revenue", per sector and per company |
| Resolver | `facts/resolve.py` | Picks the one right fact for a question, or refuses with a reason |
| Calculator | `facts/calc.py`, `facts/format.py` | Growth, margins and ratios in exact decimal arithmetic |

Plus the housekeeping P2-00 asked for: a **streaming indexer** that replaced
v1's one-pass embedding, and a throughput profile that explains why the Phase 1
indexing attempt failed.

## Why this exists at all

v1 retrieved chunks of filing text and asked a model to read the numbers off
them. Three things went wrong with that, all measured in Phase 0:

1. The prompt told the model to **"assume millions"**. Netflix reports in
   thousands. That is a 1,000× error delivered with a citation.
2. The model read whatever number was nearest in a noisy table.
3. There was no way to check an answer, because nothing recorded *which* line
   of *which* statement it came from.

Every 10-K since roughly 2009 carries machine-readable XBRL tags inside the HTML
— the filer's own numbers, with their own units and periods. Phase 0 showed
those could be extracted and that they matched the SEC's independent rendering
on 105 of 105 comparable values. Phase 2 turns that finding into a component.

## How the data flows

```
SEC EDGAR
   |
   |  facts/documents.py  — reads the filing's own FilingSummary.xml to learn
   |                        WHICH documents carry facts, then downloads them
   v
.cache/filings/<TICKER>_<accession>/*.htm
   |
   |  facts/extract.py    — parses the iXBRL tags: value, scale, sign, period,
   |                        unit; drops segment breakdowns; keeps hidden facts
   v
Fact objects (Decimal values, fully scaled and signed)
   |
   |  facts/build.py      — one transaction per filing, skipped if the document
   |                        bytes have not changed
   v
data/derived/facts.sqlite        26,041 facts / 13 bundled tickers
   |
   |  facts/resolve.py    — catalog says which filings were public on a date;
   |                        registry says which tags mean "revenue";
   |                        policy says when to refuse
   v
one Fact + its filing reference,  OR  an abstention with a reason
   |
   |  facts/calc.py + format.py
   v
"$391.04 billion ($391,035,000,000)"  or  "revenue grew 11.0%, from (637,959 - 574,785) / 574,785"
```

## The two ideas that matter most

**1. Refusing to answer is a feature.** BlackRock's FY2024 filing tags *two*
different revenue figures: `us-gaap:Revenues` = $12,794M and
`RevenueFromContractWithCustomerExcludingAssessedTax` = $20,407M. The first is a
component; the second is the income statement's "Total revenue" line. A system
with a priority list picks whichever is first and understates revenue by 37%,
confidently. This system **abstains** unless something tells it which to use —
and for BlackRock that something is a written override citing the accession and
the statement line it came from. Take the override away and it abstains again;
there is a test that proves it.

**2. Point-in-time correctness is enforced before the fact is found, not
after.** When a question carries an `as_of` date, the resolver filters *filings*
by their filing date first, and only then looks for facts. Doing it the other
way round — find the number, then check whether you were allowed to see it —
fails in the obvious human way: the number is already in your hand.

## Five likely interview questions

**"How do you know the extraction is right?"**
An independent oracle. SEC `companyfacts` is the same filings rendered by the
SEC's own pipeline, so it is a second opinion produced by different code.
Comparing *by accession* (so a restatement is not mistaken for an error) across
94 filings from 18 companies: **27,506 comparable values, 99.13% bit-exact, and
100.00% exact on the 1,861 values for the concepts the system actually uses to
answer questions. Zero scale errors, zero sign errors.** The remaining 0.87%
are filings that tag the same concept twice — rounded in prose, exact in a table
— and the two agree within the precision the filer declared.

**"What's the hardest part of parsing XBRL?"**
Not the parsing. The *selection*. One filing holds thousands of facts for the
same concept across different periods, different segments, and different
precisions, and almost all of them are wrong answers to any given question. The
rules that matter are: only non-dimensional (consolidated) facts; only durations
of 350–380 days, because Apple's fiscal year is 363 days and a 365-day window
rejects everything Apple reports; instants exactly at the period end; and
**pool every document of a submission before resolving anything**, because
Wells Fargo's 10-K wrapper holds 18 facts and the exhibit beside it holds 7,285
that reference the wrapper's period definitions. Parsed separately, neither one
works.

**"Why Decimal instead of float?"**
Because 2,725 facts in this corpus carry a negative `scale` attribute — they are
percentages tagged as, say, `64` with `scale="-2"`. Applying `10**-2` in binary
floating point is inexact, and the error is invisible until two such numbers are
added and the fourth decimal place is wrong. Money is the canonical case where
you cannot afford a representation that is *nearly* right.

**"What happens when you don't know?"**
There are eleven named reasons for refusing, and they are deliberately
distinguishable. "We have no filing for 2015" is `period_not_covered`. "2030
hasn't happened" is `future_period`. "The FY2024 10-K existed but not on the
date you asked about" is `period_not_filed_as_of`. "This filing tags two
candidates with different values" is `ambiguous_concept`. A single "sorry, I
don't know" would make all four look like the same failure, and three of them
are not failures at all.

**"What did you get wrong?"**
Several things, and each was caught by a check rather than by review. Five
filings refused to extract because `ixt-sec:numwordsen` did not know the word
`nil` or how to read "three million" — a gap my 12-filing survey missed and a
65-filing survey found. One unreadable cover-page date was taking down whole
filings, discarding 1,127 good numeric facts over one string. My own
cross-check script reported 26 phantom discrepancies because its comparison key
omitted the start date, and Microsoft tags annual and quarterly dividends
*ending on the same day*. And the first version of the statement-validation
check called 73 values "conflicts" when the real problem was that v1's parser
had given it a heading instead of a statement.

## Three known weaknesses

1. **Coverage is 81.3%, and the gap is real.** Of 780 (company, metric, year)
   combinations, 146 return `metric_not_found_in_filing`. Most are legitimate —
   banks have no gross-profit line, brokers tag no "operating income" — but
   nothing yet distinguishes "this company genuinely does not report that" from
   "our candidate list is missing the tag they used". That distinction is what
   P2-11's report exists to make, and it is still a human judgement.

2. **Statement validation leans on v1's parser, which is unreliable.** The check
   that confirms a value appears on the rendered statement can only work where
   the parsed statement text exists and is actually the statement. It often is
   not: the section labelled "Consolidated Statements of Operations" is 106
   characters of heading for Amazon, empty for JPMorgan, and over a megabyte for
   Goldman Sachs. 51 values are marked `conflict` and spot-checking says they
   are parser defects, not extraction errors — but "spot-checking says" is
   weaker than a number. Phase 3 reads the same sections for the text path, so
   this has to be faced there.

3. **Only each filing's own year is stored, not its comparative columns.** A
   10-K prints two or three prior years beside the current one, and this engine
   deliberately ignores them: FY2023 revenue comes from the FY2023 filing. That
   is the right default — the two can differ after a restatement, and preferring
   whichever was convenient would make answers depend on which filings happened
   to be built — but it means a trend question needs every year to have been
   built, and a year outside the five-filing window simply is not there.
