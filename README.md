# SEC Filings Research Assistant

**Ask questions about US public companies' annual reports and get answers you
can check — with the filing, the XBRL tag and the filing date attached, or an
explicit refusal.**

`v2.0.0-rc1` · private portfolio project · research tool, **not investment
advice**

---

## 1. What it does

* **Answers numeric questions from the filing's own inline-XBRL tags**, not
  from a model reading prose — through a deterministic resolver and a
  `Decimal` calculator that no language model touches.
* **Answers as of a date.** `as_of=2024-06-30` means only filings that were
  public on 2024-06-30 are in scope, enforced by a validator that refuses to
  construct an answer carrying a later citation.
* **Refuses, with a named reason.** Eleven of them, plus seven typed error
  codes for dependency failures, so "I can't" and "it's broken" are different
  answers.

**What makes it different from a RAG demo:** verified numbers, point-in-time
answers, and explicit abstention — all three measured against an 80-item gold
set with deterministic scorers and four ablation variants, rather than
asserted.

---

## 2. Results at a glance

Post-fix, on the 80-item gold set. Every rate carries a Wilson 95% interval,
because 80 items does not support fine distinctions.

| variant | what is removed | score | 95% CI | flags |
|---|---|---:|---:|---|
| **V3 — shipped** | nothing | **73/80 (91.2%)** | 83.0–95.7% | **none** |
| V2 | `as_of` scope and the abstention gate | 63/80 (78.8%) | 68.6–86.3% | **4 look-ahead answers** |
| V1 | the facts engine (numbers read from text) | 37/80 (46.2%) | 35.7–57.1% | 19 uncited numbers |
| V1-generous | the facts engine, but a 5× context budget | 50/80 (62.5%) | 51.5–72.3% | 28 uncited numbers |

Against the **v1 prototype this replaced**, on the frozen 69-item subset where
V0 is a fair control:

| | V0 (prototype) | V1 | V2 | **V3** |
|---|---:|---:|---:|---:|
| overall | 19/69 (27.5%) | 33/69 (47.8%) | 52/69 (75.4%) | **62/69 (89.9%)** |
| 95% CI | 18.4–39.0% | 36.5–59.4% | 64.0–84.0% | **80.5–95.0%** |
| look-ahead violations | — | 0 | 4 | **0** |
| uncited / invalid citations | 15 / 36 | 19 / 0 | — | **0 / 0** |

**The honest caveats, up front:**

* **37 of the 80 expected answers were hand-checked against filing snippets by
  a person.** The other 43 are cross-checked by machine against SEC
  `companyfacts` (an independent pipeline over the same filings) or, for
  refusals, narrative and dates, have no oracle by nature. The gold set
  records which tier each item is in (`owner` > `companyfacts` > `auto`).
* **Narrative is the weakest area, and it is automated-scorer-only.** V3
  answers **8 of 15** narrative items under the automated scorer (CI
  30.1–75.2%); **5 of the 15 are over-refusals** even though the section audit
  confirms the expected section exists and carries the topic. **These results
  are not human-verified.** No one has checked whether the 8 passes are good
  answers or merely well-sourced ones.
* **13 of the 36-item V3-versus-V1 gap is context budget, not architecture.**
  The V1-generous arm exists to say that. What the budget does *not* explain:
  all 20 of V1-generous's numeric passes are *right value, no fact citation*
  against V3's 25 *correct*; 28 uncited numbers against zero; and 19× the
  tokens (1,024,250 against 54,047) for 23 fewer items. **The facts engine's
  value is robustness under free-tier limits, zero generator tokens and exact
  traceability — as well as accuracy, not instead of it.**
* **Pre-fix numbers exist and are kept as "before" evidence**, not quoted
  here: before the context-budget fix, 14 V1 items were literally unanswerable
  (prompts no provider accepts) while the rest got unlimited context. Only the
  post-fix column describes a system that can run.

Method: [`docs/EVAL.md`](docs/EVAL.md). Full tables, per-item rows and failure
analysis: [`reports/phase4/REPORT.md`](reports/phase4/REPORT.md). What to be
sceptical about: [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

---

## 3. Architecture

```
                         ┌──────────────────────────────┐
  question ──────────────▶  routing/   rules first      │
  (+ optional as_of)     │  entities · period · metric  │
                         │  focus · intent              │
                         │  (a model only for genuine   │
                         │   ambiguity — 30/30 smoke    │
                         │   questions need none)       │
                         └───────────────┬──────────────┘
                                         │
                         ┌───────────────▼──────────────┐
                         │  catalog/   the clock        │
                         │  which filings exist, and    │
                         │  when each became public.    │
                         │  The only thing that decides │
                         │  point-in-time scope.        │
                         └───────┬──────────────┬───────┘
         numeric / computed      │              │   narrative
      ┌──────────────────────────▼──┐        ┌──▼───────────────────────────┐
      │  FACTS PATH   no model call │        │  TEXT PATH                   │
      │                             │        │                              │
      │  facts/resolve.py           │        │  retrieval/                  │
      │    tiered concept candidates│        │    dense (bge) + BM25        │
      │    → one fact, or abstain   │        │    → fuse → cross-encoder    │
      │  facts/calc.py              │        │    → focus boost             │
      │    margin · growth · CAGR   │        │  bounded parent-chunk window │
      │    · ratio · difference     │        │    6,000-token budget        │
      │    in Decimal               │        │  generator → {found, answer} │
      │  answering/facts_answer.py  │        │  answering/text_answer.py    │
      │    deterministic template   │        │                              │
      └──────────────┬──────────────┘        └──────────────┬───────────────┘
                     └────────────┬─────────────────────────┘
                                  │
                  ┌───────────────▼────────────────┐
                  │  answering/abstain.py          │
                  │    11 reasons → 11 messages    │
                  │  answering/outcome.py          │
                  │    G1 no untraceable number    │
                  │    G2 no citation after as_of  │
                  │    G3 no reasonless refusal    │
                  │    G4 no outage-as-question    │
                  └───────────────┬────────────────┘
                                  │
                       Outcome  →  api/ (200) or 503 + error_code
```

**Request lifecycle.** `POST /query {question, as_of?}` → size cap and rate
limit → `query.ask()` → `route()` resolves entities, period, metric, focus and
intent with rules (a model only if genuinely ambiguous) → the catalog narrows
the filing set to what was public on `as_of` → **facts path**: resolve one
fact per operand, compute in `Decimal`, render a deterministic sentence, cite
each operand — *zero model calls*; or **text path**: retrieve over the
in-scope collections, rerank, assemble bounded context, ask the generator for
`{found, answer}`, prune citations to the sources actually used and renumber
them `1..n` → `answering/abstain.py` if neither path can answer → `Outcome` is
constructed, and its four validators reject anything untraceable, look-ahead,
reasonless or mislabelled → HTTP 200 with the answer and citations, or **503**
with a typed `error_code`.

`answering/outcome.py` is where the guarantees live. They are **validators on
a constructor**, not conventions: an outcome that violates one cannot be
built, so a defect becomes an exception at the point of construction rather
than a wrong answer on a screen.

---

## 4. Features

Each in one paragraph; the full page, with a real captured example and a
stated limitation for every one, is [`docs/FEATURES.md`](docs/FEATURES.md).

**Facts engine and calculator.** Inline-XBRL is parsed into a local store of
**29,002 facts** across 94 submissions and 18 filers; 12 registry metrics map
to *per-filer tiered candidate concepts* (one global priority list is how the
prototype read BlackRock's revenue 37% low), and arithmetic is `Decimal` in
`facts/calc.py`. Coverage is 634 of 780 resolutions (81.3%) with **zero
ambiguous**; the extractor agrees with SEC `companyfacts` on **99.9787% of
23,429 comparable pairs**, with zero scale and zero sign errors.

**Point in time (`as_of`).** A catalog of 365 annual filings and 29
amendments, with real filing dates, is the only thing that decides scope — and
it resolves a ticker across every CIK it has filed under, which BlackRock
needs. V3 scores 8/8 on the point-in-time items with 0 look-ahead violations;
ablate the guard and the same system commits 4.

**Abstention and typed errors.** Eleven named refusal reasons, each with its
own message, and seven typed error codes surfaced as HTTP 503 — so a model
outage is an outage, not "which company did you mean?". Two validators make
both properties unrepresentable rather than conventional.

**Citations.** Every answer names ticker, fiscal label, accession, filing date
and either the XBRL concept or the section. Indices are exactly `1..n`, and
the generator failover order *requires* ASCII `[N]` markers — two otherwise
capable models were excluded because they emit fullwidth `【1】`, which the
parser would silently read as zero citations.

**Rules-first router.** Entities, periods, metrics and intent resolved in
code; all 30 smoke questions route with **zero** LLM calls, which is why seven
of the eight demo queries return in about 0.03 s and why comprehension does
not degrade when a free tier throttles.

**Text and RAG path.** Dense (bge-base, 512 tokens) + BM25, fused, reranked by
a cross-encoder, focus-boosted, scoped by the catalog — **44/45 (97.8%)
section hit@5, MRR 0.870**. The parser rewrite plus re-index moved that from
75.0% to 92.5% on the filings both indexes hold.

**Free-tier LLM client.** One client for every call: ordered failover per role
(chosen by a measured 24-call bake-off, not by published limits), a prompt
cache, bounded retry, a budget, and typed exceptions. A prompt too large for
the list's smallest member raises its own error *before* the failover loop —
it used to subclass the rate-limit error, and that cost a day of wrong
diagnosis.

**Section catalog.** The parser picks the maximum-weight heading subsequence
with strictly increasing line *and* Item priority; the audit measures the
result at **290 of 429 slices usable, Item 1 and Item 1A at 39/39**. Nothing
user-visible may claim a slice is complete.

**Evaluation harness.** 80 *planned* gold items built through the product's
own resolver and **rejected** if the oracle disagrees; deterministic scorers
with no LLM judge; five variants run resumably with every model call
instrumented; retrieval ablations that spend zero generation tokens.

---

## 5. Quickstart

**Prerequisites.** Python 3.12, macOS or Linux, ~8 GB RAM, ~2 GB disk. No
paid services — every provider used has a free tier.

```bash
make setup          # virtualenv + dependencies
make test           # 1,428 offline tests; no network, no API key needed
```

**`.env` keys** (copy `.env.example`; the file is gitignored and never read or
printed by any tooling):

| key | what it is for | needed for |
|---|---|---|
| `edgar_email` | SEC requires a contact address in the User-Agent | fetching filings |
| `GEMINI_API_KEY` | primary generator (free tier) | narrative answers |
| `GROQ_API_KEY` | primary router / judge (free tier) | ambiguous questions |
| `ADMIN_TOKEN` | **optional.** Unset — the default — means every `/admin/*` route and `/ingest` returns **503** and `?debug=1` hands out nothing | admin routes |

**Building the data.**

```bash
make catalog        # filing catalog from SEC EDGAR         (minutes)
make facts          # inline-XBRL facts store               (minutes)
make ingest         # download, parse, chunk and index      (see below)
```

> ⚠️ **A fresh clone is not one command away from a demo.** `make catalog` and
> `make facts` are quick, but building the text index means embedding ~38,000
> chunks, which measured **about 5 hours on an 8 GB laptop** at 2.2–2.4
> chunks/s. The one-command-from-a-clean-clone goal is **not met**, and
> pretending otherwise would waste a reader's evening. The facts path — every
> numeric, computed, comparison and point-in-time answer — works as soon as
> `make catalog` and `make facts` finish; only narrative questions need the
> index.

**Running it.**

```bash
make up             # API + UI at http://localhost:8000
make demo-check     # pre-flight: health, LLM state, collections, 8 real queries, smoke
```

`make demo-check` takes about 20 seconds and prints a PASS/FAIL table. Note
that it and `make up` cannot both hold the store — Qdrant's local mode takes an
exclusive lock — so run the check first, or point it at the running server:
`python scripts/demo_check.py --base-url http://localhost:8000`.

One question from the command line, no server:

```bash
python query.py "What was Apple's revenue in fiscal 2024?"
python query.py --as-of 2024-03-01 "What was Apple's latest annual revenue?"
```

---

## 6. Demo

The presenter's guide — 3-, 8- and 15-minute scripts, a pre-flight checklist,
a troubleshooting table and saved transcripts as a plan B — is
[`docs/DEMO.md`](docs/DEMO.md).

The eight example questions, in the order to ask them (**the first seven call
no model at all**):

1. What was Netflix's revenue in fiscal 2024? *(a filer that reports in thousands)*
2. What was JPMorgan's total net revenue for fiscal 2024? *(a bank's own revenue line)*
3. What was Apple's operating margin in fiscal 2024? *(deterministic calculator)*
4. Compare Apple and Microsoft revenue in fiscal 2024 *(different fiscal year ends)*
5. *(as of 2024-06-30)* What was Apple's revenue in fiscal 2024? *(point in time)*
6. What was SpaceX's revenue in fiscal 2024? *(abstention: not an SEC filer)*
7. Should I buy Apple stock? *(abstention: out of scope)*
8. What risks does Apple disclose about its supply chain? *(retrieval — the only model call)*

Real captured output for all eight: [`docs/demo/TRANSCRIPTS.md`](docs/demo/TRANSCRIPTS.md).

---

## 7. How it is evaluated

**The gold set** is 80 *planned* items — numeric 25, computed 12,
compare/trend 10, narrative 15, `as_of` 8, abstain 10 — each named with the
reason it exists, covering four sectors, all twelve metrics and three awkward
filer properties (Netflix reports in thousands; Wells Fargo moves Items
1A/7/8 into an EX-13 exhibit; BlackRock tags two revenue concepts 37% apart).
Expected values are resolved through the product's own resolver and then
**checked against SEC `companyfacts`**; a value the oracle disagrees with
**fails the build** rather than being downgraded and shipped.

**The scorers** are deterministic — numeric tolerance, scale and sign checks,
section-id matching, and flags for uncited numbers and look-ahead citations.
No model judges anything, because a judge would make the headline depend on
the judge's taste.

**The variants** are V0 (the v1 prototype, run from the `v1-baseline` tag, not
described from memory), V1 (facts engine off), V2 (`as_of` and the abstention
gate off), V3 (shipped) and V1-generous (V1 at a 5× context budget, pinned to
one model). **The ablations** measure retrieval alone — bm25, dense, hybrid,
hybrid+rerank, the shipped default, and parent-context-off — spending zero
generation tokens.

Reproduce it:

```bash
python -m eval.gold.build_gold --check     # gold set rebuilds byte-identically
python -m eval.runner --variant V3         # resumable; --report-only re-scores
python -m eval.ablations                   # retrieval arms, no generation tokens
python -m eval.mini_eval                   # the offline subset CI enforces
python scripts/score_d21_subset.py         # the frozen 69-item paired subset
```

No cost-per-query figure appears anywhere: free tiers only, so there is no
price to multiply by, and inventing one would put a fabricated number in a
results table.

---

## 8. Limitations and future work

The short version; the full list, with why each is deferred, is
[`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

* **Narrative is automated-scorer-only, n=15, and 5 of 15 are over-refusals.**
  The human rating gate was not completed, so no narrative figure here is
  human-verified. Improving narrative generation is the **top** item of future
  work.
* **80 gold items**, with intervals up to 30 points wide. 37 owner-verified,
  43 machine-verified or unverifiable by nature.
* **12 metrics**; anything else is refused. Two routing defects and one wrong
  refusal reason are open and itemised.
* **One item (JPM FY2022) is outside the text index.** Item 7 parses usably in
  25 of 40 filings, Item 3 in 6 of 40.
* **A fresh clone needs ~5 hours to build the index.**
* No follow-up handling, no mixed numeric+narrative answers, no hosting, no
  response caching, no slimmed container image.

---

## 9. Repository layout

| path | what is in it |
|---|---|
| `routing/` · `catalog/` | question understanding; the filing catalog that decides `as_of` scope |
| `facts/` | inline-XBRL extraction, the concept registry, resolution, the calculator |
| `retrieval/` · `ingestion/` | parsing, chunking, indexing, hybrid retrieval and reranking |
| `answering/` | the typed `Outcome`, its four validators, the abstention gate, both answer renderers |
| `generation/` · `llm/` | prompts and citation parsing; the one multi-provider free-tier client |
| `api/` · `ui/` | FastAPI app, admin auth (fail-closed), rate limiting, the browser UI |
| `query.py` | the dispatcher (`ask()`) and a CLI |
| `eval/` | gold set, scorers, runner, ablations, the offline subset CI enforces |
| `scripts/` | one-purpose tools: hygiene, security, demo-check, re-index, audits, sheets |
| `tests/` | 1,428 offline tests (unit + integration), plus a `live` marker |
| `docs/` | spec, decisions, evaluation method, limitations, features, demo, this brief |
| `reports/` | every phase's report and its raw artifacts |

| document | |
|---|---|
| [`docs/INTERVIEW_BRIEF.md`](docs/INTERVIEW_BRIEF.md) | the project in one page |
| [`docs/FEATURES.md`](docs/FEATURES.md) | each feature, with a real example and its limitation |
| [`docs/DEMO.md`](docs/DEMO.md) | how to present it live |
| [`docs/EVAL.md`](docs/EVAL.md) | how it is evaluated, and why that way |
| [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) | read before trusting a number |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | every non-trivial choice, with what would change it |
| [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) | the specification, phase by phase |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | what changed in each phase |

---

## 10. Provenance, licence, data

The code began as an earlier MIT-licensed RAG prototype of the author's; this
is a new personal portfolio repository with fresh history. The prototype is
preserved at tag `v1-baseline` and is **run** as the V0 baseline rather than
described from memory. `LICENSE` and `NOTICE` are kept.

**Data source:** public filings from the SEC's EDGAR system, fetched with a
contact address in the User-Agent as SEC requires, cached locally, and treated
as read-only. No filing content is redistributed in this repository.

**Licence:** MIT.

> **Not investment advice.** This is a research tool over public filings. It
> states what a company reported, with the document it reported it in. It does
> not value securities, forecast, or recommend anything — and when asked to,
> it refuses by name.
