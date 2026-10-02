# CLAUDE.md — Financial_RAG v2 (standing instructions)

Read this file at the start of every session, then read `docs/PROJECT_SPEC.md` (the full specification).
Precedence if instructions conflict: owner's latest message > this file > `docs/PROJECT_SPEC.md` > code comments.

## What this project is
An SEC 10-K research assistant. v2 adds to the existing RAG pipeline: (1) a **Facts path** that answers
numeric questions from inline-XBRL facts plus a deterministic calculator, (2) **point-in-time (`as_of`)
correctness** so answers only use filings public on that date, (3) **explicit abstention and error states**,
(4) a **measured evaluation** (gold set, deterministic scorers, ablations). Owner: Dhamodaran (personal
portfolio project). The owner supervises; you write code, run tests, and write reports. The owner must be able to
explain every component, so prefer simple, explicit designs and write the briefing documents the spec requires.

## Phase status (update this table at every gate; the owner approves each start)
| Phase | Name | Status |
|---|---|---|
| Step 0 | Bootstrap: new private GitHub repo via `gh`, root cleanup (`docs/BOOTSTRAP.md`) | NOT STARTED, awaiting owner |
| 0 | Audit and baseline | COMPLETE (report in `reports/phase0/REPORT.md`) |
| 1 | Foundation, hardening, catalog, true baseline | NOT STARTED, awaiting owner: "Start Phase 1" |
| 2 | Facts engine (extract, store, resolve, calculate) | NOT STARTED |
| 3 | Routing, answers, `as_of`, abstention, UI | NOT STARTED |
| 4 | Evaluation, ablations, documentation | NOT STARTED |
| 5 | Productization and release | NOT STARTED |

## Standing rules
1. **Evidence over assertion.** Never claim a result you did not produce by running a command. Cite the command and output.
2. **Phase gates.** Do only the current phase. At its end: run all phase tests, write the phase report and logs
   (spec section 13), update the status table, print the summary, and STOP with the line
   `PHASE N COMPLETE — awaiting owner approval`. Start the next phase only after the owner writes "Approved: start Phase N+1".
3. **Tests first for fixes.** Reproduce each defect with a failing test, then fix it. No network or real LLM in unit/integration tests.
4. **Small reversible commits** on phase branches (spec section 14). Never commit to `main`. Never force-push.
5. **Secrets.** Never read, print, log, or commit `.env` or key values. Report only "set" or "not set".
6. **Raw data is read-only** (`data/raw/`). Derived data goes in `data/derived/`. Generated artifacts are gitignored unless the spec says otherwise.
7. **LLM budget.** All LLM calls go through `llm/client.py` (multi-provider free-tier failover, cache, retry, budget, typed errors). Stop cleanly on a rate or budget limit and mark remaining work `not_run`. Do not loop.
8. **No paid services, no production system.** Free tiers only. If a task would need a paid service or a payment card, stop and report. There is no deployment today; a hosted instance (if the owner later creates one) is checked only with `scripts/smoke.py --base-url <url>` (read-only + at most 8 `POST /query`). Never call `/admin/*` or `/ingest` on a hosted instance.
9. **Decisions and deviations.** Log every non-trivial choice in `docs/DECISIONS.md` (options, choice, reason, what would change it). If the spec is wrong or blocked, record it in the phase report and propose a fix. Do not silently diverge. Ask the owner only when blocked.
10. **No scope creep.** Ideas outside the spec go in the report's "Proposals" section. They are not built.
11. **Honesty in every report.** Report failures, skipped items, and unverified claims plainly. Never invent numbers.
12. **Environment.** Owner is on macOS (Apple Silicon). Use `pathlib`, UTF-8 everywhere, and no OS-specific shell assumptions in scripts.
13. **Provenance.** The code started from an earlier MIT-licensed prototype; this is a new personal portfolio repository with fresh history. Keep `LICENSE` and `NOTICE`. Do not copy data, secrets, or deployment links from the old project, and do not reference its URLs or repository.
14. **GitHub.** Use `git` and `gh` only against this project's own `origin`. Push only at the points the spec names (Step 0 and each phase gate), open pull requests with `gh pr create`, and leave merging to the owner. Never force-push, delete or rename a repository, change visibility or settings, or push to another remote. Never read or print tokens.

## Commands (the Makefile is created in P1-02; keep these targets working afterwards)
`make setup` · `make test` (offline unit + integration) · `make test-live` · `make lint` · `make ingest` ·
`make catalog` · `make facts` · `make eval-smoke` · `make eval-full` · `make up`
