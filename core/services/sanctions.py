"""Strikes, sanctions and their review (V3 section 7).

Staff never edit a lifecycle field directly; these functions are the only path,
and each one writes an audit event naming the reason. Automatic sanctions are
applied by the reconciler; this module covers manual violations, manual
suspensions, lifts and the review a void opens.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from core.models import Notification, Suspension, User, Violation

from .audit import record_audit
from .errors import Code, OperationRejected
from .outbox import enqueue
from .policy import current_policy
from .refs import reload_for_update

logger = logging.getLogger(__name__)


def record_manual_violation(
    ctx,
    *,
    user: User,
    note: str,
    counts_as_strike: bool = True,
    occurred_at=None,
    incident=None,
) -> Violation:
    """Record a staff-observed violation and re-evaluate the strike threshold."""
    if not (note or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)

    now = ctx.now
    policy = current_policy()
    occurred_at = occurred_at or now

    violation = Violation.objects.create(
        user=user,
        kind=Violation.Kind.MANUAL,
        occurred_at=occurred_at,
        # The window is frozen at recording time, so a later policy edit cannot
        # retrospectively lengthen it.
        expires_at=occurred_at + timedelta(days=policy.strike_window_days),
        counts_as_strike=counts_as_strike,
        actor=ctx.actor,
        note=note.strip(),
        incident=incident,
    )

    record_audit(
        action="violation.recorded",
        entity_type="Violation",
        entity_id=violation.pk,
        actor=ctx.actor,
        changes={
            "user": user.pk,
            "counts_as_strike": counts_as_strike,
            "occurred_at": occurred_at,
            "expires_at": violation.expires_at,
        },
        reason=note,
    )

    # A staff violation change can complete a threshold, so evaluate immediately.
    from . import reconcile

    reconcile._apply_strike_thresholds(now, ctx.actor)
    return violation


def void_violation(ctx, *, violation: Violation, reason: str) -> dict:
    """Void a violation.

    Voiding a *consumed* strike does not silently lift the sanction it caused:
    it opens a review that staff must decide explicitly (V3 section 7).
    """
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    violation = reload_for_update(violation)
    if violation.voided_at is not None:
        raise OperationRejected(Code.TERMINAL_STATUS, violation_id=violation.pk)

    was_consumed_by = violation.consumed_by
    violation.voided_at = ctx.now
    violation.voided_by = ctx.actor
    violation.void_reason = reason.strip()
    violation.save(update_fields=["voided_at", "voided_by", "void_reason"])

    record_audit(
        action="violation.voided",
        entity_type="Violation",
        entity_id=violation.pk,
        actor=ctx.actor,
        changes={
            "consumed_by_suspension": getattr(was_consumed_by, "pk", None),
            "counts_as_strike": violation.counts_as_strike,
        },
        reason=reason,
    )

    review_required = was_consumed_by is not None and was_consumed_by.lifted_at is None

    if review_required:
        enqueue(
            kind="sanction_applied",
            recipient=violation.user,
            dedupe_key=f"violation_voided_review:{violation.pk}",
            payload={
                "suspension_id": was_consumed_by.pk,
                "review": True,
                "reason": reason,
                "starts_at": was_consumed_by.starts_at.isoformat(),
                "ends_at": was_consumed_by.ends_at.isoformat() if was_consumed_by.ends_at else None,
            },
        )
        logger.info(
            "Voiding violation %s opened a review of suspension %s",
            violation.pk,
            was_consumed_by.pk,
        )

    return {
        "violation_id": violation.pk,
        "review_required": review_required,
        "suspension_id": getattr(was_consumed_by, "pk", None),
    }


def apply_manual_suspension(ctx, *, user: User, reason: str, ends_at=None) -> Suspension:
    """Create a staff suspension, or update the one already in force.

    V3 section 4: multiple concurrently active suspensions are prevented in the
    service under the common lock; staff edit the active sanction rather than
    creating an overlapping one.
    """
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)

    now = ctx.now
    user = reload_for_update(user)
    active = Suspension.objects.active(now).filter(user=user).first()
    if active is not None:
        return extend_or_shorten_suspension(
            ctx, suspension=active, ends_at=ends_at, reason=reason, staff_override=True
        )

    if ends_at is not None and ends_at <= now:
        raise OperationRejected(Code.INVALID_INPUT)

    suspension = Suspension.objects.create(
        user=user,
        starts_at=now,
        ends_at=ends_at,
        source=Suspension.Source.MANUAL,
        reason=reason.strip(),
        created_by=ctx.actor,
        start_notified_at=now,
    )

    record_audit(
        action="suspension.applied",
        entity_type="Suspension",
        entity_id=suspension.pk,
        actor=ctx.actor,
        changes={"starts_at": now, "ends_at": ends_at, "source": suspension.source},
        reason=reason,
    )
    enqueue(
        kind="sanction_applied",
        recipient=user,
        dedupe_key=f"suspension_applied:{suspension.pk}",
        payload={
            "suspension_id": suspension.pk,
            "starts_at": now.isoformat(),
            "ends_at": ends_at.isoformat() if ends_at else None,
            "reason": reason,
            "manual": True,
        },
    )

    _cancel_scheduled(ctx, user=user, suspension=suspension, reason="SUSPENSION")
    return suspension


def extend_or_shorten_suspension(ctx, *, suspension: Suspension, ends_at, reason: str, staff_override=False):
    """Adjust an active sanction in place rather than stacking a second one."""
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    suspension = reload_for_update(suspension)
    if suspension.lifted_at is not None:
        raise OperationRejected(Code.TERMINAL_STATUS, suspension_id=suspension.pk)
    if ends_at is not None and ends_at <= suspension.starts_at:
        raise OperationRejected(Code.INVALID_INPUT)

    previous_end = suspension.ends_at
    suspension.ends_at = ends_at
    # A change of end time warrants a fresh notice.
    suspension.end_notified_at = None
    suspension.save(update_fields=["ends_at", "end_notified_at"])

    record_audit(
        action="suspension.adjusted",
        entity_type="Suspension",
        entity_id=suspension.pk,
        actor=ctx.actor,
        changes={
            "previous_ends_at": previous_end,
            "ends_at": ends_at,
            "staff_override": staff_override,
        },
        reason=reason,
    )
    return suspension


def lift_suspension(ctx, *, suspension: Suspension, reason: str) -> Suspension:
    """Lift a sanction early.

    Consumed strikes stay consumed, so an early lift does not let the same
    strikes trigger a second automatic suspension (V3 section 7).
    """
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT)
    suspension = reload_for_update(suspension)
    if suspension.lifted_at is not None:
        raise OperationRejected(Code.TERMINAL_STATUS, suspension_id=suspension.pk)

    suspension.lifted_at = ctx.now
    suspension.lifted_by = ctx.actor
    suspension.lift_reason = reason.strip()
    suspension.save(update_fields=["lifted_at", "lifted_by", "lift_reason"])

    record_audit(
        action="suspension.lifted",
        entity_type="Suspension",
        entity_id=suspension.pk,
        actor=ctx.actor,
        changes={"lifted_at": suspension.lifted_at, "consumed_strikes_remain": True},
        reason=reason,
    )
    enqueue(
        kind="sanction_lifted",
        recipient=suspension.user,
        dedupe_key=f"suspension_lifted:{suspension.pk}",
        payload={
            "suspension_id": suspension.pk,
            "lifted_at": suspension.lifted_at.isoformat(),
            "reason": reason,
        },
    )
    return suspension


def _cancel_scheduled(ctx, *, user: User, suspension: Suspension, reason: str) -> int:
    """Cancel remaining SCHEDULED bookings without extra penalty."""
    from core.models import Booking

    remaining = list(
        Booking.objects.filter(user=user, status=Booking.Status.SCHEDULED)
        .order_by("slot_start")
        .select_related("room")
    )
    for booking in remaining:
        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = ctx.now
        booking.cancelled_by = ctx.actor if getattr(ctx.actor, "pk", None) else None
        booking.cancel_reason = reason
        booking.late_cancel = False
        booking.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "late_cancel"])
        record_audit(
            action="booking.cancelled",
            entity_type="Booking",
            entity_id=booking.pk,
            actor=ctx.actor,
            changes={"status": booking.status, "reason": reason},
            reason="Cancelled due to suspension; no additional penalty.",
        )
        enqueue(
            kind="booking_cancelled",
            recipient=user,
            dedupe_key=f"booking_cancelled:{booking.pk}",
            payload={
                "booking_id": booking.pk,
                "room_number": booking.room.number,
                "room_label": str(booking.room),
                "slot_start": booking.slot_start.isoformat(),
                "slot_end": booking.slot_end.isoformat(),
                "reason": reason,
            },
        )
    return len(remaining)


def pending_appeals(user) -> list[dict]:
    """Appeal information shown on My bookings (V3 section 9)."""
    rows = []
    for suspension in Suspension.objects.filter(user=user).select_related("lifted_by")[:10]:
        strikes = list(suspension.consumed_strikes.all())
        rows.append(
            {
                "suspension": suspension,
                "strikes": strikes,
                "voided_strikes": [s for s in strikes if s.voided_at is not None],
            }
        )
    return rows


def strike_summary(user, now) -> dict:
    """Separate historical violations from strikes toward the next sanction."""
    policy = current_policy()
    eligible = Violation.objects.eligible_strikes(now).filter(user=user)
    upcoming = [
        {
            "violation": violation,
            "expires_at": violation.expires_at,
        }
        for violation in eligible.order_by("expires_at")
    ]
    return {
        "eligible_count": len(upcoming),
        "threshold": policy.strike_threshold,
        "window_days": policy.strike_window_days,
        "upcoming": upcoming,
        "total_history": Violation.objects.filter(user=user).count(),
        "consumed": Violation.objects.filter(user=user, consumed_by__isnull=False).count(),
    }


def failed_notifications(user=None):
    query = Notification.objects.filter(status=Notification.Status.EXHAUSTED)
    if user is not None:
        query = query.filter(recipient=user)
    return query.order_by("-created_at")
