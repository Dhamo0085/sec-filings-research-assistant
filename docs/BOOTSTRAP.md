# Step 0 — Bootstrap runbook: create the new GitHub repository and clean the root

Run **once**, before Phase 1. Read `CLAUDE.md` and `docs/PROJECT_SPEC.md` first. This step is not a numbered phase.
Output: `reports/bootstrap/REPORT.md`. When finished, stop with `BOOTSTRAP COMPLETE — awaiting owner approval to start Phase 1`.

## Ground rules for this step
- The owner has already run `gh auth login`. Never read, print, or store tokens. Never read `.env`.
- **Allowed GitHub actions:** create the one new repository named below (private), push `main`, `v2-dev`, and tag `v1-baseline` to `origin`.
- **Forbidden:** force-push; deleting or renaming repositories; changing visibility or any repo setting; pushing to any other remote; creating tokens, secrets, or deploy keys; touching any other repository of the owner.
- If any check fails, **stop and report**. Do not improvise around a failure.
- Log every command with its exit code to `logs/bootstrap/commands.log` (the `logs/` folder is gitignored).
- Default repository name: `sec-filings-research-assistant` (private). The owner may override it in the prompt.

## B0 — Preflight (read-only)
1. `pwd`; confirm the folder contains `CLAUDE.md`, `docs/PROJECT_SPEC.md`, `docs/BOOTSTRAP.md`, `query.py`, and `api/`.
2. `git --version`, `gh --version`, `gh auth status`. It must show an authenticated github.com account. If not, STOP and tell the owner to run `gh auth login`.
3. If a `.git` directory already exists: print `git remote -v` and `git log --oneline | head -5`, then STOP and report. Do not touch unknown history.
4. Record the GitHub login and numeric id: `gh api user -q .login`, `gh api user -q .id`.
5. Print the root inventory: `ls -la`, per-directory file counts and sizes.

## B1 — Safety scan (before any commit)
1. Confirm `.env` is covered by `.gitignore` (after `git init`, `git check-ignore .env` must succeed).
2. Scan files that would be committed for key-like strings:
   `grep -rEn "(gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)" . --exclude-dir=.git --exclude-dir=.venv-phase0 --exclude-dir=.venv --exclude-dir=venv --exclude-dir=data --exclude-dir=.cache --exclude=.env`
   Report **file:line only**, never the matched text. Any hit blocks the commit until the owner decides.
3. Flag any file larger than 5 MB that would be staged.

## B2 — Remove local-only junk (disposable, never committed)
Delete if present: `.venv-phase0/`, `.venv/`, `venv/`, `data/chat_history.db`, any `*.zip` in the root, every `.DS_Store`, every `__pycache__/`, `.pytest_cache/`.
Do **not** delete `.cache/` or the local data folders (`data/raw`, `data/parsed`, `data/chunks`, `data/qdrant`, `data/pending_index`); they are gitignored and expensive to rebuild. Report their sizes.
Never copy any file named like `Rate_Limit*.html` into the repo (it contains the owner's account details).

## B3 — Identity and ignore rules
1. Repo-local git identity only (never edit global config): `user.name` = the existing global name if set, otherwise the GitHub login; `user.email` = `<id>+<login>@users.noreply.github.com`.
2. Append, idempotently, to `.gitignore`: `.venv*/`, `.cache/`, `data/derived/`, `data/chat_history.db`, `logs/`, `*.zip`, `.DS_Store`, `llm/limits.local.yaml`.
   (`reports/` is committed; `logs/` is not.)

## B4 — Commit 1: v1 import
1. `git init -b main`
2. Stage everything **except** `CLAUDE.md`, `docs/PROJECT_SPEC.md`, `docs/BOOTSTRAP.md`. Keep `LICENSE`, `NOTICE`, the Phase 0 artifacts (`reports/phase0/`, `eval/phase0/`, `tests/phase0/`, `docs/DECISIONS.md`), and all v1 code.
3. Review `git status --short`: confirm no secrets, no junk, no file over 5 MB. Print the staged-file count and the 10 largest staged files.
4. `git commit -m "Initial import: v1 prototype code and Phase 0 audit artifacts"`
5. `git tag -a v1-baseline -m "v1 code as imported, before v2 work"`

## B5 — Commit 2: planning documents
Stage `CLAUDE.md`, `docs/PROJECT_SPEC.md`, `docs/BOOTSTRAP.md`; commit `docs: add v2 project specification and standing instructions`.

## B6 — Create the GitHub repository and push
1. `gh repo create <REPO_NAME> --private --source=. --remote=origin --description "SEC 10-K research assistant: verified numbers, point-in-time answers, measured evaluation" --push`
   If the name already exists on the account: STOP and report (do not overwrite).
2. Verify: `git remote -v` shows `github.com/<login>/<REPO_NAME>`; `gh repo view --json url,visibility,defaultBranchRef` shows `PRIVATE` and branch `main`.
3. `git push origin v1-baseline`
4. `git checkout -b v2-dev` and `git push -u origin v2-dev`.

## B7 — Cleanup commit on `v2-dev`
Classify every root-level entry and apply. Use `git rm` (the history keeps everything, so this is reversible).

| Entry | Action | Reason |
|---|---|---|
| `railway.toml` | REMOVE | deployment config for a hosting platform we do not use (D18) |
| `colab.ipynb` | REMOVE | notebook tied to the old project; v2 does not use it |
| `.devcontainer/` | REMOVE | unused dev-container config |
| `README.md` | REPLACE content with the stub below | full rewrite in Phase 4 |
| `.env.example` | REWRITE | v2 variables, no values |
| `test_setup.py` | DEFER | remove in Phase 1 once `tests/` and `make test` exist |
| `evaluation/` | KEEP | optional RAGAS script until Phase 4 replaces it |
| `api/ generation/ ingestion/ retrieval/ routing/ ui/ config.py models.py query.py run_ingestion.py requirements.txt Dockerfile .dockerignore .gitignore` | KEEP | v1 code and packaging still needed |
| `reports/phase0/ eval/phase0/ tests/phase0/ docs/` | KEEP | audit history and documents |
| anything else not listed | REVIEW | list in the report; do **not** delete without owner approval |

**README stub (exact content):**
```
# SEC Filings Research Assistant (v2, in progress)

A research assistant over SEC 10-K filings: verified numbers from structured XBRL facts,
point-in-time (`as_of`) answers, explicit abstention, and a measured evaluation.

**Status:** under active development. The earlier prototype is preserved at tag `v1-baseline`.
See `docs/PROJECT_SPEC.md` for the design and phase plan.

> Research tool, not investment advice.
```
**`.env.example` (exact content):**
```
GEMINI_API_KEY=
GROQ_API_KEY=
edgar_email=
ADMIN_TOKEN=
```
After removals: grep the remaining tracked files (excluding the historical records in `reports/phase0/`, `eval/phase0/`, `tests/phase0/`, and this runbook) for references to removed or third-party items (`railway`, `colab`, `test_setup`, `up.railway.app`, the old repository URL) and neutralize them in comments and docs only. **Do not change program logic in this step.** Then run `python -m compileall -q .` (syntax check only; exclude virtual environments).
Commit: `chore: remove deployment-specific and legacy files; add README stub and .env.example`, then `git push origin v2-dev`.

## B8 — Report
Write `reports/bootstrap/REPORT.md` with: preflight results; the full KEEP/REMOVE/DEFER/REVIEW table with the actual files found; commits (hash, message); tags; branches; repository URL and visibility; staged-file stats; secrets-scan result (file:line only); open items. Commit and push it to `v2-dev`. Print a 10-line summary, then:
`BOOTSTRAP COMPLETE — awaiting owner approval to start Phase 1`
