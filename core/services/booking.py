"""Advance reservations (V3 section 3.2).

An advance reservation applies when ``now < slot_start <= now + 7 days``. There
is no 24-hour minimum. At the exact start instant the future action no longer
applies: a stale confirmation is rejected with a refreshed "Use now" offer and is
never silently turned into an attendance declaration.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q

from core.models import (
    BLOCKING_STATUSES,
    Booking,
    BookingControl,
    RecurringReservation,
    Room,
    advance_deadline,
)

from . import slots
from .calendar import assert_slot_open
from .eligibility import assert_may_reserve_room
from .errors import Code, OperationRejected
from .outbox import enqueue
from .policy import current_policy
from .quota import assert_quota_and_adjacency, no_show_reclaim
from .refs import reload_for_update

logger = logging.getLogger(__name__)

BOOKING_DETAIL_FIELDS = ("title", "purpose", "participant_names")
BOOKING_DETAIL_LIMITS = {"title": 120, "purpose": 500, "participant_names": 500}


def normalise_details(details: dict | None) -> dict[str, str]:
    """Return bounded, plain-text booking details for forms and emails."""
    details = details or {}
    return {
        field: str(details.get(field) or "").strip()[: BOOKING_DETAIL_LIMITS[field]]
        for field in BOOKING_DETAIL_FIELDS
    }


def build_payload(
    room: Room,
    slot_start,
    details: dict | None = None,
) -> dict:
    """The idempotency payload for an advance booking.

    Room and slot are part of the fingerprint, so a replayed key cannot change
    the requested room or hour (V3 section 5).
    """
    return {
        "action": "advance_booking",
        "room_id": room.pk,
        "room_number": room.number,
        "slot": slots.encode_slot(slot_start),
        **normalise_details(details),
    }


def build_update_payload(booking: Booking, details: dict | None = None) -> dict:
    """Fingerprint an edit so an idempotent retry cannot change its meaning."""
    return {
        "action": "update_booking_details",
        "booking_id": booking.pk,
        **normalise_details(details),
    }


def validate_advance_target(*, user, room: Room, slot_start, now, enforce_horizon: bool = True) -> None:
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
    if enforce_horizon and not slots.is_within_horizon(now, slot_start, policy.horizon_days):
        raise OperationRejected(Code.OUTSIDE_HORIZON, horizon_days=policy.horizon_days)

    assert_may_reserve_room(user, room, now)
    assert_slot_open(slot_start, room)
    assert_quota_and_adjacency(user, slot_start, room=room, now=now)


def _created_kind(status) -> str:
    """The email a newly created booking gets: a request receipt or a confirmation."""
    return "booking_pending" if status == Booking.Status.PENDING_APPROVAL else "booking_confirmation"


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

    # Staff may approve every room, so their own request is approved as made
    # rather than left waiting for themselves (D-41).
    self_approved = room.requires_approval and is_booking_staff(user)
    if room.requires_approval and not self_approved:
        status = Booking.Status.PENDING_APPROVAL
    else:
        status = Booking.Status.SCHEDULED
    booking = Booking.objects.create(
        user=user,
        room=room,
        slot_start=slot_start,
        slot_end=slot_end,
        deadline=deadline,
        status=status,
        source=Booking.Source.ADVANCE,
        approved_at=now if self_approved else None,
        approved_by=user if self_approved else None,
        policy_version=policy,
        title=ctx.payload.get("title", ""),
        purpose=ctx.payload.get("purpose", ""),
        participant_names=ctx.payload.get("participant_names", ""),
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
            "title": booking.title,
            "purpose": booking.purpose,
            "participant_names": booking.participant_names,
        },
    )

    enqueue(
        kind=_created_kind(status),
        recipient=user,
        dedupe_key=f"{_created_kind(status)}:{booking.pk}",
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
            "title": booking.title,
            "purpose": booking.purpose,
            "participant_names": booking.participant_names,
            "status": booking.status,
        },
    )

    return {
        "booking_id": booking.pk,
        "room_id": room.pk,
        "slot": slots.encode_slot(slot_start),
        "status": booking.status,
        "deadline": deadline.isoformat(),
    }


def is_booking_staff(user) -> bool:
    """Operational staff and the maintainer: may approve, and book outside student limits."""
    return bool(
        getattr(user, "is_active", False)
        and (getattr(user, "is_superuser", False) or getattr(user, "is_operational_staff", False))
    )


def build_staff_payload(room: Room, local_date, start_hour: int, end_hour: int, details=None) -> dict:
    return {
        "action": "staff_booking",
        "room_id": room.pk,
        "date": local_date.isoformat(),
        "start_hour": start_hour,
        "end_hour": end_hour,
        **normalise_details(details),
    }


def create_staff_booking(ctx, *, room: Room, local_date, start_hour: int, end_hour: int) -> dict:
    """Hold a room for several consecutive hours on behalf of the department (D-41).

    For rehearsals, events and teaching that staff arrange. The student limits
    (two-day window, daily quota, upcoming-hours cap, no adjacent hours, room
    audience) do not apply, and a room that needs approval is approved as made.
    The calendar still does: opening hours, closures and class hours are
    respected, and an hour someone already holds is never taken over. Either
    every hour is booked or none is.
    """
    actor = ctx.actor
    now = ctx.now
    if not is_booking_staff(actor):
        raise OperationRejected(Code.NOT_ELIGIBLE)
    if not (0 <= start_hour < end_hour <= 24):
        raise OperationRejected(Code.INVALID_INPUT)

    starts = [slots.slot_start_for(local_date, hour) for hour in range(start_hour, end_hour)]
    if starts[0] <= now:
        raise OperationRejected(Code.RANGE_STARTED)
    horizon_days = settings.STAFF_BOOKING_HORIZON_DAYS
    if starts[-1] > now + timedelta(days=horizon_days):
        raise OperationRejected(Code.OUTSIDE_HORIZON, horizon_days=horizon_days)
    for slot_start in starts:
        assert_slot_open(slot_start, room)

    holding = Booking.objects.filter(slot_start__in=starts, status__in=list(BLOCKING_STATUSES))
    if holding.filter(room=room).exists():
        raise OperationRejected(Code.RANGE_TAKEN)
    if holding.filter(user=actor).exists():
        # One person holds one room per hour (booking_user_slot_unique).
        raise OperationRejected(Code.ADJACENCY_CONFLICT)

    policy = current_policy()
    created = []
    for slot_start in starts:
        booking = Booking.objects.create(
            user=actor,
            room=room,
            slot_start=slot_start,
            slot_end=slots.slot_end_for(slot_start),
            deadline=advance_deadline(slot_start, policy.checkin_grace_minutes),
            status=Booking.Status.SCHEDULED,
            source=Booking.Source.ADVANCE,
            is_staff_booking=True,
            approved_at=now,
            approved_by=actor,
            policy_version=policy,
            title=ctx.payload.get("title", ""),
            purpose=ctx.payload.get("purpose", ""),
            participant_names=ctx.payload.get("participant_names", ""),
        )
        created.append(booking)
        ctx.audit(
            action="booking.created",
            entity_type="Booking",
            entity_id=booking.pk,
            actor=actor,
            changes={
                "room": room.number,
                "slot_start": booking.slot_start,
                "slot_end": booking.slot_end,
                "source": booking.source,
                "status": booking.status,
                "staff_booking": True,
                "policy_version": policy.version,
                "title": booking.title,
                "purpose": booking.purpose,
            },
        )

    return {
        "booking_ids": [booking.pk for booking in created],
        "room_id": room.pk,
        "hours": len(created),
    }


def update_booking_details(ctx, *, booking: Booking, details: dict | None = None) -> dict:
    """Edit the descriptive details of a future reservation.

    Room and hour stay fixed. Keeping those immutable makes the edit safe to use
    from a small form: it cannot bypass room conflicts, adjacency or quota rules.
    """
    actor = ctx.actor
    booking = reload_for_update(booking)
    if booking.user_id != getattr(actor, "pk", None):
        raise OperationRejected(Code.NOT_OWNER)
    if (
        booking.status not in {Booking.Status.SCHEDULED, Booking.Status.PENDING_APPROVAL}
        or ctx.now >= booking.slot_start
    ):
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)

    values = normalise_details(details)
    before = {field: getattr(booking, field) for field in BOOKING_DETAIL_FIELDS}
    if before == values:
        raise OperationRejected(Code.INVALID_INPUT)

    for field, value in values.items():
        setattr(booking, field, value)
    booking.details_version += 1
    booking.save(update_fields=[*BOOKING_DETAIL_FIELDS, "details_version"])

    ctx.audit(
        action="booking.updated",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=actor,
        changes={"before": before, "after": values, "details_version": booking.details_version},
    )

    enqueue(
        kind="booking_changed",
        recipient=booking.user,
        dedupe_key=f"booking_changed:{booking.pk}:{booking.details_version}",
        payload={
            "booking_id": booking.pk,
            "room_number": booking.room.number,
            "room_label": str(booking.room),
            "slot_start": booking.slot_start.isoformat(),
            "slot_end": booking.slot_end.isoformat(),
            "slot_start_local": slots.format_local(booking.slot_start),
            "deadline_local": slots.format_local(booking.deadline),
            "title": booking.title,
            "purpose": booking.purpose,
            "participant_names": booking.participant_names,
            "details_version": booking.details_version,
        },
    )

    return {"booking_id": booking.pk, "details_version": booking.details_version}


def build_move_payload(booking: Booking, room: Room) -> dict:
    return {
        "action": "move_booking",
        "booking_id": booking.pk,
        "room_id": room.pk,
    }


def move_booking(ctx, *, booking: Booking, room: Room) -> dict:
    """Move a future reservation to a different room, keeping the same hour.

    The move is a single in-place update of ``room``, not a cancel followed by a
    create. That matters for three reasons:

    * It is atomic without any extra machinery. The whole operation already runs
      under the ``BookingControl`` lock in one transaction, so if the target room
      turns out to be taken the savepoint is discarded and the student keeps the
      room they had. They can never end up holding nothing, which is exactly the
      failure mode that makes cancel-then-rebook unsafe.
    * ``booking_user_slot_unique`` is keyed on (user, slot_start), which the move
      does not touch, while ``booking_room_slot_unique`` on (room, slot_start)
      remains the final arbiter of a race the pre-check missed.
    * Quota and adjacency are unchanged by definition: the date and the hour are
      the same, and no booking is added or removed. Re-running those checks here
      would reject the move against the student's own row.

    An occurrence of a weekly series is refused: ``RecurringReservation`` records
    the room for the series, so moving one occurrence would leave the series row
    disagreeing with its own booking.
    """
    actor = ctx.actor
    now = ctx.now
    booking = reload_for_update(booking)

    if booking.user_id != getattr(actor, "pk", None):
        raise OperationRejected(Code.NOT_OWNER)

    if (
        booking.status not in {Booking.Status.SCHEDULED, Booking.Status.PENDING_APPROVAL}
        or now >= booking.slot_start
    ):
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)

    if booking.recurrence_id is not None:
        raise OperationRejected(Code.RECURRING_OCCURRENCE, booking_id=booking.pk)

    previous_room = booking.room
    if previous_room.pk == room.pk:
        raise OperationRejected(Code.SAME_ROOM, booking_id=booking.pk)

    slot_start = booking.slot_start

    # The target has to satisfy everything the original reservation had to, apart
    # from the rules the hour already settled. Instrument restrictions and the
    # room calendar are room-specific, so both are re-checked against the target.
    assert_may_reserve_room(actor, room, now)
    assert_slot_open(slot_start, room)

    if no_show_reclaim(actor, room, slot_start):
        raise OperationRejected(Code.NO_SHOW_RECLAIM)

    if (
        Booking.objects.filter(
            room=room,
            slot_start=slot_start,
            status__in=list(BLOCKING_STATUSES),
        )
        .exclude(pk=booking.pk)
        .exists()
    ):
        raise OperationRejected(Code.SLOT_TAKEN)

    status = Booking.Status.PENDING_APPROVAL if room.requires_approval else Booking.Status.SCHEDULED

    booking.room = room
    booking.status = status
    # Reused as the booking's change generation so the outbox dedupe key for a
    # move cannot collide with an earlier detail edit on the same row.
    booking.details_version += 1
    booking.save(update_fields=["room", "status", "details_version"])

    ctx.audit(
        action="booking.moved",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=actor,
        changes={
            "from_room": previous_room.number,
            "to_room": room.number,
            "slot_start": slot_start,
            "status": booking.status,
            "details_version": booking.details_version,
        },
    )

    kind = "booking_pending" if status == Booking.Status.PENDING_APPROVAL else "booking_changed"
    enqueue(
        kind=kind,
        recipient=booking.user,
        dedupe_key=f"{kind}:{booking.pk}:{booking.details_version}",
        payload={
            "booking_id": booking.pk,
            "room_number": room.number,
            "room_label": str(room),
            "previous_room_number": previous_room.number,
            "previous_room_label": str(previous_room),
            "slot_start": slot_start.isoformat(),
            "slot_end": booking.slot_end.isoformat(),
            "slot_start_local": slots.format_local(slot_start),
            "deadline_local": slots.format_local(booking.deadline),
            "title": booking.title,
            "purpose": booking.purpose,
            "participant_names": booking.participant_names,
            "details_version": booking.details_version,
            "status": booking.status,
        },
    )

    return {
        "booking_id": booking.pk,
        "room_id": room.pk,
        "previous_room_id": previous_room.pk,
        "status": booking.status,
        "details_version": booking.details_version,
    }


def cancel_recurring_reservation(ctx, *, recurrence: RecurringReservation, reason: str = "") -> dict:
    """Cancel only future occurrences and close a student's active series."""
    from .cancel import cancel as cancel_booking
    from .errors import Code, OperationRejected
    from .refs import reload_for_update

    recurrence = reload_for_update(recurrence)
    if recurrence.user_id != getattr(ctx.actor, "pk", None):
        raise OperationRejected(Code.NOT_OWNER)
    if recurrence.status != RecurringReservation.Status.ACTIVE:
        raise OperationRejected(Code.TERMINAL_STATUS)
    future = list(
        recurrence.occurrences.filter(
            status__in=[Booking.Status.PENDING_APPROVAL, Booking.Status.SCHEDULED],
            slot_start__gt=ctx.now,
        ).select_related("room", "user")
    )
    if not future:
        raise OperationRejected(Code.TERMINAL_STATUS)
    for occurrence in future:
        cancel_booking(ctx, booking=occurrence, reason=reason or "SERIES_CANCELLATION")
    recurrence.status = RecurringReservation.Status.CANCELLED
    recurrence.cancelled_at = ctx.now
    recurrence.save(update_fields=["status", "cancelled_at"])
    ctx.audit(
        action="recurring_reservation.cancelled",
        entity_type="RecurringReservation",
        entity_id=recurrence.pk,
        actor=ctx.actor,
        changes={"cancelled_booking_ids": [booking.pk for booking in future]},
        reason=reason,
    )
    return {"series_id": recurrence.pk, "cancelled_booking_ids": [booking.pk for booking in future]}


def decide_approval(ctx, *, booking: Booking, decision: str, note: str = "") -> dict:
    """Approve or reject one room-scoped pending request."""
    from .errors import Code, OperationRejected
    from .refs import reload_for_update

    booking = reload_for_update(booking)
    if booking.status != Booking.Status.PENDING_APPROVAL:
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)
    note = (note or "").strip()[:1000]
    if decision == "APPROVED":
        booking.status = Booking.Status.SCHEDULED
        booking.approved_at = ctx.now
        booking.approved_by = ctx.actor
        booking.approval_note = note
        booking.save(update_fields=["status", "approved_at", "approved_by", "approval_note"])
        ctx.audit(
            action="booking.approved",
            entity_type="Booking",
            entity_id=booking.pk,
            actor=ctx.actor,
            changes={"status": booking.status, "approved_at": booking.approved_at},
            reason=note,
        )
        enqueue(
            kind="booking_confirmation",
            recipient=booking.user,
            # Distinct per approval: a booking confirmed at creation, then moved
            # into an approval room and approved, must still get this email.
            dedupe_key=f"booking_confirmation:{booking.pk}:approved:{booking.approved_at.isoformat()}",
            payload={
                "booking_id": booking.pk,
                "room_number": booking.room.number,
                "room_label": str(booking.room),
                "slot_start": booking.slot_start.isoformat(),
                "slot_end": booking.slot_end.isoformat(),
                "slot_start_local": slots.format_local(booking.slot_start),
                "deadline_local": slots.format_local(booking.deadline),
                "source": booking.source,
                "grace_minutes": current_policy().checkin_grace_minutes,
                "title": booking.title,
                "purpose": booking.purpose,
                "participant_names": booking.participant_names,
            },
        )
        return {"booking_id": booking.pk, "status": booking.status}
    if decision != "REJECTED":
        raise OperationRejected(Code.INVALID_INPUT)

    booking.status = Booking.Status.REJECTED
    booking.approval_note = note or "The room administrator did not approve this request."
    booking.save(update_fields=["status", "approval_note"])
    ctx.audit(
        action="booking.rejected",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=ctx.actor,
        changes={"status": booking.status},
        reason=booking.approval_note,
    )
    enqueue(
        kind="booking_rejected",
        recipient=booking.user,
        dedupe_key=f"booking_rejected:{booking.pk}",
        payload={
            "booking_id": booking.pk,
            "room_number": booking.room.number,
            "room_label": str(booking.room),
            "slot_start": booking.slot_start.isoformat(),
            "slot_end": booking.slot_end.isoformat(),
            "slot_start_local": slots.format_local(booking.slot_start),
            "reason": booking.approval_note,
        },
    )
    return {"booking_id": booking.pk, "status": booking.status}


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
