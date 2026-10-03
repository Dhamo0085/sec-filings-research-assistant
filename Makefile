# Financial_RAG v2 — developer entry points (P1-02).
# Targets are the ones named in CLAUDE.md; keep them working.
#
# Everything runs inside a local virtualenv at $(VENV) so a clean clone needs
# only `make setup`.

VENV    ?= .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
PYTEST  := $(VENV)/bin/pytest
RUFF    := $(VENV)/bin/ruff
# The phase whose reports/ directory test artifacts are written to. Bumped at
# each phase so a later run cannot overwrite an earlier phase's committed
# junit.xml/coverage.xml (a Phase 1 runner overwrote a committed Phase 0
# artifact that way; see reports/phase1/REPORT.md section 6).
PHASE   ?= phase3
comma   := ,
HOST    ?= 127.0.0.1
PORT    ?= 8000
BASE_URL ?= http://localhost:$(PORT)

.DEFAULT_GOAL := help
.PHONY: help setup test test-live lint fmt ingest index catalog facts eval-smoke eval-full up hygiene clean

help:   ## Show the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip

setup: $(VENV)/bin/python   ## Create the venv and install serving + dev dependencies
	$(PIP) install --quiet -r requirements-dev.txt
	@echo "setup complete: $$($(PY) --version) in $(VENV)"

setup-eval: $(VENV)/bin/python   ## Additionally install the optional RAGAS stack
	$(PIP) install --quiet -r requirements-eval.txt

# --allow-unix-socket is required, not a loophole: starlette's TestClient runs
# the ASGI app on an asyncio event loop, and the loop's internal self-pipe is an
# AF_UNIX socketpair. Blocking it makes every API test fail with a 500 rather
# than exercising the app. AF_UNIX cannot reach the network, so the intent -
# no network in offline tests - still holds, and
# tests/unit/test_offline_guarantee.py asserts that network access really is
# blocked under these flags.
test: ## Offline unit + integration tests (network blocked; must pass with no .env)
	$(PYTEST) tests/unit tests/integration -m "not live and not slow" \
	  --disable-socket --allow-unix-socket \
	  --cov=. --cov-report=term-missing --cov-report=xml:reports/$(PHASE)/tests/coverage.xml \
	  --junitxml=reports/$(PHASE)/tests/junit.xml

test-live: ## Tests that need the network or a real LLM
	$(PYTEST) -m "live" --no-cov

lint: ## ruff check
	$(RUFF) check .

fmt: ## ruff format
	$(RUFF) format .

hygiene: ## Repo hygiene scan: secrets, large files, third-party references (P1-13)
	$(PY) scripts/check_repo_hygiene.py

ingest: ## Download + parse + chunk + index the bundled companies
	$(PY) run_ingestion.py

catalog: ## Build the filing catalog from SEC EDGAR submissions (P1-09)
	$(PY) -m catalog.build

index: ## Stream-index the chunk corpus one collection at a time (P2-00c)
	$(PY) -m ingestion.indexer $(if $(ONLY),--only $(ONLY),)

facts: ## Build the iXBRL facts store from the catalog (P2-04)
	$(PY) -m facts.build $(if $(ONLY),$(foreach t,$(subst $(comma), ,$(ONLY)),--ticker $(t)),)

eval-smoke: ## Small evaluation run (Phase 4)
	@echo "eval-smoke: not implemented until Phase 4 (eval/runner.py, task P4-04)." && exit 1

eval-full: ## Full gold-set evaluation (Phase 4)
	@echo "eval-full: not implemented until Phase 4 (eval/runner.py, task P4-05)." && exit 1

up: ## Run the API locally
	$(VENV)/bin/uvicorn api.app:app --host $(HOST) --port $(PORT)

smoke: ## Read-only smoke check against a running instance (P1-11)
	$(PY) scripts/smoke.py --base-url $(BASE_URL)

clean: ## Remove caches and build artifacts (keeps data/ and .cache/)
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
