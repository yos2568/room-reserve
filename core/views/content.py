"""Informational pages: rules, help and the draft privacy notice.

The rules copy is a paraphrase of the local regulation mapped in V3 section 1. It
deliberately does not claim a blanket drinks ban, and it publishes the
application policy choices (seven-day horizon, quota, grace, strikes) separately
from the regulation, because those are not stated in the PDF.
"""

from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from core.services.policy import current_policy

from ._helpers import now


@require_GET
def rules(request):
    return render(
        request,
        "core/rules.html",
        {"policy": current_policy(), "moment": now()},
    )


@require_GET
def help_page(request):
    return render(
        request,
        "core/help.html",
        {"policy": current_policy(), "moment": now()},
    )


@require_GET
def privacy(request):
    """Draft until the faculty approves the data-handling basis (V3 section 10)."""
    return render(
        request,
        "core/privacy.html",
        {"policy": current_policy(), "moment": now()},
    )
