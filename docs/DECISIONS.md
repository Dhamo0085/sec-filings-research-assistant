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
