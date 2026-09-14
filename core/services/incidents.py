"""Service incidents (V3 sections 7 and 11).

A recorded incident interval and room scope means affected students are not
penalised. Scheduled bookings inside it can be cancelled without penalty, and if
no-shows were already recorded staff voids the affected strikes and reviews any
resulting sanction. Attendance is never silently backdated.
"""

from __future__ import annotations

import logging
from datetime import datetime

from core.models import Booking, Room, ServiceIncident, Suspension, Violation

from . import clock
from .audit import record_audit
from .errors import Code, OperationRejected
from .outbox import enqueue

logger = logging.getLogger(__name__)


def create_incident(
    ctx,
    *,
    starts_at: datetime,
    ends_at: datetime,
    rooms: list[Room],
    reason: str,
    cancel_scheduled: bool = True,
) -> dict:
    """Record an incident and optionally cancel the affected scheduled bookings."""
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    if ends_at <= starts_at:
        raise OperationRejected(Code.INVALID_INPUT)

    incident = ServiceIncident.objects.create(
        starts_at=starts_at,
        ends_at=ends_at,
        reason=reason.strip(),
        created_by=ctx.actor,
    )
    if rooms:
        incident.rooms.set(rooms)

    room_ids = {room.pk for room in rooms}
    affected = (
        Booking.objects.filter(slot_start__lt=ends_at, slot_end__gt=starts_at)
        .filter(status__in=[Booking.Status.SCHEDULED, Booking.Status.NO_SHOW])
        .select_related("room", "user")
    )
    affected = [booking for booking in affected if not room_ids or booking.room_id in room_ids]

    cancelled = 0
    existing_no_shows = 0
    for booking in affected:
        booking.incident = incident
        if booking.status == Booking.Status.NO_SHOW:
            existing_no_shows += 1
            booking.save(update_fields=["incident"])
            continue

        if not cancel_scheduled:
            booking.save(update_fields=["incident"])
            continue

        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = ctx.now
        booking.cancelled_by = ctx.actor if getattr(ctx.actor, "pk", None) else None
        booking.cancel_reason = "SERVICE_INCIDENT"
        # An incident cancellation is never the student's late cancel.
        booking.late_cancel = False
        booking.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "late_cancel",
                "incident",
            ]
        )
        cancelled += 1
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
                "reason": "SERVICE_INCIDENT",
                "incident_reason": reason,
            },
        )

    record_audit(
        action="incident.created",
        entity_type="ServiceIncident",
        entity_id=incident.pk,
        actor=ctx.actor,
        changes={
            "starts_at": starts_at,
            "ends_at": ends_at,
            "rooms": sorted(room_ids),
            "cancelled": cancelled,
            "existing_no_shows": existing_no_shows,
        },
        reason=reason,
    )

    return {
        "incident_id": incident.pk,
        "cancelled": cancelled,
        "existing_no_shows": existing_no_shows,
    }


def void_incident_violations(ctx, *, incident: ServiceIncident, reason: str) -> dict:
    """Void no-show strikes recorded during an incident and flag sanctions to review.

    V3 section 7: "If no-shows were already recorded, staff voids affected strikes
    and reviews sanctions; do not silently backdate attendance."
    """
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)

    room_ids = {room.pk for room in incident.rooms.all()}
    bookings = (
        Booking.objects.filter(slot_start__lt=incident.ends_at, slot_end__gt=incident.starts_at)
        .filter(status=Booking.Status.NO_SHOW)
        .select_related("room")
    )
    booking_ids = [
        booking.pk for booking in bookings if not room_ids or booking.room_id in room_ids
    ]

    violations = list(
        Violation.objects.filter(booking_id__in=booking_ids, kind=Violation.Kind.NO_SHOW)
        .filter(voided_at__isnull=True)
        .select_related("consumed_by")
    )

    suspensions_to_review: set[int] = set()
    for violation in violations:
        violation.voided_at = ctx.now
        violation.voided_by = ctx.actor if getattr(ctx.actor, "pk", None) else None
        violation.void_reason = reason.strip()
        violation.incident = incident
        violation.save(update_fields=["voided_at", "voided_by", "void_reason", "incident"])
        if violation.consumed_by_id and violation.consumed_by.lifted_at is None:
            suspensions_to_review.add(violation.consumed_by_id)

    record_audit(
        action="incident.violations_voided",
        entity_type="ServiceIncident",
        entity_id=incident.pk,
        actor=ctx.actor,
        changes={
            "voided": len(violations),
            "suspensions_to_review": sorted(suspensions_to_review),
        },
        reason=reason,
    )

    return {
        "voided": len(violations),
        "suspensions_to_review": sorted(suspensions_to_review),
    }


def open_incidents():
    return ServiceIncident.objects.filter(resolved_at__isnull=True).prefetch_related("rooms")


def sanctions_under_review():
    """Active suspensions whose consumed strikes have since been voided."""
    return (
        Suspension.objects.active(clock.now())
        .filter(consumed_strikes__voided_at__isnull=False)
        .distinct()
        .select_related("user")
    )
