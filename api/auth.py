"""Admin authentication (P1-06, implements D13, fixes K7).

v1 guarded each admin route inline with::

    if settings.admin_token and token != settings.admin_token:
        raise HTTPException(403, detail="Invalid or missing token")

Three problems, all measured in Phase 0:

1. **Fail-open.** With ``admin_token`` unset the ``and`` short-circuits to
   False and the request proceeds unauthenticated. Phase 0 confirmed this was
   live on the old deployment: ``GET /admin/disk-usage`` with no token returned
   400 (the *next* check in the handler) rather than 403, and 400 is only
   reachable once the token check has passed.
2. **Token in the URL.** The token arrived as ``?token=`` — a query string,
   which lands in server logs, browser history and referrer headers.
3. **Non-constant-time compare.** ``!=`` on a secret leaks timing information.

D13 fixes all three: the ``X-Admin-Token`` header, ``hmac.compare_digest``,
and an unset token **disabling** the routes with 503 instead of opening them.

Usage::

    @app.delete("/admin/collection/{name}", dependencies=[Depends(require_admin)])
    def delete_collection_endpoint(name: str): ...
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException

from config import settings

# The HTTP header name, not a secret. ruff's S105 heuristic flags any
# constant whose name contains "TOKEN".
ADMIN_TOKEN_HEADER = "X-Admin-Token"  # noqa: S105


def require_admin(
    x_admin_token: Optional[str] = Header(default=None, alias=ADMIN_TOKEN_HEADER),
) -> None:
    """FastAPI dependency: allow the request only with a valid admin token.

    Raises:
        HTTPException 503: ``ADMIN_TOKEN`` is not configured, so admin routes
            are disabled. Fail-closed — this is the case v1 left wide open.
        HTTPException 403: a token is configured but the supplied one is
            missing or wrong.
    """
    configured = settings.admin_token
    if not configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Admin routes are disabled because ADMIN_TOKEN is not set. "
                "Set it in the environment to enable them."
            ),
        )
    supplied = x_admin_token or ""
    # compare_digest on equal-length byte strings; it also tolerates the
    # length mismatch case without branching on content.
    if not hmac.compare_digest(supplied.encode("utf-8"), configured.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid or missing admin token.")


def is_admin(x_admin_token: Optional[str]) -> bool:
    """Whether this token is the admin one, without raising (P3-08).

    ``require_admin`` guards a whole route. ``?debug=1`` on ``/query`` is a
    different shape: the route stays public and only the ``trace`` field is
    privileged, so the check has to return a boolean rather than reject the
    request. Same constant-time comparison, same fail-closed rule — an unset
    ``ADMIN_TOKEN`` means nobody is an admin (D13).
    """
    configured = settings.admin_token
    if not configured:
        return False
    return hmac.compare_digest((x_admin_token or "").encode("utf-8"),
                               configured.encode("utf-8"))


# The dependency object, so route declarations and the test that enumerates
# routes both refer to the same callable.
ADMIN_DEPENDENCY = Depends(require_admin)


def iter_api_routes(app):
    """Yield every (method, path, route) the app actually serves.

    ``app.routes`` is NOT a flat list. In this FastAPI version an included
    router appears as a single nested ``_IncludedRouter`` entry whose own
    routes hang off ``original_router.routes``, so a naive loop over
    ``app.routes`` silently misses every route added via ``include_router`` —
    which is exactly the set that contained the unauthenticated chat routes
    (spec issue N5). Recursing is what makes the T1-03 inventory trustworthy.
    """
    seen = set()

    def _walk(container, depth=0):
        if depth > 5:
            return
        for route in getattr(container, "routes", []) or []:
            sub = getattr(route, "original_router", None)
            if sub is not None:
                _walk(sub, depth + 1)
                continue
            path = getattr(route, "path", None)
            if path is None:
                continue
            for method in sorted(getattr(route, "methods", set()) or set()):
                key = (method, path, id(route))
                if key in seen:
                    continue
                seen.add(key)
                yield_route = (method, path, route)
                results.append(yield_route)

    results = []
    _walk(app)
    return results


def route_is_admin_guarded(app, method: str, path: str) -> bool:
    """Whether (method, path) carries the admin dependency.

    Used by T1-03 to enumerate routes and fail if a new unguarded route
    appears. Returns False for unknown routes and for public ones, so the test
    cannot pass vacuously.
    """
    method = method.upper()
    for m, p, route in iter_api_routes(app):
        if p != path or m != method:
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            return False
        if _dependant_requires_admin(dependant):
            return True
        # An included router can carry the dependency at router level rather
        # than on the individual route.
        for dep in getattr(route, "dependencies", None) or []:
            if _is_require_admin(getattr(dep, "dependency", None)):
                return True
        return False
    return False


def _is_require_admin(call) -> bool:
    """Identify require_admin by module+name, not identity.

    importlib.reload(api.auth) rebinds require_admin, while modules that
    imported ADMIN_DEPENDENCY earlier still hold the previous function object.
    An `is` comparison then reports a guarded route as unguarded, which would
    be a false alarm in T1-03 — or, if the polarity were reversed, a false pass.
    """
    return (
        callable(call)
        and getattr(call, "__name__", "") == "require_admin"
        and getattr(call, "__module__", "") == "api.auth"
    )


def _dependant_requires_admin(dependant) -> bool:
    """Depth-first search for require_admin in a dependency tree."""
    if _is_require_admin(getattr(dependant, "call", None)):
        return True
    return any(
        _dependant_requires_admin(sub)
        for sub in (getattr(dependant, "dependencies", None) or [])
    )
