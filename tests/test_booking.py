"""Booking rules: horizon, calendar, quota, adjacency and the transaction protocol.

Maps to acceptance cases A02, A03, A07, A08, A11 and A14.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from core.models import AuditEvent, Booking, Violation
from core.services import clock, slots
from core.services.errors import Code
from tests import factories, helpers

pytestmark = pytest.mark.django_db


def day_offset(base, days):
    return (base.astimezone(slots.BANGKOK) + timedelta(days=days)).date()


# --- A02: horizon and calendar -------------------------------------------------


def test_same_day_future_booking_succeeds(frozen, student, rooms):
    slot_start = slots.slot_start_for(factories.bangkok(2026, 9, 14).date(), 11)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)

    assert outcome.ok, outcome.code
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    assert booking.status == Booking.Status.SCHEDULED
    assert booking.source == Booking.Source.ADVANCE
    assert booking.deadline == slot_start + timedelta(minutes=15)
    assert booking.slot_end - booking.slot_start == timedelta(hours=1)
    assert booking.slot_date == factories.bangkok(2026, 9, 14).date()


def test_exactly_the_horizon_ahead_succeeds(frozen, student, rooms):
    # The window is two days (D-38): Monday's frozen moment reaches Wednesday.
    target = day_offset(frozen, 2)
    slot_start = slots.slot_start_for(target, 11)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)
    assert outcome.ok, outcome.code


def test_beyond_the_horizon_fails(frozen, student, rooms):
    target = day_offset(frozen, 3)
    slot_start = slots.slot_start_for(target, 11)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)
    assert not outcome.ok
    assert outcome.code == Code.OUTSIDE_HORIZON


@pytest.mark.parametrize(
    "hour,label",
    [(7, "before opening"), (20, "after closing"), (23, "late night")],
)
def test_off_hour_rejected(frozen, student, rooms, hour, label):
    # Tomorrow, so the slot is in the future and only the calendar can reject it.
    tomorrow = day_offset(frozen, 1)
    slot_start = slots.slot_start_for(tomorrow, hour)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)
    assert not outcome.ok, label
    assert outcome.code == Code.CLOSED, label


def test_past_off_hour_is_reported_as_elapsed(frozen, student, rooms):
    """07:00 today has finished; that is a different fact from 'closed'."""
    today = factories.bangkok(2026, 9, 14).date()
    slot_start = slots.slot_start_for(today, 7)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)
    assert not outcome.ok
    assert outcome.code == Code.SLOT_ELAPSED


def test_weekend_rejected(frozen, wide_horizon, student, rooms):
    saturday = factories.bangkok(2026, 9, 19).date()
    assert saturday.weekday() == 5, "fixture date should be a Saturday"
    slot_start = slots.slot_start_for(saturday, 11)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)
    assert not outcome.ok
    assert outcome.code == Code.CLOSED


def test_hour_unaligned_slot_rejected(frozen, student, rooms):
    slot_start = slots.slot_start_for(factories.bangkok(2026, 9, 14).date(), 11) + timedelta(minutes=30)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)
    assert not outcome.ok
    assert outcome.code == Code.INVALID_SLOT


# --- A03: the exact hour boundary ---------------------------------------------


def test_at_exact_hour_advance_is_stale_and_offers_use_now(frozen, student, rooms):
    """A POST built before the hour must not silently become a check-in."""
    slot_start = slots.floor_to_slot(frozen)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)

    assert not outcome.ok
    assert outcome.code == Code.STALE_HOUR
    assert outcome.data["remaining_minutes"] == 20
    assert Booking.objects.count() == 0


def test_elapsed_slot_rejected(frozen, student, rooms):
    slot_start = slots.floor_to_slot(frozen) - timedelta(hours=2)
    outcome = helpers.advance_booking(student, rooms[0], slot_start)
    assert not outcome.ok
    assert outcome.code == Code.SLOT_ELAPSED


# --- A07: quota ----------------------------------------------------------------


def test_quota_allows_two_and_rejects_the_third(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    first = helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11))
    second = helpers.advance_booking(student, rooms[1], slots.slot_start_for(today, 13))
    third = helpers.advance_booking(student, rooms[2], slots.slot_start_for(today, 15))

    assert first.ok and second.ok
    assert not third.ok
    assert third.code == Code.QUOTA_EXCEEDED


def test_completed_and_no_show_count_toward_quota(frozen, student, rooms):
    """A completed 08:00 session plus a no-show 10:00 means no third booking."""
    today = factories.bangkok(2026, 9, 14).date()
    factories.make_booking(
        student, rooms[0], slot_start=slots.slot_start_for(today, 8), status=Booking.Status.COMPLETED
    )
    factories.make_booking(
        student, rooms[1], slot_start=slots.slot_start_for(today, 10), status=Booking.Status.NO_SHOW
    )

    outcome = helpers.advance_booking(student, rooms[2], slots.slot_start_for(today, 12))
    assert not outcome.ok
    assert outcome.code == Code.QUOTA_EXCEEDED


def test_cancellation_restores_quota(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    first = helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11))
    helpers.advance_booking(student, rooms[1], slots.slot_start_for(today, 13))

    cancelled = helpers.cancel(student, Booking.objects.get(pk=first.data["booking_id"]), "unwell")
    assert cancelled.ok, cancelled.code

    third = helpers.advance_booking(student, rooms[2], slots.slot_start_for(today, 15))
    assert third.ok, third.code


# --- A08: adjacency ------------------------------------------------------------


def test_same_hour_in_another_room_rejected(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11))
    outcome = helpers.advance_booking(student, rooms[1], slots.slot_start_for(today, 11))
    assert not outcome.ok
    assert outcome.code == Code.ADJACENCY_CONFLICT


def test_adjacent_hour_rejected(frozen, student, rooms):
    """Hours either side of an existing booking are blocked across rooms.

    The anchor is 12:00 because the clock is frozen at 10:40, so both 11:00 and
    13:00 are still future slots and only adjacency can reject them.
    """
    today = factories.bangkok(2026, 9, 14).date()
    first = helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 12))
    assert first.ok, first.code

    for hour in (11, 13):
        outcome = helpers.advance_booking(student, rooms[1], slots.slot_start_for(today, hour))
        assert not outcome.ok, hour
        assert outcome.code == Code.ADJACENCY_CONFLICT, hour


def test_short_walk_in_still_blocks_the_next_hour(db, student, rooms):
    """A walk-in at 08:45 occupies the 08:00 slot and blocks 09:00.

    Uses its own clock at 08:45 so that both the in-use walk-in and the 09:00
    target are meaningful; at the default fixture time 09:00 would already have
    elapsed.
    """
    moment = factories.bangkok(2026, 9, 14, 8, 45)
    with clock.frozen_clock(moment):
        today = moment.date()
        factories.make_booking(
            student,
            rooms[0],
            slot_start=slots.slot_start_for(today, 8),
            status=Booking.Status.IN_USE,
            source=Booking.Source.WALK_IN,
            checked_in_at=slots.slot_start_for(today, 8) + timedelta(minutes=45),
        )

        outcome = helpers.advance_booking(student, rooms[1], slots.slot_start_for(today, 9))
        assert not outcome.ok
        assert outcome.code == Code.ADJACENCY_CONFLICT


def test_two_hour_separation_allowed(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    first = helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11))
    second = helpers.advance_booking(student, rooms[1], slots.slot_start_for(today, 13))
    assert first.ok and second.ok


def test_room_already_booked_by_another_student(frozen, student, other_student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    slot_start = slots.slot_start_for(today, 11)
    first = helpers.advance_booking(student, rooms[0], slot_start)
    assert first.ok

    outcome = helpers.advance_booking(other_student, rooms[0], slot_start)
    assert not outcome.ok
    assert outcome.code == Code.SLOT_TAKEN


# --- A14: rejection still commits reconciliation -------------------------------


def test_rejected_booking_still_records_the_due_no_show(frozen, student, other_student, rooms):
    """The due no-show work that made the rejection correct must persist."""
    today = factories.bangkok(2026, 9, 14).date()
    expired = factories.make_booking(
        other_student,
        rooms[0],
        slot_start=slots.slot_start_for(today, 9),
        status=Booking.Status.SCHEDULED,
    )
    assert expired.deadline < frozen

    # This target is fine, but the actor already holds two bookings, so it fails.
    factories.make_booking(other_student, rooms[7], slot_start=slots.slot_start_for(today, 14))
    factories.make_booking(other_student, rooms[8], slot_start=slots.slot_start_for(today, 16))

    outcome = helpers.advance_booking(other_student, rooms[1], slots.slot_start_for(today, 11))
    assert not outcome.ok
    assert outcome.code == Code.QUOTA_EXCEEDED

    expired.refresh_from_db()
    assert expired.status == Booking.Status.NO_SHOW, "reconciliation must survive the rejection"
    assert Violation.objects.filter(booking=expired, kind=Violation.Kind.NO_SHOW).count() == 1


def test_rejected_request_does_not_send_confirmation(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11))
    helpers.advance_booking(student, rooms[1], slots.slot_start_for(today, 13))

    from core.models import Notification

    before = Notification.objects.count()
    outcome = helpers.advance_booking(student, rooms[2], slots.slot_start_for(today, 15))
    assert not outcome.ok
    assert Notification.objects.count() == before


# --- A11: idempotency ----------------------------------------------------------


def test_same_key_retry_returns_the_recorded_result(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    slot_start = slots.slot_start_for(today, 11)

    first = helpers.advance_booking(student, rooms[0], slot_start, key="key-123")
    second = helpers.advance_booking(student, rooms[0], slot_start, key="key-123")

    assert first.ok and second.ok
    assert second.replayed is True
    assert first.data["booking_id"] == second.data["booking_id"]
    assert Booking.objects.count() == 1


def test_same_key_with_changed_payload_is_rejected(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    first = helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11), key="key-abc")
    assert first.ok

    # Same key, different room: must not be treated as the same operation.
    second = helpers.advance_booking(student, rooms[5], slots.slot_start_for(today, 11), key="key-abc")
    assert not second.ok
    assert second.code == Code.IDEMPOTENCY_PAYLOAD_MISMATCH
    assert Booking.objects.count() == 1


def test_replay_after_status_change_does_not_resurrect(frozen, student, rooms):
    """A replayed result is returned even if the booking has since changed."""
    today = factories.bangkok(2026, 9, 14).date()
    slot_start = slots.slot_start_for(today, 11)
    first = helpers.advance_booking(student, rooms[0], slot_start, key="key-replay")
    booking = Booking.objects.get(pk=first.data["booking_id"])

    helpers.cancel(student, booking, "changed my mind")
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED

    replay = helpers.advance_booking(student, rooms[0], slot_start, key="key-replay")
    assert replay.ok
    assert replay.replayed is True

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED, "replay must not resurrect the booking"
    assert Booking.objects.count() == 1


# --- Audit ---------------------------------------------------------------------


def test_booking_writes_an_audit_event(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11))
    events = AuditEvent.objects.filter(action="booking.created", entity_id=str(outcome.data["booking_id"]))
    assert events.count() == 1
    assert events.first().actor_id == student.pk


def test_audit_events_are_append_only(frozen, student, rooms):
    today = factories.bangkok(2026, 9, 14).date()
    helpers.advance_booking(student, rooms[0], slots.slot_start_for(today, 11))
    event = AuditEvent.objects.first()
    from django.core.exceptions import ValidationError

    event.action = "tampered"
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        event.delete()


def test_clock_is_frozen_for_the_test(frozen, student, rooms):
    assert clock.is_frozen()
    assert clock.now().astimezone(slots.BANGKOK).strftime("%Y-%m-%d %H:%M") == "2026-09-14 10:40"
