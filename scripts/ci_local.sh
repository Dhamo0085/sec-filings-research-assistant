#!/usr/bin/env bash
# The local mirror of .github/workflows/ci.yml (P4-08, T4-04).
#
# Same steps, same order, same thresholds, so "CI will be green" is something
# that can be checked before pushing rather than discovered afterwards. A test
# asserts that this file and the workflow run the same set of steps.
#
#   ./scripts/ci_local.sh
#
# Uses .venv if it exists, like the Makefile.
#
# ONE THING THIS MIRROR CANNOT REPRODUCE: it runs in the repository, where your
# .env exists and your model caches are warm, so a test that silently depends on
# either still passes here. CI has neither. That gap is not theoretical — the
# first CI run failed 15 tests that pass locally: seven needed `edgar_email`
# from .env, seven imported a module that DOWNLOADS a tokenizer at import, and
# one read the developer's real .cache/edgar because a monkeypatch had no effect
# against a default bound at import. All three are fixed; the gap remains, so
# treat a green mirror as "probably green in CI", not as a guarantee.
#
# To reproduce CI's isolation locally:
#   cd "$(mktemp -d)" && PYTHONPATH=<repo> HOME="$(mktemp -d)" \
#     <repo>/.venv/bin/python -m pytest <repo>/tests/unit <repo>/tests/integration \
#     -m "not live and not slow" --disable-socket --allow-unix-socket
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Lint"
"$PY" -m ruff check .

step "Repository hygiene"
"$PY" scripts/check_repo_hygiene.py --self-test
"$PY" scripts/check_repo_hygiene.py

step "Offline tests"
"$PY" -m pytest tests/unit tests/integration \
  -m "not live and not slow" \
  --disable-socket --allow-unix-socket \
  --junitxml=reports/ci/junit.xml

step "Gold set is reproducible from its plan"
"$PY" -c "import sys; sys.path.insert(0,'.'); from pathlib import Path; from eval.gold.schema import read_jsonl, validate; p=validate(read_jsonl(Path('eval/gold/gold_v1.jsonl'))); print('\n'.join(str(x) for x in p)); sys.exit(1 if p else 0)"

step "Offline mini-evaluation"
"$PY" -m eval.mini_eval --verbose

printf '\n\033[1;32mCI steps passed.\033[0m\n'
