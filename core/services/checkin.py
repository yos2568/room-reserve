"""Owner check-in for an advance reservation (V3 section 3.2).

The window is start inclusive to start + grace exclusive. Forfeiture begins
exactly at the deadline. A row whose deadline has passed has already been
reconciled to NO_SHOW by the time the body runs, so the status check is the
authoritative guard rather than a second time comparison.
"""

from __future__ import annotations

from core.models import Booking

from . import slots
from .eligibility import assert_can_operate
from .errors import Code, OperationRejected
from .policy import current_policy
from .refs import reload_for_update


def build_payload(booking: Booking) -> dict:
    return {
        "action": "check_in",
        "booking_id": booking.pk,
        "room_id": booking.room_id,
        "slot": slots.encode_slot(booking.slot_start),
    }


def check_in(ctx, *, booking: Booking, room=None, staff_assisted: bool = False, reason: str = "") -> dict:
    """Move a SCHEDULED booking to IN_USE."""
    now = ctx.now
    actor = ctx.actor

    # Re-read under a row lock. A concurrent reconciliation may already have made
    # this booking a no-show, and that committed fact must beat the caller's copy;
    # otherwise a check-in could overwrite a NO_SHOW and leave a strike attached
    # to an in-use booking.
    booking = reload_for_update(booking)

    if not staff_assisted and booking.user_id != getattr(actor, "pk", None):
        raise OperationRejected(Code.NOT_OWNER)

    if room is not None and booking.room_id != room.pk:
        raise OperationRejected(Code.WRONG_ROOM)

    if booking.status == Booking.Status.IN_USE:
        raise OperationRejected(Code.ALREADY_CHECKED_IN, booking_id=booking.pk)
    if booking.status != Booking.Status.SCHEDULED:
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)

    policy = current_policy()
    if booking.deadline is None:
        # A walk-in never has a deadline; guard against inconsistent data.
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)

    if now < booking.slot_start:
        raise OperationRejected(
            Code.CHECK_IN_NOT_OPEN,
            booking_id=booking.pk,
            opens_at=booking.slot_start.isoformat(),
        )
    if now >= booking.deadline:
        raise OperationRejected(
            Code.CHECK_IN_CLOSED,
            booking_id=booking.pk,
            deadline=booking.deadline.isoformat(),
        )

    assert_can_operate(booking.user, now)

    booking.status = Booking.Status.IN_USE
    booking.checked_in_at = now
    booking.checked_in_by = actor if getattr(actor, "pk", None) else None
    booking.save(update_fields=["status", "checked_in_at", "checked_in_by"])

    ctx.audit(
        action="booking.checked_in",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=actor,
        changes={
            "status": booking.status,
            "checked_in_at": now,
            "staff_assisted": staff_assisted,
            "grace_minutes": policy.checkin_grace_minutes,
        },
        reason=reason,
    )

    return {
        "booking_id": booking.pk,
        "status": booking.status,
        "checked_in_at": now.isoformat(),
        "slot_end": booking.slot_end.isoformat(),
        "remaining_minutes": slots.remaining_minutes(now, booking.slot_end),
    }
