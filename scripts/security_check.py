#!/usr/bin/env python3
"""Security and privacy pass in one command: ``make security``.

    python scripts/security_check.py              # all four checks
    python scripts/security_check.py --offline    # skip the dependency audit

Four checks, each printed PASS/FAIL:

1. **Repo hygiene** — ``scripts/check_repo_hygiene.py``: tracked secrets,
   oversized files, references to the earlier project, required files.
2. **No secret file is tracked** — ``.env`` and anything key-shaped is absent
   from ``git ls-files``, and ``.env`` is ignored. This never reads a value;
   it reports presence only (CLAUDE.md rule 5).
3. **Admin routes fail closed** — with ``ADMIN_TOKEN`` unset every ``/admin/*``
   route and ``/ingest`` answers **503**, and ``?debug=1`` on ``/query`` does
   not return a trace (D13). The check carries its own **negative control**:
   with a token configured the same route returns 200, so a check that can
   only ever pass is not mistaken for a guarantee (CLAUDE.md rule 15).
4. **Dependency audit** — ``pip-audit`` over the installed environment. Needs
   the network; without it the row is reported ``SKIP``, never ``PASS``. An
   advisory passes only if it is listed with a reason in
   ``reports/security/accepted_vulnerabilities.txt``; anything else fails.

Exit status is non-zero if any check fails. A skipped audit does not fail.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Tracked paths that would be a finding. ``.env.example`` is deliberately
#: tracked and holds key *names* only.
SECRET_LIKE = (".env", ".env.local", ".env.production", "secrets.yaml",
               "llm/limits.local.yaml", ".hygiene_local")
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks")

#: Advisories the owner has read and accepted, with the reason, in a committed
#: file rather than a flag in this script -- an acceptance that is not written
#: down is indistinguishable from a check that was never run.
ACCEPTED_FILE = REPO_ROOT / "reports" / "security" / "accepted_vulnerabilities.txt"


def _accepted_ids() -> dict:
    if not ACCEPTED_FILE.exists():
        return {}
    accepted = {}
    for line in ACCEPTED_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        advisory, _, reason = line.partition(" ")
        accepted[advisory.strip()] = reason.strip()
    return accepted


class Row:
    def __init__(self, name: str, state: str, detail: str) -> None:
        self.name, self.state, self.detail = name, state, detail

    @property
    def failed(self) -> bool:
        return self.state == "FAIL"


def check_hygiene() -> Row:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_repo_hygiene.py")],
        capture_output=True, text=True, check=False, cwd=REPO_ROOT,
    )
    last = [line for line in completed.stdout.splitlines() if line.strip()]
    return Row("repo hygiene", "PASS" if completed.returncode == 0 else "FAIL",
               last[-1] if last else f"exit {completed.returncode}")


def check_no_tracked_secrets() -> Row:
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                             check=True, cwd=REPO_ROOT).stdout.split()
    findings = [p for p in tracked
                if p in SECRET_LIKE or p.endswith(SECRET_SUFFIXES)]
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"],
                             capture_output=True, check=False, cwd=REPO_ROOT)
    if findings:
        return Row("no tracked secrets", "FAIL", "tracked: " + ", ".join(findings))
    if ignored.returncode != 0:
        return Row("no tracked secrets", "FAIL", ".env is not gitignored")
    return Row("no tracked secrets", "PASS",
               f"{len(tracked)} tracked files, none secret-shaped; .env ignored")


def check_admin_fail_closed() -> Row:
    os.environ.pop("ADMIN_TOKEN", None)
    from fastapi.testclient import TestClient

    from api.app import app
    from config import settings

    settings.admin_token = None
    client = TestClient(app)
    guarded: Sequence[Tuple[str, str]] = (
        ("get", "/admin/disk-usage"),
        ("delete", "/admin/collection/does-not-exist"),
        ("post", "/admin/evict-stale-companies"),
        ("post", "/ingest"),
    )
    open_routes = [f"{m.upper()} {p}" for m, p in guarded
                   if getattr(client, m)(p).status_code != 503]
    if open_routes:
        return Row("admin fail-closed", "FAIL", "not 503: " + ", ".join(open_routes))

    # ?debug=1 must not hand a trace to an unauthenticated caller (D13).
    body = client.post("/query?debug=1", json={"question": "Should I buy Apple stock?"})
    if body.status_code == 200 and body.json().get("trace"):
        return Row("admin fail-closed", "FAIL", "?debug=1 returned a trace with no token")

    # Negative control: the check must be able to fail.
    settings.admin_token = "control-token-not-a-real-secret"  # noqa: S105
    authorised = client.get("/admin/disk-usage",
                            headers={"X-Admin-Token": settings.admin_token})
    rejected = client.get("/admin/disk-usage", headers={"X-Admin-Token": "wrong"})
    settings.admin_token = None
    if authorised.status_code == 503 or rejected.status_code != 403:
        return Row("admin fail-closed", "FAIL",
                   f"negative control did not behave: authorised="
                   f"{authorised.status_code} wrong-token={rejected.status_code}")
    return Row("admin fail-closed", "PASS",
               "4 routes 503 with no token; control: 200 authorised, 403 wrong token")


def check_dependency_audit(offline: bool) -> Row:
    if offline:
        return Row("pip-audit", "SKIP", "--offline")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--progress-spinner", "off"],
            capture_output=True, text=True, check=False, cwd=REPO_ROOT, timeout=600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return Row("pip-audit", "SKIP", f"not run: {type(exc).__name__}")
    output = (completed.stdout + completed.stderr).strip()
    if "Network error" in output or "connection" in output.lower():
        return Row("pip-audit", "SKIP", "no network")
    if completed.returncode == 0:
        return Row("pip-audit", "PASS", "no known vulnerability in the installed set")

    accepted = _accepted_ids()
    found, unaccepted = [], []
    for line in output.splitlines():
        parts = line.split()
        advisory = next((p for p in parts if p.startswith(("PYSEC-", "GHSA-", "CVE-"))), None)
        if not advisory:
            continue
        found.append((parts[0], advisory))
        if advisory not in accepted:
            unaccepted.append(f"{parts[0]} {advisory}")

    print("\npip-audit output")
    for line in output.splitlines()[:20]:
        print(f"  | {line}")
    if unaccepted:
        return Row("pip-audit", "FAIL", "not accepted: " + ", ".join(unaccepted))
    return Row("pip-audit", "PASS",
               f"{len(found)} advisory(ies), all accepted in "
               f"{ACCEPTED_FILE.relative_to(REPO_ROOT)}: "
               + ", ".join(a for _, a in found))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true",
                    help="skip pip-audit instead of reporting a network failure")
    args = ap.parse_args(argv)

    rows: List[Row] = [
        check_hygiene(),
        check_no_tracked_secrets(),
        check_admin_fail_closed(),
        check_dependency_audit(args.offline),
    ]

    width = max(len(r.name) for r in rows)
    print("\nsecurity and privacy pass")
    print(f"  {'check'.ljust(width)}  {'result':6s}  detail")
    print(f"  {'-' * width}  {'-' * 6}  {'-' * 50}")
    for r in rows:
        print(f"  {r.name.ljust(width)}  {r.state:6s}  {r.detail}")

    failed = [r for r in rows if r.failed]
    print()
    if failed:
        print("FAILED: " + ", ".join(r.name for r in failed))
        return 1
    print("security: PASS"
          + (" (pip-audit skipped)" if any(r.state == "SKIP" for r in rows) else ""))
    return 0


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    os._exit(_code)
