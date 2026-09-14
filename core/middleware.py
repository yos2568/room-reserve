"""Request-scoped correlation IDs used by the audit trail.

V3 section 4 requires each AuditEvent to carry a request/correlation ID so that a
booking, its outbox rows and its audit row can be tied together after the fact.
"""

from __future__ import annotations

import uuid
from threading import local

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
