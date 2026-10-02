"""
Phase 0 / Step 1.6 — K7: route exposure, STATIC analysis only.

Parses api/app.py and api/chat.py with `ast`.  No route is ever called.
For each route: method, path, and whether the handler body references
settings.admin_token (directly or via a dependency helper it calls).
"""
from __future__ import annotations

import ast
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGETS = [REPO_ROOT / "api" / "app.py", REPO_ROOT / "api" / "chat.py"]
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _names_in(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Name):
            out.add(n.id)
    return out


def _calls_in(node: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def collect(path: Path) -> list[dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    # map helper-function name -> whether its body mentions admin_token
    helpers: dict[str, bool] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            helpers[node.name] = "admin_token" in _names_in(node)

    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            f = dec.func
            if not (isinstance(f, ast.Attribute) and f.attr in HTTP_METHODS):
                continue
            router_obj = f.value.id if isinstance(f.value, ast.Name) else "?"
            route_path = ""
            if dec.args and isinstance(dec.args[0], ast.Constant):
                route_path = dec.args[0].value

            direct = "admin_token" in _names_in(node)
            via_helper = any(helpers.get(c, False) for c in _calls_in(node))
            # a FastAPI dependency in the signature that itself checks the token
            deps = _names_in(node.args) if node.args else set()
            via_dep = any(helpers.get(d, False) for d in deps)
            checks = direct or via_helper or via_dep

            rows.append(
                {
                    "file": str(path.relative_to(REPO_ROOT)),
                    "method": f.attr.upper(),
                    "router": router_obj,
                    "path": route_path,
                    "handler": node.name,
                    "line": node.lineno,
                    "checks_admin_token": checks,
                    "check_mechanism": (
                        "direct" if direct else "called-helper" if via_helper
                        else "dependency" if via_dep else "none"
                    ),
                }
            )
    return rows


def main() -> int:
    rows: list[dict] = []
    for t in TARGETS:
        if t.exists():
            rows.extend(collect(t))
        else:
            print(f"MISSING: {t}")
    rows.sort(key=lambda r: (r["file"], r["path"]))

    out_dir = REPO_ROOT / "reports" / "phase0"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "k7_routes.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    unauth = [r for r in rows if not r["checks_admin_token"]]
    admin_unauth = [r for r in unauth if r["path"].startswith("/admin") or r["path"] == "/ingest"]
    summary = {
        "total_routes": len(rows),
        "routes_checking_admin_token": len(rows) - len(unauth),
        "routes_without_any_token_check": len(unauth),
        "sensitive_routes_without_token_check": admin_unauth,
        "routes": rows,
    }
    (out_dir / "k7_routes.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"{'METHOD':7s} {'PATH':34s} {'TOKEN?':8s} {'MECHANISM':14s} HANDLER")
    for r in rows:
        print(f"{r['method']:7s} {r['path']:34s} "
              f"{('YES' if r['checks_admin_token'] else 'NO'):8s} "
              f"{r['check_mechanism']:14s} {r['handler']} ({r['file']}:{r['line']})")
    print(f"\nTotal routes: {len(rows)};  without any admin_token check: {len(unauth)}")
    print("Sensitive (/admin/* or /ingest) WITHOUT token check:")
    for r in admin_unauth:
        print(f"  {r['method']} {r['path']}  -> {r['handler']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
