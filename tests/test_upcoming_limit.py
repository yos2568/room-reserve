"""The 2-day window and the 4-upcoming-hours cap (D-38).

A student books at most two days ahead, at most two bookings a day, and holds at
most four hours that have not finished yet (advance bookings and walk-ins alike).
When one of those hours ends, a slot frees up and they can book again.

The frozen moment is Monday 2026-09-14 10:40, so the window is Mon–Wed.
"""

from __future__ import annotations

import pytest

from core.models import Booking
from core.services import clock, quota, slots
from core.services.errors import Code
from tests import factories, helpers

pytestmark = pytest.mark.django_db

MON = factories.bangkok(2026, 9, 14).date()
TUE = factories.bangkok(2026, 9, 15).date()
WED = factories.bangkok(2026, 9, 16).date()


def _book(student, room, day, hour):
    return helpers.advance_booking(student, room, slots.slot_start_for(day, hour))


def _hold_four(student, rooms):
    """Two on Monday and two on Tuesday: the daily limit allows it, the cap is now full."""
    for room, day, hour in (
        (rooms[0], MON, 13),
        (rooms[1], MON, 16),
        (rooms[2], TUE, 11),
        (rooms[3], TUE, 14),
    ):
        outcome = _book(student, room, day, hour)
        assert outcome.ok, outcome.code


def test_a_fifth_upcoming_hour_is_refused(frozen, student, rooms):
    _hold_four(student, rooms)

    outcome = _book(student, rooms[4], WED, 11)

    assert not outcome.ok
    assert outcome.code == Code.UPCOMING_LIMIT
    assert quota.upcoming_hours(student, clock.now()) == 4


def test_a_finished_hour_frees_a_slot(frozen, student, rooms):
    _hold_four(student, rooms)

    # Monday 14:05: the 13:00 hour has finished, so three are still upcoming.
    with clock.frozen_clock(factories.bangkok(2026, 9, 14, 14, 5)):
        assert quota.upcoming_hours(student, clock.now()) == 3
        outcome = _book(student, rooms[4], WED, 11)

    assert outcome.ok, outcome.code


def test_a_walk_in_in_progress_counts_towards_the_cap(frozen, student, rooms):
    walk_in = helpers.use_now(student, rooms[5])  # the current 10:00 hour
    assert walk_in.ok, walk_in.code
    for room, day, hour in ((rooms[0], MON, 16), (rooms[1], TUE, 11), (rooms[2], TUE, 14)):
        assert _book(student, room, day, hour).ok

    outcome = _book(student, rooms[3], WED, 11)

    assert outcome.code == Code.UPCOMING_LIMIT


def test_cancelled_and_no_show_hours_do_not_count(frozen, student, rooms):
    factories.make_booking(student, rooms[0], day=TUE, hour=16, status=Booking.Status.CANCELLED)
    factories.make_booking(student, rooms[1], day=MON, hour=8, status=Booking.Status.NO_SHOW)

    assert quota.upcoming_hours(student, clock.now()) == 0


def test_the_daily_limit_of_two_still_applies(frozen, student, rooms):
    assert _book(student, rooms[0], TUE, 9).ok
    assert _book(student, rooms[1], TUE, 13).ok

    outcome = _book(student, rooms[2], TUE, 16)

    assert outcome.code == Code.QUOTA_EXCEEDED


def test_three_days_ahead_is_outside_the_window(frozen, student, rooms):
    thursday = factories.bangkok(2026, 9, 17).date()

    outcome = _book(student, rooms[0], thursday, 11)

    assert outcome.code == Code.OUTSIDE_HORIZON


def test_the_cap_is_a_versioned_policy_value(frozen, student, rooms, staff_user):
    from core.services.policy import create_policy_version, current_policy

    assert current_policy().max_upcoming_hours == 4
    create_policy_version(snapshot={"max_upcoming_hours": 1}, actor=staff_user, label="Tighter")

    assert _book(student, rooms[0], TUE, 11).ok
    assert _book(student, rooms[1], WED, 11).code == Code.UPCOMING_LIMIT
