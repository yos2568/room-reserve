"""The deterministic suggester: "which room should I take?" (D-29).

The panel ranks rooms the same services the grid renders, so it cannot disagree
with the table and never guesses: a suggestion must be legal for this viewer
(calendar, class hours, audience), must not collide with the viewer's own
adjacent bookings, and must fit the remaining daily quota. The current hour is
the walk-in exception, so it is listed for everyone the calendar allows.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.models import Room
from core.services import clock, slots, suggest
from tests import factories

pytestmark = pytest.mark.django_db

# The frozen moment is Monday 2026-09-14 10:40; the current slot is 10:00–11:00.
LATER_TODAY = date(2026, 9, 14)


def piano_student():
    user = factories.make_user()
    factories.make_roster_entry(user, instrument="เปียโน")
    return user


def violin_student():
    user = factories.make_user()
    factories.make_roster_entry(user, instrument="ไวโอลิน")
    return user


# --- Free right now ---------------------------------------------------------------


def test_free_now_lists_open_rooms_and_excludes_occupied(frozen, student, rooms):
    factories.make_booking(factories.make_user(), rooms[0], day=LATER_TODAY, hour=10)

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=student)

    numbers = [card.room.number for card in result.free_now]
    assert rooms[0].number not in numbers
    assert rooms[1].number in numbers
    # Frozen at 10:40, so twenty minutes of the hour remain.
    assert result.free_now[0].remaining_minutes == 20
    assert result.can_act


def test_free_now_excludes_class_hours(frozen, student, rooms):
    factories.make_weekly_block(rooms[0], weekday=0, start_hour=10, end_hour=12)

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=student)

    numbers = [card.room.number for card in result.free_now]
    assert rooms[0].number not in numbers
    assert rooms[1].number in numbers


def test_free_now_includes_restricted_room_for_any_student(frozen, student, rooms):
    """The walk-in rule ignores the reservation audience by design."""
    factories.make_restricted_room()

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=student)

    assert "303" in [card.room.number for card in result.free_now]


def test_free_now_only_exists_for_today(frozen, student, rooms):
    result = suggest.suggestions(date(2026, 9, 15), clock.now(), user=student)

    assert result.free_now == []
    assert result.hours  # the future date still shows bookable hours


# --- Bookable hours ----------------------------------------------------------------


def test_hour_groups_start_in_the_future_and_skip_occupied_rooms(frozen, student, rooms):
    factories.make_booking(factories.make_user(), rooms[0], day=LATER_TODAY, hour=11)

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=student)

    hours = {group.slot_start for group in result.hours}
    assert all(slot > clock.now() for slot in hours)
    eleven = next(group for group in result.hours if clock.local_time(group.slot_start).hour == 11)
    assert rooms[0] not in eleven.rooms
    assert rooms[1] in eleven.rooms


def test_hour_groups_skip_the_viewer_own_adjacent_bookings(frozen, student, rooms):
    factories.make_booking(student, rooms[0], day=LATER_TODAY, hour=13)

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=student)

    by_hour = {clock.local_time(group.slot_start).hour: group for group in result.hours}
    # The viewer's own hour and its neighbours are refused in *every* room, so
    # those hours vanish from the panel entirely; the next four shown are
    # 11:00 (two hours before) then 15:00 onward.
    assert set(by_hour) == {11, 15, 16, 17}
    assert rooms[0] in by_hour[11].rooms  # two hours away is legal again


def test_hour_groups_hide_restricted_rooms_from_other_instruments(frozen, rooms):
    violin = violin_student()
    factories.make_restricted_room()

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=violin)

    assert all("303" not in [room.number for room in group.rooms] for group in result.hours)

    pianist = piano_student()
    piano_result = suggest.suggestions(LATER_TODAY, clock.now(), user=pianist)
    assert any("303" in [room.number for room in group.rooms] for group in piano_result.hours)


def test_quota_exhausted_suggests_nothing_and_says_so(frozen, student, rooms):
    factories.make_booking(student, rooms[0], day=LATER_TODAY, hour=13)
    factories.make_booking(student, rooms[1], day=LATER_TODAY, hour=16)

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=student)

    assert result.quota_exhausted
    assert result.free_now == []
    assert result.hours == []


def test_anonymous_viewer_sees_rooms_without_actions(frozen, db):
    factories.make_rooms(9)

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=None)

    assert not result.can_act
    assert not result.quota_exhausted
    assert result.free_now  # availability is public


def test_suspended_viewer_cannot_act(frozen, student, rooms):
    from datetime import timedelta

    from core.models import Suspension

    Suspension.objects.create(
        user=student,
        source=Suspension.Source.MANUAL,
        starts_at=clock.now(),
        ends_at=clock.now() + timedelta(days=3),
        reason="test",
    )

    result = suggest.suggestions(LATER_TODAY, clock.now(), user=student)

    assert not result.can_act


# --- Refusal alternatives (for_slot) ------------------------------------------------


def test_for_slot_excludes_the_blocked_room_and_names_others(frozen, student, other_student, rooms):
    factories.make_booking(other_student, rooms[0], day=LATER_TODAY, hour=15)

    found = suggest.for_slot(slots.slot_start_for(LATER_TODAY, 15), clock.now(), user=student)

    assert rooms[0] not in found
    assert rooms[1] in found


def test_for_slot_respects_class_hours_and_audience(frozen, rooms):
    pianist = piano_student()
    violin = violin_student()
    room304 = Room.objects.create(number="304", label="ห้อง 304", position=11)
    factories.make_weekly_block(room304, weekday=0, start_hour=15, end_hour=16)
    factories.make_restricted_room()

    at_15 = slots.slot_start_for(LATER_TODAY, 15)
    pianist_found = suggest.for_slot(at_15, clock.now(), user=pianist)
    violin_found = suggest.for_slot(at_15, clock.now(), user=violin)

    assert room304 not in pianist_found  # class in session
    assert room304 not in violin_found
    assert Room.objects.get(number="303") in pianist_found
    assert Room.objects.get(number="303") not in violin_found


def test_for_slot_ignores_past_slots(frozen, student, rooms):
    found = suggest.for_slot(slots.slot_start_for(LATER_TODAY, 9), clock.now(), user=student)

    assert found == []


# The refusal view renders the same data; a smoke check that the page carries
# alternatives when the slot is taken lives in the booking-flow tests above
# (the view layer is a thin pass-through of suggest.for_slot).
