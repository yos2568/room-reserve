"""Request-scoped concerns: correlation IDs and cache directives.

V3 section 4 requires each AuditEvent to carry a request/correlation ID so that a
booking, its outbox rows and its audit row can be tied together after the fact.
V3 section 10 requires that no personal data reach a public cache.
"""

from __future__ import annotations

import uuid
from threading import local

from django.utils.cache import patch_vary_headers

_state = local()


def current_correlation_id() -> uuid.UUID | None:
    return getattr(_state, "correlation_id", None)


class RequestContextMiddleware:
    """Attach a correlation ID to the request and to a thread-local."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        correlation_id = self._resolve(request)
        request.correlation_id = correlation_id
        _state.correlation_id = correlation_id
        try:
            response = self.get_response(request)
        finally:
            _state.correlation_id = None
        response["X-Request-ID"] = str(correlation_id)
        return response

    @staticmethod
    def _resolve(request) -> uuid.UUID:
        # An inbound header is accepted only when it parses as a UUID, so it can
        # never inject an arbitrary string into logs or audit rows.
        raw = request.headers.get("X-Request-ID")
        if raw:
            try:
                return uuid.UUID(raw)
            except (ValueError, AttributeError, TypeError):
                pass
        return uuid.uuid4()


class PersonalResponseCacheMiddleware:
    """Keep personal responses out of shared caches (V3 section 10).

    Django sets no ``Cache-Control`` of its own, so without this a proxy could
    store a signed-in student's page and hand it to the next person through that
    proxy. Anything personalised, and anything that is not a plain read, is
    explicitly unstoreable.

    Anonymous pages keep the default and are marked to vary on the session
    cookie, so a cache can never serve a signed-in body to a signed-out visitor.
    They are deliberately not marked ``public``: those pages carry the CSRF
    cookie, and a shared cache allowed to store one would replay it to others.
    """

    SAFE_METHODS = ("GET", "HEAD")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.user.is_authenticated or request.method not in self.SAFE_METHODS:
            response["Cache-Control"] = "private, no-store"
            response["Pragma"] = "no-cache"
        else:
            # Belt and braces for a shared cache that ignores Cache-Control.
            response["Cache-Control"] = "private, max-age=0, must-revalidate"

        patch_vary_headers(response, ("Cookie",))
        return response
