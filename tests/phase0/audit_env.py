"""Phase 0 / Step 0.1 + 0.3 + 0.4 — environment and inventory. Secrets are never printed."""
from __future__ import annotations

import importlib, json, os, platform, subprocess, sys, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "reports" / "phase0" / "environment.json"

PKGS = ["qdrant_client", "fastembed", "groq", "ragas", "pydantic", "pydantic_settings",
        "lxml", "bs4", "fastapi", "uvicorn", "nltk", "tiktoken", "pandas", "numpy",
        "sec_edgar_downloader", "loguru", "requests", "pytest"]


def versions(python: str) -> dict:
    code = ("import importlib,json;out={}\n"
            f"for m in {PKGS!r}:\n"
            "    try:\n"
            "        mod=importlib.import_module(m); out[m]=getattr(mod,'__version__',None) or 'installed(no __version__)'\n"
            "    except Exception as e: out[m]=f'MISSING ({type(e).__name__})'\n"
            "print(json.dumps(out))")
    try:
        p = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
        return json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else {"error": p.stderr[-400:]}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def env_flag(name: str) -> str:
    """Report ONLY whether a secret is set — never its value."""
    v = os.environ.get(name)
    dotenv = REPO_ROOT / ".env"
    in_dotenv = False
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(f"{name}="):
                in_dotenv = bool(line.split("=", 1)[1].strip())
    return ("set in environment" if v else
            "set in .env" if in_dotenv else
            "NOT SET")


def dir_stats(p: Path) -> dict:
    if not p.exists():
        return {"exists": False}
    files = [f for f in p.rglob("*") if f.is_file()]
    return {"exists": True, "file_count": len(files),
            "total_bytes": sum(f.stat().st_size for f in files)}


def main() -> None:
    venv_py = REPO_ROOT / ".venv-phase0" / "bin" / "python"
    res = {
        "os": {"platform": platform.platform(), "system": platform.system(),
               "machine": platform.machine(), "python_running_this": sys.version},
        "note_on_versions": (
            "The project's own dependencies are NOT installed in this checkout. "
            "`.venv-phase0` is a Phase 0-only virtualenv holding the minimum needed to "
            "run the static/unit audits (it is NOT the project runtime)."
        ),
        "project_interpreter_packages": versions(sys.executable),
        "phase0_venv_packages": versions(str(venv_py)) if venv_py.exists() else {"error": "absent"},
        "requirements_txt": (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines(),
        "secrets_present": {
            "groq_api": env_flag("groq_api"),
            "edgar_email": env_flag("edgar_email"),
            "admin_token": env_flag("admin_token"),
            "dotenv_file_exists": (REPO_ROOT / ".env").exists(),
        },
        "git": {},
        "data_inventory": {
            name: dir_stats(REPO_ROOT / "data" / name)
            for name in ["raw", "parsed", "chunks", "qdrant", "test_sets", "pending_index"]
        },
        "data_root": dir_stats(REPO_ROOT / "data"),
    }

    try:
        p = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
        res["git"] = {"is_repo": p.returncode == 0, "message": (p.stdout + p.stderr).strip()}
    except Exception as exc:
        res["git"] = {"is_repo": False, "message": f"{type(exc).__name__}: {exc}"}

    # 0.4 import / warm-up timing
    t0 = time.perf_counter()
    try:
        sys.path.insert(0, str(REPO_ROOT))
        importlib.import_module("query")
        res["import_query"] = {"ok": True, "seconds": round(time.perf_counter() - t0, 2)}
    except Exception as exc:
        res["import_query"] = {"ok": False, "seconds": round(time.perf_counter() - t0, 2),
                               "error": f"{type(exc).__name__}: {exc}"}

    # Qdrant collections (local)
    try:
        from retrieval.vector_store import list_collections
        res["qdrant_local_collections"] = list(list_collections())
    except Exception as exc:
        res["qdrant_local_collections"] = {"error": f"{type(exc).__name__}: {exc}"}

    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "requirements_txt"}, indent=2)[:3000])


if __name__ == "__main__":
    main()
