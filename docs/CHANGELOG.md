# Changelog

## Phase 2 — facts engine (2026-10-02)

Report: `reports/phase2/REPORT.md` · Owner explainer: `docs/explainers/phase2.md`

**The headline: numeric answers now come from the filer's own XBRL tags, and
that extraction is independently verified.** Against SEC `companyfacts` — the
same filings rendered by the SEC's own pipeline — 27,506 comparable values from
94 submissions across 18 companies: **99.13% bit-exact overall, 100.00% exact on
the 1,861 values for the 23 concepts the system uses to answer questions, zero
scale errors, zero sign errors, zero unclassified mismatches.**

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

### Known at this gate
- T2-10's "≥ 99.5% exact" is **not met** corpus-wide (99.13%). The entire
  shortfall is 233 values in filings that tag one concept twice, rounded in
  prose and exact in a table; they agree within the filer's declared precision
  and none is a registry concept.
- **P2-00(e) core-ticker indexing is incomplete** at the gate: MSFT and JPM
  indexed, GOOGL/BLK/GS still running. Resumable.
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
