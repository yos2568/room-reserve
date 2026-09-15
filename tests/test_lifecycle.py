"""Check-in and use-now behaviour at the exact boundaries (A03-A06).

The V3 acceptance examples must be executable:

* At 10:40 reserve 11:00 today; check-in opens 11:00 and closes exactly 11:15.
* At 11:00 a free room can be used now until 12:00; a stale advance POST asks
  for reconfirmation instead of checking anyone in.
* At 11:15 an absent reservation forfeits; another eligible user enters at 11:22
  and finishes at 12:00.
* At 11:45 a never-booked or previously cancelled room supports use-now until 12:00.
* At 19:59 a walk-in ends at 20:00 with a short-time warning; at 20:00 rejected.
"""

from __future__ import annotations

import pytest

from core.models import Booking, Violation
from core.services import clock, slots
from core.services.errors import Code
from tests import factories, helpers

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14)


def at(hour, minute=0, second=0):
    return factories.bangkok(2026, 9, 14, hour, minute, second)


# --- A04: the check-in window ---------------------------------------------------


def test_check_in_before_start_fails(db, student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))
    with clock.frozen_clock(at(10, 59)):
        outcome = helpers.check_in(student, booking)
    assert not outcome.ok
    assert outcome.code == Code.CHECK_IN_NOT_OPEN


def test_check_in_at_start_succeeds(db, student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))
    with clock.frozen_clock(at(11, 0)):
        outcome = helpers.check_in(student, booking)
    assert outcome.ok, outcome.code
    booking.refresh_from_db()
    assert booking.status == Booking.Status.IN_USE
    assert booking.checked_in_at == at(11, 0)


def test_check_in_just_before_deadline_succeeds(db, student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))
    with clock.frozen_clock(at(11, 14, 59)):
        outcome = helpers.check_in(student, booking)
    assert outcome.ok, outcome.code
    booking.refresh_from_db()
    assert booking.status == Booking.Status.IN_USE


def test_check_in_at_deadline_fails_and_records_no_show(db, student, rooms):
    """Forfeiture begins exactly at the deadline (11:15)."""
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))
    with clock.frozen_clock(at(11, 15)):
        outcome = helpers.check_in(student, booking)

    assert not outcome.ok
    assert outcome.code == Code.TERMINAL_STATUS
    booking.refresh_from_db()
    assert booking.status == Booking.Status.NO_SHOW
    assert Violation.objects.filter(booking=booking, kind=Violation.Kind.NO_SHOW).count() == 1


def test_wrong_room_check_in_rejected(db, student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))
    with clock.frozen_clock(at(11, 5)):
        outcome = helpers.check_in(student, booking, room=rooms[3])
    assert not outcome.ok
    assert outcome.code == Code.WRONG_ROOM


def test_another_student_cannot_check_in(db, student, other_student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))
    with clock.frozen_clock(at(11, 5)):
        outcome = helpers.check_in(other_student, booking)
    assert not outcome.ok
    assert outcome.code == Code.NOT_OWNER


# --- A03/A05: use-now ------------------------------------------------------------


def test_use_now_at_exact_hour(db, student, rooms):
    """At 11:00 a free room can be used now until 12:00."""
    with clock.frozen_clock(at(11, 0)):
        outcome = helpers.use_now(student, rooms[0])
    assert outcome.ok, outcome.code
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    assert booking.source == Booking.Source.WALK_IN
    assert booking.status == Booking.Status.IN_USE
    assert booking.slot_start == slots.slot_start_for(DAY.date(), 11)
    assert booking.slot_end == slots.slot_start_for(DAY.date(), 12)
    assert booking.deadline is None


def test_use_now_keeps_original_slot_end(db, student, rooms):
    """A walk-in at 11:22 belongs to the 11:00 slot and ends at 12:00, not 12:22."""
    with clock.frozen_clock(at(11, 22)):
        outcome = helpers.use_now(student, rooms[0])
    assert outcome.ok, outcome.code
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    assert booking.slot_start == slots.slot_start_for(DAY.date(), 11)
    assert booking.slot_end == slots.slot_start_for(DAY.date(), 12)
    assert outcome.data["remaining_minutes"] == 38


def test_no_show_release_allows_another_student(db, student, other_student, rooms):
    """After 11:15 the forfeited room is free; the other student finishes at 12:00."""
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))

    with clock.frozen_clock(at(11, 16)):
        outcome = helpers.use_now(other_student, rooms[0])
    assert outcome.ok, outcome.code

    booking.refresh_from_db()
    assert booking.status == Booking.Status.NO_SHOW
    assert Violation.objects.filter(booking=booking).count() == 1

    walk_in = Booking.objects.get(pk=outcome.data["booking_id"])
    assert walk_in.original_no_show_id == booking.pk
    assert walk_in.slot_end == slots.slot_start_for(DAY.date(), 12)


def test_no_show_owner_cannot_reclaim_same_room_but_can_use_another(db, student, other_student, rooms):
    today = DAY.date()
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(today, 11))

    with clock.frozen_clock(at(11, 16)):
        # Forfeit the original by having any mutation reconcile it. A booking by
        # the other student in a *different* room triggers reconciliation.
        outcome = helpers.use_now(other_student, rooms[5])
        assert outcome.ok, outcome.code
        booking.refresh_from_db()
        assert booking.status == Booking.Status.NO_SHOW

        # The room/hour is free but the no-show owner cannot take it back.
        outcome = helpers.use_now(student, rooms[0])
        assert not outcome.ok
        assert outcome.code == Code.NO_SHOW_RECLAIM

        # A no-show does not block adjacency, and one strike leaves quota for
        # another room, so a different room is allowed (V3 section 3.2).
        outcome = helpers.use_now(student, rooms[1])
        assert outcome.ok, outcome.code


def test_released_room_taken_by_someone_else_reports_taken_to_the_owner(db, student, other_student, rooms):
    today = DAY.date()
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(today, 11))

    with clock.frozen_clock(at(11, 16)):
        # The other student takes the released room first.
        outcome = helpers.use_now(other_student, rooms[0])
        assert outcome.ok, outcome.code
        booking.refresh_from_db()
        assert booking.status == Booking.Status.NO_SHOW

        # Now the original owner comes back: the room is simply taken.
        outcome = helpers.use_now(student, rooms[0])
        assert not outcome.ok
        assert outcome.code == Code.SLOT_TAKEN


def test_cancelled_slot_supports_use_now(db, student, other_student, rooms):
    """An ordinarily cancelled reservation frees the current hour for walk-ins."""
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))

    with clock.frozen_clock(at(11, 45)):
        # Cancel is disallowed after start, so the cancellation happened before.
        pass

    # Simulate the cancellation happening just before the hour began.
    with clock.frozen_clock(at(10, 50)):
        helpers.cancel(student, booking, "plan changed")
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED

    with clock.frozen_clock(at(11, 45)):
        outcome = helpers.use_now(other_student, rooms[0])
    assert outcome.ok, outcome.code
    walk_in = Booking.objects.get(pk=outcome.data["booking_id"])
    assert walk_in.slot_end == slots.slot_start_for(DAY.date(), 12)


def test_held_reservation_blocks_use_now_within_grace(db, student, other_student, rooms):
    """A reservation inside its grace period is still the owner's."""
    factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(DAY.date(), 11))

    with clock.frozen_clock(at(11, 10)):  # inside grace
        outcome = helpers.use_now(other_student, rooms[0])
    assert not outcome.ok
    assert outcome.code == Code.RESERVATION_HELD


def test_use_now_at_1959_short_warning(db, student, rooms):
    with clock.frozen_clock(at(19, 59)):
        outcome = helpers.use_now(student, rooms[0])
    assert outcome.ok, outcome.code
    assert outcome.data["remaining_minutes"] == 1
    assert outcome.data["short_warning"] is True


def test_use_now_at_2000_rejected(db, student, rooms):
    """At 20:00 the day is closed; a use-now cannot start."""
    with clock.frozen_clock(at(20, 0)):
        outcome = helpers.use_now(student, rooms[0])
    assert not outcome.ok
    assert outcome.code == Code.CLOSED


def test_walk_in_has_no_deadline_and_no_second_grace(db, student, rooms):
    """A use-now is already in use: it has no check-in deadline at all."""
    with clock.frozen_clock(at(11, 50)):
        outcome = helpers.use_now(student, rooms[0])
    assert outcome.ok, outcome.code
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    assert booking.deadline is None
    assert booking.status == Booking.Status.IN_USE
    assert booking.checked_in_at == at(11, 50)


def test_use_now_blocked_for_ineligible_accounts(db, rooms):
    pending = factories.make_user(eligibility="PENDING")
    unverified = factories.make_user(verified=False, eligibility="APPROVED")

    with clock.frozen_clock(at(11, 5)):
        outcome = helpers.use_now(pending, rooms[0])
        assert not outcome.ok
        assert outcome.code == Code.PENDING_APPROVAL

        outcome = helpers.use_now(unverified, rooms[1])
        assert not outcome.ok
        assert outcome.code == Code.EMAIL_UNVERIFIED


def test_use_now_respects_quota(db, student, rooms):
    today = DAY.date()
    # Two prior charged bookings on the same date.
    factories.make_booking(
        student, rooms[0], slot_start=slots.slot_start_for(today, 9), status=Booking.Status.COMPLETED
    )
    factories.make_booking(student, rooms[1], slot_start=slots.slot_start_for(today, 13))

    with clock.frozen_clock(at(15, 20)):
        outcome = helpers.use_now(student, rooms[2])
    assert not outcome.ok
    assert outcome.code == Code.QUOTA_EXCEEDED
