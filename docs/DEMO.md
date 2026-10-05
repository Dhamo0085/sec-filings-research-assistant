# Demo guide

Three scripts — 3, 8 and 15 minutes — plus a pre-flight checklist, the order
to ask things in, a troubleshooting table, and a plan B.

**The one rule: facts first, narrative last.** Seven of the eight demo
queries call **no model at all** and return in about 0.03 s. Only the
narrative question needs the LLM, and the LLM is the only part a free tier can
take away from you mid-demo.

---

## 10 minutes before

```bash
make demo-check
```

One command, about 20 seconds, prints a PASS/FAIL table over `/health`
(including the LLM state), collections loaded, all eight demo queries run for
real, and `scripts/smoke.py`. **12/12 PASS is the green light.**

Then, in order:

- [ ] **Plug in the laptop.** The embedding model and Qdrant are CPU-heavy and
      macOS throttles hard on battery.
- [ ] **Quit other apps.** This machine has 8 GB; Qdrant in local mode holds
      every collection in RAM.
- [ ] **`make demo-check`** → expect `12/12 PASS`.
- [ ] If the LLM row says anything but `ok`, **plan to skip D8 live** and read
      it from [`demo/TRANSCRIPTS.md`](demo/TRANSCRIPTS.md). Say so out loud —
      "this one needs a free-tier provider and I'd rather not gamble on it" is
      a better look than a spinner.
- [ ] **Start the server** in its own terminal and leave it there:
      ```bash
      make up
      ```
      Browse to <http://localhost:8000> and ask **one** warm-up question, so
      the embedding model is loaded before anyone is watching. The first
      narrative question of a session is slow; the rest are not.
- [ ] Have [`demo/TRANSCRIPTS.md`](demo/TRANSCRIPTS.md) open in a second tab.
- [ ] Have `reports/phase4/REPORT.md` open in a third, at the A1 table.

Note that `make demo-check` and `make up` **cannot both hold the store**:
Qdrant's local mode takes an exclusive lock. Run the check first, then start
the server — or, if the server is already up, run
`python scripts/demo_check.py --base-url http://localhost:8000`.

---

## The eight queries

| | what to type | path | LLM? |
|---|---|---|---|
| **D1** | What was Netflix's revenue in fiscal 2024? | facts | no |
| **D2** | What was JPMorgan's total net revenue for fiscal 2024? | facts | no |
| **D3** | What was Apple's operating margin in fiscal 2024? | facts + calculator | no |
| **D4** | Compare Apple and Microsoft revenue in fiscal 2024 | facts | no |
| **D5** | *(as of 2024-06-30)* What was Apple's revenue in fiscal 2024? | point in time | no |
| **D6** | What was SpaceX's revenue in fiscal 2024? | abstention | no |
| **D7** | Should I buy Apple stock? | abstention | no |
| **D8** | What risks does Apple disclose about its supply chain? | retrieval | **yes** |

---

## 3-minute script

Three queries. The point is: *verified numbers, point-in-time, honest refusal.*

**1. D1 — Netflix revenue 2024.**
*Type:* `What was Netflix's revenue in fiscal 2024?`
*Point at:* the `Definition used:` line and the accession number in `SOURCES`.
*Expect:* `$39.00 billion ($39,000,966,000)`, one citation, instant.
*Why it matters:* "Netflix reports in thousands. The prototype this replaced
had a prompt telling the model to assume millions — every Netflix figure was
1,000× wrong and read perfectly fluently. This number comes from the
company's own XBRL tag, so the scale is the filer's, not a guess."

**2. D5 — the same question, as of 2024-06-30.**
*Type:* set the as-of date to `2024-06-30`, ask `What was Apple's revenue in fiscal 2024?`
*Point at:* `abstained (period_not_filed_as_of)` and the date **2024-11-01**.
*Expect:* a refusal that names the real filing date and offers FY2021–2023.
*Why it matters:* "Point-in-time correctness is a validator, not a filter —
an answer carrying a citation newer than the as-of date cannot be constructed.
With that guard ablated the same system commits four look-ahead answers on the
gold set; with it on, zero."

**3. D7 — Should I buy Apple stock?**
*Point at:* `abstained (out_of_scope)`.
*Why it matters:* "The prototype had no abstention path at all, so an
unanswerable question produced a fluent answer assembled from whatever
retrieval returned. Eleven named reasons now, each with its own message."

---

## 8-minute script

The three above, then:

**4. D3 — Apple operating margin 2024.**
*Point at:* `Computed as 123216000000 / 391035000000 x 100`, and the **two**
citations.
*Expect:* `31.51%`.
*Why it matters:* "The arithmetic is `Decimal` in `facts/calc.py`, not a model
doing mental maths in a prompt — and both operands are cited separately, so
you can check each one."

**5. D4 — Compare Apple and Microsoft revenue in fiscal 2024.**
*Point at:* the per-company attribution and the sentence
*"the fiscal years end on different dates, so the periods are not identical."*
*Why it matters:* "Apple's FY2024 ends in September, Microsoft's in June. A
text-only system states both numbers and lets you believe they are
comparable."

**6. D8 — Apple supply-chain risks.** *(the only model call)*
*Point at:* the citation — `Apple Inc. FY2024 — Item 1A: Risk Factors`.
*Expect:* about 5–10 s, a cited paragraph.
*Why it matters:* "This is the RAG path, and it's the weakest part. Retrieval
finds the right section 97.8% of the time on a 45-item set — but under the
automated scorer only 8 of 15 narrative gold items pass, and 5 of those are
over-refusals. I'd rather tell you that than show you only the ones that work."

---

## 15-minute script

Everything above, then D2 and D6, then the three things worth more than a
query each:

**7. D2 — JPMorgan total net revenue 2024.**
*Why it matters:* "Banks don't have a 'revenue' line in the sense Apple does.
Metric resolution is per-filer tiered candidates, not one global priority
list — a single list is how the prototype read one filer's revenue 37% low."

**8. D6 — SpaceX.**
*Why it matters:* "`company_not_found`, with the reason: it may be private, may
file under another name, or may not file at all."

**9. The evaluation.** Open `reports/phase4/REPORT.md` at table A1.
```
V3 (shipped)  73/80 (91.2%)   0 flags
V2 (no as_of) 63/80 (78.8%)   4 look-ahead violations
V1 (no facts) 37/80 (46.2%)   19 uncited numbers
V0 (v1)       19/69 (27.5%)   15 uncited, 36 invalid citations
```
*Say:* "Four variants, one gold set, deterministic scorers, no LLM judge. The
V2 row is the ablation working: turn the point-in-time guard off and the same
system answers four questions from filings that weren't public."

**10. The honest caveat, unprompted.** "Thirteen of the thirty-six-item gap
between V3 and V1 is context budget, not architecture — I measured that
separately, because the first reading of it would have overstated the facts
engine. And the narrative numbers have never been human-rated; they're the
automated scorer's."

**11. The cost line.** `make demo-check` output, 7 of 8 rows at 0.03 s.
*Say:* "V3 answers all 80 gold questions on 54,047 generator tokens because
the facts path never calls a model. The text-only variant needs 4.5× that to
score half as well."

---

## Which queries need no LLM at all

**D1 D2 D3 D4 D5 D6 D7** — all seven, including every refusal. They read the
facts store and the catalog, both SQLite, and return in 0.02–0.04 s.

**D8 is the only one that calls a model.** If the LLM is unavailable it
returns HTTP **503** with `error_code: llm_unavailable` — which is itself
worth showing once, deliberately, rather than being surprised by.

---

## Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `Storage folder data/qdrant is already accessed by another instance` | Qdrant local mode takes an **exclusive lock**; `make up`, `make demo-check`, a test run or a stray Python process are all holding it | `lsof +D data/qdrant` or `pgrep -fl "uvicorn\|pytest\|demo_check"`, stop the other one, retry. Only ever **one** process on the store. |
| `[Errno 48] Address already in use` | port 8000 is taken (often a previous `make up` that did not exit) | `lsof -ti :8000 \| xargs kill`, or `make up PORT=8001` |
| `/health` says `llm` is not `ok`, or D8 returns 503 `llm_unavailable` | free-tier provider is rate-limited, or no key is set | Demo D1–D7 and read D8 from [`demo/TRANSCRIPTS.md`](demo/TRANSCRIPTS.md). Do **not** retry in a loop — that burns the daily quota. |
| D8 returns 503 `llm_rate_limited` | the day's or the minute's request quota is spent | Wait, or switch provider order via `GENERATOR_PROVIDERS`; the prompt cache serves a question already asked for free. |
| The first query takes 20–40 s, the rest are instant | the bge embedding model loads lazily on the first text query; Qdrant pages collections into RAM | **Ask one warm-up question before the demo starts.** This is the single most common "it's broken" that isn't. |
| Everything is slow and the fans are loud | the machine is swapping (8 GB, Qdrant holds all collections in RAM) | Quit other apps. Embedding throughput falls from ~2.3 to ~0.8 chunks/s under memory pressure. |
| `collections_loaded: 0` in `/health` | `QDRANT_PATH` points somewhere empty, or the index was never built | `ls data/qdrant` — expect 39 collection directories. If it is genuinely empty, the index must be rebuilt (~5 hours); demo D1–D7, which do not need it. |
| A numeric answer is missing | `data/derived/facts.sqlite` is absent | `make facts` (offline, from `.cache/`, a few minutes) |
| `make demo-check` passes but the UI shows nothing | the browser is pointed at a different port than `make up` is serving | Check the port in the `make up` output. |

---

## Plan B

[`docs/demo/TRANSCRIPTS.md`](demo/TRANSCRIPTS.md) holds **real captured output
for all eight queries**, taken against the live store on 2026-10-05. If the
live demo fails, open it and read from it — and say plainly that it is a
saved transcript, not a live answer. A recorded real result read honestly is
worth more than a live result that does not arrive.

If the whole machine is uncooperative, the fallback order is:

1. `python query.py "<question>"` from the terminal — no server, no browser.
2. The transcripts.
3. `reports/phase4/REPORT.md` table A1 — talk about the measurement instead of
   the product. It is the more interesting half anyway.
