"""Use-now walk-ins (V3 section 3.2).

A walk-in belongs to the *current* slot and always ends at the original hour
boundary: no extra hour, no renewed grace, and no early self-checkout. It applies
equally to never-booked rooms, ordinarily cancelled reservations and released
no-shows. The original no-show owner cannot take that same room and hour back.
"""

from __future__ import annotations

from core.models import BLOCKING_STATUSES, Booking, Room

from . import slots
from .calendar import assert_slot_open
from .eligibility import assert_may_use_room
from .errors import Code, OperationRejected
from .outbox import enqueue
from .policy import current_policy
from .quota import assert_quota_and_adjacency, no_show_reclaim

# Below this many minutes remaining the UI must warn prominently before confirming.
SHORT_WARNING_MINUTES = 5


def build_payload(room: Room, slot_start) -> dict:
    return {
        "action": "use_now",
        "room_id": room.pk,
        "room_number": room.number,
        "slot": slots.encode_slot(slot_start),
    }


def current_slot(now):
    """The slot containing ``now``; a use-now is always for this slot."""
    return slots.floor_to_slot(now)


def room_availability_for_now(room: Room, slot_start, now):
    """Why the room is or is not free for the current hour.

    Reconciliation has already run when this is called from a mutation, so a
    reservation still inside its grace legitimately holds the room.
    """
    blocker = (
        Booking.objects.filter(
            room=room,
            slot_start=slot_start,
            status__in=list(BLOCKING_STATUSES),
        )
        .select_related("user")
        .first()
    )
    if blocker is None:
        return None, None
    if blocker.status == Booking.Status.SCHEDULED and blocker.deadline and now < blocker.deadline:
        return Code.RESERVATION_HELD, blocker
    return Code.SLOT_TAKEN, blocker


def use_now(ctx, *, room: Room) -> dict:
    """Create an already-in-use booking for the remainder of the current hour."""
    now = ctx.now
    user = ctx.actor
    slot_start = current_slot(now)
    slot_end = slots.slot_end_for(slot_start)

    assert_may_use_room(user, room, now)
    if room.requires_approval:
        # Every path into an approval room goes through staff; a walk-in cannot.
        raise OperationRejected(Code.APPROVAL_ROOM_NO_WALK_IN, room_id=room.pk)
    assert_slot_open(slot_start, room)

    code, blocker = room_availability_for_now(room, slot_start, now)
    if code is not None:
        raise OperationRejected(code, room_id=room.pk, booking_id=getattr(blocker, "pk", None))

    assert_quota_and_adjacency(user, slot_start, room=room)

    # Provenance: if the previous holder of this slot forfeited it, record the link.
    released_from = (
        Booking.objects.filter(
            room=room,
            slot_start=slot_start,
            status=Booking.Status.NO_SHOW,
        )
        .exclude(user=user)
        .first()
    )

    policy = current_policy()
    remaining = slots.remaining_minutes(now, slot_end)

    booking = Booking.objects.create(
        user=user,
        room=room,
        slot_start=slot_start,
        slot_end=slot_end,
        deadline=None,
        status=Booking.Status.IN_USE,
        source=Booking.Source.WALK_IN,
        checked_in_at=now,
        checked_in_by=user,
        original_no_show=released_from,
        policy_version=policy,
    )

    ctx.audit(
        action="booking.walk_in",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=user,
        changes={
            "room": room.number,
            "slot_start": slot_start,
            "slot_end": slot_end,
            "remaining_minutes": remaining,
            "released_from_booking": getattr(released_from, "pk", None),
            "policy_version": policy.version,
        },
        reason="Current-slot use-now confirmation.",
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
            "source": booking.source,
            "remaining_minutes": remaining,
        },
    )

    return {
        "booking_id": booking.pk,
        "room_id": room.pk,
        "status": booking.status,
        "slot": slots.encode_slot(slot_start),
        "slot_end": slot_end.isoformat(),
        "remaining_minutes": remaining,
        "short_warning": remaining < SHORT_WARNING_MINUTES,
    }


def no_show_blocks_reclaim(user, room, slot_start) -> bool:
    return no_show_reclaim(user, room, slot_start)
