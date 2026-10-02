"""T1-03 — admin and destructive routes must be fail-CLOSED (K7, D13).

v1 guarded every admin route with:

    if settings.admin_token and token != settings.admin_token:
        raise HTTPException(403, ...)

which is fail-OPEN: with the token unset the condition short-circuits and the
request proceeds unauthenticated. Phase 0 proved this was live — GET
/admin/disk-usage with no token returned 400 (the next check) instead of 403,
which is only reachable once the token check has passed.

D13 requires the X-Admin-Token header, a constant-time compare, and an unset
token DISABLING the routes (503) rather than opening them.

This test enumerates app.routes, so a newly added unguarded route fails it.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

# Routes that are deliberately public, as (METHOD, path) pairs.
# Keyed by method on purpose: a path-only allow-list would whitelist a
# destructive DELETE that happens to share a path with a public GET, which is
# precisely the DELETE /chat/sessions/{sid} case (N5).
PUBLIC_ROUTES = {
    ("GET", "/"), ("GET", "/favicon.svg"), ("GET", "/health"),
    ("GET", "/collections"), ("GET", "/ingest/status"), ("POST", "/query"),
    ("GET", "/openapi.json"), ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"), ("GET", "/redoc"),
    # chat read/write for an ordinary user (session privacy is P5-04).
    # NOTE: the chat router carries prefix="/chat", so these are the real
    # paths. Phase 0's static audit printed the decorator arguments only and
    # therefore reported them without the prefix.
    ("POST", "/chat"), ("GET", "/chat/sessions"),
    ("GET", "/chat/sessions/{sid}"), ("GET", "/chat/review"),
}

# Routes P1-06 removes outright: they only served the old hosted deployment.
REMOVED_PATHS = {
    "/admin/restore-data",
    "/admin/migrate-to-remote",
    "/admin/migrate-to-remote/status",
}

# (method, path) pairs that must be guarded by the admin dependency.
EXPECTED_GUARDED = {
    ("GET", "/admin/disk-usage"),
    ("DELETE", "/admin/collection/{name}"),
    ("POST", "/admin/evict-stale-companies"),
    ("POST", "/ingest"),
    ("DELETE", "/chat/sessions/{sid}"),
    ("PATCH", "/chat/sessions/{sid}/turns/{tid}"),
}


def _reload_app(monkeypatch, token):
    """Rebuild the app so settings.admin_token is read fresh."""
    import api.app
    import api.auth
    import config
    if token is None:
        monkeypatch.setattr(config.settings, "admin_token", None)
    else:
        monkeypatch.setattr(config.settings, "admin_token", token)
    # api.auth is deliberately NOT reloaded: other modules already hold its
    # ADMIN_DEPENDENCY, and rebinding it would leave stale references behind.
    importlib.reload(api.app)
    return api.app.app


def _iter_routes(app):
    """Enumerate served routes, recursing into included routers.

    app.routes is not flat: an included router is one nested entry, so a plain
    loop misses every route registered through include_router -- including the
    chat routes this test exists to check (N5).
    """
    from api.auth import iter_api_routes
    for method, path, _route in iter_api_routes(app):
        if method in {"HEAD", "OPTIONS"}:
            continue
        yield method, path


def test_negative_control_enumeration_sees_included_routers():
    """CLAUDE.md rule 15: prove the enumeration finds include_router routes.

    A flat loop over app.routes returns zero /chat paths, so without this
    control every assertion about the chat routes below would pass vacuously.
    """
    import api.app
    flat = {getattr(r, "path", None) for r in api.app.app.routes}
    assert not any(p and p.startswith("/chat") for p in flat), (
        "if app.routes became flat, this control is obsolete -- re-check _iter_routes"
    )
    recursed = {p for _m, p in _iter_routes(api.app.app)}
    assert any(p.startswith("/chat") for p in recursed), (
        "recursive enumeration must find the included chat routes"
    )


# ── route inventory ─────────────────────────────────────────────────────────

def test_no_unguarded_non_public_route_exists(monkeypatch):
    """Fails if a new route appears that is neither public nor guarded."""
    app = _reload_app(monkeypatch, "secret-token")
    from api.auth import route_is_admin_guarded

    unguarded = []
    for method, path in _iter_routes(app):
        if (method, path) in PUBLIC_ROUTES:
            continue
        if not route_is_admin_guarded(app, method, path):
            unguarded.append((method, path))
    assert not unguarded, (
        f"these routes are neither in PUBLIC_PATHS nor admin-guarded: {unguarded}. "
        "Guard them, or add them to PUBLIC_PATHS with a reason."
    )


def test_expected_routes_are_guarded(monkeypatch):
    app = _reload_app(monkeypatch, "secret-token")
    from api.auth import route_is_admin_guarded
    present = set(_iter_routes(app))
    for method, path in EXPECTED_GUARDED:
        assert (method, path) in present, f"{method} {path} is missing from the app"
        assert route_is_admin_guarded(app, method, path), f"{method} {path} is NOT guarded"


def test_old_deployment_routes_are_removed(monkeypatch):
    app = _reload_app(monkeypatch, "secret-token")
    paths = {p for _, p in _iter_routes(app)}
    still_there = REMOVED_PATHS & paths
    assert not still_there, f"these routes should have been removed in P1-06: {still_there}"


def test_no_route_accepts_a_token_in_the_query_string(monkeypatch):
    """Spec section 15: tokens travel in headers, never in URLs."""
    app = _reload_app(monkeypatch, "secret-token")
    offenders = []
    for route in app.routes:
        for dep in getattr(getattr(route, "dependant", None), "query_params", []) or []:
            if "token" in dep.name.lower():
                offenders.append((route.path, dep.name))
    assert not offenders, f"token accepted as a query parameter: {offenders}"


# ── fail-closed behaviour ───────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", sorted(EXPECTED_GUARDED))
def test_unset_token_disables_the_route(monkeypatch, method, path):
    """K7: with ADMIN_TOKEN unset the route must be DISABLED (503), not open."""
    app = _reload_app(monkeypatch, None)
    client = TestClient(app, raise_server_exceptions=False)
    url = path.replace("{name}", "AAPL_2024").replace("{sid}", "s1").replace("{tid}", "1")
    resp = client.request(method, url)
    assert resp.status_code == 503, (
        f"{method} {url} returned {resp.status_code} with no ADMIN_TOKEN set; "
        f"expected 503 (fail-closed). Body: {resp.text[:200]}"
    )


@pytest.mark.parametrize("method,path", sorted(EXPECTED_GUARDED))
def test_wrong_token_is_rejected(monkeypatch, method, path):
    app = _reload_app(monkeypatch, "correct-token")
    client = TestClient(app, raise_server_exceptions=False)
    url = path.replace("{name}", "AAPL_2024").replace("{sid}", "s1").replace("{tid}", "1")
    resp = client.request(method, url, headers={"X-Admin-Token": "wrong-token"})
    assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}, expected 403"


@pytest.mark.parametrize("method,path", sorted(EXPECTED_GUARDED))
def test_missing_header_with_token_configured_is_rejected(monkeypatch, method, path):
    app = _reload_app(monkeypatch, "correct-token")
    client = TestClient(app, raise_server_exceptions=False)
    url = path.replace("{name}", "AAPL_2024").replace("{sid}", "s1").replace("{tid}", "1")
    resp = client.request(method, url)
    assert resp.status_code == 403, f"{method} {url} -> {resp.status_code}, expected 403"


def test_correct_token_passes_the_guard(monkeypatch):
    """The guard must let a correct token through (not a blanket deny)."""
    app = _reload_app(monkeypatch, "correct-token")
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/admin/disk-usage", headers={"X-Admin-Token": "correct-token"})
    assert resp.status_code != 403, "a correct token must not be rejected"
    assert resp.status_code != 503, "a correct token must not report the route disabled"


def test_public_routes_need_no_token(monkeypatch):
    app = _reload_app(monkeypatch, "correct-token")
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/health", "/collections"):
        code = client.get(path).status_code
        # Assert only that auth does not block it. /collections touches the
        # Qdrant store, which may legitimately be busy or absent in CI, so a
        # 500 there is not an auth failure.
        assert code not in (401, 403, 503), f"{path} should be public, got {code}"


# ── constant-time compare ───────────────────────────────────────────────────

def test_compare_is_constant_time(monkeypatch):
    """D13 requires hmac.compare_digest, not ==."""
    import inspect

    import api.auth as auth
    src = inspect.getsource(auth)
    assert "compare_digest" in src, "admin token comparison must use hmac.compare_digest"


def test_negative_control_guard_detection_can_fail(monkeypatch):
    """CLAUDE.md rule 15: prove route_is_admin_guarded can return False.

    Without this, a helper that always returned True would make every guard
    assertion above pass vacuously.
    """
    app = _reload_app(monkeypatch, "secret-token")
    from api.auth import route_is_admin_guarded
    assert route_is_admin_guarded(app, "GET", "/health") is False
    assert route_is_admin_guarded(app, "GET", "/admin/disk-usage") is True
