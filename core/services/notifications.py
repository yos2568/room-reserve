"""Notification rendering and delivery.

Two rules from V3 section 10 shape this module:

* Every message is revalidated immediately before sending, because a concurrent
  cancellation can race an external provider send. Links must resolve current
  state, so a stale reminder is dropped rather than delivered.
* An expired verification generation is never sent by a retry.
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from core.models import Booking, Invitation, Notification, Suspension

from . import clock, posters

logger = logging.getLogger(__name__)

KIND_EMAIL_VERIFICATION = "email_verification"
KIND_INVITATION = "invitation"
KIND_PASSWORD_RESET = "password_reset"
KIND_BOOKING_CONFIRMATION = "booking_confirmation"
KIND_BOOKING_REMINDER = "booking_reminder"
KIND_BOOKING_CANCELLED = "booking_cancelled"
KIND_NO_SHOW = "no_show"
KIND_SANCTION_APPLIED = "sanction_applied"
KIND_SANCTION_LIFTED = "sanction_lifted"
KIND_ELIGIBILITY_DECISION = "eligibility_decision"

_SUBJECTS = {
    KIND_EMAIL_VERIFICATION: _("Confirm your Room Reserve email address"),
    KIND_INVITATION: _("Your Room Reserve invitation"),
    KIND_PASSWORD_RESET: _("Reset your Room Reserve password"),
    KIND_BOOKING_CONFIRMATION: _("Practice room booked"),
    KIND_BOOKING_REMINDER: _("Reminder: your practice room booking"),
    KIND_BOOKING_CANCELLED: _("Practice room booking cancelled"),
    KIND_NO_SHOW: _("Missed practice room booking"),
    KIND_SANCTION_APPLIED: _("Booking suspension applied"),
    KIND_SANCTION_LIFTED: _("Booking suspension lifted"),
    KIND_ELIGIBILITY_DECISION: _("Your Room Reserve account decision"),
}


class MessageStale(Exception):
    """The event no longer applies; the notification is cancelled, not sent."""


def _booking_context(payload: dict, extra: dict | None = None) -> dict:
    context = dict(payload)
    slot_start = payload.get("slot_start")
    slot_end = payload.get("slot_end")
    if slot_start:
        context["slot_start_display"] = clock.local_time(datetime.fromisoformat(slot_start)).strftime(
            "%d/%m/%Y %H:%M"
        )
    if slot_end:
        context["slot_end_display"] = clock.local_time(datetime.fromisoformat(slot_end)).strftime("%H:%M")
    context.update(extra or {})
    return context


def revalidate(notification: Notification, now) -> dict:
    """Return the render context, or raise MessageStale when the event is void."""
    payload = dict(notification.payload or {})
    kind = notification.kind

    if kind in {KIND_BOOKING_CONFIRMATION, KIND_BOOKING_REMINDER, KIND_BOOKING_CANCELLED, KIND_NO_SHOW}:
        booking_id = payload.get("booking_id")
        booking = Booking.objects.filter(pk=booking_id).first()
        if booking is None:
            raise MessageStale("Booking no longer exists.")

        if kind == KIND_BOOKING_CONFIRMATION and booking.status not in {
            Booking.Status.SCHEDULED,
            Booking.Status.IN_USE,
            Booking.Status.COMPLETED,
        }:
            raise MessageStale(f"Booking is {booking.status}.")
        if kind == KIND_BOOKING_REMINDER and (
            # A reminder is pointless once the booking is no longer upcoming.
            booking.status != Booking.Status.SCHEDULED or now >= booking.slot_start
        ):
            raise MessageStale("Reminder no longer applies.")
        if kind == KIND_BOOKING_CANCELLED and booking.status != Booking.Status.CANCELLED:
            raise MessageStale("Booking is not cancelled.")
        if kind == KIND_NO_SHOW and booking.status != Booking.Status.NO_SHOW:
            raise MessageStale("Booking is not a no-show.")

        context = _booking_context(payload, {"booking": booking, "room": booking.room})
        # The QR check-in link travels with the confirmation and the reminder
        # (D-31). The token is resolved to a path at render time, inside the
        # notification's language, from the booking row as it exists when the
        # message is actually sent.
        if kind in {KIND_BOOKING_CONFIRMATION, KIND_BOOKING_REMINDER} and booking.checkin_token:
            context["checkin_token"] = booking.checkin_token
        return context

    if kind in {KIND_EMAIL_VERIFICATION, KIND_INVITATION, KIND_PASSWORD_RESET}:
        invitation_id = payload.get("invitation_id")
        invitation = Invitation.objects.filter(pk=invitation_id).first()
        if invitation is None:
            raise MessageStale("Invitation no longer exists.")
        # An expired generation must never be delivered by a retry.
        if invitation.revoked_at is not None:
            raise MessageStale("Invitation was superseded.")
        if invitation.used_at is not None:
            raise MessageStale("Invitation was already used.")
        if invitation.expires_at <= now:
            raise MessageStale("Invitation expired.")
        return dict(payload, invitation=invitation)

    if kind == KIND_SANCTION_APPLIED:
        suspension_id = payload.get("suspension_id")
        suspension = Suspension.objects.filter(pk=suspension_id).first()
        if suspension is None or suspension.lifted_at is not None:
            raise MessageStale("Sanction no longer active.")
        return dict(payload, suspension=suspension)

    if kind == KIND_SANCTION_LIFTED:
        suspension_id = payload.get("suspension_id")
        suspension = Suspension.objects.filter(pk=suspension_id).first()
        if suspension is None:
            raise MessageStale("Suspension no longer exists.")
        return dict(payload, suspension=suspension)

    if kind == KIND_ELIGIBILITY_DECISION:
        return dict(payload)

    return payload


def _render_context(notification: Notification, context: dict) -> dict:
    """Shared email fields; called inside the notification's language override."""
    enriched = dict(context)
    enriched.setdefault("site_base_url", settings.SITE_BASE_URL.rstrip("/"))
    enriched.setdefault(
        "support_contact",
        {
            "name": settings.SUPPORT_CONTACT_NAME,
            "phone": settings.SUPPORT_CONTACT_PHONE,
            "email": settings.SUPPORT_CONTACT_EMAIL,
        },
    )
    enriched.setdefault("recipient", notification.recipient)
    if enriched.get("checkin_token"):
        enriched["checkin_path"] = reverse("core:checkin_qr", args=[enriched["checkin_token"]])
    return enriched


def render(notification: Notification, context: dict) -> tuple[str, str]:
    language = notification.language or "th"
    with translation.override(language):
        subject = str(_SUBJECTS.get(notification.kind, _("Room Reserve notification")))
        enriched = _render_context(notification, context)
        body = render_to_string(f"core/email/{notification.kind}.txt", enriched)
    return subject, body


def render_html(notification: Notification, context: dict) -> str | None:
    """The HTML alternative, when the kind has one (D-31).

    The booking confirmation embeds the QR check-in code inline, so a student
    can scan it straight from the email at the door. Only kinds that carry a
    ``checkin_token`` get a QR; everything else falls back to plain text only.
    """
    if not context.get("checkin_token"):
        return None
    language = notification.language or "th"
    with translation.override(language):
        enriched = _render_context(notification, context)
        enriched["qr_data_uri"] = posters.qr_data_uri(enriched["site_base_url"] + enriched["checkin_path"])
        return render_to_string(f"core/email/{notification.kind}.html", enriched)


def deliver(notification: Notification, now) -> None:
    """Send one notification. Raises on failure so the caller can back off."""
    if not settings.OUTBOX_ENABLED:
        raise RuntimeError("Outbox delivery is disabled by configuration.")

    context = revalidate(notification, now)
    subject, body = render(notification, context)
    html = render_html(notification, context)

    recipient = notification.recipient
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient.email],
    )
    if html:
        message.attach_alternative(html, "text/html")
    # A stable Message-ID lets a provider deduplicate an accidental double send.
    message.extra_headers = {"X-RoomReserve-Event": notification.dedupe_key}
    message.send(fail_silently=False)


def drain(now=None, limit: int | None = None) -> dict:
    """Deliver one batch of due notifications. Safe to call repeatedly."""
    from . import outbox

    now = now or clock.now()
    if not settings.OUTBOX_ENABLED:
        return {"claimed": 0, "sent": 0, "failed": 0, "stale": 0, "disabled": True}

    claimed = outbox.claim_due(now, limit=limit)
    sent = failed = stale = 0

    for notification in claimed:
        try:
            deliver(notification, now)
        except MessageStale as exc:
            outbox.cancel(notification, reason=str(exc))
            stale += 1
        except Exception as exc:
            logger.warning("Delivery failed for %s: %s", notification.dedupe_key, exc)
            outbox.mark_failed(notification, exc, now=now)
            failed += 1
        else:
            outbox.mark_sent(notification, now=now)
            sent += 1

    return {"claimed": len(claimed), "sent": sent, "failed": failed, "stale": stale}
