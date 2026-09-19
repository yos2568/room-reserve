"""Shared view helpers: operation wrappers, guards and result rendering."""

from __future__ import annotations

import functools

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import resolve_url
from django.utils.http import url_has_allowed_host_and_scheme

from core.security import client_ip_allowed
from core.services import clock
from core.services.errors import OperationOutcome, status_for
from core.services.protocol import run_operation


def safe_next_url(request, candidate: str | None, fallback: str) -> str:
    """Only ever redirect to this host.

    Rejects absolute and protocol-relative targets so a crafted ``next`` cannot
    bounce a signed-in user to another site (V3 section 10).
    """
    if candidate and url_has_allowed_host_and_scheme(
        url=candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return resolve_url(fallback)


def staff_required(view):
    """Operational staff or the technical maintainer. Everyone else is refused."""

    @functools.wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_active or not (user.is_operational_staff or user.is_superuser):
            raise PermissionDenied("Operational staff access required.")
        if not client_ip_allowed(request, settings.STAFF_ALLOWED_IPS):
            raise PermissionDenied("Staff access is not available from this network.")
        return view(request, *args, **kwargs)

    return wrapper


def maintainer_required(view):
    @functools.wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Maintainer access required.")
        return view(request, *args, **kwargs)

    return wrapper


def operation_key(request, field: str = "operation_key") -> str | None:
    """Read the idempotency key a form submitted with its payload."""
    key = request.POST.get(field) or request.headers.get("X-Operation-Key")
    if not key:
        return None
    return key.strip()[:128] or None


def run_view_operation(*, request, operation: str, payload: dict, body, key: str | None = None):
    """Run an operation for the signed-in user.

    The actor is always ``request.user``: a client-supplied user id is never
    trusted (V3 section 3.1).
    """
    actor = request.user if request.user.is_authenticated else None
    return run_operation(
        actor=actor,
        operation=operation,
        payload=payload,
        key=key,
        body=body,
    )


def add_outcome_message(request, outcome: OperationOutcome, *, success_message: str | None = None) -> None:
    """Surface a result to the user, translated, via the messages framework."""
    if outcome.ok:
        if outcome.replayed:
            messages.info(request, "ผลลัพธ์เดิมถูกใช้ซ้ำ / Showing the result of your earlier submission.")
        elif success_message:
            messages.success(request, success_message)
        else:
            messages.success(request, outcome.message)
    else:
        messages.error(request, outcome.message)


def outcome_status(outcome: OperationOutcome) -> int:
    return status_for(outcome.code, default=200)


def now():
    """The authoritative time, for read-only views."""
    return clock.now()


def redirect_back(request, fallback: str = "core:home") -> HttpResponseRedirect:
    return HttpResponseRedirect(safe_next_url(request, request.POST.get("next"), fallback))
