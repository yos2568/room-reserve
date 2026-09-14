"""Liveness and readiness probes.

V3 section 9: minimal process liveness plus app and DB readiness, with
operational detail restricted. A failed mail provider must not fail liveness and
trigger restart loops (V3 section 11), so SMTP is never probed here.
"""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def liveness(request):
    """Process liveness only: no database, no SMTP, no cache."""
    return JsonResponse({"status": "ok"})


@require_GET
def readiness(request):
    """Application and database readiness. Details stay deliberately terse."""
    payload = {"status": "ok", "database": "ok"}
    status_code = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        payload = {"status": "unavailable", "database": "error"}
        status_code = 503

    return JsonResponse(payload, status=status_code)
