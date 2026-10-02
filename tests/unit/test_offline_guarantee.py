"""Negative control for the offline test guarantee (CLAUDE.md rule 15).

`make test` runs with `--disable-socket --allow-unix-socket`. The unix-socket
allowance is needed because starlette's TestClient runs the ASGI app on an
asyncio event loop whose internal self-pipe is an AF_UNIX socketpair.

That allowance must not quietly re-open the network. These tests assert that
outbound network access still fails under exactly the flags `make test` uses,
so "275 tests passed offline" means what it says.

They are skipped when pytest-socket is not active, so a bare `pytest` run does
not report a spurious failure.
"""
from __future__ import annotations

import socket

import pytest

pytestmark = pytest.mark.unit


def _require_blocking() -> None:
    """Skip at RUNTIME if pytest-socket has not patched the socket module.

    This must be checked inside the test body, not in a skipif marker:
    skipif conditions are evaluated at collection time, before pytest-socket
    installs its guard, so a marker-based check always saw "not active" and
    skipped every test here - an offline guarantee that silently never ran.
    """
    if "pytest_socket" not in getattr(socket.socket, "__module__", ""):
        pytest.skip("pytest-socket is not active (run via `make test`)")


def test_tcp_socket_creation_is_blocked():
    _require_blocking()
    with pytest.raises(BaseException) as exc:
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    assert "SocketBlocked" in type(exc.value).__name__, type(exc.value).__name__


def test_dns_resolution_is_blocked():
    _require_blocking()
    with pytest.raises(BaseException) as exc:
        socket.getaddrinfo("example.com", 443)
    assert "SocketBlocked" in type(exc.value).__name__, type(exc.value).__name__


def test_an_http_request_is_blocked():
    """The thing that actually matters: requests cannot reach the network."""
    _require_blocking()
    import requests
    try:
        requests.get("https://example.com", timeout=5)
    except BaseException as exc:
        assert "SocketBlocked" in type(exc).__name__ or isinstance(
            exc, requests.exceptions.RequestException
        ), f"unexpected failure type: {type(exc).__name__}"
    else:
        pytest.fail("an outbound HTTP request succeeded; the network is not blocked")


def test_unix_socketpair_is_still_allowed():
    """The allowance the TestClient depends on."""
    _require_blocking()
    a, b = socket.socketpair()
    try:
        a.send(b"ping")
        assert b.recv(4) == b"ping"
    finally:
        a.close()
        b.close()


def test_no_dotenv_required_for_import():
    """`make test` must pass with no .env present (spec section 10).

    tests/unit/test_settings_lazy.py proves the import path in a scrubbed
    subprocess; this is the cheap in-process assertion that config exposes the
    lazy accessors rather than failing at import.
    """
    import config
    assert hasattr(config, "require_groq_api")
    assert hasattr(config, "MissingSettingError")
    assert config.settings is not None
