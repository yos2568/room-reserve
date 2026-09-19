"""The teaching timetable: recurring weekly blocks on rooms 303 and 304 (D-28).

Rooms 303 and 304 are teaching rooms as well as practice rooms. A class that
meets every week makes its hours unreservable and unwalkable, shows on the grid
as a class rather than as an unexplained gap, and never invalidates a booking
that already exists.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.models import Booking, Closure, Room, WeeklyBlock
from core.services import availability, calendar, clock, slots
from core.services import rooms as rooms_service
from core.services.errors import Code
from tests import factories, helpers

pytestmark = pytest.mark.django_db

# The frozen moment is Monday 2026-09-14 10:40, so hour 10 is in progress.
NEXT_TUESDAY = date(2026, 9, 15)
NEXT_MONDAY = date(2026, 9, 21)


def cell_for(grid, room, hour):
    for row in grid["rows"]:
        if row["room"].pk == room.pk:
            for cell in row["cells"]:
                if clock.local_time(cell.slot_start).hour == hour:
                    return cell
    raise AssertionError(f"no cell for {room} at {hour}:00")


# --- Reservations ---------------------------------------------------------------


def test_advance_booking_rejected_in_class_hour(frozen, student, rooms):
    factories.make_weekly_block(rooms[0], weekday=1, start_hour=13, end_hour=15)

    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(NEXT_TUESDAY, 13))

    assert not outcome.ok
    assert outcome.code == Code.CLASS_IN_SESSION
    assert not Booking.objects.filter(room=rooms[0]).exists()


def test_free_hours_of_the_same_day_stay_bookable(frozen, student, rooms):
    # 13:00–15:00 is taken by a class; 12:00 and 15:00 are not, and they are not
    # adjacent to each other, so one student may hold both.
    factories.make_weekly_block(rooms[0], weekday=1, start_hour=13, end_hour=15)

    before = helpers.advance_booking(student, rooms[0], slots.slot_start_for(NEXT_TUESDAY, 12))
    after = helpers.advance_booking(student, rooms[1], slots.slot_start_for(NEXT_TUESDAY, 15))

    assert before.ok, before.code
    assert after.ok, after.code


def test_block_recurs_every_matching_weekday(frozen, student, rooms):
    factories.make_weekly_block(rooms[0], weekday=0, start_hour=10, end_hour=12)

    # This Monday is partly past (frozen at 10:40); the next one is refused too.
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(NEXT_MONDAY, 11))

    assert not outcome.ok
    assert outcome.code == Code.CLASS_IN_SESSION


def test_validity_window_bounds_the_block(frozen, student, rooms):
    # Both rooms carry the same Friday class; on rooms[0] the semester window has
    # already ended, on rooms[1] it never ends. Friday 2026-09-18 is the only
    # Friday inside the 7-day horizon, so both bookings target the same hour.
    friday = date(2026, 9, 18)
    factories.make_weekly_block(
        rooms[0], weekday=4, start_hour=13, end_hour=15, valid_until=date(2026, 9, 17)
    )
    factories.make_weekly_block(rooms[1], weekday=4, start_hour=13, end_hour=15)

    # The rejected booking creates nothing, so it goes first: an allowed booking
    # on the same hour would otherwise trip the adjacency rule for the second.
    active = helpers.advance_booking(student, rooms[1], slots.slot_start_for(friday, 13))
    expired = helpers.advance_booking(student, rooms[0], slots.slot_start_for(friday, 13))

    assert not active.ok
    assert active.code == Code.CLASS_IN_SESSION
    assert expired.ok, expired.code


def test_closure_wins_over_class_hour(frozen, student, rooms):
    room = rooms[0]
    factories.make_weekly_block(room, weekday=1, start_hour=13, end_hour=15)
    day_start = slots.slot_start_for(NEXT_TUESDAY, 0)
    Closure.objects.create(
        room=room,
        starts_at=day_start,
        ends_at=day_start + timedelta(hours=24),
        reason="building maintenance",
    )

    outcome = helpers.advance_booking(student, room, slots.slot_start_for(NEXT_TUESDAY, 13))

    assert not outcome.ok
    assert outcome.code == Code.CLOSED


# --- Walk-ins -------------------------------------------------------------------


def test_walk_in_rejected_during_class_hour(frozen, student, rooms):
    # The frozen moment is Monday 10:40; block Monday 10:00–12:00.
    factories.make_weekly_block(rooms[0], weekday=0, start_hour=10, end_hour=12)

    outcome = helpers.use_now(student, rooms[0])

    assert not outcome.ok
    assert outcome.code == Code.CLASS_IN_SESSION


def test_walk_in_allowed_outside_class_hours(frozen, student, rooms):
    factories.make_weekly_block(rooms[0], weekday=0, start_hour=13, end_hour=15)

    outcome = helpers.use_now(student, rooms[0])

    assert outcome.ok, outcome.code
    assert outcome.data["status"] == Booking.Status.IN_USE


# --- Check-in of an existing booking --------------------------------------------


def test_existing_booking_still_checks_in_when_hour_becomes_blocked(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=NEXT_TUESDAY, hour=13)
    factories.make_weekly_block(rooms[0], weekday=1, start_hour=13, end_hour=15)

    with clock.frozen_clock(factories.bangkok(2026, 9, 15, 13, 5)):
        outcome = helpers.check_in(student, booking)

    assert outcome.ok, outcome.code
    booking.refresh_from_db()
    assert booking.status == Booking.Status.IN_USE


# --- The grid -------------------------------------------------------------------


def test_grid_shows_class_cells_with_reason(frozen, student, rooms):
    factories.make_weekly_block(rooms[0], weekday=1, start_hour=13, end_hour=15, reason="HARMONY")

    grid = availability.public_grid(NEXT_TUESDAY, clock.now(), user=student)

    blocked = cell_for(grid, rooms[0], 13)
    assert blocked.state == availability.SlotState.CLOSED
    assert blocked.block_reason == "HARMONY"
    assert blocked.closed_reason == ""

    # The hours on either side of the class stay open.
    assert cell_for(grid, rooms[0], 12).can_reserve
    assert cell_for(grid, rooms[0], 15).can_reserve


def test_bookable_slot_starts_skip_class_hours(frozen, rooms):
    factories.make_weekly_block(rooms[0], weekday=1, start_hour=13, end_hour=15)

    hours = {clock.local_time(start).hour for start in calendar.bookable_slot_starts(NEXT_TUESDAY, rooms[0])}

    assert {13, 14}.isdisjoint(hours)
    assert {12, 15}.issubset(hours)


# --- Provisioning (stalls 1–9, rooms 303 and 304) --------------------------------


def test_ensure_rooms_provisions_the_real_floor(frozen, db):
    report = rooms_service.ensure_rooms()

    assert report["retired"] == 0
    active = {room.number: room for room in Room.objects.filter(is_active=True)}
    assert set(active) == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "303", "304"}

    assert active["303"].is_restricted
    assert sorted(active["303"].allowed_category_values) == ["PERCUSSION", "PIANO"]
    assert not active["304"].is_restricted
    assert active["304"].label == "ห้อง 304 (ห้องบรรยาย 1)"

    assert WeeklyBlock.objects.filter(room=active["304"]).count() == 3
    assert not WeeklyBlock.objects.filter(room=active["303"]).exists()


def test_ensure_rooms_is_idempotent_and_retires_strays(frozen, db):
    rooms_service.ensure_rooms()
    stray = Room.objects.create(number="10", label="ห้องซ้อมใหญ่", position=10)

    rooms_service.ensure_rooms()

    stray.refresh_from_db()
    assert not stray.is_active
    assert Room.objects.filter(is_active=True).count() == 11
    assert WeeklyBlock.objects.filter(room__number="304").count() == 3


def test_retired_room_returns_when_configured_again(frozen, db):
    rooms_service.ensure_rooms()
    reduced = {"303": {"reservation_scope": "LISTED", "categories": ("PIANO", "PERCUSSION")}}
    rooms_service.ensure_rooms(count=9, overrides=reduced, weekly_blocks={})
    room = Room.objects.get(number="304")
    assert not room.is_active

    rooms_service.ensure_rooms()
    room.refresh_from_db()
    assert room.is_active
