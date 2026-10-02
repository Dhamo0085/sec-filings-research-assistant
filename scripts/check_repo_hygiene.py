#!/usr/bin/env python3
"""Repo hygiene checks in one reliable command (P1-13). Run via `make hygiene`.

Three checks over the files git actually tracks:

1. **Key-like strings** — report ``file:line`` only, never the matched text.
2. **Large files** — anything over 5 MB that would be committed.
3. **Third-party references** — deployment URLs or the old repository name, in
   any tracked file outside the frozen historical records.

Why this exists as a script rather than a shell pipeline: during Step 0 two
``xargs``-based shell checks silently produced false results. One printed a
garbled match count from batching; the other reported "(no matches)" because a
non-zero exit from the final ``xargs`` batch triggered an ``||`` fallback,
hiding 28 real hits. Trusting that would have shipped vendor references into
the new repository. CLAUDE.md rule 15 came out of it: prefer a script, and
prove the check can fail before trusting a pass.

Hence ``--self-test``: it plants a known key-like string, an oversized file and
a third-party URL in a scratch directory and asserts each check catches them.
A check that cannot fail is not evidence.

Exit codes: 0 all clear · 1 findings · 2 could not run (e.g. not a git repo).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, NamedTuple, Sequence

MAX_FILE_BYTES = 5 * 1024 * 1024

# Deliberately narrow: each pattern matches a real provider's key shape, so a
# hit is worth stopping for rather than a plausible-looking random string.
KEY_PATTERNS = {
    "groq": re.compile(rb"gsk_[A-Za-z0-9]{20,}"),
    "google": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "openai": re.compile(rb"sk-[A-Za-z0-9]{20,}"),
    "github_classic": re.compile(rb"ghp_[A-Za-z0-9]{30,}"),
    "github_pat": re.compile(rb"github_pat_[A-Za-z0-9_]{30,}"),
    "private_key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

# Third-party DEPLOYMENT references (spec test T1-12, D17).
#
# Scope note: "Financial_RAG" is deliberately NOT matched. It is this
# project's own working name — CLAUDE.md, docs/PROJECT_SPEC.md and the
# Makefile all use it in their titles — so matching it would flag the owner's
# own documents. The old GitHub repository's actual name/URL is not known to
# this script; see the Phase 1 report's owner questions. Add it here when the
# owner supplies it.
REFERENCE_PATTERNS = {
    "railway_app_url": re.compile(rb"[A-Za-z0-9.-]*\.up\.railway\.app", re.I),
    "railway_config": re.compile(rb"\brailway\.toml\b", re.I),
    "render_app_url": re.compile(rb"[A-Za-z0-9.-]*\.onrender\.com", re.I),
    "heroku_app_url": re.compile(rb"[A-Za-z0-9.-]*\.herokuapp\.com", re.I),
}

# Frozen historical records: Phase 0 and Step 0 deliberately document the old
# deployment, so they are exempt from the reference check (and only that one).
REFERENCE_EXEMPT_PREFIXES = (
    "reports/",
    "eval/phase0/",
    "tests/phase0/",
    "docs/BOOTSTRAP.md",
    "docs/PROJECT_SPEC.md",
    "docs/DECISIONS.md",
    "scripts/check_repo_hygiene.py",   # this file names the patterns
)


class Finding(NamedTuple):
    check: str
    location: str      # "path" or "path:line" — never the matched content
    detail: str


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120, check=False
    )


def tracked_files(root: Path) -> List[str]:
    proc = _git(["ls-files", "-z"], root)
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.split("\0") if p]


def check_keys(root: Path, files: Iterable[str]) -> List[Finding]:
    out: List[Finding] = []
    for rel in files:
        path = root / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:          # binary
            continue
        for lineno, line in enumerate(data.split(b"\n"), 1):
            for name, pattern in KEY_PATTERNS.items():
                if pattern.search(line):
                    out.append(Finding("secret", f"{rel}:{lineno}", f"pattern={name}"))
    return out


def check_large_files(root: Path, files: Iterable[str]) -> List[Finding]:
    out: List[Finding] = []
    for rel in files:
        try:
            size = (root / rel).stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            out.append(Finding("large-file", rel, f"{size / 1e6:.1f} MB > 5 MB"))
    return out


def check_references(root: Path, files: Iterable[str]) -> List[Finding]:
    out: List[Finding] = []
    for rel in files:
        if rel.startswith(REFERENCE_EXEMPT_PREFIXES):
            continue
        path = root / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:4096]:
            continue
        for lineno, line in enumerate(data.split(b"\n"), 1):
            for name, pattern in REFERENCE_PATTERNS.items():
                if pattern.search(line):
                    out.append(Finding("reference", f"{rel}:{lineno}", f"pattern={name}"))
    return out


def check_required_files(root: Path, files: Iterable[str]) -> List[Finding]:
    """LICENSE and NOTICE must stay tracked (D17 provenance, T1-12)."""
    tracked = set(files)
    return [
        Finding("required-file", name, "missing from the tracked tree")
        for name in ("LICENSE", "NOTICE")
        if name not in tracked
    ]


def check_untracked_runtime_files(root: Path, files: Iterable[str]) -> List[Finding]:
    """.env and the chat database must never be tracked (T1-10, T1-12)."""
    tracked = set(files)
    out = []
    for name in (".env", "data/chat_history.db"):
        if name in tracked:
            out.append(Finding("must-not-track", name, "is tracked but must not be"))
    for rel in tracked:
        if rel.startswith("data/chat_history.db"):
            out.append(Finding("must-not-track", rel, "chat history must not be tracked"))
    return out


CHECKS = (
    ("secrets", check_keys),
    ("large files", check_large_files),
    ("third-party references", check_references),
    ("required files", check_required_files),
    ("untracked runtime files", check_untracked_runtime_files),
)


def run(root: Path) -> List[Finding]:
    files = tracked_files(root)
    findings: List[Finding] = []
    for _label, fn in CHECKS:
        findings.extend(fn(root, files))
    return findings


# ── self-test (negative controls) ───────────────────────────────────────────

def self_test() -> int:
    """Plant a known violation per check and assert each is caught."""
    print("hygiene self-test: planting known violations in a scratch repo")
    failures = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(["init", "-q"], root)
        _git(["config", "user.email", "t@example.com"], root)
        _git(["config", "user.name", "t"], root)
        (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
        (root / "NOTICE").write_text("provenance\n", encoding="utf-8")
        (root / "clean.py").write_text("x = 1\n", encoding="utf-8")

        # one planted violation per check
        planted_key = "gsk_" + ("A" * 32)
        (root / "leak.py").write_text(f'KEY = "{planted_key}"\n', encoding="utf-8")
        (root / "big.bin").write_bytes(b"\x01" * (MAX_FILE_BYTES + 1024))
        (root / "stale.md").write_text(
            "see https://example-app.up.railway.app/ for the old deploy\n", encoding="utf-8"
        )
        _git(["add", "-A"], root)

        findings = run(root)
        by_check = {}
        for f in findings:
            by_check.setdefault(f.check, []).append(f.location)

        expectations = {
            "secret": "leak.py:1",
            "large-file": "big.bin",
            "reference": "stale.md:1",
        }
        for check, where in expectations.items():
            hits = by_check.get(check, [])
            if any(h.startswith(where) for h in hits):
                print(f"  PASS  {check:14s} caught {where}")
            else:
                print(f"  FAIL  {check:14s} did NOT catch {where} (saw {hits})")
                failures.append(check)

        # the clean file must not be reported
        if any("clean.py" in f.location for f in findings):
            print("  FAIL  clean.py was reported (false positive)")
            failures.append("false-positive")
        else:
            print("  PASS  clean file not reported")

        # removing the planted files must yield a clean run
        for name in ("leak.py", "big.bin", "stale.md"):
            (root / name).unlink()
        _git(["add", "-A"], root)
        remaining = run(root)
        if remaining:
            print(f"  FAIL  clean tree still reported findings: {remaining}")
            failures.append("clean-tree")
        else:
            print("  PASS  clean tree reports nothing")

        # the required-file check must fire when LICENSE is gone
        (root / "LICENSE").unlink()
        _git(["add", "-A"], root)
        if any(f.check == "required-file" for f in run(root)):
            print("  PASS  required-file  caught missing LICENSE")
        else:
            print("  FAIL  required-file  did NOT catch missing LICENSE")
            failures.append("required-file")

    if failures:
        print(f"\nself-test FAILED for: {', '.join(failures)}")
        return 1
    print("\nself-test passed: every check demonstrably fails on a planted violation")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true",
                    help="prove each check can fail, then exit")
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    try:
        findings = run(root)
    except RuntimeError as exc:
        print(f"could not run: {exc}", file=sys.stderr)
        return 2

    files = tracked_files(root)
    print(f"repo hygiene: {len(files)} tracked files under {root}")
    for label, fn in CHECKS:
        hits = fn(root, files)
        status = f"{len(hits)} finding(s)" if hits else "clean"
        print(f"  {label:26s} {status}")
        for f in hits:
            print(f"      {f.location}  [{f.detail}]")

    if findings:
        print(f"\n{len(findings)} finding(s). Matched content is never printed.")
        return 1
    print("\nall checks clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
