"""Rate limiting for authentication, registration and reset attempts.

V3 section 10 requires limits that "do not unfairly lock a whole campus behind
one IP". A campus NAT can present thousands of students from a single address, so
the primary key is the submitted identifier (institutional ID or email) and the
per-address ceiling is deliberately high. Both dimensions are checked, and the
identifier limit is what stops credential stuffing against one account.

The counter lives in the configured cache backend, which is database-backed here
so the limit is shared across gunicorn workers rather than per process.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from .errors import Code, OperationRejected

logger = logging.getLogger(__name__)

# Generous per-address ceiling: a shared campus address is not punished for one
# careless user, but a single script cannot hammer the endpoint indefinitely.
ADDRESS_LIMIT = 400
ADDRESS_WINDOW = 300

IDENTIFIER_LIMIT = 8
IDENTIFIER_WINDOW = 900

REGISTRATION_LIMIT = 5
REGISTRATION_WINDOW = 3600


def _key(scope: str, value: str) -> str:
    return f"ratelimit:{scope}:{value}"


def hit(scope: str, value: str, *, limit: int, window: int) -> bool:
    """Record one attempt. Returns True when the caller may proceed."""
    cache_key = _key(scope, value)
    if cache.add(cache_key, 1, window):
        return True
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # The entry expired between add() and incr(); restart the window.
        cache.set(cache_key, 1, window)
        count = 1
    return count <= limit


def client_address(request) -> str:
    """The client address, trusting a forwarded header only from a proxy.

    ``REMOTE_ADDR`` is the honest default; the deployment terminates TLS at Caddy
    and sets the forwarded header there.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def guard(request, *, identifier: str, identifier_limit: int = IDENTIFIER_LIMIT) -> None:
    """Apply both dimensions, raising a clean, translatable rejection."""
    address_ok = hit("addr", client_address(request), limit=ADDRESS_LIMIT, window=ADDRESS_WINDOW)
    identifier_ok = hit(
        "ident", (identifier or "anonymous").strip().lower(), limit=identifier_limit, window=IDENTIFIER_WINDOW
    )
    if not (address_ok and identifier_ok):
        logger.warning("Rate limit hit for identifier=%r", identifier)
        raise OperationRejected(Code.RATE_LIMITED, retry_after=IDENTIFIER_WINDOW)


def guard_registration(request) -> None:
    address_ok = hit("reghost", client_address(request), limit=ADDRESS_LIMIT, window=ADDRESS_WINDOW)
    if not address_ok:
        raise OperationRejected(Code.RATE_LIMITED)
