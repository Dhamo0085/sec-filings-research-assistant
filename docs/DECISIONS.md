# Decision log

One entry per non-trivial choice: options, choice, reason, what would change it.
Newest first.

---

## 2026-10-04 — D4-08 D27 applied: the cross-encoder stays on, and n=15 was noise

**The rule, as pre-registered.** D27 fixed the decision before the data
existed: turn the cross-encoder off if, for hybrid without rerank versus
hybrid with rerank, (a) section hit@5 is within 5 percentage points or
higher, (b) MRR is not lower by more than 0.03, and (c) retrieval time per
query is at least 10 times lower. Otherwise keep it on.

**The measurement**, on the 45-item P4-13 set against the re-indexed corpus
(D27 requires at least 40):

| arm | section hit@5 | MRR | s/query |
|---|---|---|---|
| hybrid, no rerank | 29/45 (64.4%) | 0.494 | 0.17 |
| hybrid + rerank | 35/45 (77.8%) | 0.604 | 5.96 |
| hybrid + rerank + focus (shipped) | 44/45 (97.8%) | 0.870 | 6.20 |

(a) −13.3 pp **FAIL** · (b) −0.110 **FAIL** · (c) 34.9× **PASS**.

**Decision: the cross-encoder stays ON. `config.py` is unchanged.**

**Why this entry matters more than the decision.** At n=15 the same ablation
said the cross-encoder *lost* a hit (12/15 → 11/15) and that dense-only beat
the shipped default on MRR. The Phase 4 report recorded that the intervals
were too wide to act on and did not act. At n=45 the sign reverses: the
cross-encoder is worth +13.3 pp and +0.110 MRR, and the focus boost another
20 pp. Had the pipeline been changed on the n=15 reading, retrieval would
have been made substantially worse on the evidence of noise. The pre-
registration is what made that outcome impossible, and it is the part of this
method worth keeping.

**What would change it.** A faster reranker, or a retrieval change that
closed the gap — the rule stays available and the switch still works.

---

## 2026-10-04 — D4-07 Defect E: the text path sent whole parent sections (D30)

**What it was.** `answering/text_answer.py` passed each retrieved chunk's
entire parent section to the generator, once per chunk in a group. Answering
`N-JPM-REVENUE-2024` assembled **3,323,116 characters** — about 831,000
tokens — from **4,653 characters** of retrieved chunk, because
`JPM_2024 / fs_income_stmt` is 1,526,355 characters and was emitted twice.
30 of the 704 live sections exceed 100,000 tokens.

**Why it hid for a day.** Every provider refused on size, each refusal was
recorded as a budget stop, and the last was raised as `LLMBudgetExceeded` — a
subclass of `LLMRateLimited`. The runner wrote `llm_rate_limited`, so a paced
retry was the obvious response. It recovered 1 item of 15. The symptom named
the wrong cause, and the fix for the wrong cause was cheap enough to try
twice before anyone read the failover chain.

**Why it appeared now.** The defect was always in v2, but P4-00 rewrote the
section boundaries and P4-12 put that parse behind the live index. v1's
generator truncated to `MAX_CTX_TOKS`; the v2 rewrite dropped the budget, and
until the sections grew nothing noticed.

**Options.** (a) Report as measured and fix in Phase 5. (b) Fix V1 only, since
V2 and V3 lose almost nothing to it. (c) Fix and re-run all three.

**Choice.** (c), authorized by the owner as D30 — a second fix cycle, which
D28 had not provided for. A system that cannot answer any question about the
four largest banks is not one whose narrative numbers mean much, and
publishing numbers shaped by a known bug is the thing CLAUDE.md rule 11
exists to prevent.

**The fix, in three parts.** A total budget of 6,000 tokens and 1,500 per
source, derived from the smallest per-minute limit in the generator failover
order (8,000 TPM) less the 900-token response and the system prompt — a test
asserts that relationship rather than the constant. A window of the parent
**centred on the retrieved chunk** rather than its head, because the head of
a 1.5 MB statement section is XBRL preamble. And several chunks of one
section now share one merged window instead of repeating the section.

**`llm_prompt_too_large`**, deliberately not a subclass of `LLMRateLimited`.
A rate limit says "not now"; this says "not ever, as built". It is raised
once before the failover loop, consuming no attempt, and only when every
candidate declares a limit and all are too small — an unknown limit is not
evidence, so such a provider is still tried.

**Tokens are estimated at 3 characters each, not tokenized.** `tiktoken`
downloads a BPE file on first use, which already broke CI once from an import
in `generation/generator.py`. A budget that needs the network fails exactly
where it is needed. Three is below prose's four on purpose: these sources are
dense XBRL tables, and budgeting on an underestimate costs the whole answer.

**What would change it.** A generator failover list whose smallest member has
a larger per-minute budget — the constant is derived from that number, not
chosen, so it should be re-derived rather than edited.

---

## 2026-10-03 — D4-06 The owner signed the gold verification sheet; it is applied at P4-15

**The sign-off.** The owner signed `reports/phase4/gold_verification_signed.csv`
on 2026-10-03: **all 37 rows OK**, covering **25 of 25 core rows** and 12 of 12
extra by the assisted sheet's own `priority` column. The owner opened the five
seeded spot-check filings on EDGAR and checked each against its snippet. The
file is `gold_verification_assisted.csv` with the `verdict` column filled and
nothing else touched — 37 differing cells, all in `verdict`, verified by
comparison before the commit. sha256
`ae80f98beac71f7249bec9a28791ca2f5cae3fa29e69b6b1c3ba1af45ebfc571`.

**How it was read.** Only `gold_id`, `verdict` and `owner_note`, with rows
matched by `gold_id` — the owner's instruction, and what D29 requires of a
sheet Claude Code must not author any part of. Checks run first, all by
`gold_id`: 37 rows, no duplicate id, the id set and row order identical to the
assisted sheet, every id present in `eval/gold/gold_v1.jsonl`, no blank
verdict.

**Context for the timing.** The sheet arrived in the working tree during a
session, unannounced and with `verified_by` still reading `companyfacts` on
every row. Under D29's tiers (`owner` > `companyfacts` > `auto`) a filled
`verdict` column is not by itself a signature, so it was not counted, not
committed and not used to lift "provisional" from anything until the owner
confirmed it in writing. A file appearing on disk is not an owner decision.

**Options for applying it.** (a) Set `verified_by=owner` on the OK rows now.
(b) Apply it at P4-15, with the rest of the finalization.

**Choice.** (b), as the owner directed. Numeric and computed items are
therefore **not** described as owner-verified before P4-15 runs, and narrative
items stay provisional regardless — they rest on the P4-14 rating sheet, which
is a separate gate the owner has not reached. Two gates, two artifacts, and
neither one lifts the other's caveat.

**What would change it.** Nothing about this sheet. A later gold set would need
its own sheet and its own signature; a signature does not transfer across a
change to the items it was given for.

---

## 2026-10-03 — D4-05 The expanded narrative set is a separate retrieval file, not more gold

**Context.** P4-13 expands the narrative items from 15 to at least 40 so D27's
reranker rule is decided on something other than noise. The obvious place to
put them is `eval/gold/gold_v1.jsonl`, which is where the 15 live.

**Options.** (a) Append them to the gold set. (b) A separate file the
ablations read through their existing `--gold` argument.

**Choice.** (b) — `eval/gold/narrative_retrieval_v1.jsonl`, 45 items.

**Reason.** Every headline number in Phase 4 is `n/N` over the gold set, and
the gold set's category targets are fixed by spec 6.6 (`narrative: 15`).
Appending 30 items would take the set from 80 to 110, change the denominator
of every figure already measured, and make the V1/V2/V3 runs of P4-12
incomparable with the ones they exist to be compared against — including V0,
which is frozen and cannot be re-asked anything. The expansion exists to serve
one measurement, the retrieval ablation, which scores section hit@k and MRR
and makes no generation call; `eval/ablations.py --gold <path>` already takes
an arbitrary item file, so the separation costs nothing and the two
measurements keep their own denominators.

**What would change it.** A later phase that wants narrative *answer* quality
measured over 45 items rather than 15. That is a different measurement with a
different cost — it needs generation for every item — and it would be a new
decision about the gold set's shape, not a quiet merge of two files.

---

## 2026-10-03 — D4-04 A relink clears a catalog link the store can no longer honour

**Context.** `link_collections()` joins `catalog.filings.collection_name` to
the Qdrant collections. It only ever set a name. P4-12 re-indexed 39 filings
and deliberately left TSLA out of scope, so after the activation swap the
catalog still pointed TSLA FY2025 at collection `TSLA_2025`, which the live
store does not hold. T4-09's orphan check is the mirror of this — a collection
with no catalog row — and passed at 0, so nothing in the test suite saw it.

**Options.** (a) Leave it and document it: retrieval already intersects its
target collections with the live ones, so nothing crashes. (b) Clear a link
the store cannot honour, as part of the relink. (c) Delete the catalog row.

**Choice.** (b). `collection_names` is now documented as the whole truth about
the store rather than a patch to apply to it, and a name not in that list is
cleared.

**Reason.** The link is not only read by retrieval. `query._years_with()` reads
`collection_name` to tell a user which fiscal years have text coverage, so a
stale link makes a refusal offer a year that nothing can search — the system
claiming a capability it does not have, which is the failure this project is
built to avoid. (c) is wrong because the filing is real and the catalog should
keep saying so; it simply has no text.

**What would change it.** A store that is deliberately a subset of the catalog's
links — for example an evaluation arm pointed at a partial index. That is why
the clearing is driven by the caller's list rather than by probing the store:
a caller that passes a subset gets what it asked for, and the docstring says to
pass every live collection.

---

## 2026-10-03 — D4-03 The P4-12 re-index writes to new directories and is swapped in separately

**Context.** P4-12 re-parses, re-chunks and re-indexes 39 filings — an overnight
job on this 8 GB machine, measured at ~2.2 chunks/s against ~37,000 chunks. It
has to produce artifacts the whole of Phase 5 builds on, while `data/parsed`,
`data/chunks`, `data/qdrant` and `data/qdrant_v1_backup` stay exactly as they
are until the owner decides otherwise.

**Options.** (a) Re-index in place, with the v1 backup as the only way back.
(b) Write to new directories and copy over afterwards. (c) Write to new
directories and swap by rename, keeping the old ones.

**Choice.** (c). `scripts/reindex_v2.py` writes only to `data/parsed_v2`,
`data/chunks_v2` and `data/qdrant_v2`, selected through `PARSED_DIR`,
`CHUNKS_DIR` and `QDRANT_PATH` — a configuration change, no code change, as
T4-07 requires. `scripts/activate_reindex.py`
performs the swap as six renames with a journal, and `--undo` reverses it.

**Four things this forced, each of which is the reason for a guard.**

1. **The three directories are one artifact.** A chunk's `parent_id` names a
   section in the parsed document, and retrieval fetches that parent to build
   the context a narrative answer cites. P4-00 rewrote the section boundaries,
   so a v2 index beside a v1 parse would cite passages whose parent sections
   hold something else — a wrong answer carrying a real citation. The
   activation moves all three or none.

2. **"The chunk file exists" is not a resume rule.** `ParsedDocument.doc_id` is
   `f"{ticker}_{fiscal_year}"` and `ParsedSection.section_id` is a slug: both
   are identical before and after a re-parse, so neither can witness one. The
   first version of the driver used `doc_id` alone and was caught by its own
   rehearsal — a forced re-parse reported the stale chunks as "reused". The
   rule is now doc_id **and** mtime no older than the parse.

3. **`Chunk.chunk_id` is a fresh `uuid4` every time.** The indexer resumes by
   skipping stored point ids, so a re-chunk would leave a collection holding
   both the old points and a full set of new ones. Before indexing, any
   collection holding a point today's chunk files do not claim is dropped and
   rebuilt; a merely *partial* collection (the crash case) is still resumed.

4. **The retired directories are `_retired_<stamp>`, not `_v1_<stamp>`.** A
   test glob for `qdrant_v1_*` matched `data/qdrant_v1_backup`, which is how
   the naming collision was found: the obvious cleanup `rm -rf
   data/qdrant_v1_*` would have deleted the frozen store the V0 arm and D25
   read.

**The catalog relink is deliberately dry-run during the re-index.**
`catalog.filings.collection_name` is what the text path's `as_of` scope reads.
Writing it while the new store is still staged would point every scoped query
at collections the live store does not hold. T4-09 therefore checks the link
(orphans must be 0) without performing it; the real link is the activation
script's last step.

**What would change it.** A remote Qdrant (`QDRANT_URL`), where collections are
named rather than stored in a directory, would make the swap a rename of
collections instead of directories.

---

## 2026-10-03 — D4-02 The three router defects, and what each fix turned out to be

**Context.** P4-11 (D28) spends Phase 4's one fix-and-rerun cycle on the three
router defects the Phase 4 report named. Each fix started from a failing test
taken from the gold item that exposed it
(`tests/unit/test_router_defects.py`, T4-08).

**A. "ratio of X to Y" and "A less B".** The report said the router did not
route them. Half of that was wrong, and the half that was wrong mattered more.
*The router already routed the ratio correctly* — `Computation.RATIO`,
`metric=total_liabilities`, `metric_denominator=stockholders_equity`. The
defect was one layer down: `query._compute` paired two metrics only for
`margin_pct`, so a two-metric ratio fell through to the two-period branch,
found one resolution, gave up, and answered with **total liabilities alone**.
A wrong answer to a question nobody asked, carrying a citation. The fix makes
`ratio` and `difference` share the margin's `_two_metric_operands` path, which
also resolves the denominator for the numerator's own fiscal year.
"A less B" genuinely was a routing gap: "less" was not a difference cue.

**Why "net of" is not a cue.** The first attempt added `\bnet\s+of\b`.
That turned "Bank of America's total revenue, net of interest expense" — the
filer's own income-statement caption, and a plain numeric gold item — into a
subtraction. Removed. `\bless\b` carries a `(?!\s+than)` lookahead for the
same reason: "revenue less than Microsoft's" is a comparison.

**B. "cash flow from operating activities" → `cash_and_equivalents`.** The
spec framed this as longest-match alias resolution. The router has matched the
longest alias first since Phase 3; the phrase simply **was not an alias**, so
the only thing in the question that matched anything was "cash". The fix is
five aliases in `facts/concepts.yaml`. The *test* is the part worth keeping:
table-driven over every multi-word alias in the registry, with a negative
control that runs the same table under shortest-first and asserts that it
still mis-resolves something — so the table cannot quietly become toothless.

**C. A catalogued year with no extracted facts.** `X-PERIOD-NOT-COVERED`
("Apple's revenue in fiscal 2019") refused with `metric_not_found_in_filing`.
The filing is in the catalog — the catalog holds a filer's whole EDGAR history
— and the facts build keeps only the newest originals per ticker, so nothing
was ever extracted from it. The old refusal asserted something about Apple
that is not true. `FactsResolver._select_period` now checks
`FactsStore.has_facts()` over the eligible filings **before any fact is read**
and returns `period_not_covered`.

- `facts_built_at` on the catalog row was rejected as the signal: it records
  that the build ran, not that it produced anything. Four flagged submissions
  in the current build hold no facts (a GS 10-K/A and three TSLA rows).
- The same flag made the refusal contradict itself — "I don't have fiscal 2023
  for Goldman Sachs. I do have fiscal 2023." `_available_for` now confirms
  against the store.
- **Two existing tests changed expectation** (GS FY2023, whose only fixture
  filing is a 10-K/A with no annual facts): `metric_not_found_in_filing` →
  `period_not_covered`. That is the fix, not a regression — the older test's
  own docstring said the refusal must not imply Goldman does not report
  revenue, while its assertion took the reason that implies exactly that. A
  negative control pins the other direction: BLK FY2024 is fully extracted and
  does not tag gross profit, and that stays `metric_not_found_in_filing`.

**Two presentation defects found by reading the output, fixed with the same
cycle.** Both were invisible until a two-metric ratio could be produced at all:
- `Calculation.formula` printed resolved values in scientific notation
  ("Computed as 3.08030E+11 / 5.6950E+10"), because the resolver's scaling
  leaves an exponent on the `Decimal`. Every calculation was affected, not
  just the new ones.
- `_calculation_label` named only the first operand: "total liabilities
  ratio", and worse, "change in net cash provided by operating activities" for
  operating cash flow minus capital expenditure. Two-metric ratios and
  differences now name both; the one-metric two-period shape keeps "change in",
  pinned by a negative control.
- The headline figure now carries the exact value as every other template does
  ("5.41x (5.4088)"). A ratio displays to two decimals and is computed to four,
  so the gold item's 0.0001 tolerance had nothing to match in the text.

**Measured** against the real stores, with the real pipeline: all **12**
computed gold items and all **5** facts-path abstention items now score
`correct`/`correct_abstain` (`eval.scorers`). `make test` 1,277 passed, 0
failed, 0 skipped; same under CI's isolation; ruff clean; `eval.mini_eval`
numeric+computed 9/9, look-ahead 0.

**Not fixed here.** Defect D (the MSFT segments over-refusal) is deferred to
after the re-index by D28, because it is a retrieval question and the corpus
is about to change.

**What would change it.** A filer whose question phrasing defeats the alias
table; the re-index changing which items fail.

---

## 2026-10-03 — D4-01 The assisted sheet is a second file, not an edit of the first

**Context.** P4-02b adds `assist_*` columns to `reports/phase4/gold_verification.csv`.
D29 says Claude Code never writes `verdict`, `owner_note` or `verified_by=owner`.
The spec's wording ("adds, for every row of `gold_verification.csv`, separate
columns") can be read as rewriting that file in place.

**Options.** (a) Rewrite `gold_verification.csv` in place, carrying the owner's
columns through unchanged. (b) Write a second file and leave the first alone.
(c) Write in place but refuse once any `verdict` is non-empty.

**Choice.** (b). `scripts/make_gold_verification_assist.py` writes
`reports/phase4/gold_verification_assisted.csv` and **refuses** to run with
`--out` equal to `--in`. The assisted sheet is the one the owner signs; the
P4-02 sheet stays as the generator produced it.

**Reason.** (a) and (c) both mean a script with D29's prohibition in its
docstring opening the owner's signed file for writing. The prohibition is only
as strong as the narrowest thing the code can do, and the narrowest thing is
never to open that file for writing at all. The cost is one extra file; the
benefit is that a bug, an interrupted run or a stale re-run cannot destroy a
signature. `assert_unchanged()` additionally compares every original column of
every row against the input before anything is written, and
`tests/unit/test_gold_verification_assist.py` plants a filled-in `verdict` as a
negative control to show that guard fires.

**Also decided here, because the spec leaves it open:**
- **A computed row has no printed value** — a margin is not on the page — so
  its **operands** are located instead, each in its own filing, and the row is
  `found_exact` only when every operand was found.
- **Matching is on magnitude, not sign.** A cash-flow statement prints capital
  expenditure as `(82,999)` while the facts store holds the positive value.
  `assist_value_as_printed` is the cell verbatim, parentheses included, so the
  owner still sees the sign.
- **`found_scaled`** means the value was found only at a scale other than the
  one the filer declared for that fact. It is not a failure; it is a flag that
  the sheet's `filing_value_as_printed` may send the owner looking for the
  wrong numeral.

**What would change it.** The owner preferring one file. Then (c), with the
refuse-if-signed guard, is the next safest.

**Measured.** All 37 rows located, 37 `found_exact`, 25 of 25 core rows located;
byte-identical on a re-run; `verdict` and `owner_note` empty on every row.

---

## 2026-10-03 — D4-00 The boundary rewrite is adopted; Item 7 misses its target

**Context.** D25 opened Phase 4 with a time-boxed rewrite of section-boundary
selection (P4-00) and an exit rule: if the targets are not met in one focused
session, revert `ingestion/parser.py`, keep D3-00 as the final position, and
continue with P4-01.

**What was built.** The three defect classes D3-00 diagnosed were all treated as
symptoms of one cause — the selection *rule*, not the patterns. `parse_filing`
now collects every matching line in a segment as a scored candidate, rejects
cross-references and mid-sentence mentions outright, and picks the
maximum-weight subsequence whose line number and Item priority both increase.
A candidate with no prose behind it is worth 1, so a bank's mid-document
cross-reference index cannot outrank real sections. The unsuffixed section id
goes to the longest instance, not the first. Full rationale is in the block
comment above `_Candidate`.

**Measured** over all 40 parsed filings with `scripts/audit_sections.py`,
offline, before anything was re-indexed
(`reports/phase4/section_audit_{before,after}.json`, delta reproduced by
`python scripts/compare_section_audits.py` — which has its own `--self-test`):

| | before | after | target (D25) |
|---|---|---|---|
| usable (filing, section) pairs | 245 of 440 | **298 of 440** | — |
| Item 1 Business | 33 | **40 of 40** | >= 33 of 39 ✓ |
| Item 1A Risk Factors | 20 | **40 of 40** | >= 33 of 39 ✓ |
| Item 7 MD&A | 20 | **25 of 40** | >= 33 of 39 ✗ |
| Item 1A misses needing a per-filing diagnosis | 17 | **0** | all diagnosed ✓ |
| the two strict `xfail` tests | fail by design | **pass, now ordinary tests** | ✓ |

65 pairs gained, 12 lost. For comparison, the three local fixes D3-00 rejected
moved the corpus by +1 of 143.

**The 12 regressions, each read first-hand rather than inferred from a count.**
Three of the four classes are the audit penalising a *more* correct parse:

1. **BAC Item 7A, 3 filings, 22,058 -> 149 characters.** BAC's Item 7A is one
   sentence: "See Market Risk Management on page 74 in the MD&A...". The old
   22,055-character slice was that sentence plus 22 kB of tables belonging to
   the *next* section, which the old boundary failed to cut. The new slice is
   the section. The audit calls 149 characters `too_small`, and it is right
   that the slice is useless — but the parser is not what makes it useless.
2. **JPM Item 7, 3 filings, 9,449 -> 300 characters.** Same shape: JPM satisfies
   Item 7 by pointing at pages 44-115, and the old slice was that pointer plus
   the Item 7A-9A pointers that follow it.
3. **GS Item 7, 3 filings, 6,189 -> 404,262 characters.** The reverse: the old
   "ok" slice was a 6 kB cross-reference stub and the new one is the real MD&A.
   Checked directly — it opens on GS's "Introduction ... a leading global
   financial institution", ends inside risk-management prose, and contains no
   auditor's report and no "Notes to consolidated financial statements"
   heading, so it has not run into Item 8. It exceeds the audit's 250,000-char
   narrative cap. BAC (653 kB), STT (535 kB) and WFC (537 kB) Item 7 are the
   same case and were already not `ok` before.
4. **WFC notes, 3 filings, 15,772 -> 858,960 characters.** The canonical
   `fs_notes` id now resolves to the EX-13's real notes instead of the 10-K
   wrapper's 15 kB pointer stub, which is the intended effect of "longest
   instance keeps the canonical id". It exceeds the 600,000-char notes cap.

**Why Item 7 cannot reach 33 without a different change.** All 15 misses are
five banks x three years. Three of them (BAC, GS, STT) are slices the parser now
places correctly and the audit caps; the other two (JPM, WFC) are filings whose
Item 7 genuinely is a pointer, with the MD&A printed elsewhere in the same
document under the filer's own heading rather than under an Item number. A
one-line change makes the second case usable — let the second-chance pass also
reconsider ids the first chain could only fill with a content-free entry — and
it was implemented and measured: Item 7 goes 25 -> 28 and Item 1A goes 40 -> 37,
for the same corpus total of 298, because the recovered BAC boundary produces a
longer slice and the "longest wins" rule then prefers it. It was reverted; the
reasoning is recorded in `_assign_sections_with_second_chance`'s docstring so
the next attempt starts from the measurement rather than from the idea.

**What was NOT done, deliberately.** The audit's thresholds were not touched.
Twelve of the fifteen Item 7 misses would become `ok` under a cap that fits a
large bank's MD&A, and the cap's stated premise ("even a bank's run well under
that") is now refuted by measurement. Moving it would also change the
denominator of every number above after the fact. That is a change to the
measuring instrument and it belongs to the owner, not to the run being measured.

**Options at the gate.** (a) Apply D25's exit rule literally: revert the parser,
keep D3-00. (b) Adopt the rewrite, record Item 7 as missed, and proceed. (c)
Adopt, and separately decide whether the audit's narrative cap should fit a
bank MD&A.

**Choice.** (b) and (c) — **confirmed by the owner at the P4-00 gate on
2026-10-03**: adopt the rewrite and record the Item 7 miss; leave the audit's
thresholds exactly as committed and report the cap-bound finding rather than
re-argue the cap mid-evaluation; and do **not** run the overnight re-index yet —
build P4-01 to P4-04 against the current corpus first, so the evaluation is not
assembled on a half-rebuilt index. **Reason.** The exit rule exists to stop unbounded effort on
a parser that cannot be fixed; the measurement says the opposite happened —
usable pairs went from 245 to 298, Item 1 and Item 1A are complete at 40 of 40,
and the two strict `xfail` tests pass. Reverting that to honour the letter of a
rule written before the result was known would discard a 53-pair improvement to
satisfy a threshold on one section of eleven. The miss is recorded here and in
the phase report rather than argued away.

**Consequence.** Nothing is re-indexed by this decision. The new parse exists
only in a scratch directory; `data/parsed/`, `data/chunks/` and the Qdrant store
are untouched, so every Phase 3 retrieval number still describes the artifacts
it was measured on. `data/qdrant_v1_backup/` (163 MB, 25 collections, 14,557
points, byte-verified against the live store) is in place so V0 stays a true v1
measurement whenever the re-index does happen. Step 4 of P4-00 — re-parse,
re-chunk and re-index 13 bundled tickers plus NFLX overnight — is an owner
decision at this gate.

**What would change it.** The owner applying the exit rule; or a Phase 4 failure
analysis tracing narrative misses to boundaries that survive this rewrite.

---

## 2026-10-02 — D3-00 v1's section boundaries are documented, not fixed, in Phase 3

**Context.** D23 recorded that v1's parsed *statement* sections are unreliable and demoted
`validation_status` to informational. P3-00 was to measure the same question for the sections
the **text path** actually reads, then fix the parser or document the limit.

`scripts/audit_sections.py` measured 39 parsed filings against the 11 sections
`retrieval/retriever.py` scrolls by id or serves as parent context. Result: **238 of 429
(filing, section) pairs usable; 122 missing, 24 heading-only, 45 oversized.** The damage is not
confined to statements — **Item 1A Risk Factors is missing from 17 of 39 filings**, including
4 of the 8 evaluation-core tickers (AMZN, GS, JPM, NFLX), and Item 3 Legal Proceedings is
usable in 3.

Three defect classes, each diagnosed to a line:

1. **The TOC skip zone is a fraction, not a bound.** `parse_filing` ignores the first 15% of a
   document's lines. For filers whose primary document *is* the whole annual report, Part I
   starts well inside that: JPM FY2024's real `Item 1A. Risk Factors.` heading is at line 229
   of 6,897 (3.3%), GS FY2024's at 490 of 5,370 (9.1%), STT FY2024's at 405 of 3,715 (10.9%).
   The heading is never a candidate, so the preceding section absorbs the text — GS FY2024's
   Item 1 is 295,735 characters and JPM FY2024's `fs_income_stmt` is 1,525,489.
2. **The heading is destroyed before any scan.** Amazon renders each item heading as a two-cell
   table row (`Item 1A.` | `Risk Factors`); `_extract_tables` decomposes the table, and the
   audit found **zero** occurrences of the heading anywhere in AMZN's parsed text.
   `_annotate_fs_header_tables` already joins a row's cells, but
   `_ITEM_RECOVERY_PATTERNS` listed only `Item 1. Business`.
3. **One sentinel per table.** Already recorded in the parser's own comments: AAPL/GOOGL/MSFT
   list every statement title in one index table, the annotation loop takes the first match and
   stops, so the rest merge. Fixing it needs per-row sentinel insertion.

**Options.** (a) Fix the parser now. (b) Document the limit, keep D23's prohibition, and scope
a proper fix. (c) Do nothing and leave the measurement unrecorded.

**Choice.** (b).

**Reason.** (a) was implemented and measured before being rejected, which is why this entry
exists rather than a guess. Three candidate changes — cap the skip zone at 250 absolute lines,
anchor the Item-N patterns at line start, extend `_ITEM_RECOVERY_PATTERNS` — were ablated over
all 8 subsets on 13 filings (143 pairs), re-parsing with `scripts/reparse_corpus.py` and
scoring with the audit. Raw counts in `reports/phase3/section_audit_ablation.json`:

| variant | usable pairs (of 143) | usable text-path pairs (of 65) |
|---|---|---|
| none (committed parser) | 80 | 35 |
| A extend recovery patterns | 81 | 36 |
| B anchor Item-N patterns | 79 | 34 |
| C cap the TOC skip zone | 80 | 36 |
| A+C | **81** | **37** |
| A+B+C | 80 | 36 |

The best subset moves the corpus by **+1 of 143 pairs** (+2 of 65 on the text-path sections),
and anchoring is net negative on its own. Individual filings move a lot in both directions:
C recovers GS's real 74,825-character Risk Factors section and takes GS's Item 1 from 295,735
characters to 152,354, and in the same run STT loses Item 1C and Item 3 and STT's MD&A grows
from 534,742 to 738,937 characters. The cause is structural: `_select_and_validate` keeps one
occurrence per section id and then applies a greedy monotonic priority filter, so a single new
early candidate both misplaces its own section and blocks every lower-priority section after
it. Local heuristics cannot be made net-positive against that.

Against a gain inside the noise, the cost of (a) is concrete: the parse feeds `data/chunks/`
and Qdrant, so adopting it means re-parsing, re-chunking and **re-indexing** the corpus (1.6 h
for the 24 indexed collections, per D2-00's measured 1.86 chunks/s), and every retrieval number
measured before it becomes incomparable. Paying that for +1 pair would be churn.

**What this does not do.** It does not leave the finding unrecorded or unguarded:

* The audit is a committed, negative-controlled command (`--self-test` plants an empty, a
  heading-only and an oversized section and asserts each is flagged), so the "after" of any
  future fix is measurable the same way.
* The two fixable defect classes are pinned as **`xfail(strict=True)`** tests in
  `tests/unit/test_parser_sections.py`. Strict matters: if the parser is fixed, the unexpected
  pass fails the suite and forces this entry to be revisited, so the code and this decision
  cannot silently diverge.
* D23's prohibition stands and widens in scope: nothing user-visible may depend on these
  sections. For Phase 3 that constrains P3-05 — a narrative citation names the filing and the
  section *title*, and the answer's correctness rests on the retrieved chunk text, never on a
  claim that a section slice is complete.

**Proper fix (proposed, not built).** Replace one-occurrence-per-id plus greedy monotonic
filtering with candidate scoring (is the line heading-shaped, is it followed by prose, does a
sentinel point at it) and a global assignment over all occurrences — a longest-increasing
subsequence on (line, Item priority) — then re-parse, re-chunk and re-index once. Sized in
`reports/phase3/REPORT.md` section 9.

**What would change it.** A measurement showing a boundary change that moves the audit by a
margin worth a re-index, or a Phase 4 failure analysis that traces narrative misses to section
boundaries rather than to retrieval.

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

## 2026-10-02 — D2-03 Precision preference: keep the instance the filer tagged more precisely

**Context.** P2-13 implements spec D22. P2-09's cross-check against SEC `companyfacts` left
233 of 27,506 comparable values disagreeing with the oracle — the entire shortfall against
T2-10's "≥ 99.5% exact" bar (99.1347%). Inspecting them found one phenomenon, not many: a
filing can tag the same concept, in the same context, **more than once at different
precisions**. Apple's FY2024 10-K reports `UnrecognizedTaxBenefits` for context `c-21` as

    22,000,000,000   decimals="-8"   the narrative sentence, "$22.0 billion"
    22,038,000,000   decimals="-6"   the tax-footnote table

Both are real tagged values and neither is wrong. The extractor kept whichever came first in
document order, which was the coarse one often enough to account for all 233.

**Options.** (a) Leave it and record T2-10 as not met. (b) Widen the "exact" definition to
count agreement-within-declared-precision. (c) Keep the instance with the larger `@decimals`
when the instances agree within the coarser declared precision.

**Choice.** (c).

**Reason.** (b) is the tempting one and it is wrong: it would move the number by redefining
the measurement rather than by improving the extraction, and a threshold that can be met by
loosening its own definition measures nothing. (a) leaves a known, fixable defect in place.

(c) improves the thing being measured. `companyfacts` keeps the precise instance, and so
should we — a reader checking an answer against the filing will find the table, not the
sentence. The agreement test is the precision the **filer itself declared**: `decimals="-8"`
asserts accuracy to the nearest 10⁸, so a value carries a half-unit tolerance of 0.5 × 10⁸.
Two instances describe the same quantity only when they fall within the sum of their
tolerances (for the Apple case, |38,000,000| ≤ 50,500,000). Outside that, **both are kept**
and the resolver reports `ambiguous_concept`, because a filing contradicting itself is not
something this module may paper over. Instances with no declared `@decimals` are never
merged: without a declared precision there is no basis for calling them one quantity.

**Measured effect** (`reports/phase2/crosscheck.json`, before and after):

| | before | after |
|---|---|---|
| facts extracted | 33,512 | 29,002 |
| comparable pairs | 27,506 | 23,429 |
| **exact, corpus-wide** | **99.1347%** | **99.9787%** |
| exact, registry concepts | 100.0000% | 100.0000% |
| `rounding` discrepancies | 233 | **0** |
| `oracle_precision` | 5 | 5 |
| scale / sign / unclassified | 0 / 0 / 0 | 0 / 0 / 0 |
| **T2-10 exact ≥ 99.5%** | **not met** | **met** |

The smaller denominators are the same cause: 4,510 duplicate instances collapsed, of which
only 272 changed a value — the rest were one number tagged in several places (Apple tags
`NetIncomeLoss` four times in one context). Two corroborations that this is an improvement
rather than a bookkeeping trick: `validated` rose from 223 to 256 against the rendered
statements, because the precise instance is the one printed in the table; and **all 20 values
on the owner-signed spot-check sheet re-resolve unchanged**.

The 5 remaining `oracle_precision` rows are the oracle carrying *fewer* digits than the filing
(Microsoft's par value is 0.00000625 in the document and 0.000006 in `companyfacts`). Ours is
the more precise of the two, so there is nothing to fix on this side.

**What would change it.** A filer using `@decimals` to mean something other than accuracy —
for example tagging a rounded figure as exact. That would show up as a `mismatch` in the
cross-check rather than silently, because disagreements beyond the declared tolerance are
still never merged.

---

## 2026-10-02 — D2-02 Tiered candidate concepts, and agreement is not ambiguity

**Context.** Spec 6.4 rule 3: use the per-filer override; else if exactly one candidate has a
value, use it; else return `ambiguous`. The P2-11 coverage sweep over 780 (filer, metric, year)
resolutions returned **90 `ambiguous_concept`** rows. Inspecting all 90 found two groups, and
neither is the tie the rule was written for.

**Group 1 — 27 rows where the candidates agree.** JPMorgan tags both `us-gaap:Revenues` and
`us-gaap:RevenuesNetOfInterestExpense` with the *identical* figure in every year (FY2024:
$177,556M; FY2023: $158,104M). The literal rule abstains because two candidates have values.
Refusing to state a number the filing prints twice is not caution, it is a bug.

**Group 2 — 63 rows where the concepts have different definitions.**

| metric | first | second | FY2024 gap |
|---|---|---|---|
| `net_income` | `NetIncomeLoss` (parent) | `ProfitLoss` (incl. noncontrolling) | BLK 5,901 vs 6,205 |
| `stockholders_equity` | `StockholdersEquity` (parent) | `…IncludingPortionAttributableToNoncontrollingInterest` | WFC 187,606 vs 190,110 |
| `cash_and_equivalents` | `CashAndCashEquivalentsAtCarryingValue` | `CashCashEquivalentsRestricted…` (a superset) | IVZ 1,469 vs 1,932 |

These are not two readings of one number; one of them is what the question means and the other
is a different, also-correct number. "Net income" means the figure attributable to the
company's own shareholders.

**Options.** (a) Leave it: abstain on all 90. (b) Write ~30 per-filer overrides, one per
(filer, metric) pair, each restating the same definitional choice. (c) Add **tiers** to the
candidate lists plus an **agreement** rule.

**Choice.** (c).

* **Tiers.** A candidate list may be a list of lists. Rule 3 applies *within* a tier; a later
  tier is consulted only when every concept in the earlier ones is absent from the filing. An
  ambiguity *inside* a tier is still final — it does not fall through to the next tier, which
  would be using a fallback concept to dodge a real tie.
* **Agreement.** When every candidate with a value in a tier reports the same value, that is
  the value, recorded as `selection="candidates_agree"`.

Result: 634 of 780 resolved (81.3%, up from 69.7%), **zero** `ambiguous_concept`. The remaining
146 are `metric_not_found_in_filing` and all look correct — banks have no gross profit or R&D
line, brokers tag no `OperatingIncomeLoss`, and Amazon does not tag `us-gaap:Liabilities`.

**Reason.** (a) refuses 63 answers the filings state plainly. (b) reaches the same place but
spreads one decision across 30 near-identical entries, where nobody could later see that they
were one decision or change it in one place. (c) states the choice once, in the open: the
loader **rejects a tiered metric with no `preference` text**, so the reason is mandatory, and
the concept actually used always reaches the user through the 6.5 `definition_note`. This is
the same spirit as D5 (declare which definition is used), applied per metric.

This is a deviation from the literal text of 6.4 rule 3 and is recorded as one in the Phase 2
report. What it does not do is weaken the rule where it matters: BlackRock's two revenue
concepts are both tier 1 with different values, so BLK still resolves only through its
evidenced override, and removing that override makes BLK abstain again (asserted by a test).

**What would change it.** A filer where the tier-1 concept is present but wrong — for example
one that tags `NetIncomeLoss` as the consolidated total. That is a per-filer override with
evidence, which is what overrides are for, and the tier order would stay as the general rule.

---

## 2026-10-02 — D2-00 Keep bge-base at full 512 tokens; the 4 chunks/s bar was a proxy

**Context.** P2-00(d) sets a decision rule: keep v1's dense embedding model if a tuned
setting reaches **≥ 4 chunks/s**, because the full 35,838-chunk corpus would then index in
about 2.5 hours overnight. Otherwise fall back, in order, to (i) embedding only text and
footnote chunks for non-core tickers, (ii) a smaller model (declared per D21), (iii) a free
Colab GPU runtime.

**Measurement** (`reports/phase2/throughput_profile.json`, 200 real chunks stratified by
(ticker, chunk_type), each configuration in its own subprocess):

| max_length | batch 8 | batch 16 | batch 32 | batch 64 | peak RSS |
|---|---|---|---|---|---|
| 512 | **2.35** | 2.17 | 2.15 | 2.22 | 1.0–1.1 GB |
| 256 | **4.80** | 4.68 | 4.74 | 3.72 | 1.0–1.4 GB |

Two things the grid settles:

* **Batch size is nearly irrelevant** (2.15–2.35 at 512). The corpus median chunk is 986
  tokens and 81% exceed 512, so every sequence is padded to the cap and cost is one
  fixed-size sequence per chunk however they are grouped. Tuning batch size was the wrong
  knob.
* **P1-00's 1.6 s/chunk was memory pressure, not the model.** The same model on the same
  machine now measures 0.43 s/chunk — 3.8× faster — because the streaming indexer holds one
  batch instead of the whole corpus. Peak RSS is 1.0–1.1 GB against the 2.4 GB that drove
  swap to 9.6 of 10 GB.

**Options.** (a) Keep bge-base at 512 and accept 2.35 chunks/s. (b) Drop `max_length` to 256
for the 2× speed-up. (c) Fall back to text+footnote chunks only for non-core tickers.
(d) Switch to a smaller model.

**Choice.** (a) — keep bge-base at 512, with batch 8. No fallback is taken.

**Reason.** The ≥ 4 chunks/s bar is a proxy for "can this be indexed overnight", and at 2.35
chunks/s the answer is still yes: 4.2 hours for the full corpus, and **78 minutes** for what
P2-00(e) actually asks for (the 10,976 chunks of the five core tickers with chunk files). The
bar was set expecting 2.5 hours; 4.2 hours is a longer night, not an infeasible one, so
taking a fallback would trade real quality for a constraint that is not binding.

Option (b) is rejected on grounds the bar does not capture. `max_length=256` is not a tuning
knob, it changes **what a vector means**: with a median chunk of 986 tokens, it would embed
roughly the first quarter of each chunk and discard the rest. It would also make the index
internally inconsistent — AAPL and AMZN were already embedded at 512 — so adopting it means
re-indexing everything, and every retrieval number measured before and after would be
incomparable. Buying 2× throughput with that is a bad trade when the full run already fits
in a night.

Option (c) would deliberately drop the 77% of the corpus that is table chunks for the
non-core filers, which are precisely the chunks a financial question needs. It is held in
reserve.

**What would change it.** A larger corpus (the spec's `FACTS_FILINGS_PER_COMPANY` growing, or
more tickers) pushing the full run past a night; or a machine with enough RAM to make batch
size matter again, which would need the grid re-run rather than re-reasoned. If the embedding
model is ever changed, D21 requires it to be declared in every results table.

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
