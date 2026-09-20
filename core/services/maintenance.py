"""Closures, calendar overrides, room and account deactivation, policy versions.

Closure flow (V3 section 8): preview the affected sessions, confirm under the
shared lock while rechecking the preview, then cancel scheduled bookings with
notices. Sessions already in use are shown prominently and flagged for on-site
follow-up rather than being silently completed. Reopening restores neither
bookings nor a completed slot.
"""

from __future__ import annotations

from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db.models import Q

from core.models import (
    Booking,
    CalendarOverride,
    Closure,
    InstrumentCategory,
    Room,
    Suspension,
    User,
)

from . import slots
from .audit import record_audit
from .errors import Code, OperationRejected
from .outbox import enqueue
from .policy import create_policy_version as _create_policy_version
from .refs import reload_for_update
from .rooms import set_audience


def preview_closure(room: Room | None, starts_at: datetime, ends_at: datetime) -> dict:
    """Read-only preview. GET must not write, so this never changes state."""
    if ends_at <= starts_at:
        raise OperationRejected(Code.INVALID_INPUT)

    scope = Q(room=room) if room is not None else Q()
    affected = (
        Booking.objects.filter(scope)
        .filter(status__in=[Booking.Status.PENDING_APPROVAL, Booking.Status.SCHEDULED, Booking.Status.IN_USE])
        .filter(slot_start__lt=ends_at, slot_end__gt=starts_at)
        .select_related("room", "user")
        .order_by("slot_start")
    )
    scheduled = [
        b for b in affected if b.status in {Booking.Status.PENDING_APPROVAL, Booking.Status.SCHEDULED}
    ]
    in_use = [b for b in affected if b.status == Booking.Status.IN_USE]

    return {
        "scheduled": scheduled,
        "in_use": in_use,
        "scheduled_count": len(scheduled),
        "in_use_count": len(in_use),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "room": room,
    }


def create_closure(
    ctx,
    *,
    room: Room | None,
    starts_at: datetime,
    ends_at: datetime,
    reason: str,
    acknowledged_in_use: bool = False,
) -> Closure:
    """Create a closure and cancel the scheduled bookings it affects."""
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    if ends_at <= starts_at:
        raise OperationRejected(Code.INVALID_INPUT)

    # Recheck under the shared lock: the preview may be stale by now.
    preview = preview_closure(room, starts_at, ends_at)
    if preview["in_use_count"] and not acknowledged_in_use:
        raise OperationRejected(
            Code.INVALID_INPUT,
            requires_acknowledgement=True,
            in_use_count=preview["in_use_count"],
        )

    closure = Closure.objects.create(
        room=room,
        starts_at=starts_at,
        ends_at=ends_at,
        reason=reason.strip(),
        created_by=ctx.actor,
        acknowledged_in_use=acknowledged_in_use,
    )

    for booking in preview["scheduled"]:
        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = ctx.now
        booking.cancelled_by = ctx.actor
        booking.cancel_reason = "CLOSURE"
        booking.late_cancel = False
        booking.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "late_cancel"])
        record_audit(
            action="booking.cancelled",
            entity_type="Booking",
            entity_id=booking.pk,
            actor=ctx.actor,
            changes={"status": booking.status, "reason": "CLOSURE", "closure_id": closure.pk},
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
                "reason": "CLOSURE",
                "closure_reason": reason,
            },
        )

    for booking in preview["in_use"]:
        booking.needs_review = True
        booking.review_note = (
            f"Room closed during an active session ({reason.strip()}). "
            "Confirm on site whether the student was asked to leave."
        )
        booking.save(update_fields=["needs_review", "review_note"])

    record_audit(
        action="closure.created",
        entity_type="Closure",
        entity_id=closure.pk,
        actor=ctx.actor,
        changes={
            "room": getattr(room, "number", None),
            "all_rooms": room is None,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "cancelled": len(preview["scheduled"]),
            "flagged_in_use": len(preview["in_use"]),
        },
        reason=reason,
    )
    return closure


def revoke_closure(ctx, *, closure: Closure, reason: str) -> Closure:
    """Reopen a room. Cancelled bookings stay cancelled and completed slots stay used."""
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    closure = reload_for_update(closure)
    if closure.revoked_at is not None:
        raise OperationRejected(Code.TERMINAL_STATUS, closure_id=closure.pk)

    closure.revoked_at = ctx.now
    closure.revoked_by = ctx.actor
    closure.revoked_reason = reason.strip()
    closure.save(update_fields=["revoked_at", "revoked_by", "revoked_reason"])

    record_audit(
        action="closure.revoked",
        entity_type="Closure",
        entity_id=closure.pk,
        actor=ctx.actor,
        changes={"restores_bookings": False, "makes_slots_reusable": False},
        reason=reason,
    )
    return closure


def complete_session_early(ctx, *, booking: Booking, reason: str) -> Booking:
    """Staff-confirmed evacuation or incident completion.

    The historical slot stays occupied, so the room is not resold for that hour
    (V3 section 6).
    """
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    booking = reload_for_update(booking)
    if booking.status != Booking.Status.IN_USE:
        raise OperationRejected(Code.TERMINAL_STATUS, booking_id=booking.pk)

    booking.status = Booking.Status.COMPLETED
    booking.actual_end = ctx.now
    booking.completion_reason = reason.strip()
    booking.needs_review = False
    booking.review_note = ""
    booking.save(update_fields=["status", "actual_end", "completion_reason", "needs_review", "review_note"])

    record_audit(
        action="booking.completed_early",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=ctx.actor,
        changes={"actual_end": booking.actual_end, "slot_end": booking.slot_end},
        reason=reason,
    )
    return booking


def resolve_review(ctx, *, booking: Booking, note: str) -> Booking:
    """Clear the on-site review flag once staff have checked what happened."""
    if not (note or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    booking = reload_for_update(booking)
    booking.needs_review = False
    booking.review_note = note.strip()
    booking.save(update_fields=["needs_review", "review_note"])
    record_audit(
        action="booking.review_resolved",
        entity_type="Booking",
        entity_id=booking.pk,
        actor=ctx.actor,
        reason=note,
    )
    return booking


def set_calendar_override(
    ctx, *, local_date: date, is_open: bool, open_hour: int | None, close_hour: int | None, reason: str
) -> CalendarOverride:
    """Create or replace the override for a date.

    An override changes the day's opening hours. It can never defeat an explicit
    closure, because closures are evaluated separately and win.
    """
    if close_hour is not None and open_hour is not None and close_hour <= open_hour:
        raise OperationRejected(Code.INVALID_INPUT)
    if is_open and (open_hour is None or close_hour is None):
        raise OperationRejected(Code.INVALID_INPUT)

    with_overrides = {
        "is_open": is_open,
        "open_hour": open_hour if is_open else None,
        "close_hour": close_hour if is_open else None,
        "reason": reason,
        "created_by": ctx.actor,
    }

    override, created = CalendarOverride.objects.update_or_create(
        local_date=local_date, defaults=with_overrides
    )

    record_audit(
        action="calendar.override_set" if created else "calendar.override_replaced",
        entity_type="CalendarOverride",
        entity_id=override.pk,
        actor=ctx.actor,
        changes={
            "local_date": local_date,
            "is_open": is_open,
            "open_hour": open_hour,
            "close_hour": close_hour,
        },
        reason=reason,
    )
    return override


def set_room_audience(ctx, *, room: Room, scope: str, categories) -> dict:
    """Change who may reserve a room, and record what it was before.

    Existing bookings are deliberately left alone: a student who already holds a
    reservation keeps it, and can still check in. Restricting a room is not a
    reason to cancel somebody's booking after the fact.
    """
    if scope not in Room.ReservationScope.values:
        raise OperationRejected(Code.INVALID_INPUT)

    known = {choice for choice, _ in InstrumentCategory.choices}
    unknown = sorted(set(categories) - known)
    if unknown:
        raise OperationRejected(Code.INVALID_INPUT, unknown_categories=unknown)

    room = reload_for_update(room)
    changes = set_audience(room=room, scope=scope, categories=categories)

    ctx.audit(
        action="room.audience_changed",
        entity_type="Room",
        entity_id=room.pk,
        actor=ctx.actor,
        changes={"room": room.number, **changes},
        reason="Staff changed who may reserve this room.",
    )

    return {"room_id": room.pk, **changes}


def deactivate_room(ctx, *, room: Room, reason: str) -> dict:
    """Take a room out of service, cancelling its scheduled bookings."""
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)

    room = reload_for_update(room)
    room.is_active = False
    room.save(update_fields=["is_active"])

    scheduled = list(
        Booking.objects.filter(
            room=room,
            status__in=[Booking.Status.PENDING_APPROVAL, Booking.Status.SCHEDULED],
        ).select_related("user")
    )
    for booking in scheduled:
        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = ctx.now
        booking.cancelled_by = ctx.actor
        booking.cancel_reason = "ROOM_DEACTIVATED"
        booking.late_cancel = False
        booking.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "late_cancel"])
        enqueue(
            kind="booking_cancelled",
            recipient=booking.user,
            dedupe_key=f"booking_cancelled:{booking.pk}",
            payload={
                "booking_id": booking.pk,
                "room_number": room.number,
                "room_label": str(room),
                "slot_start": booking.slot_start.isoformat(),
                "slot_end": booking.slot_end.isoformat(),
                "reason": "ROOM_DEACTIVATED",
            },
        )

    flagged = list(Booking.objects.filter(room=room, status=Booking.Status.IN_USE))
    for booking in flagged:
        booking.needs_review = True
        booking.review_note = f"Room deactivated during an active session ({reason})."
        booking.save(update_fields=["needs_review", "review_note"])

    record_audit(
        action="room.deactivated",
        entity_type="Room",
        entity_id=room.pk,
        actor=ctx.actor,
        changes={"cancelled": len(scheduled), "flagged_in_use": len(flagged)},
        reason=reason,
    )
    return {"cancelled": len(scheduled), "flagged": len(flagged)}


def deactivate_account(ctx, *, user: User, reason: str) -> dict:
    """Retire an account without deleting history.

    Bookings and sanctions are preserved: relationships are PROTECT, and account
    retirement never cascade-deletes them (V3 section 4).
    """
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    user = reload_for_update(user)
    if user.is_superuser:
        raise OperationRejected(Code.INVALID_INPUT)

    user.is_active = False
    user.save(update_fields=["is_active"])

    scheduled = list(
        Booking.objects.filter(
            user=user,
            status__in=[Booking.Status.PENDING_APPROVAL, Booking.Status.SCHEDULED],
        )
    )
    for booking in scheduled:
        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = ctx.now
        booking.cancelled_by = ctx.actor
        booking.cancel_reason = "ACCOUNT_DEACTIVATED"
        booking.late_cancel = False
        booking.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "late_cancel"])

    flagged = list(Booking.objects.filter(user=user, status=Booking.Status.IN_USE))
    for booking in flagged:
        booking.needs_review = True
        booking.review_note = f"Account deactivated during an active session ({reason})."
        booking.save(update_fields=["needs_review", "review_note"])

    record_audit(
        action="account.deactivated",
        entity_type="User",
        entity_id=user.pk,
        actor=ctx.actor,
        changes={"cancelled": len(scheduled), "flagged_in_use": len(flagged)},
        reason=reason,
    )
    return {"cancelled": len(scheduled), "flagged": len(flagged)}


def update_policy(ctx, *, snapshot: dict, label: str, note: str):
    """Create the next immutable policy version from validated values."""
    try:
        policy = _create_policy_version(snapshot=snapshot, actor=ctx.actor, label=label, note=note)
    except ValidationError as exc:
        raise OperationRejected(Code.INVALID_INPUT, errors=getattr(exc, "messages", [])) from exc

    record_audit(
        action="policy.version_created",
        entity_type="PolicyVersion",
        entity_id=policy.pk,
        actor=ctx.actor,
        changes={"version": policy.version, "snapshot": policy.snapshot},
        reason=note or label,
    )
    return policy


def active_suspensions(now):
    return Suspension.objects.active(now).select_related("user").order_by("ends_at")


def reviews_pending():
    return Booking.objects.filter(needs_review=True, status=Booking.Status.IN_USE).select_related(
        "room", "user"
    )


def slot_date_bounds(local_date: date):
    """UTC bounds covering a Bangkok local date, for range queries."""
    start = slots.slot_start_for(local_date, 0)
    return start, start + slots.SLOT_DELTA * 24
