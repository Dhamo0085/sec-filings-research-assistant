"""T4-04: the CI workflow and its local mirror cannot drift apart.

A workflow file is not executed by the test suite, so it rots quietly: a step
added to CI and not to the local script means "CI will be green" stops being
checkable before pushing, and a step added locally and not to CI means a check
nobody enforces. These tests compare the two and pin the properties that make
CI meaningful — that it runs with no network, no key and no data directory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MIRROR = REPO_ROOT / "scripts" / "ci_local.sh"

#: The commands both must run, as a recognisable fragment of each.
REQUIRED_STEPS = (
    "ruff check .",
    "scripts/check_repo_hygiene.py --self-test",
    "pytest tests/unit tests/integration",
    "eval.mini_eval",
)


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def _mirror_text() -> str:
    assert MIRROR.is_file(), f"{MIRROR} is missing"
    return MIRROR.read_text(encoding="utf-8")


@pytest.mark.parametrize("fragment", REQUIRED_STEPS)
def test_both_run_every_required_step(fragment):
    assert fragment in _workflow_text(), f"{fragment!r} missing from the workflow"
    assert fragment in _mirror_text(), f"{fragment!r} missing from the local mirror"


def test_the_workflow_is_valid_yaml_with_one_job():
    import yaml

    document = yaml.safe_load(_workflow_text())
    assert document["name"] == "CI"
    # PyYAML reads a bare `on:` key as the boolean True — a real and confusing
    # YAML wart, pinned here so a future reader does not "fix" the workflow.
    triggers = document.get("on", document.get(True))
    assert set(triggers) >= {"push", "pull_request"}
    assert list(document["jobs"]) == ["check"]


def test_the_offline_tests_block_the_network():
    """Without --disable-socket a test that reaches the network passes on a
    machine that happens to be online, which is how an offline guarantee rots."""
    for text in (_workflow_text(), _mirror_text()):
        assert "--disable-socket" in text


def test_ci_never_asks_for_a_secret():
    """CI has no key, and nothing in it may expect one.

    `make test` has to pass with no .env (T1-05); a workflow that quietly
    depends on a secret would make that guarantee untestable in the one place
    it is checked automatically.
    """
    text = _workflow_text()
    assert "secrets." not in text, "the workflow references a repository secret"
    for name in ("GROQ_API", "GEMINI_API_KEY", "OPENAI_API_KEY", "EDGAR_EMAIL"):
        assert name not in text, f"{name} appears in the workflow"


def test_the_mirror_fails_on_the_first_error():
    """`set -euo pipefail`, or a failing step is reported as a pass."""
    assert re.search(r"^set -euo pipefail$", _mirror_text(), re.M)


def test_the_mirror_is_executable():
    import os
    import stat

    mode = os.stat(MIRROR).st_mode
    assert mode & stat.S_IXUSR, "scripts/ci_local.sh is not executable"


def test_the_thresholds_are_stated_where_they_are_enforced():
    """The two numbers P4-08 fixes must be findable from the workflow itself."""
    text = _workflow_text()
    assert "look-ahead" in text.lower()
    assert "100%" in text or "numeric + computed" in text


def test_the_mini_eval_threshold_is_not_vacuous():
    """A threshold over zero items is always met.

    eval/mini_eval.py fails rather than passing when no numeric item ran; this
    is the negative control for that, since the fixtures and the gold set can
    drift apart without either one being wrong on its own.
    """
    from eval.gold.schema import read_jsonl
    from eval.mini_eval import fixture_filings, select

    gold = read_jsonl(REPO_ROOT / "eval" / "gold" / "gold_v1.jsonl")
    items = select(gold, fixture_filings())
    exact = [i for i in items if i["category"] in ("numeric", "computed")]
    assert len(exact) >= 5, (
        f"only {len(exact)} numeric/computed gold items can be answered from "
        "the committed fixtures; the CI threshold is close to vacuous"
    )
