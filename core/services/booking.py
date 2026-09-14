"""Advance reservations (V3 section 3.2).

An advance reservation applies when ``now < slot_start <= now + 7 days``. There
is no 24-hour minimum. At the exact start instant the future action no longer
applies: a stale confirmation is rejected with a refreshed "Use now" offer and is
never silently turned into an attendance declaration.
"""

from __future__ import annotations

import logging

from django.db.models import Q

from core.models import BLOCKING_STATUSES, Booking, BookingControl, Room, advance_deadline

from . import slots
from .calendar import assert_slot_open
from .eligibility import assert_may_use_room
from .errors import Code, OperationRejected
from .outbox import enqueue
from .policy import current_policy
from .quota import assert_quota_and_adjacency

logger = logging.getLogger(__name__)


def build_payload(room: Room, slot_start) -> dict:
    """The idempotency payload for an advance booking.

    Room and slot are part of the fingerprint, so a replayed key cannot change
    the requested room or hour (V3 section 5).
    """
    return {
        "action": "advance_booking",
        "room_id": room.pk,
        "room_number": room.number,
        "slot": slots.encode_slot(slot_start),
    }


def validate_advance_target(*, user, room: Room, slot_start, now) -> None:
    """All preconditions for a future reservation. Raises OperationRejected."""
    if not slots.is_hour_aligned(slot_start):
        raise OperationRejected(Code.INVALID_SLOT)

    slot_end = slots.slot_end_for(slot_start)

    if slot_start <= now:
        if now < slot_end:
            # The page was rendered before the hour began. Offer "Use now"
            # instead of checking the student in by surprise.
            raise OperationRejected(
                Code.STALE_HOUR,
                slot=slots.encode_slot(slot_start),
                room_id=room.pk,
                remaining_minutes=slots.remaining_minutes(now, slot_end),
            )
        raise OperationRejected(Code.SLOT_ELAPSED)

    policy = current_policy()
    if not slots.is_within_horizon(now, slot_start, policy.horizon_days):
        raise OperationRejected(Code.OUTSIDE_HORIZON, horizon_days=policy.horizon_days)

    assert_may_use_room(user, room, now)
    assert_slot_open(slot_start, room)
    assert_quota_and_adjacency(user, slot_start, room=room)


def create_advance_booking(ctx, *, room: Room, slot_start) -> dict:
    """Create the SCHEDULED booking. Called inside the protocol's savepoint."""
    user = ctx.actor
    now = ctx.now
    validate_advance_target(user=user, room=room, slot_start=slot_start, now=now)

    slot_end = slots.slot_end_for(slot_start)

    if Booking.objects.filter(
        room=room,
        slot_start=slot_start,
        status__in=list(BLOCKING_STATUSES),
    ).exists():
        raise OperationRejected(Code.SLOT_TAKEN)

    policy = current_policy()
    deadline = advance_deadline(slot_start, policy.checkin_grace_minutes)

    booking = Booking.objects.create(
        user=user,
        room=room,
        slot_start=slot_start,
        slot_end=slot_end,
        deadline=deadline,
        status=Booking.Status.SCHEDULED,
        source=Booking.Source.ADVANCE,
        policy_version=policy,
    )

    ctx.audit(
        action="booking.created",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=user,
        changes={
            "room": room.number,
            "slot_start": slot_start,
            "slot_end": slot_end,
            "deadline": deadline,
            "source": booking.source,
            "status": booking.status,
            "policy_version": policy.version,
        },
    )

    enqueue(
        kind="booking_confirmation",
        recipient=user,
        dedupe_key=f"booking_confirmation:{booking.pk}",
        payload={
            "booking_id": booking.pk,
            "room_number": room.number,
            "room_label": str(room),
            "slot_start": slot_start.isoformat(),
            "slot_end": slot_end.isoformat(),
            "slot_start_local": slots.format_local(slot_start),
            "deadline_local": slots.format_local(deadline),
            "source": booking.source,
            "grace_minutes": policy.checkin_grace_minutes,
        },
    )

    return {
        "booking_id": booking.pk,
        "room_id": room.pk,
        "slot": slots.encode_slot(slot_start),
        "status": booking.status,
        "deadline": deadline.isoformat(),
    }


def booking_conflict_for(user, slot_start):
    """A conflicting booking held by the same user, used for friendly messages."""
    return (
        Booking.objects.filter(user=user, status__in=list(BLOCKING_STATUSES))
        .filter(
            Q(slot_start=slot_start)
            | Q(slot_start=slot_start - slots.SLOT_DELTA)
            | Q(slot_start=slot_start + slots.SLOT_DELTA)
        )
        .select_related("room")
        .first()
    )


def control_row() -> BookingControl:
    return BookingControl.objects.get(pk=1)
