# Decision log

One entry per non-trivial choice: options, choice, reason, what would change it.
Newest first.

---

## 2026-10-01 — D0.5 Phase 0 ran without git

**Context.** The working copy at `/Users/dhamo_85/Downloads/Financial_RAG-main` has no `.git`
directory; it is an unpacked source archive.

**Options.** (a) `git init` and commit a baseline so Phase 0 can branch as instructed;
(b) run Phase 0 read-only without version control and report the gap.

**Choice.** (b).

**Reason.** `git init` cannot recover history, so Steps 0.2 (authorship by directory) and the
tracked-file half of 1.7 remain unanswerable either way. Initialising a repo is also a
structural change to the owner's working copy, which Phase 0 is not authorised to make.
Phase 0 added only new files under the directories its instructions permit.

**What would change it.** The owner pointing Phase 0 at the real git clone, which would make
0.2 and 1.7 answerable and let Phase 1 branch normally.

---

## 2026-10-01 — D0.4 iXBRL feasibility tested on re-fetched filings, not `data/raw`

**Context.** Step 2 is specified as read-only analysis of `data/raw`. `data/raw` does not exist
in this checkout, and the instruction in that case is to skip to Step 2.6 — which also needs
filings. D1 would then have had no evidence at all.

**Options.** (a) Skip Step 2 and report D1 as unevaluable; (b) fetch the same primary documents
the pipeline downloads into `.cache/` and run the feasibility study there.

**Choice.** (b), for 11 filings across Technology, Banking, Asset Management and Media.

**Reason.** D1 is the central architectural question of v2. `.cache/` is an allowed Phase 0
output directory, `data/raw` was never written, and the documents fetched are byte-identical to
what `ingestion/downloader.py` retrieves from the same accessions.

**What would change it.** Re-running `eval/phase0/ixbrl_extract.py` against a populated
`data/raw` once the owner re-ingests; the script takes a path list and needs no change.

---

## 2026-10-01 — D0.3 Concept resolution cannot be one global priority list

**Context.** Step 2.3 specifies revenue alternates in a fixed priority order. BlackRock FY2024
tags **both** `Revenues` = $12,794 M and `RevenueFromContractWithCustomerExcludingAssessedTax`
= $20,407 M. The fixed order picks the first, which is a component, not total revenue — a 37%
understatement. SEC `companyfacts` confirms both tags, so this is a selection error, not an
extraction error.

**Options.** (a) Keep a single global priority list; (b) resolve per sector; (c) resolve per
filer, validated against the income-statement total.

**Choice.** Recommend (c) for Phase 2, with (b) as the fallback for unseen filers.

**Reason.** 4 of 11 filings (JPM, BAC, BLK, GS — all financials) tag 2–4 competing consolidated
revenue concepts whose values differ by up to 44% (BAC: $146,607 M vs the correct $101,887 M).
Non-financials tagged exactly one candidate each. A global ordering that is right for banks is
wrong for asset managers.

**What would change it.** Evidence that a single ordering satisfies every filer in the bundled
set plus on-demand ingests — `eval/phase0/revenue_ambiguity.json` already refutes this for four.

---

## 2026-10-01 — D0.2 `companyfacts` used as oracle only, never as the fact source

**Context.** SEC's `companyfacts` API returns clean, pre-extracted numeric facts and would be
far less work than parsing iXBRL.

**Options.** (a) Use `companyfacts` at runtime; (b) use it only to validate local extraction.

**Choice.** (b), consistent with D1 as written in `CLAUDE.md`.

**Reason.** `companyfacts` is a live, mutable, network-dependent endpoint carrying restatements;
it is not versioned with the filing and cannot be made point-in-time correct, which breaks the
`as_of` guarantee that is the point of v2. Local extraction is reproducible offline and pins to
an accession. Phase 0 measured the cost of (b) as low: 105/105 extracted values matched the
oracle exactly.

**What would change it.** A requirement for concepts that only exist in the SEC-normalised
taxonomy, or local extraction accuracy falling materially below the oracle.

---

## 2026-10-01 — D0.1 Audit tests stub heavy imports rather than installing the full stack

**Context.** Phase 0 forbids editing source. `import query` pulls in qdrant-client, fastembed,
nltk and tiktoken, none of which are installed, and `ingestion/chunker.py` calls
`nltk.download()` at import time.

**Options.** (a) Install the full runtime (~1 GB with ONNX); (b) install a light subset and
stub the heavy modules in `sys.modules` before import.

**Choice.** (b), in `tests/phase0/conftest.py`.

**Reason.** Every K1/K11 test monkeypatches the functions that would use those packages, so the
real ones are never exercised. Stubbing also keeps the audit offline and stops the import-time
`nltk.download()` from touching the network. `qdrant-client` was installed for real afterwards,
because K9 genuinely needed local-mode behaviour.

**What would change it.** Any Phase 1 test that needs real embeddings or a real Qdrant store.

---

## 2026-10-02 — D1-04b Role model orders, chosen by measurement

**Context.** Spec Appendix A proposed roles from *declared* free-tier limits:
Gemini Flash-Lite as primary router and generator, Gemma 4 for router/decomposer/judge
on the strength of its 14.4K requests/day. P1-04 required a bake-off before committing.

**What was measured.** 24 calls, 6 candidates x 4 fixed prompts
(`eval/phase1/bakeoff.py`, raw rows in `reports/phase1/bakeoff.json`): JSON validity,
citation-format compliance, honest refusal, latency.

| entry | JSON | cites correctly | usable by pipeline | marker style | refusal | median |
|---|---|---|---|---|---|---|
| gemini:gemini-3.5-flash-lite | 2/2 | 1/1 | 1/1 | ascii | 1/1 | 0.74 s |
| gemini:gemini-3.1-flash-lite | 2/2 | 1/1 | 1/1 | ascii | 1/1 | 1.69 s |
| groq:qwen/qwen3.8-27b | 2/2 | 1/1 | 1/1 | ascii | 1/1 | 0.66 s |
| groq:openai/gpt-oss-120b | 2/2 | 1/1 | **0/1** | **fullwidth** | 1/1 | 0.45 s |
| groq:openai/gpt-oss-20b | 2/2 | 1/1 | **0/1** | **fullwidth** | 1/1 | 0.51 s |
| gemini:gemma-4-26b-a4b-it | **0/2** | 1/1 | 1/1 | ascii | **0/1** | **81 s** |

**Two findings that changed the plan.**

1. **The gpt-oss models cite with fullwidth brackets.** They answered
   `...net sales of $391,035 million【1】【2】` — correct sources, correct values, but
   U+3010/U+3011 instead of `[1]`. `generation/citations.py` parses ASCII `[N]`, so the
   pipeline would see **zero** citations in a perfectly cited answer, and the citation
   remap would silently do nothing. This is invisible unless you look for it: the answer
   reads correctly to a human.
2. **Gemma 4 is unusable for the roles Appendix A suggested.** 0/2 valid JSON and ~81 s
   median latency against under 1 s for every other candidate, despite the best RPD on
   offer. Declared limits said "use it"; measurement said no.

**Options.** (a) Follow Appendix A's suggested roles. (b) Normalise fullwidth brackets in
the pipeline so gpt-oss can generate. (c) Choose roles by measured compliance and encode
the requirement in the capability registry.

**Choice.** (c), with the citation format expressed as an `ascii_citations` capability:

- **router** `groq:openai/gpt-oss-20b → gemini:gemini-3.5-flash-lite → gemini:gemini-3.1-flash-lite`
  — the router only emits JSON, where the bracket style is irrelevant, so Groq leads and
  Gemini's scarcer 500 requests/day is preserved for the generator.
- **generator** `gemini:gemini-3.5-flash-lite → gemini:gemini-3.1-flash-lite → groq:qwen/qwen3.8-27b`
  — requires `ascii_citations`, which excludes both gpt-oss models. Gemini's 250K TPM
  leads because a generator prompt carries retrieved chunks; qwen is last because its
  **measured** 8000 TPM may not fit one.
- **judge** `groq:openai/gpt-oss-120b → groq:qwen/qwen3.8-27b` — a judge emits JSON, not
  citations, so gpt-oss is fine, and leading with Groq satisfies rule 10 (never the model
  that generated).

**Reason for (c) over (b).** Normalising brackets would make the pipeline tolerate a
format drift we do not control, and would hide the same class of problem the next time a
model invents a new marker style. Requiring the format and recording *why* each model
qualifies keeps the failure visible. (b) remains available if gpt-oss is ever needed as a
generator.

**What would change it.** A model's behaviour drifting (these are preview models), a new
free-tier entry, or evidence that the fullwidth style is stable enough to normalise
safely. Re-run `eval/phase1/bakeoff.py`; it is cached, so a re-run with unchanged models
costs nothing.

---

## 2026-10-02 — D1-04c Groq free-plan limits: one measured, the rest unknown

**Context.** O1 left Groq's free-plan limits unresolved: they are not on the models page,
and the page's published numbers are developer-plan figures.

**What was measured.** During the bake-off the client read rate-limit response headers.
Exactly one model returned parseable limits: `groq:qwen/qwen3.8-27b` →
**1000 requests/day, 8000 tokens/minute**. The `openai/gpt-oss-*` models and both Gemini
Flash-Lite models did not return headers in the form the client parses.

**Choice.** Record the measured pair in `llm/limits.local.yaml` (gitignored, owner-maintained)
and leave the rest `null`. `null` means "not yet measured" and does **not** block a call —
treating unknown as zero would have blocked every gpt-oss request.

**Consequence worth noting.** qwen's 8000 TPM is small for a generator prompt carrying
retrieved chunks, which is why it is last in the generator order rather than first despite
scoring 4/4.

**What would change it.** Groq publishing free-plan limits, or a 429 whose body carries
the real numbers. O1 stays PARTIALLY RESOLVED.

---

## 2026-10-02 — D1-02 Deferred ruff rule families

**Context.** The Phase 1 exit criteria require `make lint` clean. v1 was never linted;
ruff's default rule set reported 431 findings, 273 of them typing-annotation
modernisation (`List[str]` → `list[str]`, `Optional[X]` → `X | None`).

**Options.** (a) Accept ruff's shifting defaults. (b) Pin a rule set and fix everything it
reports. (c) Pin a rule set, fix the findings that matter, and defer the rest with reasons.

**Choice.** (c). `pyproject.toml` pins the rule set explicitly so it cannot drift with a
ruff release. Deferred, each with a reason in the config: `UP` (pyupgrade) would rewrite
300+ annotations across v1 code with no behavioural effect and would bury the Phase 1
diff; `BLE001` (blind except) has 48 occurrences, and the ones that matter are the F1
defect fixed deliberately in P1-05 — a blanket sweep would mix a real fix into unrelated
edits. Typographic punctuation, module-level `global` singletons and deliberate
best-effort `try/except/pass` sites are exempted as intentional.

Two findings were **fixed** rather than deferred because they were correctness issues, not
style: five `raise` sites inside `except` blocks now chain with `from exc`, and two `zip()`
calls over parallel batches use `strict=True` — ruff's own autofix would have written
`strict=False`, preserving a silent truncation that in `vector_store` would mean chunks
were never indexed.

One finding was investigated and dismissed: `S608` (SQL injection) in `api/chat.py` is a
false positive — the only interpolated fragment comes from a fixed three-entry dict of
literal WHERE clauses. The exemption is scoped to that file so a genuinely dynamic query
elsewhere would still be reported.

**What would change it.** A dedicated commit for the `UP` family, or Phase 5's security
review wanting `BLE001` enforced repo-wide.
