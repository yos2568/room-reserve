"""Strikes, suspensions and review rules (A15-A17)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core.models import Booking, Suspension, Violation
from core.services import clock, sanctions, slots
from core.services.errors import Code
from core.services.protocol import run_operation
from core.services.reconcile import reconcile
from tests import factories, helpers
from tests.helpers import _body

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14)


def at(hour, minute=0, second=0):
    return factories.bangkok(2026, 9, 14, hour, minute, second)


def _record_manual_violation(ctx, user, note, counts_as_strike=True):
    violation = sanctions.record_manual_violation(
        ctx, user=user, note=note, counts_as_strike=counts_as_strike
    )
    return {"violation_id": violation.pk}


def _void(ctx, violation, reason):
    return sanctions.void_violation(ctx, violation=violation, reason=reason)


# --- A15: threshold, window boundary and idempotence ---------------------------


def test_three_strikes_cause_one_seven_day_suspension(db, student, rooms):
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))

    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))

    suspension = Suspension.objects.get(user=student)
    assert suspension.starts_at == at(12, 30)
    assert suspension.ends_at == at(12, 30) + timedelta(days=7)
    assert suspension.source == Suspension.Source.AUTO
    assert suspension.consumed_strikes.count() == 3


def test_a_strike_at_its_exact_expiry_does_not_count(db, student, rooms):
    """Expiry is exclusive: [occurred, occurred+30d)."""
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 8))
    with clock.frozen_clock(at(8, 20)):
        reconcile(at(8, 20))
    violation = Violation.objects.get(booking=booking)
    # Occurrence is the check-in deadline, not when the job ran.
    assert violation.occurred_at == slots.slot_start_for(DAY.date(), 8) + timedelta(minutes=15)

    expires = violation.expires_at
    assert violation.is_eligible_strike(expires) is False
    assert violation.is_eligible_strike(expires - timedelta(seconds=1)) is True


def test_repeat_reconciliation_does_not_stack_sanctions(db, student, rooms):
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))

    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))
        reconcile(at(12, 30))
        reconcile(at(12, 30))

    assert Suspension.objects.filter(user=student).count() == 1


# --- A16: consumption and review ------------------------------------------------


def test_consumed_strikes_never_resuspend(db, student, rooms):
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))

    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))
    suspension = Suspension.objects.get(user=student)

    after_end = suspension.ends_at + timedelta(minutes=1)
    with clock.frozen_clock(after_end):
        reconcile(after_end)

    assert Suspension.objects.filter(user=student).count() == 1


def test_voiding_a_consumed_strike_opens_review_not_silent_lift(db, student, staff_user, rooms):
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))

    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))
    suspension = Suspension.objects.get(user=student)
    consumed = list(suspension.consumed_strikes.all())

    with clock.frozen_clock(at(13, 0)):
        outcome = run_operation(
            actor=staff_user,
            operation="staff_void_violation",
            payload={"violation": consumed[0].pk, "reason": "system fault"},
            body=_body(lambda ctx: _void(ctx, consumed[0], "system fault")),
        )
    assert outcome.ok
    assert outcome.data["review_required"] is True
    assert outcome.data["suspension_id"] == suspension.pk

    suspension.refresh_from_db()
    assert suspension.lifted_at is None, "voiding opens a review; it never silently lifts"


def test_new_strike_after_expiry_can_trigger_again(db, student, rooms):
    """After a suspension ends, brand-new unconsumed strikes count again."""
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))

    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))
    first = Suspension.objects.get(user=student)

    # The sanction ends; the next day brings three more due no-shows.
    later = first.ends_at + timedelta(days=1)
    later_date = clock.local_date(later)
    for room, hour in zip(rooms[3:6], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(later_date, hour))

    end_of_day = clock.local_time(later).replace(hour=13, minute=0)
    with clock.frozen_clock(end_of_day):
        reconcile(end_of_day)

    assert Suspension.objects.filter(user=student).count() == 2
    first.refresh_from_db()
    second = Suspension.objects.filter(user=student).order_by("-starts_at").first()
    # Consumed strikes from the first sanction stay consumed.
    assert first.consumed_strikes.count() == 3
    assert second.consumed_strikes.count() == 3


def test_while_suspended_no_stacking(db, student, staff_user, rooms):
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))

    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))
    suspension = Suspension.objects.get(user=student)

    # While still suspended, a fresh manual violation must not stack a second one.
    with clock.frozen_clock(suspension.starts_at + timedelta(hours=1)):
        outcome = run_operation(
            actor=staff_user,
            operation="staff_record_violation",
            payload={"user": student.pk, "note": "seen leaving early"},
            body=_body(lambda ctx: _record_manual_violation(ctx, student, "seen leaving early")),
        )
    assert outcome.ok, outcome.code
    assert Suspension.objects.filter(user=student).count() == 1
    assert Violation.objects.filter(user=student, counts_as_strike=True).count() == 4


# --- A17: suspension effects -----------------------------------------------------


def test_suspension_cancels_scheduled_without_penalty_and_preserves_in_use(db, student, rooms):
    today = DAY.date()
    upcoming1 = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(today, 14))
    upcoming2 = factories.make_booking(student, rooms[1], slot_start=slots.slot_start_for(today, 16))
    in_use = factories.make_booking(
        student, rooms[2], slot_start=slots.slot_start_for(today, 12), status=Booking.Status.IN_USE
    )

    # Three no-shows trigger the suspension.
    for room, hour in zip(rooms[3:6], (8, 9, 10), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))

    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))

    upcoming1.refresh_from_db()
    upcoming2.refresh_from_db()
    in_use.refresh_from_db()
    assert upcoming1.status == Booking.Status.CANCELLED
    assert upcoming2.status == Booking.Status.CANCELLED
    assert upcoming1.cancel_reason == "SUSPENSION"
    # No extra penalty: no violation rows were created for the cancelled bookings.
    assert Violation.objects.filter(booking=upcoming1).count() == 0
    assert in_use.status == Booking.Status.IN_USE, "an in-use session may finish"


def test_suspended_user_can_sign_in_but_not_book(db, student, rooms):
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))
    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))

    # Login remains possible (the account is still active).
    assert student.is_active

    with clock.frozen_clock(at(12, 35)):
        outcome = helpers.advance_booking(student, rooms[6], slots.slot_start_for(today, 14))
        assert not outcome.ok
        assert outcome.code == Code.SUSPENDED

        outcome = helpers.use_now(student, rooms[7])
        assert not outcome.ok
        assert outcome.code == Code.SUSPENDED


def test_strike_summary_keeps_history_distinct(db, student, rooms):
    """UI must separate 'strikes toward the next suspension' from total history."""
    today = DAY.date()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=slots.slot_start_for(today, hour))
    with clock.frozen_clock(at(12, 30)):
        reconcile(at(12, 30))

    summary = sanctions.strike_summary(student, at(12, 30))
    assert summary["eligible_count"] == 0, "all three strikes are now consumed"
    assert summary["consumed"] == 3
    assert summary["total_history"] == 3
