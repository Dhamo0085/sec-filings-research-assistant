# Changelog

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
