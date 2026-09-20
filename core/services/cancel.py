"""Cancellation (V3 section 3.4).

A student may cancel only their own SCHEDULED booking, and only before it starts.
Cancelling less than 60 minutes ahead sets a reporting-only late-cancel flag.
There is no student cancellation after start and no extra penalty is invented.
"""

from __future__ import annotations

from datetime import timedelta

from core.models import Booking

from . import slots
from .errors import Code, OperationRejected
from .outbox import enqueue
from .refs import reload_for_update

LATE_CANCEL_WINDOW = timedelta(minutes=60)


def build_payload(booking: Booking, reason: str = "") -> dict:
    return {
        "action": "cancel_booking",
        "booking_id": booking.pk,
        "reason": reason,
    }


def cancel(ctx, *, booking: Booking, reason: str = "", staff_action: bool = False) -> dict:
    """Move a SCHEDULED booking to CANCELLED."""
    now = ctx.now
    actor = ctx.actor

    # Committed state wins over the caller's copy: the booking may have been
    # reconciled to NO_SHOW or already cancelled.
    booking = reload_for_update(booking)

    if not staff_action and booking.user_id != getattr(actor, "pk", None):
        raise OperationRejected(Code.NOT_OWNER)

    if booking.status == Booking.Status.CANCELLED:
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)
    if booking.status not in {Booking.Status.SCHEDULED, Booking.Status.PENDING_APPROVAL}:
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)

    if not staff_action and now >= booking.slot_start:
        raise OperationRejected(Code.CANCEL_AFTER_START, booking_id=booking.pk)

    late = now > (booking.slot_start - LATE_CANCEL_WINDOW)

    booking.status = Booking.Status.CANCELLED
    booking.cancelled_at = now
    booking.cancelled_by = actor if getattr(actor, "pk", None) else None
    booking.cancel_reason = reason
    # Administrative and staff cancellations are not the student's late cancel.
    booking.late_cancel = late and not staff_action
    booking.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancel_reason",
            "late_cancel",
        ]
    )

    ctx.audit(
        action="booking.cancelled",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=actor,
        changes={
            "status": booking.status,
            "late_cancel": booking.late_cancel,
            "staff_action": staff_action,
        },
        reason=reason,
    )

    enqueue(
        kind="booking_cancelled",
        recipient=booking.user,
        dedupe_key=f"booking_cancelled:{booking.pk}",
        payload={
            "booking_id": booking.pk,
            "room_number": booking.room.number,
            "room_label": str(booking.room),
            "slot_start": booking.slot_start.isoformat(),
            "slot_end": booking.slot_end.isoformat(),
            "slot_start_local": slots.format_local(booking.slot_start),
            "reason": reason,
            "late_cancel": booking.late_cancel,
        },
    )

    return {
        "booking_id": booking.pk,
        "status": booking.status,
        "late_cancel": booking.late_cancel,
    }
