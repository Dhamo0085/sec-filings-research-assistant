# Phase 1 explainer — foundation, hardening, catalog

A plain-language walkthrough of what Phase 1 built and why, written so the
owner can explain any part of it without re-reading the code.

---

## 1. The one-sentence version

Phase 1 did not add features. It made the existing system **honest** (it now
says when it is broken instead of pretending the user forgot something),
**testable** (272 offline tests, no network, no API key needed), and
**datable** (a catalog that knows when every filing became public, which is
what point-in-time answers will be built on).

---

## 2. Why this had to come first

Phase 0 probed the old deployment and found all five test queries answering
*"Which company are you asking about?"* in about 0.2 seconds. That is far too
fast for a language-model call, so nothing was reaching the model at all.

P1-00 then confirmed the cause: the Groq API key can see 11 models, and
**neither of the two models the code asked for is among them**. Both
`llama-3.1-8b-instant` and `llama-3.3-70b-versatile` are Enterprise-only.
Every request was getting a 404.

The damaging part was not the 404. It was that the code turned a 404 into a
polite question to the user. One `except Exception` in the classifier returned
"no company found", and the layer above rendered that as a request for
clarification. A total outage and a vague question were indistinguishable.

That is the defect Phase 1 is built around.

---

## 3. What changed, in six pieces

### 3.1 Failures are now loud (P1-05)

```
before:  LLM 404  ->  except Exception  ->  "no tickers"  ->  "Which company?"  (HTTP 200)
after:   LLM 404  ->  LLMAuthError      ->  status=error, error_code=llm_auth   ->  HTTP 503
```

Every answer now carries a **status**: `answered`, `answered_text`,
`abstained`, `clarification_needed`, or `error`. When it is `error`, an
`error_code` says which dependency failed, the API returns 503, and the UI
shows the reason. `GET /health` reports the model's state
(`ok | auth_error | rate_limited | unreachable`) from a cached probe — cached
because probing per request would spend the day's free-tier quota on health
checks.

The genuine "you didn't name a company" path still exists. It is just no
longer the place outages go to die.

### 3.2 One door for every model call (P1-04)

All language-model access goes through `llm/`. It speaks the OpenAI-compatible
HTTP API, so **providers are configuration, not code** (`llm/providers.yaml`).
It gives us:

- **ordered failover** — if Groq is rate-limited, try Gemini;
- a **capability check** — a job needing strict JSON is never sent to a model
  that cannot produce JSON;
- a **disk cache** — a repeated prompt costs zero tokens, which is what will
  make the Phase 4 ablations affordable;
- **budgets per model**, corrected from the provider's own rate-limit headers;
- **pinned mode** — evaluation runs use exactly one model, so a published
  number belongs to one model rather than to whatever answered first.

### 3.3 Citations stopped lying (P1-07)

When several sub-answers are merged, each one's `[1]`, `[2]`, `[3]` markers
must be renumbered. The old code did it with repeated find-and-replace, so a
marker already moved could be moved again:

| shift by | sub-answer in | old output | correct |
|---|---|---|---|
| 1 | `B [1] C [2] D [3]` | `B [4] C [4] D [4]` | `B [2] C [3] D [4]` |
| 2 | `B [1] C [2] D [3]` | `B [5] C [4] D [5]` | `B [3] C [4] D [5]` |

Worse, the *source list* renumbered correctly, so the text cited `[4]` while
the list said that claim came from `[2]`. Every multi-company and trend answer
was affected. It is now a single pass that cannot re-touch its own output, with
87 tests.

### 3.4 Admin routes fail closed (P1-06)

The old guard was:

```python
if settings.admin_token and token != settings.admin_token:   # fail-OPEN
```

With no token set, the `and` short-circuits and **the check does not run**.
Phase 0 proved this was live: `/admin/disk-usage` with no token returned 400,
and 400 is only reachable *after* the token check passes.

Now: the `X-Admin-Token` header, a constant-time comparison, and **no token
means the routes are disabled (503)**. Four routes that only existed for the
old hosting platform were deleted outright, including a tar upload that
extracted into the data directory. Two destructive chat routes that had no
check at all are now guarded. CORS is an allow-list instead of `*`, and
`/query` has a rate limit and a length cap.

### 3.5 Years come from the data (P1-08)

`VALID_YEARS = {2023, 2024, 2025}` was hard-coded, and any other year was
silently dropped — so the query proceeded as if no year had been named.
Consequences Phase 0 measured: Microsoft's FY2026 report (filed July 2026) was
unreachable, and the live system had an `NVDA_2026` index no query could touch.

The fix separates two questions that had been conflated:

- *Is this a plausible fiscal year?* — a parsing question, now a sanity range.
- *Do we actually have it?* — a data question, answered downstream, which can
  fetch it or say "I don't have that year".

Dropping the year in the parser destroyed the information needed to say the
second thing.

### 3.6 A catalog that knows dates (P1-09)

365 annual filings across 13 companies, each with its **period end** (what it
reports) and **filing date** (when it became public). This is the foundation
for "as of March 2024, what was the latest revenue?" — a question that is
unanswerable without knowing when each document appeared.

It also handles two real-world messes:

- **BlackRock files under two different company IDs.** It reorganised in 2024,
  so FY2023 is filed by "BlackRock Finance, Inc." and FY2024-25 by
  "BlackRock, Inc.". Looking up only one loses half the history.
- **Amendments.** A company can refile a year's report. Asking for JPMorgan's
  FY2005 now returns the original or one of its two amendments depending on
  the date you ask about — which is exactly the behaviour restatement handling
  needs.

---

## 4. How a question flows today

```
"What were Apple's net sales in fiscal 2024?"
   |
   |-- classifier (llm/, router role) --> {single_doc, AAPL, 2024, focus=revenue}
   |      a failure here now RAISES instead of returning empty
   |
   |-- resolver --> is AAPL FY2024 indexed? if not, fetch and index it
   |
   |-- retriever --> AAPL_2024 collection: dense + keyword search,
   |                 fused, reranked, parent section attached
   |
   |-- generator (llm/, generator role) --> answer text with [N] markers
   |
   `-- QueryResult(status="answered_text", citations=[...])  --> HTTP 200
```

The numbers still come from a language model reading tables. **That is Phase
2's job to fix** — and it is the project's central weakness until then.

---

## 5. Five questions an interviewer might ask

**"Your demo was broken. What happened?"**
The code requested two models the API key could not access, so every request
404'd. The real problem was that one `except Exception` turned that 404 into
"which company did you mean?", so an outage looked like a user error. I
confirmed the cause by listing the models the key can actually see, then made
dependency failures return an explicit error with a code, and added a health
endpoint that reports the model's state. The lesson I took is that a fallback
which cannot be distinguished from success is worse than a crash.

**"Why write your own provider client instead of using an SDK?"**
Because the constraint is free tiers, and no single free tier is reliable
enough. I needed ordered failover across providers, per-model budgets, and a
cache — and I needed evaluation runs pinned to one model so a published number
means something. The providers all expose an OpenAI-compatible endpoint, so the
client is one HTTP call plus the policy, and adding a provider is a config
edit.

**"How do you know your tests actually test anything?"**
Twice during this work a check reported "all clear" when it was not: a shell
pipeline hid 28 real matches behind an exit-code fallback, and a route
inventory missed every route registered through a sub-router — which was
exactly the set of unguarded routes it existed to find. So checks now carry
negative controls: the hygiene script plants a fake key, an oversized file and
a stale URL and asserts each is caught; the route test asserts it can see
sub-router routes. A check that has never failed has not been tested.

**"Why is the catalog separate from the vector store?"**
Because a stored chunk has no date on it. To answer "as of March 2024" you
must exclude documents filed after that date, and the chunk cannot tell you
when its filing appeared. Putting dates in the catalog means point-in-time
filtering happens when choosing *which documents to search*, so it needed no
re-indexing and no change to the stored data.

**"What's still wrong?"**
Numbers still come from a model reading a messy table, and the prompt tells it
to assume millions — which is wrong for Netflix, who report in thousands, a
1000x error. Fiscal-year labels use the calendar year of the period end rather
than the company's own label. And the evaluation is still 8 questions with a
judge from the same model family as the generator. Phases 2 and 4.

---

## 6. Three known weaknesses in what Phase 1 shipped

1. **The baseline is the "before" picture, not a clean one.** It had to run on
   substituted models, because the models v1 named are unavailable. It measures
   "v1 as it can actually run today", which is the honest comparison but not
   the same as what the original author tested against.

2. **The rate limiter is per process and in memory.** Behind multiple workers
   each one keeps its own budget. Fine for a local-first deployment, wrong for
   anything shared — stated in the code rather than left to be discovered.

3. **Fiscal labels are still inferred.** `fiscal_label` is the calendar year
   the period ended, not the company's own label. It happens to agree for all
   13 companies in the catalog, but it is an assumption, and P2-03 replaces it
   by reading the label out of the filing itself.
