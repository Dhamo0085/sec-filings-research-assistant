# Phase 2 report — Facts engine

Branch `phase-2-facts-engine` · spec v1.5 · 2026-10-02
Protocol: `docs/PROJECT_SPEC.md` section 13.
**Updated after P2-13 (closure).** The owner's spot-check came back 20/20 OK and
the D22 precision fix is implemented, so §1, §4.1 and §8 now carry the
post-closure numbers; §4.1a records the before/after.

---

## 1. Summary

- **The facts engine works and is independently verified.** 29,002 facts from
  94 submissions across 18 companies. Against SEC `companyfacts` — the same
  filings rendered by the SEC's own pipeline — **23,429 comparable values,
  99.9787% bit-exact, 100.0000% exact on the 1,047 values for the 23 concepts
  the system actually uses to answer questions, 0 scale errors, 0 sign errors,
  0 unclassified mismatches.**
- **T2-10's thresholds are now all met.** They were not at the first gate
  (99.1347%); P2-13 implemented the precision-preference fix (D22 / D2-03) and
  the corpus-wide figure went to **99.9787%**, with the `rounding` discrepancy
  class eliminated entirely (233 → 0). Before and after in §4.1a.
- **The owner's spot-check came back 20 of 20 OK, zero WRONG**, so P2-13 had no
  rows to fix. All 20 values re-resolve unchanged after the D22 rebuild.
- **Ambiguity is enforced, not avoided.** BlackRock resolves to $20,407M (its
  income statement's "Total revenue") only through an override that cites the
  accession and the line; remove the override and it abstains, which a test
  asserts. Coverage is 634/780 = 81.3% with **zero** `ambiguous_concept`.
- **P1-00's indexing failure is explained and fixed.** The same model on the
  same machine went from 1.6 s/chunk to 0.43 s/chunk once the indexer stopped
  holding the whole corpus. Peak RSS 1.34 GB against 2.4 GB. It was memory
  pressure, not the model (D2-00).
- **DEI labels verified: 65 of 65 match** `period_end.year`, including the cases
  most likely to differ (MSFT FY2026 ending in June, Apple's September
  year-ends). D6 is now measured rather than assumed.
- **29 of 65 filings are multi-document submissions**, not just Wells Fargo. A
  build reading only `primary_doc` would under-extract nearly half the corpus.
- **The fail-loud transform policy caught five filings** that would otherwise
  have produced wrong numbers, and a 65-filing survey found two vocabulary gaps
  a 12-filing survey had missed.
- **Six defects were in my own code and checks**, each caught by a check rather
  than by review (section 6). Two would have shipped false confidence.
- **P2-00(e) is complete.** All eight evaluation-core tickers are indexed —
  AAPL, AMZN, MSFT, JPM, GOOGL, NFLX, BLK, GS — in 24 collections holding
  13,520 points. 10,848 chunks embedded in 1.6 h at 1.86 chunks/s, peak RSS
  never above 1.37 GB (§4.3a). D21's precondition for Phase 3 is met.
- Suite: **547 passed, 0 skipped**, `facts/` 88.7%, `catalog/` 95.4%, ruff
  clean, hygiene clean. The former skip is gone: `.hygiene_local` is now filled,
  so the T2-12 tree test runs for real.
- **Three defects in my own code were found during closure**, one of them a
  test that was lying (§6.3).

## 2. What changed

9 commits on `phase-2-facts-engine`, 62 files, +59,435 / −114 (the bulk is
`reports/phase2/crosscheck.csv`).

| Commit | Subject |
|---|---|
| `b292dc3` | docs: spec v1.4, STATE.md, Phase 2 branch opened |
| `f6b2306` | chore: remove `test_setup.py`; hygiene reads owner patterns (P2-00a/b) |
| `3b2a081` | feat: streaming indexer, bounded and resumable (P2-00c) |
| `02a0d4a` | feat: iXBRL extraction and the trimmed real fixtures (P2-01/02/03) |
| `e56a1c7` | feat: filing documents, facts store, metric registry (P2-03/04/05) |
| `dfe6aba` | feat: facts build, resolver, coverage report (P2-04/06/07/11) |
| `fb4b4f1` | feat: calculator, formatter, oracle cross-check (P2-08/09) |
| `6449bc4` | feat: cross-check filers, spot-check sheet, resolver tests (P2-09/10) |
| `b07da12` | test: cover the build driver and the document fetcher |

**New modules**

| Module | Purpose |
|---|---|
| `facts/extract.py` | iXBRL → typed facts: contexts, units, scale/sign/format, nil, hidden facts, pooled submissions |
| `facts/store.py` | SQLite per spec 6.2; replace-per-accession in one transaction; content hash for T2-08 |
| `facts/build.py` | `make facts`; idempotent on a hash of the document bytes; one failure never stops the run |
| `facts/documents.py` | Reads a filing's own `FilingSummary.xml` to learn which documents carry facts |
| `facts/concepts.yaml` / `concepts.py` | Metric registry: aliases, sector candidate lists, tiered preferences, evidenced overrides |
| `facts/resolve.py` | Spec 6.4 policy + the 6.5 abstain enum; `as_of` before fact selection; D14 |
| `facts/calc.py` / `format.py` | Decimal arithmetic with unit/period/zero/negative guards; magnitude-safe formatting |
| `ingestion/indexer.py` | Streaming indexer: per collection, bounded batches, resumable by point id |
| `scripts/` | `profile_indexing.py`, `make_ixbrl_fixtures.py`, `verify_dei_labels.py`, `facts_coverage.py`, `crosscheck_companyfacts.py`, `make_spotcheck_sheet.py` |

**Modified**: `scripts/check_repo_hygiene.py` (4th check), `catalog/store.py`
(`set_fiscal_label`, `set_exhibit_docs`), `catalog/cik_overrides.yaml` (5
cross-check filers), `config.py` (`index_batch_size`), `Makefile` (`PHASE`,
`index`, real `facts`), `pyproject.toml`, `ingestion/embedder.py`,
`ingestion/auto_ingest.py`, `run_ingestion.py`, `api/app.py`,
`retrieval/vector_store.py`. **Removed**: `test_setup.py`.

## 3. Tests

Command (exactly as run):

```
.venv/bin/pytest tests/unit tests/integration -m "not live and not slow" \
  --disable-socket --allow-unix-socket \
  --cov=facts --cov=catalog --cov=ingestion/indexer.py \
  --cov-report=term --cov-report=xml:reports/phase2/tests/coverage.xml \
  --junitxml=reports/phase2/tests/junit.xml
```

**547 passed, 0 skipped, 17.7 s.** Network blocked, no `.env` present.

| ID | Test | Where | Result |
|---|---|---|---|
| T2-01 | scale/sign/format transforms; unknown format fails loudly | `test_facts_extract.py` | PASS |
| T2-02 | dimensional excluded; 52/53-week window; short periods; instant at period end | `test_facts_extract.py` | PASS |
| T2-03 | WFC wrapper + EX-13 pooled resolves; neither alone | `test_facts_extract.py` | PASS |
| T2-04 | BLK override honoured; without it → `ambiguous`; BAC bank candidates | `test_facts_resolve.py` | PASS |
| T2-05 | `as_of` eligibility; 10-K/A selection; `restated` flag | `test_facts_resolve.py` | PASS |
| T2-06 | calculator units + 5 Hypothesis property tests | `test_facts_calc.py` | PASS |
| T2-07 | thousands/millions/billions boundaries; negatives; percent rounding | `test_facts_calc.py` | PASS |
| T2-08 | identical rebuild (both ways) + negative control | `test_facts_build.py` | PASS |
| T2-09 | 25 golden (ticker, metric, year) tuples, all oracle-verified | `test_facts_resolve.py` | PASS |
| T2-10 | cross-check thresholds | `scripts/crosscheck_companyfacts.py` | **PASS** after P2-13 (§4.1a) |
| T2-11 | streaming indexer: one collection at a time, bounded batches, resume | `test_stream_indexer.py` + `test_stream_indexer_qdrant.py` | PASS |
| T2-12 | `.hygiene_local` pattern detected; file gitignored; tree clean | `test_repo_hygiene.py` | PASS |
| T2-13 | precision preference: agreeing instances collapse to the larger `decimals`; disagreements are not merged; negative control; deterministic rebuild | `test_facts_precision.py` | PASS |

**Coverage** (spec section 10 requires ≥ 85% on `facts/` and `catalog/`):

| Package | Statements | Missed | Coverage |
|---|---|---|---|
| `facts/` | 1,453 | 164 | **88.7%** |
| `catalog/` | 306 | 14 | **95.4%** |
| `ingestion/indexer.py` | — | — | included in the run |
| Measured total | 1,759 | 178 | 90% |

**The skip is gone.** At the first gate
`test_tracked_tree_has_no_old_project_names` skipped because `.hygiene_local`
was present but empty, so the old project's identifiers were not scanned for.
The owner has now filled it, the test runs, and the tracked tree is clean
against it. That "clean" is load-bearing, so it has its own negative control:
the supplied pattern was read from the file (never printed), planted in a
throwaway file, and confirmed caught, with a clean file confirmed not reported.
`--self-test` separately plants 11 violations and catches all 11.

## 4. Metrics

### 4.1 Oracle cross-check (P2-09, T2-10)

`reports/phase2/crosscheck.csv`, `crosscheck.json`. Compared **by accession**
and period, so a restatement cannot look like an extraction error.

| | |
|---|---|
| Companies / submissions | 18 / 94 |
| Facts extracted | 29,002 |
| Comparable `(accn, concept, start, end, unit)` pairs | 23,429 |
| **Bit-exact** | **23,424 = 99.9787%** |
| Agreeing within the filer's declared precision | 23,429 = **100%** |
| `rounding` (same concept tagged twice at two precisions) | **0** |
| `oracle_precision` (the oracle carries fewer digits than the filing) | 5 = 0.021% |
| Unclassified mismatches | **0** |
| **Scale errors** | **0** |
| **Sign errors** | **0** |
| `not_in_oracle` (the oracle has no row to compare) | 5,573 |
| **Exact on the 23 registry concepts** | **1,047 / 1,047 = 100.0000%** |

**T2-10 verdict: all thresholds met.** exact ≥ 99.5% PASS at 99.9787% · scale
errors 0 PASS · sign errors 0 PASS · every discrepancy classified PASS.

The 5 remaining `oracle_precision` rows are the oracle carrying *fewer* digits
than the filing — Microsoft's par value is 0.00000625 in the document and
0.000006 in `companyfacts`. Ours is the more precise of the two, so there is
nothing to fix on this side.

### 4.1a Before and after the precision fix (P2-13, D22 / D2-03)

At the first gate this threshold was **not met**, and that is worth keeping in
the record rather than overwriting. The shortfall was one phenomenon: a filing
can tag the same concept, in the same context, more than once at different
precisions. Apple's FY2024 10-K reports `UnrecognizedTaxBenefits` for context
`c-21` as 22,000,000,000 with `decimals="-8"` (the narrative sentence) and
22,038,000,000 with `decimals="-6"` (the tax-footnote table). The extractor kept
whichever came first in document order.

| | before (first gate) | after (P2-13) |
|---|---|---|
| Facts extracted | 33,512 | 29,002 |
| Comparable pairs | 27,506 | 23,429 |
| **Exact, corpus-wide** | **99.1347%** | **99.9787%** |
| Exact, registry concepts | 100.0000% | 100.0000% |
| `rounding` discrepancies | 233 | **0** |
| `oracle_precision` | 5 | 5 |
| Scale / sign / unclassified | 0 / 0 / 0 | 0 / 0 / 0 |
| **T2-10 exact ≥ 99.5%** | **not met** | **met** |

**The option not taken.** The quickest way to "meet" this threshold would have
been to count agreement-within-declared-precision as exact. That moves the
number by redefining the measurement, and a threshold that can be met by
loosening its own definition measures nothing. The fix changes the extraction
instead: keep the instance the filer tagged more precisely, and only when the
two agree within the coarser declared tolerance. Values that disagree beyond it
are **never** merged — both are kept and the resolver reports
`ambiguous_concept`, because a filing contradicting itself is not something this
module may paper over.

The smaller denominators have the same cause: 4,510 duplicate instances
collapsed, of which only **272 changed a value**. The rest were one number
tagged in several places (Apple tags `NetIncomeLoss` four times in one context).

Three independent corroborations that this improved the data rather than the
bookkeeping:

1. `validated` rose from 223 to 256 against the rendered statements — the
   precise instance is the one printed in the table.
2. **All 20 values on the owner-signed spot-check sheet re-resolve unchanged.**
3. Coverage is identical: 634/780 = 81.3%, still zero `ambiguous_concept`.

Reasoning and the agreement rule: `docs/DECISIONS.md` **D2-03**.

### 4.2 Coverage and ambiguity (P2-11)

`reports/phase2/facts_coverage.{json,csv}` — 65 (ticker, fiscal label) pairs ×
12 metrics = 780 resolutions.

| Status | N | Share |
|---|---|---|
| resolved | 634 | **81.3%** |
| `metric_not_found_in_filing` | 146 | 18.7% |
| `ambiguous_concept` | **0** | 0% |

Validation status of the 634: `validated` 223 · `unvalidated` 360 ·
`conflict` 51.

The 146 absences, by metric, all consistent with the filings:
`gross_profit` 55 (banks and brokers have no such line), `rd_expense` 46,
`operating_income` 25 (BAC/GS/JPM/STT/WFC tag no `OperatingIncomeLoss`),
`capex` 15 (BAC/JPM/WFC), `total_liabilities` 5 (Amazon tags no
`us-gaap:Liabilities`).

**Before D2-02 this was 69.7% resolved with 90 ambiguous rows.** All 90 were
inspected: 27 were candidates reporting the *identical* value and 63 were pairs
of concepts with different definitions. Neither is the tie the rule was written
for. Details in `docs/DECISIONS.md` D2-02.

**Corroboration from the untuned filers.** The table above is scoped to the
bundled 13, which is the project's corpus and the figure quoted throughout this
report. Including the five cross-check-only filers — which have no sector entry,
no override and no fixture — gives **902 of 1,080 = 83.5% resolved, still with
zero `ambiguous_concept`** (`facts_coverage_all18.json`). That the untuned
filers resolve slightly *better* than the tuned ones, and introduce no new
ambiguity, is the strongest available evidence that the registry's candidate
lists generalise rather than having been fitted to the thirteen companies they
were written against.

### 4.3 Indexing throughput (P2-00d)

`reports/phase2/throughput_profile.json` — 200 real chunks stratified by
(ticker, chunk_type), each configuration in its own subprocess.

| max_length | batch 8 | batch 16 | batch 32 | batch 64 | peak RSS |
|---|---|---|---|---|---|
| **512** | **2.35** | 2.17 | 2.15 | 2.22 | 1.0–1.1 GB |
| 256 | 4.80 | 4.68 | 4.74 | 3.72 | 1.0–1.4 GB |

- **P1-00's 1.6 s/chunk was memory pressure.** 0.43 s/chunk now — 3.8× faster,
  same model, same machine.
- **Batch size barely matters for speed** (9% across 8→64): 81% of chunks exceed
  512 tokens, so every sequence pads to the cap and cost is one fixed-size
  sequence per chunk however they are grouped.
- **It matters a great deal for memory in a real run.** Against an index that
  already holds collections, batch 64 reached 3.05 GB RSS at 1.93 chunks/s
  while batch 8 held 1.34 GB at 2.33 chunks/s — faster *and* 56% lighter.
  Qdrant local mode keeps every existing collection in RAM, which the grid's
  fresh temp directories did not capture. The grid alone would have led to the
  wrong default.
- 256 tokens was rejected (D2-00): with a median chunk of 986 tokens it would
  embed roughly the first quarter of each chunk, and AAPL/AMZN are already
  embedded at 512, so adopting it means re-indexing everything and every
  retrieval number measured before and after becomes incomparable.

### 4.3a Core-ticker indexing (P2-00e) — complete

`reports/phase2/index_{MSFT,JPM,GOOGL,BLK,GS}.json`, run in the spec's order
with `caffeinate -i`.

| Ticker | Chunks | Seconds | chunks/s | Peak RSS |
|---|---|---|---|---|
| MSFT | 1,182 | 427 | 2.77 | 1.37 GB |
| JPM | 4,954 | 2,814 | 1.76 | 1.08 GB |
| GOOGL | 970 | 541 | 1.79 | 1.01 GB |
| BLK | 1,529 | 660 | 2.32 | 0.99 GB |
| GS | 2,213 | 1,395 | 1.59 | 0.95 GB |
| **Total** | **10,848** | **5,837 (1.6 h)** | **1.86** | **≤ 1.37 GB** |

NFLX needed the full pipeline rather than just indexing, because it is an
on-demand filer with no chunk files: download, parse and chunk produced 891
chunks across FY2023–2025, and the whole thing — fetch to searchable — took
about 9 minutes. That run is also the end-to-end verification of the P2-00(c)
rewiring on the **production on-demand path**: `ingestion/auto_ingest.py` drove
the streaming indexer, including the background pass for the 65 non-priority
chunks, with `force_reindex` correctly dropped (see the commit note on
`3b2a081` — v1's flag meant "upsert anyway" while the new one deletes, so
keeping it would have wiped the 826 chunks a user was already searching).

Final state: 24 collections, 13,520 points.

| Ticker | Collections |
|---|---|
| AAPL · AMZN · MSFT · JPM · GOOGL · NFLX · GS | 3 each |
| BLK | 2 — **FY2023 is absent**, because BlackRock's FY2023 10-K is filed under its old CIK (1364742) and was never chunked. The catalog knows about it (N1, dual CIK); the text index does not. Noted for Phase 4's V0 subset. |
| TSLA | 1 — left over from a Phase 1 auto-ingest during the baseline measurement |

### 4.4 Corpus facts worth recording

- **iXBRL formats**: across all 250 cached documents (65 filings) there are
  exactly **7** distinct `ix:nonFraction/@format` values — `ixt:num-dot-decimal`
  (127,132), none (104,468), `ixt:fixed-zero` (24,403), `ixt:numdotdecimal`
  (8,539), `ixt-sec:numwordsen` (648), `ixt:zerodash` (603),
  `ixt:numcommadecimal` (1). All seven are implemented.
- `ixt-sec:numwordsen` uses 16 distinct texts, including `nil` (6) and
  `three million` (1).
- 2,725 facts use a negative `@scale` (−2 or −4): percentages.
- 32 facts are `xsi:nil`, all `us-gaap:CommitmentsAndContingencies`.
- `ix:hidden` holds facts across **2,001 distinct concepts** for the banks, not
  just the `dei:` cover page.
- Chunk corpus: median 986 tokens, 81% over 512, 77% table chunks.

### 4.5 DEI fiscal labels (P2-03)

`reports/phase2/dei_labels.json` — newest 5 filings × 13 tickers.
**65 of 65 match** the inferred `period_end.year`; catalog now records
`fiscal_label_source='dei'` for those 65 and `period_end` for the other 300.
**29 of 65 are multi-document submissions**, and `exhibit_docs` is populated.

### 4.6 Facts store

26,041 facts / 66 submissions / 13 tickers / 3,040 concepts for the bundled set;
33,512 / 94 / 18 / 3,492 including the cross-check-only filers.
`make facts` twice → all `unchanged`, identical content hash; `--rebuild` of all
66 → identical content hash (T2-08).

## 5. Deviations from spec, and decisions

| # | Deviation | Why | Recorded |
|---|---|---|---|
| 0 | ~~Tiered candidate lists were a deviation~~ — **no longer one.** Spec v1.5 rewrote 6.4 rule 3 to match D2-02, so the tiered preference list and the candidates-agree rule are now the specification | the owner adopted the reasoning below after reviewing it | **D2-02**, spec v1.5 |
| 1 | *(historical, kept for the record)* **Tiered candidate lists**, and "candidates that agree are not ambiguous" — a deviation from the literal text of 6.4 rule 3 **as it stood at v1.4** | The literal rule abstained on 90 resolutions, of which 27 were a filer tagging one number twice (refusing to state a number the filing prints twice is a bug) and 63 were definitional pairs where one concept is what the question means. Tiers state that choice once, with a `preference` reason the loader **requires**, instead of 30 near-identical overrides. An ambiguity *inside* a tier still abstains. | **D2-02** |
| 2 | **Kept bge-base at 512 tokens** although the tuned setting reaches 2.35 chunks/s, below P2-00(d)'s ≥ 4 bar | The bar is a proxy for "indexable overnight". 4.2 h for the full corpus and 78 min for what P2-00(e) asks is still an overnight job, and the fallbacks cost real quality (truncating 81% of chunks, or dropping the table chunks that financial questions need). No fallback taken. | **D2-00** |
| 3 | `facts/documents.py` is not in spec section 7's module list | A submission's documents must be discovered from its own `FilingSummary.xml`, and `data/raw/` is read-only (CLAUDE.md rule 6, restated by P2-04). One new file in an existing package. | this report |
| 4 | `config.index_batch_size` added, which is not in spec section 8 | The measured value from P2-00(d). Separate from `embedding_batch_size`, which also governs query encoding. | **D2-00** |
| 5 | Validation matches statements by section **title**, not by a `fs_income_stmt` key | v1's parser names sections by heading text; there is no such key. Renaming v1's sections would invalidate the Phase 1 baseline artifacts. | this report |
| 6 | `crosscheck.csv` holds the 27,506 comparable rows, not all 33,512 | At 33,512 rows it was 4.6 MB against this repo's own 5 MB hygiene limit, so one more filer would have broken `make hygiene`. The omitted rows are `not_in_oracle` (no comparison exists) and their count and concepts are in the JSON. | this report |
| 7 | `logs/phase2/commands.log` is not committed | `logs/` is gitignored by the Step 0 rules; Phase 1 did the same. The appendix carries the list. | this report |

## 6. Defects

### 6.1 Found in v1 / the inherited code

| ID | Defect | Status |
|---|---|---|
| K2 | "Assume millions" prompt vs Netflix reporting in thousands | **Addressed** — scale is read from the tag, never inferred; asserted at the extractor, the resolver and the formatter |
| K3 | iXBRL facts discarded at parse | **Addressed** — `facts/extract.py` |
| N6 | `format` transforms (zero-dash etc.) unhandled | **Addressed** — all 7 corpus formats implemented, unknown ones fatal |
| N7 | 52/53-week fiscal years | **Addressed** — 350–380 day window; Apple's 363-day FY2024 asserted |
| N1 | BlackRock dual CIK | **Addressed** in P1-09; its dual *revenue concept* addressed here by an evidenced override |
| — | `ingestion/embedder.py` and `retrieval/vector_store.py` both documented `BAAI/bge-large-en-v1.5` at 1024 dimensions | **Fixed** — `config.py` has said bge-base at 768 since v1, so both comments described a model the code never loaded |
| — | v1's one-pass `index_chunks` holds the whole corpus before the first upsert | **Replaced** on every production path (P2-00c); kept only for `eval/phase1/index_per_ticker.py`, which P2-00 says to retain until the new indexer is verified |
| **NEW** | **v1's parsed statement sections are unreliable.** Across the 36 parsed documents the section titled "Consolidated Statements of Operations" is 1,116,433 characters for one filer, 106 characters of bare heading for Amazon, and **empty for JPMorgan in all three years**. Alphabet's "Consolidated Balance Sheets" section contains the auditors' report. | **Open** — section 7 item 5. Phase 3's text path reads the same sections, so it has to be faced there |

### 6.2 Found in my own Phase 2 code and checks

Each was caught by a check, not by review. Two would have shipped false
confidence.

| # | Defect | How it surfaced | Consequence if shipped |
|---|---|---|---|
| 1 | `ixt-sec:numwordsen` did not know `nil` or scale words (`three million`) | the P2-03 sweep: BAC FY2021 and STT FY2022–2025 refused to extract | 5 of 65 filings unextractable — loudly, which is why this is a gap and not a wrong number |
| 2 | **One unreadable cover-page date took down a whole filing.** A date arriving as `SeptemberÂ 28, 2024` raised out of `parse_document` | the fixture round-trip check | 1,127 good numeric facts discarded over one string in the header |
| 3 | `libxml2` defaults an undeclared HTML document to latin-1, so UTF-8 bytes came back mojibake | same | the real filings declare their charset and were never affected, which is exactly why this stayed invisible until a trimmed fixture did not |
| 4 | **`survey_formats` pooled numeric and non-numeric formats**, so `unsupported_numeric_formats` reported 10 "unsupported" transforms on a corpus that extracts cleanly | reading its own output | a survey that cries wolf is worse than none: the next person adds transforms nothing needs |
| 5 | **The cross-check's comparison key omitted `start`.** Microsoft tags annual *and quarterly* dividends ending on the same day, so the quarterly row overwrote the annual one | 26 "mismatches" that were all one bug | **26 phantom discrepancies in this report**, and a reader would reasonably have doubted the other 20,000 |
| 6 | **`validation_status` called 73 values `conflict`** when the real problem was v1's parser handing it a heading instead of a statement | the values were verifiably right (AMZN FY2023 revenue of 574,785M) | 73 phantom disagreements; `conflict` now requires ≥ 20 numeric tokens in the text and `validation_detail` says which case applies |
| 7 | Fixture selection took the first N facts per concept, dropping the consolidated annual one (45 of Apple's 54 revenue facts are dimensional) | the builder's own re-parse check | fixtures that look plausible and do not contain the thing they exist to demonstrate |
| 8 | `calc.py` rounded half-to-even (Decimal's default) while `format.py` rounded half-up | reading both | the calculator and the formatter disagreeing in the last printed digit, for no stated reason |
| 9 | Two of my own test expectations were wrong arithmetic (ratio `0.3851` vs `0.3850`; CAGR `10.73` vs `10.74`) | the tests failed | — ; recomputed independently at 40 digits before correcting |
| 10 | Formatter printed `$1000.00 million` for 999,999,999, and `$6.08 per share (6.08)` | boundary tests | — |
| 11 | A literal U+00A0 crept into a character class in `resolve.py` | reading the file back | invisible in every diff it survived |
| 12 | Abstention messages printed Decimal's exponent form (`1.2794E+10 vs 2.0407E+10`) | a resolver test | a user asked to decode scientific notation to see that one is 12.8 billion and the other 20.4 |
| 13 | The unit tests' fake store accepted non-UUID point ids that real Qdrant rejects | the integration test failed on its first run | the fake agreeing with itself; the integration test earns its keep |

### 6.3 Found during closure (P2-13)

| # | Defect | How it surfaced | Consequence if shipped |
|---|---|---|---|
| 14 | **`FactsResolver(statements=None)` meant "use the default", not "disabled".** Every fixture-based test that passed `None` was silently reading the machine's real `data/parsed/` directory | the new D23 guard test, which thought it had disabled validation and got `validated` back | a suite whose results depend on what happens to be on disk — it would have passed here and behaved differently on a clean checkout |
| 15 | A count assertion in `test_facts_build.py` (`facts > 20`) went stale the moment D22 collapsed duplicates | the test failed after the rebuild | — ; replaced with the property that matters: each concept is stored exactly once, with the right value |
| 16 | `make_spotcheck_sheet.py` crashed on `relative_to` when asked to write outside the repo, *after* doing the work | re-running it to a scratch path to compare against the signed sheet | a legitimate use (writing a comparison copy elsewhere) failing with a traceback |

## 7. Owner actions and questions

1. **Core-ticker indexing (P2-00e) finished after the report was first
   drafted** — all eight tickers, 24 collections, 13,520 points (§4.3a). The one
   residual gap is **BLK FY2023**, whose 10-K sits under BlackRock's old CIK and
   has no chunk files; the facts store has it, the text index does not. Decide
   whether Phase 4's V0 subset needs it.
2. **Spot-check sheet: DONE, 20 of 20 OK, zero WRONG.** Nothing to fix. The
   three rows chosen because they were most likely to be wrong — BLK's
   prioritised revenue, Netflix's thousands, Goldman's negative operating cash
   flow — were each confirmed against the named statement line. All 20
   re-resolve unchanged after the D22 rebuild.
3. **`.hygiene_local`: DONE.** Filled, gitignored, tracked tree clean against
   it, with a negative control proving the supplied pattern is live (§3). The
   T2-12 skip is gone.
4. **T2-10: now met** (99.9787%, §4.1a). No decision needed.
5. **v1's parsed statement sections are badly unreliable** (§6.1, last row).
   Spec v1.5 turns this into **D23**: `validation_status` is informational only
   and must never affect an answer or its confidence wording — now guarded by
   two tests — and **P3-00** audits section quality before the text path is
   built on it. This remains the finding most likely to cost Phase 3 real time.
6. **BLK FY2023 is still absent from the text index.** Spec v1.5's **D24** and
   **P3-06** make ingestion catalog-driven across every CIK a ticker has filed
   under, with this filing as the proof case, so no action is needed now.
7. Per CLAUDE.md rule 2, **Phase 3 must not start until you write
   "Approved: start Phase 3"**.

## 8. Gate checklist

- [x] T2-01 … T2-09, T2-11, T2-12, T2-13 pass offline
- [x] **T2-10 cross-check thresholds met** — exact 99.9787% ≥ 99.5%, 0 scale
      errors, 0 sign errors, every discrepancy classified (§4.1, §4.1a)
- [x] DEI fiscal-label verification reported (65/65, §4.5)
- [x] Spot-check sheet produced (20 rows, §7 item 2)
- [x] Coverage and ambiguity report produced (§4.2)
- [x] `make lint` clean · `make hygiene` clean
- [x] Coverage ≥ 85% on `facts/` (88.7%) and `catalog/` (96.4%)
- [x] `docs/explainers/phase2.md`, `DECISIONS.md`, `CHANGELOG.md`, `STATE.md`,
      `CLAUDE.md` status table updated
- [x] **P2-00(e) core-ticker indexing complete** — 8 tickers, 24 collections,
      13,520 points (§4.3a); BLK FY2023 absent for the dual-CIK reason
- [x] Owner spot-check sheet completed — 20/20 OK, no WRONG rows to fix
- [x] P2-13 closure: precision fix implemented, store rebuilt (T2-08 still
      holds), cross-check re-run, before/after recorded
- [ ] Pull request reviewed and merged by the owner

## 9. Proposals (not built)

1. ~~**Prefer the more precisely tagged instance**~~ — **BUILT** in P2-13 as
   D22 / D2-03. It was the entire T2-10 shortfall; §4.1a has the before/after.
2. ~~**A `statement_text` quality check**~~ — **SCHEDULED** as P3-00 by spec
   v1.5 (D23), with a negative control required.
3. **Store the comparative prior-year columns** beside each filing's own year,
   flagged as restated-by, so a trend question can be answered from one filing
   and a restatement becomes visible rather than invisible.
4. **A `decimals`-aware numeric scorer** for Phase 4, since "displayed
   precision" is now a measured property of this corpus and not a guess.
5. ~~`.cache/company_tickers.json` contains a cached SEC **error page**~~ —
   **SCHEDULED** as P3-00(b) by spec v1.5.

## 10. Appendix — commands

The full list with timestamps and exit codes is in `logs/phase2/commands.log`
(gitignored, as in Phase 1). The ones that produced the numbers above:

| Exit | Command | Produced |
|---|---|---|
| 0 | `python scripts/profile_indexing.py` | `throughput_profile.json` (§4.3) |
| 0 | `python scripts/make_ixbrl_fixtures.py` | 8 fixtures, 3–28 KB (P2-01) |
| 0 | `python scripts/verify_dei_labels.py --filings 5` | `dei_labels.json` (§4.5) |
| 0 | `python -m facts.build --filings 5` | `facts_build.json`, 26,041 facts |
| 0 | `python scripts/facts_coverage.py` | `facts_coverage.{json,csv}` (§4.2, bundled 13) |
| 0 | `python scripts/facts_coverage.py --all-tickers` | `facts_coverage_all18.json` (§4.2) |
| 0 | `python scripts/crosscheck_companyfacts.py` | `crosscheck.{csv,json}` (§4.1); exit 0 after P2-13, was exit 1 before |
| 0 | `python -m facts.build --rebuild` | the D22 rebuild: 33,512 → 29,002 facts |
| 0 | `python scripts/make_spotcheck_sheet.py` | `owner_spotcheck.csv` (P2-10) |
| 0 | `pytest tests/unit tests/integration --cov` | 547 passed, 0 skipped, 90% (§3) |
| 0 | `ruff check .` | clean |
| 0 | `python scripts/check_repo_hygiene.py` | clean |
| 0 | `python scripts/check_repo_hygiene.py --self-test` | 11 planted violations, all caught |
