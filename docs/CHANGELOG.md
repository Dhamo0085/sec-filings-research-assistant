# Changelog

## Phase 3 — routing, answers, `as_of`, abstention, UI (2026-10-02)

Report: `reports/phase3/REPORT.md` · Owner explainer: `docs/explainers/phase3.md`

**The headline: the system now decides what a question is asking without a
model, answers it from the right place, refuses clearly when it cannot, and
cannot cite a filing that was not public on the date you asked about.** On a
30-question smoke run against the real stack, every question reached the status
it should, with zero look-ahead violations — and every one of them *routed*
without an LLM call.

### Added
- `answering/outcome.py` — one response type for every path (spec 6.5), with
  the four guarantees enforced as validators rather than conventions. An
  `Outcome` with a number and no traceable source (G1), a citation newer than
  the as-of date (G2), a refusal with no reason or with citations (G3), or an
  outage shaped like a question back to the user (G4) **cannot be
  constructed**. Citation indices must be exactly 1..n, which makes the Phase 0
  renumbering bug (K1) unrepresentable.
- `routing/entities.py`, `routing/periods.py` — deterministic company and
  period resolution, 143 phrase cases, injectable clock. Keeps three things
  apart that one date regex would collapse: an `as_of` cutoff, the period being
  asked about, and a fiscal label versus a calendar year.
- `routing/router.py` — rules first, the model only for genuine ambiguity, with
  `used_llm` recorded so a Phase 4 failure analysis can tell a routing mistake
  from a retrieval one. Metric aliases come from `facts/concepts.yaml` itself,
  so a metric the router can name is one the resolver can resolve.
- `answering/facts_answer.py` — deterministic templates. No model writes these
  sentences, so the number in the text is the number in the citation. Every
  answer states the period-end date (D6), the definition used (D5), the exact
  figure beside the readable one, and any restatement (D14).
- `answering/text_answer.py` — the narrative path: the catalog chooses which
  collections may be searched at the as-of date, the model returns
  `{found, answer}` instead of prose we have to interpret, and citations carry
  the accession and filing date.
- `answering/abstain.py` — one table from condition to reason to message. A
  test asserts the table covers the whole enum and that no message contains a
  figure.
- `generation/normalize.py` — citation markers normalized at the generator
  boundary and counted in the trace (D20).
- `ingestion/sec_cache.py` — SEC serves its rate-limit page with HTTP 200, and
  two fetchers were caching it as data. Both now validate before writing.
- `ingestion/catalog_ingest.py` — filing lists come from the catalog, covering
  every CIK a ticker has filed under (D24), plus the catalog/index join that
  nothing was performing.
- `scripts/audit_sections.py` — section-quality audit with its own negative
  control; `scripts/reparse_corpus.py` — offline re-parse, including filings
  that exist only in the SEC cache.
- `eval/phase3/` — the 30-question smoke evaluation and its transcripts.

### Changed
- `query.ask()` returns an `Outcome` and is a dispatcher over the above;
  `routing/classifier.py` is off the answer path.
- `POST /query` accepts `as_of`, returns the 6.5 body, echoes a sanitised
  request id, and reveals the trace only to an admin passing `?debug=1`.
- The UI gained an as-of date input, D9 status badges, the definition line,
  refusal and error states, and citation chips that link to the EDGAR filing.
- `/chat` no longer prepends conversation history to the routed question —
  with a rules-first router that turned a repeated question into a trend over
  every year the previous answer mentioned.

### Measured
- **Section quality (the uncomfortable number).** 238 of 429 audited section
  slices are usable; Item 1A Risk Factors is missing from 17 of 39 filings.
  Three candidate parser fixes were implemented and ablated over 13 filings and
  moved the corpus by **+1 of 143**, so the limit is documented rather than
  patched (**D3-00**), with the two fixable defect classes pinned as strict
  `xfail` tests.
- **BlackRock FY2023**, filed under the pre-reorganisation CIK 1364742, is
  parsed, indexed (1,037 chunks) and answerable — the D24 proof case.
- `make test` → 1,052 passed, 2 xfailed (documented), 0 skipped. Coverage:
  facts 88.8%, catalog 95.5%, llm 90.5%, answering 97.2%.

### Fixed
Twelve defects, listed in the report's section 6. Four were visible only by
driving the real UI, including the conversation-history one above.

## Phase 2 — facts engine (2026-10-02)

Report: `reports/phase2/REPORT.md` · Owner explainer: `docs/explainers/phase2.md`

**The headline: numeric answers now come from the filer's own XBRL tags, and
that extraction is independently verified.** Against SEC `companyfacts` — the
same filings rendered by the SEC's own pipeline — 23,429 comparable values from
94 submissions across 18 companies: **99.98% bit-exact overall, 100.00% exact on
the 1,047 values for the 23 concepts the system uses to answer questions, zero
scale errors, zero sign errors, zero unclassified mismatches.** All of T2-10's
thresholds are met.

### Added
- `facts/extract.py` — inline XBRL to typed facts: contexts, units, `scale`,
  `sign`, every `@format` transform in the corpus (unknown ones are **fatal**,
  not skipped), `xsi:nil` kept distinct from zero, `ix:hidden` facts collected,
  and all documents of a submission pooled before anything resolves. Decimal
  throughout.
- `facts/store.py` + `facts/build.py` — SQLite per spec 6.2 and `make facts`.
  Idempotent on a hash of the document bytes: a rebuild of all 66 submissions
  reproduces a byte-identical content hash (T2-08).
- `facts/documents.py` — reads a filing's own `FilingSummary.xml` to learn which
  documents carry facts. 29 of 65 filings are multi-document submissions, not
  just Wells Fargo.
- `facts/concepts.yaml` + `concepts.py` — the metric registry: 12 metrics,
  aliases, sector candidate lists, tiered definitional preferences, and
  per-filer overrides that the loader **rejects without written evidence**.
- `facts/resolve.py` — spec 6.4 policy and the 6.5 abstention enum. `as_of`
  filters *filings* before any fact is looked at; D14 walks eligible filings
  newest-first; `ambiguous_concept` rather than a silent pick.
- `facts/calc.py` + `facts/format.py` — Decimal arithmetic that refuses unit
  mismatches, period mismatches, a zero base and a **negative** base, and
  formatting that cannot change a magnitude.
- `ingestion/indexer.py` — streaming indexer: one collection at a time, bounded
  batches, resumable by point id, with chunks/s and peak RSS printed.
- `scripts/` — `profile_indexing.py`, `make_ixbrl_fixtures.py`,
  `verify_dei_labels.py`, `facts_coverage.py`, `crosscheck_companyfacts.py`,
  `make_spotcheck_sheet.py`.
- `tests/fixtures/ixbrl/` — 8 trimmed **real** excerpts (3–28 KB) cut
  mechanically from the cached filings, covering Apple's 52/53-week year,
  Netflix's thousands, BlackRock's dual revenue, a bank, Wells Fargo's
  wrapper/EX-13 split, a 10-K/A, negatives, and zero-vs-nil.
- A fourth repo-hygiene check reading owner-supplied patterns from a gitignored
  `.hygiene_local` (P2-00b).

### Changed
- `make facts` is real; `make index` added; `make test` writes its artifacts to
  `reports/$(PHASE)/` so a later phase cannot overwrite an earlier one's.
- Every production path now uses the streaming indexer. P1-00's 1.6 s/chunk was
  memory pressure, not the model: the same model on the same machine measures
  0.43 s/chunk at 1.34 GB peak RSS instead of 2.4 GB (D2-00).
- `config.index_batch_size` (default 8) separates indexing from query encoding.
- Catalog `fiscal_label_source` is now `dei` for the 65 verified filings, and
  `exhibit_docs` is populated.
- Corrected two comments that documented `BAAI/bge-large-en-v1.5` at 1024
  dimensions; `config.py` has said bge-base at 768 since v1.

### Removed
- `test_setup.py` (P2-00a) — `make setup` and `make test` replace it.

### Decisions
- **D2-00** keep bge-base at 512 tokens, batch 8. The ≥ 4 chunks/s bar is a
  proxy for "indexable overnight" and 4.2 h still is one; the fallbacks cost
  real quality.
- **D2-02** tiered candidate concepts, and candidates that agree are not
  ambiguous. Took coverage from 69.7% to 81.3% and ambiguity from 90 rows to
  zero, without weakening the rule where it matters — BlackRock still resolves
  only through its evidenced override, and a test proves removing it abstains.

### Closure (P2-13, after the owner's gate)
- Owner spot-check: **20 of 20 OK, zero WRONG.** All 20 re-resolve unchanged
  after the rebuild below.
- **D22 / D2-03 precision preference** implemented in `facts/extract.py`: when a
  filing tags one concept twice in one context at different precisions, keep the
  instance with the larger `@decimals` — but only when the two agree within the
  coarser declared tolerance. Disagreements beyond it are never merged; both are
  kept and the resolver abstains. Corpus-wide exactness **99.1347% → 99.9787%**,
  the `rounding` discrepancy class eliminated (233 → 0), and T2-10 goes from not
  met to met. 272 values changed; 4,510 duplicate instances collapsed.
- `.hygiene_local` filled by the owner: the tracked tree is clean against it and
  the T2-12 skip is gone (547 passed, 0 skipped).

### Known at this gate
- Nothing outstanding against the Phase 2 exit criteria.
- P2-00(e) core-ticker indexing **completed**: all eight evaluation-core
  tickers, 24 collections, 13,520 points, 10,848 chunks in 1.6 h at
  1.86 chunks/s. BLK FY2023 is the one gap — its 10-K is under BlackRock's old
  CIK and has no chunk files.
- v1's parsed statement sections are unreliable (one filer's "income statement"
  section is 106 characters of heading, JPMorgan's is empty), which makes 51
  `conflict` validations parser defects rather than extraction errors — and is
  a problem Phase 3's text path inherits.

## Phase 1 — foundation, hardening, catalog, baseline (2026-10-02)

Report: `reports/phase1/REPORT.md` · Owner explainer: `docs/explainers/phase1.md`

### Added
- `llm/` — one multi-provider, free-tier LLM client for the whole project:
  ordered `provider:model` failover, capability registry, disk cache, per-model
  budgets corrected from rate-limit headers, typed errors, and a deterministic
  `FakeLLM` for offline tests.
- `catalog/` — 365 annual filings with `period_end` and `filing_date`, the
  foundation for `as_of` correctness. Handles BlackRock's dual CIK and links
  10-K/A amendments to the filing they amend.
- `api/auth.py` — fail-closed admin auth (D13): `X-Admin-Token`,
  constant-time compare, unset token *disables* the routes.
- `api/ratelimit.py` — per-client rate limit for `POST /query`.
- `generation/citations.py` — single-pass citation remapping.
- `scripts/` — `smoke.py` (read-only service check), `check_repo_hygiene.py`
  (secrets, large files, third-party references, each with a negative control),
  `check_qdrant_filter.py`.
- `eval/phase1/` — baseline indexer, baseline planner, provider bake-off.
- `Makefile`, `pyproject.toml`, split requirements, 289 offline tests.

### Fixed
- An LLM failure no longer masquerades as a question to the user. Dependency
  failures return `status=error` with an `error_code` and HTTP 503; `/health`
  reports the model's state from a cached probe.
- Reasoning models' chain of thought no longer starves the answer: empty output
  is a typed error that fails over, and the router budget is sized from
  measurement.
- Citation markers no longer collide when sub-answers are merged.
- Fiscal years come from what is indexed, not a hard-coded set, so FY2026 is
  reachable.
- Admin routes fail closed; four routes that only served the old hosting
  platform are gone; two unauthenticated destructive chat routes are guarded;
  CORS is an allow-list.
- `import config` succeeds with no `.env`.

### Changed
- Docker pinned to Python 3.12, matching local and the baseline.
- `requirements.txt` holds only what serving and ingestion import; test and
  evaluation stacks moved to their own files.

### Known limitations
- The baseline covers 9 of 25 questions; 16 are `not_run` because indexing the
  bundled set is a ~16 hour job on this hardware (report section 4.2).
- Numbers still come from a model reading markdown tables, and the prompt still
  says "assume millions" — wrong for a thousands-reporting filer. Phase 2.
- `fiscal_label` is the calendar year of the period end, not the company's own
  label. Phase 2 reads it from the filing.
