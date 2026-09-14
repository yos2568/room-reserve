"""Reconciliation of due state.

Runs inside the control-lock transaction before the requested operation is
validated, so a rejected request still commits the no-show, completion and
suspension work that made the rejection correct (V3 sections 5, 6 and 7).

Determinism matters: overdue bookings are processed in deadline/ID order, and
sanctions are evaluated from the recorded occurrence times rather than the time
the job happened to run. If the backlog exceeds the configured request budget we
fail closed instead of skipping rows.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Count

from core.models import Booking, ServiceIncident, Suspension, Violation

from . import slots
from .audit import record_audit
from .errors import ReconciliationBacklog
from .outbox import enqueue
from .policy import current_policy

logger = logging.getLogger(__name__)


def reconcile(now, *, actor=None, budget: int | None = None) -> dict:
    """Bring persisted state up to date. Must run under the control lock."""
    budget = budget or settings.RECONCILE_BUDGET
    summary = {
        "no_shows": _reconcile_no_shows(now, budget, actor),
        "completions": _reconcile_completions(now, budget, actor),
        "suspensions": _apply_strike_thresholds(now, actor),
        "lifts": _notify_finished_suspensions(now),
    }
    return summary


def _reconcile_no_shows(now, budget: int, actor) -> int:
    due = list(
        Booking.objects.filter(status=Booking.Status.SCHEDULED, deadline__lte=now)
        .order_by("deadline", "id")
        .select_related("room", "user")
    )
    if len(due) > budget:
        raise ReconciliationBacklog(
            f"{len(due)} bookings are due for no-show reconciliation, above the budget of {budget}."
        )

    policy = current_policy()
    processed = 0
    for booking in due:
        if _incident_covers(booking, now):
            # A confirmed service incident: staff decides, we never auto-penalise.
            continue
        _record_no_show(booking, now, policy, actor)
        processed += 1
    return processed


def _record_no_show(booking: Booking, now, policy, actor) -> None:
    occurred_at = booking.deadline
    expires_at = occurred_at + timedelta(days=policy.strike_window_days)

    booking.status = Booking.Status.NO_SHOW
    booking.save(update_fields=["status"])

    violation, created = Violation.objects.get_or_create(
        booking=booking,
        kind=Violation.Kind.NO_SHOW,
        defaults={
            "user": booking.user,
            "occurred_at": occurred_at,
            "expires_at": expires_at,
            "counts_as_strike": True,
            "note": "Automatic no-show reconciliation.",
        },
    )
    if created:
        record_audit(
            action="booking.no_show",
            entity_type="Booking",
            entity_id=booking.pk,
            actor=actor,
            actor_label="" if actor else "reconciler",
            changes={
                "status": booking.status,
                "occurred_at": occurred_at,
                "expires_at": expires_at,
            },
            reason="Check-in deadline passed.",
        )
        enqueue(
            kind="no_show",
            recipient=booking.user,
            dedupe_key=f"no_show:{booking.pk}",
            payload=_booking_payload(booking),
        )
        logger.info("Booking %s recorded as no-show (violation %s)", booking.pk, violation.pk)


def _reconcile_completions(now, budget: int, actor) -> int:
    due = list(
        Booking.objects.filter(status=Booking.Status.IN_USE, slot_end__lte=now)
        .order_by("slot_end", "id")
        .select_related("room", "user")
    )
    if len(due) > budget:
        raise ReconciliationBacklog(
            f"{len(due)} sessions are due for completion, above the budget of {budget}."
        )

    for booking in due:
        booking.status = Booking.Status.COMPLETED
        booking.actual_end = booking.slot_end
        booking.save(update_fields=["status", "actual_end"])
        record_audit(
            action="booking.completed",
            entity_type="Booking",
            entity_id=booking.pk,
            actor=actor,
            actor_label="" if actor else "reconciler",
            changes={"actual_end": booking.actual_end},
            reason="Slot end reached.",
        )
    return len(due)


def _apply_strike_thresholds(now, actor) -> int:
    """Create one automatic suspension per user with enough eligible strikes."""
    policy = current_policy()
    rows = (
        Violation.objects.eligible_strikes(now)
        .values("user_id")
        .annotate(total=Count("id"))
        .filter(total__gte=policy.strike_threshold)
    )

    created_count = 0
    for row in rows:
        user_id = row["user_id"]
        # Already suspended: never stack or extend an automatic sanction.
        if Suspension.objects.active(now).filter(user_id=user_id).exists():
            continue
        _create_automatic_suspension(user_id, now, policy, actor)
        created_count += 1
    return created_count


def _create_automatic_suspension(user_id: int, now, policy, actor) -> Suspension:
    from core.models import User

    user = User.objects.get(pk=user_id)
    suspension = Suspension.objects.create(
        user=user,
        starts_at=now,
        ends_at=now + timedelta(days=policy.auto_suspension_days),
        source=Suspension.Source.AUTO,
        reason=(
            f"{policy.strike_threshold} eligible strikes within "
            f"{policy.strike_window_days} days."
        ),
        created_by=actor if getattr(actor, "pk", None) else None,
        start_notified_at=now,
    )

    # Link ALL currently eligible strikes, so none can trigger a second sanction.
    consumed = Violation.objects.eligible_strikes(now).filter(user_id=user_id)
    consumed_ids = list(consumed.values_list("pk", flat=True))
    consumed.update(consumed_by=suspension)

    record_audit(
        action="suspension.applied",
        entity_type="Suspension",
        entity_id=suspension.pk,
        actor=actor,
        actor_label="" if actor else "reconciler",
        changes={
            "starts_at": suspension.starts_at,
            "ends_at": suspension.ends_at,
            "consumed_violations": consumed_ids,
        },
        reason=suspension.reason,
    )
    enqueue(
        kind="sanction_applied",
        recipient=user,
        dedupe_key=f"suspension_applied:{suspension.pk}",
        payload={
            "suspension_id": suspension.pk,
            "starts_at": suspension.starts_at.isoformat(),
            "ends_at": suspension.ends_at.isoformat() if suspension.ends_at else None,
            "strike_count": len(consumed_ids),
            "reason": suspension.reason,
        },
    )

    _cancel_scheduled_for_suspension(user, suspension, now, actor)
    return suspension


def _cancel_scheduled_for_suspension(user, suspension, now, actor) -> int:
    """Cancel every remaining SCHEDULED booking, including beyond the sanction."""
    remaining = list(
        Booking.objects.filter(user=user, status=Booking.Status.SCHEDULED)
        .order_by("slot_start")
        .select_related("room")
    )
    for booking in remaining:
        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = now
        booking.cancelled_by = actor if getattr(actor, "pk", None) else None
        booking.cancel_reason = "SUSPENSION"
        # A suspension cancellation is not a late-cancel by the student.
        booking.late_cancel = False
        booking.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "late_cancel",
            ]
        )
        record_audit(
            action="booking.cancelled",
            entity_type="Booking",
            entity_id=booking.pk,
            actor=actor,
            actor_label="" if actor else "reconciler",
            changes={"status": booking.status, "reason": "SUSPENSION"},
            reason="Remaining bookings cancelled on suspension; no extra penalty.",
        )
        enqueue(
            kind="booking_cancelled",
            recipient=user,
            dedupe_key=f"booking_cancelled:{booking.pk}",
            payload=_booking_payload(booking) | {"reason": "SUSPENSION"},
        )
    return len(remaining)


def _notify_finished_suspensions(now) -> int:
    """Send a single lift notice when a sanction's window has passed."""
    finished = Suspension.objects.filter(
        lifted_at__isnull=True,
        ends_at__isnull=False,
        ends_at__lte=now,
        end_notified_at__isnull=True,
    ).select_related("user")

    count = 0
    for suspension in finished:
        suspension.end_notified_at = now
        suspension.save(update_fields=["end_notified_at"])
        enqueue(
            kind="sanction_lifted",
            recipient=suspension.user,
            dedupe_key=f"suspension_ended:{suspension.pk}",
            payload={
                "suspension_id": suspension.pk,
                "ends_at": suspension.ends_at.isoformat(),
                "automatic": True,
            },
        )
        count += 1
    return count


def _incident_covers(booking: Booking, now) -> bool:
    """True when a recorded service incident exempts this booking from no-show."""
    incidents = ServiceIncident.objects.filter(
        starts_at__lt=booking.slot_end, ends_at__gt=booking.slot_start
    ).prefetch_related("rooms")
    for incident in incidents:
        room_ids = {room.pk for room in incident.rooms.all()}
        if not room_ids or booking.room_id in room_ids:
            return True
    return False


def _booking_payload(booking: Booking) -> dict:
    return {
        "booking_id": booking.pk,
        "room_number": booking.room.number,
        "room_label": str(booking.room),
        "slot_start": booking.slot_start.isoformat(),
        "slot_end": booking.slot_end.isoformat(),
        "slot_start_local": slots.format_local(booking.slot_start),
        "source": booking.source,
    }


def enqueue_due_reminders(now) -> int:
    """Enqueue reminders for advance bookings inside the reminder lead window.

    Called by the scheduler tick rather than by request reconciliation, because a
    reminder is background work. A near-term reservation may receive the
    confirmation and the reminder together, and walk-ins never get a reminder
    (V3 section 10).
    """
    policy = current_policy()
    lead = timedelta(minutes=policy.reminder_lead_minutes)
    window_start = now
    window_end = now + lead

    candidates = (
        Booking.objects.filter(
            status=Booking.Status.SCHEDULED,
            source=Booking.Source.ADVANCE,
            slot_start__gt=window_start,
            slot_start__lte=window_end,
        )
        .order_by("slot_start")
        .select_related("room", "user")
    )

    count = 0
    for booking in candidates:
        _, created = enqueue(
            kind="booking_reminder",
            recipient=booking.user,
            dedupe_key=f"booking_reminder:{booking.pk}",
            payload=_booking_payload(booking),
        )
        if created:
            count += 1
    return count
