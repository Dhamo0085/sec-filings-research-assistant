"""Refuse to cache an SEC error page as if it were data (P3-00b).

The trap this closes is in ``.cache/company_tickers.json``, which holds this,
not JSON::

    <title>SEC.gov | Request Rate Threshold Exceeded</title>
    ...
    <h1>Automated access to our sites must comply with SEC.gov's Privacy
        and Security Policy.</h1>

SEC serves that page with **HTTP 200**, so ``resp.status_code != 200`` does not
catch it and neither does ``raise_for_status()``. Two fetchers then wrote the
body straight to their cache:

* ``catalog/build.py::_fetch_json`` wrote ``resp.text`` *before* calling
  ``resp.json()``, so the error page landed in ``.cache/edgar/`` and the
  exception was raised afterwards, uncaught.
* ``facts/documents.py::_get`` writes ``resp.content`` for any 200, so the
  error page would be cached as a filing document and only fail later, during
  iXBRL extraction, far from its cause.

A cache that stores failures as data is worse than no cache: the bad entry is
indistinguishable from a real response, it survives restarts, and the next run
reads it without going near the network, so the failure looks permanent and
looks like a parsing bug. Both fetchers now validate before they write, and the
rate-limit case is reported as what it is.

The module has no dependency on ``requests`` so it stays unit-testable offline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, Union

# Phrases that only appear on SEC's throttle/denial interstitial. Matching the
# <h1> as well as the <title> keeps detection working if one of them is
# reworded, and both are specific enough that a real filing cannot contain them
# in the first 4 KB.
_SEC_ERROR_MARKERS = (
    re.compile(rb"Request Rate Threshold Exceeded", re.IGNORECASE),
    re.compile(rb"Automated access to our sites must comply", re.IGNORECASE),
    re.compile(rb"Your Request Originates from an Undeclared Automated Tool",
               re.IGNORECASE),
)

# Only the head of the body is scanned: the markers are in <head>/<h1>, and a
# filing document can be tens of megabytes.
_SCAN_BYTES = 4096


def _as_bytes(body: Union[bytes, str]) -> bytes:
    return body.encode("utf-8", errors="replace") if isinstance(body, str) else body


def sec_error_reason(body: Union[bytes, str]) -> Optional[str]:
    """The reason this response is an SEC error page, or ``None`` if it is not.

    Detection is by content, not status code, because SEC returns the throttle
    page with HTTP 200.
    """
    head = _as_bytes(body)[:_SCAN_BYTES]
    for marker in _SEC_ERROR_MARKERS:
        if marker.search(head):
            return marker.pattern.decode("utf-8")
    return None


def looks_like_html(body: Union[bytes, str]) -> bool:
    """Whether the response opens as an HTML document rather than JSON."""
    head = _as_bytes(body)[:_SCAN_BYTES].lstrip()
    return head[:9].lower() == b"<!doctype" or head[:5].lower() == b"<html"


class SecResponseError(RuntimeError):
    """An SEC response that must not be cached (error page or wrong type)."""


def validated_json(body: Union[bytes, str], *, url: str = "") -> dict:
    """Parse an SEC JSON response, refusing error pages and HTML.

    Raises ``SecResponseError`` with a reason naming the URL. Callers write to
    the cache only after this returns, so a rate-limited fetch leaves no
    poisoned entry behind.
    """
    where = f" from {url}" if url else ""
    reason = sec_error_reason(body)
    if reason is not None:
        raise SecResponseError(
            f"SEC returned its error page{where} (matched {reason!r}); "
            "not caching it. This is almost always rate limiting — slow down "
            "and retry."
        )
    if looks_like_html(body):
        raise SecResponseError(f"expected JSON{where} but got an HTML document; not caching it")
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SecResponseError(f"expected JSON{where} but could not parse it: {exc}") from exc
    if not isinstance(data, dict):
        raise SecResponseError(f"expected a JSON object{where} but got {type(data).__name__}")
    return data


def validated_document(body: bytes, *, url: str = "") -> bytes:
    """Return a filing document's bytes, refusing SEC error pages.

    Filing documents are HTML/XML, so ``looks_like_html`` is not a defect here
    — only the error-page markers and an empty body are.
    """
    where = f" from {url}" if url else ""
    reason = sec_error_reason(body)
    if reason is not None:
        raise SecResponseError(
            f"SEC returned its error page{where} (matched {reason!r}); not caching it"
        )
    if not body.strip():
        raise SecResponseError(f"empty response body{where}; not caching it")
    return body


def is_poisoned_json_cache(path: Path) -> Optional[str]:
    """Why an existing cache file must not be trusted, or ``None`` if it is fine.

    Used when reading a cache written before this guard existed.
    """
    try:
        body = path.read_bytes()
    except OSError as exc:
        return f"unreadable: {exc}"
    try:
        validated_json(body, url=str(path))
    except SecResponseError as exc:
        return str(exc)
    return None
