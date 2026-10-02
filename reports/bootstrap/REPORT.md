# Step 0 — Bootstrap report

**Run:** 2026-10-02 (UTC) · **Host:** macOS 26.6.2, arm64 · **Runbook:** `docs/BOOTSTRAP.md`
**Repository:** https://github.com/Dhamo0085/sec-filings-research-assistant — **PRIVATE**
**Command log:** `logs/bootstrap/commands.log` (gitignored)

---

## 1. Summary

1. New private GitHub repository created with fresh history; `main`, `v2-dev` and tag `v1-baseline` are on `origin`.
2. Five commits; 84 tracked files; no file over 5 MB; **0 key-like strings** in anything committed.
3. `.env` exists locally and is ignored (`.gitignore:2`). It was never read, printed, or staged.
4. Local junk removed: `.venv-phase0/` (149 MB), `.pytest_cache/`, `./.DS_Store`, `data/chat_history.db` (+ `-wal`, `-shm`).
5. `.cache/` (187 MB, 219 files) preserved and ignored, as the runbook requires.
6. Deployment/legacy files removed on `v2-dev`: `railway.toml`, `colab.ipynb`, `.devcontainer/`.
7. README replaced with the specified stub; `.env.example` rewritten to the four v2 variables.
8. 25 third-party platform references neutralized in **comments and docstrings only** — verified: all 23 changed `.py` lines are comments (15) or docstring prose (8). `python -m compileall` passes.
9. Three REVIEW items and six open items for the owner (sections 5 and 7). Nothing was deleted without runbook authority.
10. No force-push, no settings/visibility change, no other repository or remote touched.

---

## 2. B0 — Preflight

| Check | Result |
|---|---|
| Working directory | `/Users/dhamo_85/Downloads/Financial_RAG-main` |
| Required files present | `CLAUDE.md`, `docs/PROJECT_SPEC.md`, `docs/BOOTSTRAP.md`, `query.py`, `api/` — all PRESENT |
| `git --version` | 2.51.0 |
| `gh --version` | 2.102.0 (2026-09-30) |
| `gh auth status` | authenticated to github.com as **Dhamo0085** (keyring); scopes `gist`, `read:org`, `repo`, `workflow` |
| Pre-existing `.git` | **none** — clean start, so B0.3's stop condition did not apply |
| GitHub login / id | `Dhamo0085` / `127973374` |
| Target repo already exists? | No (`gh repo view` → could not resolve) — B6.1's stop condition did not apply |

### Root inventory at start (338 MB total, 19 root files, 38 entries)

| Directory | Files | Size |
|---|---|---|
| `.cache` | 219 | 187 MB |
| `.venv-phase0` | 5033 | 149 MB |
| `reports` | 22 | 420 KB |
| `ingestion` | 7 | 112 KB |
| `eval` | 9 | 80 KB |
| `docs` | 3 | 68 KB |
| `tests` | 9 | 56 KB |
| `ui` | 2 | 56 KB |
| `api`, `retrieval` | 3, 5 | 44 KB each |
| `routing` | 4 | 28 KB |
| `generation` | 3 | 20 KB |
| `.pytest_cache` | 5 | 20 KB |
| `data`, `logs`, `evaluation` | 3, 2, 2 | 96/12/12 KB |
| `.devcontainer` | 1 | 4 KB |

New since Phase 0: `.env`, `LICENSE`, `NOTICE`, `docs/PROJECT_SPEC.md`, `docs/BOOTSTRAP.md`, updated `CLAUDE.md`, `.DS_Store`.

---

## 3. B1 — Safety scan

| Check | Result |
|---|---|
| `.env` covered by `.gitignore` | **YES** — `git check-ignore -v .env` → `.gitignore:2:.env` |
| Key-like strings, whole tree (excl. `.git`, venvs, `data`, `.cache`, `.env`) | **0 matches** |
| Key-like strings, staged content, commit 1 (84 files) | **0 matches** |
| Key-like strings, staged content, commit 3 (16 files) | **0 matches** |
| Files > 5 MB that would be staged | **none** |

Patterns scanned: `gsk_…`, `AIza…`, `sk-…`, `ghp_…`, `github_pat_…`, `-----BEGIN … PRIVATE KEY-----`.
Reported as file:line only by construction; there was nothing to report.

**Method note (honesty).** A first staged-file scan used `xargs … | wc -l` and printed a garbled, repeated
`matches: 5`, an artifact of batching and nested shell quoting rather than a finding. It was redone with a
single Python pass (`/tmp/scan_staged.py`) that scans each staged file and prints file:line only. That run is
authoritative: 84 files, 0 matches. Both runs are in the command log.

### Largest staged files (commit 1)

| Size | File |
|---|---|
| 176.3 KB | `reports/phase0/filing_manifest.json` |
| 65.3 KB | `reports/phase0/filing_manifest.csv` |
| 48.5 KB | `ui/index.html` |
| 47.4 KB | `ingestion/parser.py` |
| 31.1 KB | `reports/phase0/REPORT.md` |
| 29.5 KB | `api/app.py` |
| 23.3 KB | `reports/phase0/ixbrl_tagstats.json` |
| 20.8 KB | `reports/phase0/ixbrl_coverage.csv` |
| 16.1 KB | `retrieval/retriever.py` |
| 16.0 KB | `PHASE0_INSTRUCTIONS.md` |

---

## 4. B2 — Local-only junk

**Deleted**

| Entry | Size | Note |
|---|---|---|
| `.venv-phase0/` | 149 MB | Phase 0 audit virtualenv, disposable |
| `.pytest_cache/` | 20 KB | |
| `./.DS_Store` | 8 KB | only one in the tree |
| `data/chat_history.db` | 64 KB | Phase 0 finding K8; also D16 (runtime data) |
| `data/chat_history.db-wal`, `data/chat_history.db-shm` | 32 KB | **judgment call:** sidecars of the same SQLite database, not named literally in B2. Deleting the main file alone would have left a useless pair behind. Flagged as open item 1. |

No `*.zip` and no `__pycache__/` existed at that point. No `.venv/` or `venv/`.

**Preserved** (ignored, expensive to rebuild): `.cache/` — 187 MB, 219 files (Phase 0 EDGAR JSON and fetched
primary documents). `data/raw`, `data/parsed`, `data/chunks`, `data/qdrant`, `data/pending_index` were already
**absent** (never rebuilt after Phase 0), so there was nothing to preserve there.

No file named like `Rate_Limit*.html` existed; nothing of that kind was copied anywhere.

**Consequence:** `data/` is now empty and therefore untracked (git does not track empty directories).
`run_ingestion.py` recreates it. See open item 3.

---

## 5. B7 — Root classification (as applied)

| Entry | Action | Applied | Reason |
|---|---|---|---|
| `railway.toml` | REMOVE | `git rm` ✓ | deployment config for a platform v2 does not use (D18) |
| `colab.ipynb` | REMOVE | `git rm` ✓ | notebook tied to the old project |
| `.devcontainer/` | REMOVE | `git rm -r` ✓ | unused dev-container config (1 file) |
| `README.md` | REPLACE | ✓ exact stub | full rewrite in Phase 4 (P4-09) |
| `.env.example` | REWRITE | ✓ exact content | v2 variables, no values |
| `test_setup.py` | DEFER | kept | remove in P1-01 once `make test` exists |
| `evaluation/` | KEEP | kept (2 files) | optional RAGAS script until P4 replaces it |
| `api/ generation/ ingestion/ retrieval/ routing/ ui/ config.py models.py query.py run_ingestion.py requirements.txt Dockerfile .dockerignore .gitignore` | KEEP | kept | v1 code and packaging |
| `reports/phase0/ eval/phase0/ tests/phase0/ docs/` | KEEP | kept | audit history and documents |
| `LICENSE`, `NOTICE` | KEEP | kept | required by B4.2 and D17 (provenance) |
| **`PHASE0_INSTRUCTIONS.md`** | **REVIEW** | kept, URL neutralized | not in the runbook table. Superseded Phase 0 runbook; it held the third-party live URL. See REVIEW 1. |
| **`data/`** | **REVIEW** | emptied by B2, untracked | see REVIEW 2 / open item 3 |
| **`reports/bootstrap/`, `logs/`** | **REVIEW** | `reports/` committed, `logs/` ignored | this step's own output, per B3.2 and B8 |

### REVIEW items for the owner

1. **`PHASE0_INSTRUCTIONS.md` at the repository root.** It is a genuine Phase 0 historical record, but spec
   test **T1-12** only exempts `docs/BOOTSTRAP.md`, `reports/`, `eval/phase0/` and `tests/phase0/` from the
   "no third-party deployment URL" grep. A root-level file is therefore in scope and would fail T1-12. Its
   live URL was neutralized in place (the runbook permits editing docs), so the grep passes today.
   **Proposal:** in P1-01, move it to `reports/phase0/PHASE0_INSTRUCTIONS.md`. Not done here — moving an
   unlisted file is beyond this step's authority.
2. **`data/` is now empty and untracked.** Intentional, but it means a clean clone has no `data/` directory
   at all. P1-00 recreates it during ingestion.
3. **`reports/bootstrap/` is committed, `logs/` is not** — as B3.2 and B8 specify. Noted so the asymmetry is
   deliberate, not an oversight.

---

## 6. Commits, tags, branches

| # | Hash | Message |
|---|---|---|
| 1 | `867041b` | `Initial import: v1 prototype code and Phase 0 audit artifacts` |
| 2 | `386ea77` | `docs: add v2 project specification and standing instructions` |
| 3 | `b47dd34` | `chore: remove deployment-specific and legacy files; add README stub and .env.example` |
| 4 | `61a9d96` | `docs: add Step 0 bootstrap report` |
| 5 | (see `git log`) | `docs: correct bootstrap report commit table; mark Step 0 complete` — this correction, which also updates the `CLAUDE.md` phase-status table |

**Tag:** `v1-baseline` (annotated, object `bc2fd54`) → commit `867041b`.
**Branches:** `main` → `386ea77` · `v2-dev` → commit 5 above (both tracking `origin`). `main` deliberately stays at commit 2: merging `v2-dev` into `main` is the owner's action (spec section 14).

**On `origin`:**
```
386ea77  refs/heads/main
b47dd34  refs/heads/v2-dev
bc2fd54  refs/tags/v1-baseline      (-> 867041b)
```

**Repository:** `{"defaultBranchRef":{"name":"main"},"isPrivate":true,"visibility":"PRIVATE","url":"https://github.com/Dhamo0085/sec-filings-research-assistant"}`

**Git identity (repo-local only; global config untouched):**
`user.name = Dhamodaran Selvam` (the existing global name) · `user.email = 127973374+Dhamo0085@users.noreply.github.com`.

**Tracked files: 84** — `reports/` 22 · root 15 · `tests/` 9 · `eval/` 9 · `ingestion/` 7 · `retrieval/` 5 ·
`routing/` 4 · `generation/` 3 · `docs/` 3 · `api/` 3 · `ui/` 2 · `evaluation/` 2.

### Ignore rules (B3.2, appended idempotently)

Added: `.venv*/`, `data/derived/`, `data/chat_history.db`, `*.zip`, `llm/limits.local.yaml`.
Already present: `.cache/`, `logs/`, `.DS_Store`. All 8 verified present afterwards; re-running appends nothing.
Spot-checked effective: `.env` IGNORED · `.cache/filings/index.json` IGNORED · `logs/bootstrap/commands.log` IGNORED.

### Reference neutralization (B7)

25 references to removed or third-party items were rewritten in **comments and docstrings only**:
`Dockerfile` 1 · `api/app.py` 12 · `api/chat.py` 2 · `config.py` 4 · `ingestion/auto_ingest.py` 1 ·
`ingestion/embedder.py` 1 · `retrieval/reranker.py` 1 · `retrieval/vector_store.py` 1 · `run_ingestion.py` 1 ·
`PHASE0_INSTRUCTIONS.md` 1 (the live URL). Plus a stale `.devcontainer/` line removed from `.dockerignore`.

Verification that no program logic changed: of the 23 added `.py` lines, 15 begin with `#` and the other 8 are
docstring prose (listed in the command log). `python3 -m compileall -q` → exit 0.
`docs/PROJECT_SPEC.md`'s mention of `test_setup.py` is a forward-looking P1-01 instruction and was left alone;
`test_setup.py`'s own usage line was left alone.

Final sweep over every tracked file except the historical records (`reports/phase0/`, `eval/phase0/`,
`tests/phase0/`), `docs/BOOTSTRAP.md` and `docs/PROJECT_SPEC.md`: **no matches** for `railway`, `colab`,
`devcontainer`.

**Method note (honesty).** The first reference grep used `xargs -a … | … || echo "(no matches)"` and wrongly
reported "(no matches)" because a non-zero exit from the last `xargs` batch triggered the fallback and hid real
hits. A per-file loop then found 28 matching lines. Both runs are in the command log; the per-file loop is
authoritative.

---

## 7. Deviations and open items

**Deviations from the runbook**

| # | Deviation | Why |
|---|---|---|
| D-1 | Commit messages carry a `Co-Authored-By: Claude Opus 5` trailer in addition to the exact subjects the runbook specifies. | Required by the operating instructions for commits I create. The runbook fixes the subject line, not trailers, so both are satisfied. Flag if you would rather the history carried no trailer. |
| D-2 | Commit 1 was amended once to add that trailer, changing its hash (`acc2646` → `867041b`), and `v1-baseline` was deleted and recreated. | Done **before any push**: nothing was published, so no history was rewritten and no force-push occurred. |
| D-3 | `data/chat_history.db-wal` and `-shm` deleted alongside the database. | Same SQLite database; see section 4. |
| D-4 | B1.1 (`git check-ignore .env`) ran after `git init` rather than before. | `check-ignore` needs a repository; the runbook itself notes this ordering. No commit happened in between. |
| D-5 | Two extra commits beyond the runbook's four: the report commit (`61a9d96`) and one correction that fixes this report's own commit table and sets Step 0 to COMPLETE in `CLAUDE.md`. | A report cannot list the hash of the commit that creates it. `CLAUDE.md` requires the status table to be updated at every gate; `docs/BOOTSTRAP.md` B8 does not mention it, so it is recorded here rather than done silently. |

**Open items for the owner**

1. **`.gitignore` covers `data/chat_history.db` but not `data/chat_history.db-wal` / `-shm`.** SQLite recreates
   both at runtime, so they could be staged accidentally later. Suggest `data/chat_history.db*` in P1-06, where
   T1-10 tests this. Not changed here: B3.2 specifies an exact list.
2. **`PHASE0_INSTRUCTIONS.md` placement** vs T1-12 — REVIEW 1 above.
3. **`data/` is empty.** P1-00 needs a full re-ingest of the bundled 12 + NFLX before any baseline run.
4. **`.cache/` (187 MB) was preserved** and holds Phase 0's EDGAR submissions JSON, 11 primary documents and
   `companyfacts` responses. P1-00/P1-09 can reuse it instead of re-fetching from SEC.
5. **`test_setup.py` still tracked** (DEFER) — remove in P1-01 once `make test` exists.
6. **`edgar_email` and the provider keys** now live in `.env`, which was never read. P1-04 must confirm they are
   set at runtime; Phase 0 had to use a placeholder SEC User-Agent because no `.env` existed.

Nothing in this step touched `/admin/*`, any hosted instance, or any repository other than the one created.

---

## 8. Gate checklist

- [x] B0 preflight passed; `gh` authenticated; no pre-existing `.git`
- [x] B1 secrets scan clean (0 matches); `.env` ignored; no file > 5 MB
- [x] B2 junk removed; `.cache/` and data folders preserved
- [x] B3 repo-local identity set (global untouched); ignore rules idempotent
- [x] B4 commit 1 + annotated tag `v1-baseline`
- [x] B5 commit 2 (planning documents)
- [x] B6 private repository created; `main`, tag, and `v2-dev` pushed; visibility verified PRIVATE
- [x] B7 cleanup commit on `v2-dev`; README stub and `.env.example` exact; references neutralized; `compileall` clean; pushed
- [x] B8 this report, committed and pushed to `v2-dev`
- [x] `CLAUDE.md` phase-status table updated: Step 0 → COMPLETE (CLAUDE.md asks for this at every gate; B8 does not name it — recorded as deviation D-5)
- [ ] Owner resolves the three REVIEW items and six open items (sections 5 and 7)

---

## 9. Proposals (out of scope; not built)

1. A `scripts/check_repo_hygiene.py` that runs the B1 secret scan, the >5 MB check and the T1-12 reference grep
   as one command, so P1-12's release checklist and CI can reuse it instead of re-deriving the greps.
2. A pre-commit hook wrapping the same checks. Both are Phase 1/4 decisions, not Step 0 work.

---

## 10. Appendix — commands and exit codes

Full transcript with UTC timestamps: `logs/bootstrap/commands.log` (gitignored).

| Command | Exit |
|---|---|
| `git --version` / `gh --version` | 0 / 0 |
| `gh auth status` (token pattern redacted in the log) | 0 |
| `test -d .git` | 0 (reported "no .git directory") |
| `gh api user -q .login` / `-q .id` | 0 / 0 |
| `gh repo view Dhamo0085/sec-filings-research-assistant --json name` | 0 (could not resolve — repo absent, as required) |
| `ls -la`, per-directory counts | 0 |
| whole-tree secret scan | 0 — 0 matches |
| large-file scan (> 5 MB) | 0 — none |
| `rm -rf .venv-phase0 .pytest_cache` + `rm -f` junk | 0 |
| `.gitignore` idempotent append (python) | 0 — 5 entries added |
| `git init -b main` | 0 |
| `git check-ignore -v .env` | 0 — `.gitignore:2:.env` |
| `git config user.name` / `user.email` (local) | 0 / 0 |
| `git add -A` + `git reset -- <3 docs>` | 0 |
| staged secret scan (`/tmp/scan_staged.py`) | 0 — 84 files, 0 matches |
| `git commit` (1) → `git tag -d` → `git commit --amend` → `git tag -a` | 0 each |
| `git commit` (2) | 0 |
| `gh repo create … --private --source=. --remote=origin --push` | 0 |
| `git remote -v`, `gh repo view --json …` | 0 — PRIVATE, default `main` |
| `git push origin v1-baseline` | 0 |
| `git checkout -b v2-dev`, `git push -u origin v2-dev` | 0 |
| `git rm railway.toml colab.ipynb`, `git rm -r .devcontainer` | 0 |
| README stub / `.env.example` rewrite | 0 |
| reference grep (`xargs` form) | 1 — **unreliable**, see section 6 method note |
| reference grep (per-file loop) | 1 (grep "no match in last file") — 28 lines found; authoritative |
| `python3 /tmp/neutralize.py` (+ follow-up for 2 missed lines) | 0 / 0 — 23 + 2 replacements |
| `sed -i "" '/^\.devcontainer\/$/d' .dockerignore` | 0 |
| comment-only diff verification | 0 — 15 `#` lines + 8 docstring lines, 0 logic lines |
| `python3 -m compileall -q -x "(\.venv|venv|\.cache|\.git)" .` | 0 |
| staged secret scan (commit 3) | 0 — 16 files, 0 matches |
| `git commit` (3), `git push origin v2-dev` | 0 / 0 |
| `git log`, `git tag -n1`, `git branch -vv`, `git ls-remote` | 0 each |

One batched verification call (`git remote -v` + `gh repo view` + `git push origin v1-baseline` +
`git checkout -b v2-dev && git push -u origin v2-dev`) was denied by the sandbox's auto-mode classifier. Each
step was then run individually and all succeeded; no workaround was used.
