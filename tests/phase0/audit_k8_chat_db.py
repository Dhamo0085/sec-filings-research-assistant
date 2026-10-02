"""
Phase 0 / Step 1.7 — K8: committed chat history database.

Reports schema and row counts ONLY.  No chat content is read or printed.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "data" / "chat_history.db"
OUT = REPO_ROOT / "reports" / "phase0" / "k8_chat_db.json"


def _git(*args) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # pragma: no cover
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> None:
    res: dict = {"db_path": str(DB.relative_to(REPO_ROOT)), "exists": DB.exists()}

    rc, out = _git("rev-parse", "--is-inside-work-tree")
    res["git_available"] = rc == 0
    res["git_probe_output"] = out
    if rc == 0:
        res["git_ls_files_data"] = _git("ls-files", "data")[1].splitlines()[:20]
        res["git_log_for_db"] = _git("log", "--oneline", "--", "data/chat_history.db")[1].splitlines()[:20]
    else:
        res["note"] = (
            "This checkout is NOT a git repository (no .git directory) — it is an "
            "unpacked source archive. Tracking status cannot be determined here; "
            "the file's presence in the distributed archive is itself the finding."
        )

    if DB.exists():
        res["size_bytes"] = DB.stat().st_size
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            res["tables"] = {}
            for t in tables:
                n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
                res["tables"][t] = {"row_count": n, "columns": cols}
            # aggregate only — never content
            if "turns" in res["tables"] and "is_correct" in res["tables"]["turns"]["columns"]:
                rows = con.execute(
                    "SELECT is_correct, COUNT(*) FROM turns GROUP BY is_correct").fetchall()
                res["turns_is_correct_distribution"] = {str(k): v for k, v in rows}
        finally:
            con.close()

    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
