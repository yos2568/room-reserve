"""Advance reservations (V3 section 3.2).

An advance reservation applies when ``now < slot_start <= now + 7 days``. There
is no 24-hour minimum. At the exact start instant the future action no longer
applies: a stale confirmation is rejected with a refreshed "Use now" offer and is
never silently turned into an attendance declaration.
"""

from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta

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
    *,
    repeat_weekly: bool = False,
    repeat_until: str | None = None,
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
        "repeat_weekly": bool(repeat_weekly),
        "repeat_until": (repeat_until or "").strip(),
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
    assert_quota_and_adjacency(user, slot_start, room=room)


def create_advance_booking(ctx, *, room: Room, slot_start) -> dict:
    """Create the SCHEDULED booking. Called inside the protocol's savepoint."""
    if ctx.payload.get("repeat_weekly"):
        return create_recurring_booking(ctx, room=room, slot_start=slot_start)

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

    status = Booking.Status.PENDING_APPROVAL if room.requires_approval else Booking.Status.SCHEDULED
    booking = Booking.objects.create(
        user=user,
        room=room,
        slot_start=slot_start,
        slot_end=slot_end,
        deadline=deadline,
        status=status,
        source=Booking.Source.ADVANCE,
        policy_version=policy,
        title=ctx.payload.get("title", ""),
        purpose=ctx.payload.get("purpose", ""),
        participant_names=ctx.payload.get("participant_names", ""),
        # Secret token for the QR check-in link carried by the confirmation
        # email (D-31). Unguessable, unique per reservation, useless outside
        # the check-in window and to anyone but the owner.
        checkin_token=secrets.token_urlsafe(24),
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
        kind=("booking_pending" if status == Booking.Status.PENDING_APPROVAL else "booking_confirmation"),
        recipient=user,
        dedupe_key=f"{'booking_pending' if status == Booking.Status.PENDING_APPROVAL else 'booking_confirmation'}:{booking.pk}",
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


def _recurring_dates(first_date: date, repeat_until: date) -> list[date]:
    dates = []
    current = first_date
    while current <= repeat_until:
        dates.append(current)
        current += timedelta(days=7)
    return dates


def _parse_repeat_until(raw: str, first_date: date) -> date:
    try:
        repeat_until = date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise OperationRejected(Code.INVALID_INPUT) from exc
    if repeat_until < first_date:
        raise OperationRejected(Code.INVALID_INPUT)
    # Arithmetic, not a list: the cap must hold before any work is done, because
    # this runs under the booking-wide lock and ``repeat_until`` comes from a form.
    if (repeat_until - first_date).days // 7 > settings.RECURRING_MAX_WEEKS:
        raise OperationRejected(Code.INVALID_INPUT)
    return repeat_until


def create_recurring_booking(ctx, *, room: Room, slot_start) -> dict:
    """Create a bounded weekly series atomically, with ordinary booking rows."""
    from . import clock

    user = ctx.actor
    first_local = clock.local_time(slot_start)
    repeat_until = _parse_repeat_until(ctx.payload.get("repeat_until", ""), first_local.date())
    dates = _recurring_dates(first_local.date(), repeat_until)
    if len(dates) < 2 or first_local.weekday() != dates[0].weekday():
        raise OperationRejected(Code.INVALID_INPUT)

    details = normalise_details(ctx.payload)
    status = Booking.Status.PENDING_APPROVAL if room.requires_approval else Booking.Status.SCHEDULED
    policy = current_policy()
    series = RecurringReservation.objects.create(
        user=user,
        room=room,
        weekday=first_local.weekday(),
        start_hour=first_local.hour,
        start_date=dates[0],
        end_date=dates[-1],
        status=RecurringReservation.Status.ACTIVE,
        **details,
    )
    booking_ids = []
    for local_date in dates:
        occurrence_start = slots.slot_start_for(local_date, first_local.hour)
        validate_advance_target(
            user=user,
            room=room,
            slot_start=occurrence_start,
            now=ctx.now,
            enforce_horizon=False,
        )
        if Booking.objects.filter(
            room=room,
            slot_start=occurrence_start,
            status__in=list(BLOCKING_STATUSES),
        ).exists():
            raise OperationRejected(Code.SLOT_TAKEN)
        occurrence = Booking.objects.create(
            user=user,
            room=room,
            recurrence=series,
            slot_start=occurrence_start,
            slot_end=slots.slot_end_for(occurrence_start),
            deadline=advance_deadline(occurrence_start, policy.checkin_grace_minutes),
            status=status,
            source=Booking.Source.ADVANCE,
            policy_version=policy,
            title=details["title"],
            purpose=details["purpose"],
            participant_names=details["participant_names"],
            checkin_token=secrets.token_urlsafe(24),
        )
        booking_ids.append(occurrence.pk)
        ctx.audit(
            action="booking.created",
            entity_type="Booking",
            entity_id=occurrence.pk,
            actor=user,
            changes={
                "recurrence": series.pk,
                "room": room.number,
                "slot_start": occurrence_start,
                "status": status,
            },
        )
        enqueue(
            kind=("booking_pending" if status == Booking.Status.PENDING_APPROVAL else "booking_confirmation"),
            recipient=user,
            dedupe_key=f"{'booking_pending' if status == Booking.Status.PENDING_APPROVAL else 'booking_confirmation'}:{occurrence.pk}",
            payload={
                "booking_id": occurrence.pk,
                "room_number": room.number,
                "room_label": str(room),
                "slot_start": occurrence_start.isoformat(),
                "slot_end": occurrence.slot_end.isoformat(),
                "slot_start_local": slots.format_local(occurrence_start),
                "deadline_local": slots.format_local(occurrence.deadline),
                "source": occurrence.source,
                "grace_minutes": policy.checkin_grace_minutes,
                "title": occurrence.title,
                "purpose": occurrence.purpose,
                "participant_names": occurrence.participant_names,
                "status": occurrence.status,
            },
        )
    ctx.audit(
        action="recurring_reservation.created",
        entity_type="RecurringReservation",
        entity_id=series.pk,
        actor=user,
        changes={
            "room": room.number,
            "occurrences": booking_ids,
            "start_date": dates[0],
            "end_date": dates[-1],
        },
    )
    return {"series_id": series.pk, "booking_ids": booking_ids, "status": status}


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
            dedupe_key=f"booking_confirmation:{booking.pk}",
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
