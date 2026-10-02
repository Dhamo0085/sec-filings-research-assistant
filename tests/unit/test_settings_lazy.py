"""T1-05 — importing the app must succeed with no environment and no .env file.

v1 built `Settings()` at import time with `groq_api` and `edgar_email` as
required fields, so `import config` raised pydantic ValidationError whenever
.env was absent. Phase 0 measured this (reports/phase0/environment.json:
"import_query.ok = false") and the spec tracks it as N4.

These tests run the import in a SUBPROCESS with a scrubbed environment and a
working directory that contains no .env, which is the only faithful way to
test import-time behaviour (an in-process import would be served from
sys.modules and would also see the repo's real .env via the CWD).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every env var that could satisfy a required setting, scrubbed so the
# subprocess genuinely has nothing.
SCRUB = [
    "groq_api", "GROQ_API_KEY", "GEMINI_API_KEY", "edgar_email", "EDGAR_EMAIL",
    "ADMIN_TOKEN", "admin_token", "QDRANT_URL", "QDRANT_API_KEY",
    "GENERATION_MODEL", "ROUTING_MODEL", "LLM_PROVIDERS",
]


def _run_in_clean_env(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    # Keep the repo importable, but run from a directory with no .env so
    # pydantic-settings' env_file lookup finds nothing.
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("PYTHONHOME", None)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180,
    )


def test_env_file_really_absent(tmp_path):
    """Negative control (CLAUDE.md rule 15): prove the harness isolates .env.

    If this fails, the other tests in this file prove nothing.
    """
    assert not (tmp_path / ".env").exists()
    r = _run_in_clean_env(
        """
        import os
        from pathlib import Path
        print("ENV_FILE_PRESENT", Path(".env").exists())
        print("GROQ_IN_ENV", bool(os.environ.get("groq_api") or os.environ.get("GROQ_API_KEY")))
        """,
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert "ENV_FILE_PRESENT False" in r.stdout
    assert "GROQ_IN_ENV False" in r.stdout


def test_import_config_without_env_succeeds(tmp_path):
    r = _run_in_clean_env(
        """
        import config
        print("CONFIG_OK", config.settings is not None)
        print("GROQ_API_IS_NONE", config.settings.groq_api is None)
        """,
        tmp_path,
    )
    assert r.returncode == 0, f"importing config without .env failed:\n{r.stderr[-2000:]}"
    assert "CONFIG_OK True" in r.stdout
    assert "GROQ_API_IS_NONE True" in r.stdout


def test_import_query_without_env_succeeds(tmp_path):
    r = _run_in_clean_env(
        """
        import query
        print("QUERY_OK", callable(query.ask))
        """,
        tmp_path,
    )
    assert r.returncode == 0, f"importing query without .env failed:\n{r.stderr[-2000:]}"
    assert "QUERY_OK True" in r.stdout


def test_import_app_without_env_succeeds(tmp_path):
    """T1-05 proper: the FastAPI app object must be constructible with no env."""
    r = _run_in_clean_env(
        """
        from api.app import app
        print("APP_OK", app.title is not None)
        print("ROUTES", len(app.routes) > 0)
        """,
        tmp_path,
    )
    assert r.returncode == 0, f"importing api.app without .env failed:\n{r.stderr[-2000:]}"
    assert "APP_OK True" in r.stdout
    assert "ROUTES True" in r.stdout


def test_missing_secret_raises_typed_error_at_use(tmp_path):
    """Import must not fail, but *using* a missing secret must fail loudly."""
    r = _run_in_clean_env(
        """
        import config
        try:
            config.require_groq_api()
        except config.MissingSettingError as exc:
            print("TYPED_ERROR", exc.setting_name)
        else:
            print("NO_ERROR_RAISED")
        """,
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert "TYPED_ERROR groq_api" in r.stdout


@pytest.mark.parametrize(
    "name,expected",
    [
        ("rate_limit_per_min", 20),
        ("max_question_chars", 500),
        ("facts_filings_per_company", 5),
        ("enable_facts", True),
        ("enable_asof", True),
        ("enable_abstain_gate", True),
        ("retrieval_mode", "hybrid"),
        ("enable_focus_boost", True),
        ("enable_rerank", True),
        ("facts_llm_phrasing", False),
    ],
)
def test_section8_defaults(name, expected, tmp_path):
    """Spec section 8 defaults must be present and correct with no env set."""
    r = _run_in_clean_env(
        f"""
        import config
        print("VALUE", repr(getattr(config.settings, {name!r})))
        """,
        tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert f"VALUE {expected!r}" in r.stdout
