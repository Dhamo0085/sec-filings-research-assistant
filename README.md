# SEC Filings Research Assistant

A research assistant over SEC 10-K filings. It answers questions about public
companies' annual reports and, when it states a number, that number comes from
the filing's own inline-XBRL tags rather than from a model reading prose.

> Research tool, not investment advice.

```
You: What was Netflix's revenue in fiscal 2024?

Netflix, Inc.'s revenue for fiscal 2024 (year ended 2024-12-31) was
$39.00 billion ($39,000,966,000). [1]

  Total net revenue = us-gaap:Revenues, fiscal year ended 2024-12-31
  [1] NFLX 10-K, filed 2025-01-27, accession 0001065280-25-000044
```

```
You: As of 2024-10-31, what was Apple's revenue for fiscal 2024?

As of 2024-10-31, Apple Inc had not yet filed its annual report for fiscal
2024 — that filing became public on 2024-11-01. I can answer for fiscal 2021
through 2023 as of that date.
```

---

## Why it is built this way

The project began as a conventional RAG prototype: chunk the filings, embed
them, retrieve, and ask a model to read the answer out of what came back. A
Phase 0 audit of that prototype found defects that are not fixable by better
prompting, because they are properties of the architecture:

* **Numbers were read from prose.** A prompt told the model to "assume
  millions", which silently multiplies every figure from a filer reporting in
  thousands by a thousand. Netflix reports in thousands.
* **Nothing knew when a filing became public.** Asked about fiscal 2024 "as of
  June", it would happily answer from a 10-K filed in November.
* **It could not say no.** There was no abstention path, so an unanswerable
  question produced a fluent answer assembled from whatever retrieval returned.
* **Failures looked like questions.** A model outage was rendered as "Which
  company are you asking about?", so an outage and a vague question were the
  same response.

v2 keeps retrieval for prose and adds four things that address those directly: a
**facts engine** that reads the filing's own XBRL tags and a deterministic
calculator; **point-in-time scope** driven by a catalog of filing dates;
**typed outcomes** whose constructors refuse to build an untraceable number, a
look-ahead citation, a reasonless refusal or an outage dressed as a question;
and a **measured evaluation** with an independent oracle.

---

## What it does

| | |
|---|---|
| **Numeric questions** | resolved from inline-XBRL facts, with the concept, period and filing named. No model is called at all. |
| **Computed questions** | margins, growth, CAGR, ratios and differences, from a deterministic calculator over resolved facts. |
| **Comparisons and trends** | several figures, each attributed to its company and year. |
| **Narrative questions** | hybrid retrieval over filing text, answered with citations to the section the text came from. |
| **Point in time** | `as_of` restricts every answer to filings that were public on that date. |
| **Refusal** | eleven named reasons, each with its own message, rather than a guess. |

Twelve metrics are supported (`facts/concepts.yaml`). Anything else is refused
rather than guessed — guessing a concept is what made one filer's revenue read
37% low in the prototype.

---

## Architecture

```
question
   │
   ├─ routing/        rules first; the model only for genuine ambiguity
   │                  entities, periods, metric, focus, as_of
   │
   ├─ catalog/        which filings exist, and when each became public
   │                  (the only thing that decides point-in-time scope)
   │
   ├── facts path ──────────────────────────────── numeric / computed
   │     facts/resolve.py   concept candidates → one fact, or abstain
   │     facts/calc.py      margins, growth, CAGR, ratios, differences
   │     answering/facts_answer.py   deterministic template, no model
   │
   ├── text path ───────────────────────────────── narrative
   │     retrieval/        hybrid dense + BM25, rerank, focus boost,
   │                       parent-section context
   │     answering/text_answer.py   structured {found, answer} from the model
   │
   ├─ answering/abstain.py   one gate, reason → message
   └─ answering/outcome.py   the typed result every path returns
```

`answering/outcome.py` is where the guarantees live. They are validators on a
constructor, not conventions:

* **G1** an answered outcome without a fact citation and a definition note
  naming the concept and period cannot be built;
* **G2** a citation whose filing date is after `as_of` cannot be built;
* **G3** a refusal with citations, or without a reason, cannot be built;
* **G4** a dependency failure cannot be built as a clarification.

---

## Results

Measured on an 80-item gold set whose expected values are confirmed against SEC
`companyfacts` — an independent pipeline over the same filings. Reported on the
69-item paired subset, which is the items every variant has a corpus for.

| variant | | paired | look-ahead violations |
|---|---|---:|---:|
| **V3** | the shipped system | **58/69 (84.1%)** | **0** |
| V2 | point-in-time scope and abstention gate off | 50/69 (72.5%) | 4 |
| V1 | facts engine off — numbers read from text | 41/69 (59.4%) | 0 |
| V0 | the v1 prototype | see `reports/phase4/REPORT.md` | |

The V2 row is the ablation working: with point-in-time scope off, the system
answers four questions from filings that were not public on the date asked.
With it on, none.

Numeric accuracy is 17/18 on the paired subset with the facts engine and 12/18
without it. Every rate carries a Wilson 95% interval, several of which are 30
points wide — an 80-item gold set does not support fine distinctions, and the
report says so rather than implying otherwise.

Full tables, the per-item rows, the retrieval ablations and the failure analysis
are in [`reports/phase4/REPORT.md`](reports/phase4/REPORT.md). The method is in
[`docs/EVAL.md`](docs/EVAL.md). **What this does not do, and where to be
sceptical, is in [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md)** — including that
the owner's verification gate is not yet signed, which makes every figure above
provisional.

---

## Running it

```bash
make setup          # virtualenv + dependencies
make test           # offline: no network, no API key needed
make catalog        # build the filing catalog from cached SEC responses
make facts          # build the facts store from inline XBRL
make up             # API + UI at http://localhost:8000
```

Ask one question from the command line:

```bash
python query.py "What was Apple's revenue in fiscal 2024?"
python query.py --as-of 2024-03-01 "What was Apple's latest annual revenue?"
```

Reproduce the evaluation:

```bash
python -m eval.gold.build_gold --check   # the gold set rebuilds byte-identically
python -m eval.runner --variant V3       # resumable
python -m eval.ablations                 # retrieval arms, no generation tokens
python -m eval.mini_eval                 # what CI enforces, offline
```

Free-tier LLM providers only. No paid services, and no cost-per-query figure
anywhere in the reports, because there is no price to multiply by.

---

## Documentation

| | |
|---|---|
| [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) | the specification, phase by phase |
| [`docs/EVAL.md`](docs/EVAL.md) | how the system is evaluated, and why that way |
| [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) | what it does not do; read before trusting a number |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | every non-trivial choice, with what would change it |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | what changed in each phase |
| `docs/explainers/` | a plain-language one-pager per phase |

The earlier prototype is preserved at tag `v1-baseline`, and is run as the V0
baseline rather than described from memory.

---

## Licence

MIT. See `LICENSE` and `NOTICE`.
