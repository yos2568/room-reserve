"""Optional recurring teaching timetable blocks (D-28).

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
from core.services.audit import record_audit
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


def test_block_recurs_every_matching_weekday(frozen, wide_horizon, student, rooms):
    factories.make_weekly_block(rooms[0], weekday=0, start_hour=10, end_hour=12)

    # This Monday is partly past (frozen at 10:40); the next one is refused too.
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(NEXT_MONDAY, 11))

    assert not outcome.ok
    assert outcome.code == Code.CLASS_IN_SESSION


def test_validity_window_bounds_the_block(frozen, wide_horizon, student, rooms):
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


# --- Provisioning (stalls 1–10, rooms 301, 303 and 304) ----------------------------


def test_ensure_rooms_provisions_the_real_floor(frozen, db):
    report = rooms_service.ensure_rooms()

    assert report["retired"] == 0
    active = {room.number: room for room in Room.objects.filter(is_active=True)}
    assert set(active) == {
        "301",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "10",
        "303",
        "304",
    }

    assert active["303"].is_restricted
    assert sorted(active["303"].allowed_category_values) == ["PERCUSSION", "PIANO"]
    assert not active["304"].is_restricted
    assert active["304"].label == "ห้อง 304 (ห้องบรรยาย 1)"
    assert active["301"].label == "ห้อง 301 (ห้องเรียนหลัก)"
    assert active["301"].availability_only
    assert active["303"].requires_approval
    assert active["304"].requires_approval

    assert set(
        WeeklyBlock.objects.filter(room=active["301"]).values_list("weekday", "start_hour", "end_hour")
    ) == {
        (0, 13, 15),  # Monday: Piano III, IV
        (1, 8, 10),  # Tuesday: Theo Mus E Trg I
        (1, 12, 15),  # Tuesday: Ensemble
        (2, 13, 15),  # Wednesday: Piano I
        (3, 13, 15),  # Thursday: Chorus / theory
        (4, 13, 15),  # Friday: Orchestration I
    }

    assert set(
        WeeklyBlock.objects.filter(room=active["304"]).values_list("weekday", "start_hour", "end_hour")
    ) == {
        (0, 10, 12),  # Monday: Counterpoint, two dates only
        (1, 12, 14),  # Tuesday: Skill-Piano
        (3, 10, 12),  # Thursday: Harmony
        (4, 13, 15),  # Friday: Wind Pedagogy
    }
    assert set(
        WeeklyBlock.objects.filter(room=active["304"], weekday=0).values_list("valid_from", "valid_until")
    ) == {
        (date(2026, 9, 28), date(2026, 9, 28)),
        (date(2026, 11, 16), date(2026, 11, 16)),
    }
    assert not WeeklyBlock.objects.filter(room=active["303"]).exists()


def test_room_304_counterpoint_meets_only_on_the_annotated_mondays(frozen, db):
    """The A304 sheet lists Counterpoint on 28/9/69 and 16/11/69, not every Monday."""
    rooms_service.ensure_rooms()
    room = Room.objects.get(number="304")
    moment = factories.bangkok(2026, 9, 14, 10, 40)

    with clock.frozen_clock(moment):
        ordinary = availability.public_grid(date(2026, 9, 21), clock.now())
        first = availability.public_grid(date(2026, 9, 28), clock.now())
        second = availability.public_grid(date(2026, 11, 16), clock.now())
        tuesday = availability.public_grid(date(2026, 9, 15), clock.now())

    assert cell_for(ordinary, room, 10).block_reason == ""
    assert cell_for(ordinary, room, 11).block_reason == ""
    assert "COUNTERPOINT" in cell_for(first, room, 10).block_reason
    assert "COUNTERPOINT" in cell_for(first, room, 11).block_reason
    assert cell_for(first, room, 9).block_reason == ""
    assert cell_for(first, room, 12).block_reason == ""
    assert "COUNTERPOINT" in cell_for(second, room, 10).block_reason
    assert "SKILL-PIANO" in cell_for(tuesday, room, 12).block_reason
    assert "วณีสอน" in WeeklyBlock.objects.get(room=room, weekday=4).reason


def test_room_301_is_availability_only_and_cannot_be_reserved(frozen, student, db):
    rooms_service.ensure_rooms()
    room = Room.objects.get(number="301")

    grid = availability.public_grid(NEXT_TUESDAY, clock.now(), user=student)
    cell = cell_for(grid, room, 11)
    assert cell.state == availability.SlotState.VIEW_ONLY
    assert not cell.can_reserve
    assert not cell.can_use_now

    outcome = helpers.advance_booking(student, room, slots.slot_start_for(NEXT_TUESDAY, 11))
    assert not outcome.ok
    assert outcome.code == Code.CLOSED


def test_ensure_rooms_is_idempotent_and_retires_strays(frozen, db):
    rooms_service.ensure_rooms()
    stray = Room.objects.create(number="11", label="ห้องซ้อมใหญ่", position=11)

    rooms_service.ensure_rooms()

    stray.refresh_from_db()
    assert not stray.is_active
    assert Room.objects.filter(is_active=True).count() == 13
    assert WeeklyBlock.objects.filter(room__number="304").count() == 5


def test_retired_room_returns_when_configured_again(frozen, db):
    rooms_service.ensure_rooms()
    reduced = {"303": {"reservation_scope": "LISTED", "categories": ("PIANO", "PERCUSSION")}}
    rooms_service.ensure_rooms(count=9, overrides=reduced, weekly_blocks={})
    room = Room.objects.get(number="304")
    assert not room.is_active

    rooms_service.ensure_rooms()
    room.refresh_from_db()
    assert room.is_active


def test_seed_does_not_undo_a_staff_deactivation(frozen, db):
    rooms_service.ensure_rooms()
    room = Room.objects.get(number="5")
    room.is_active = False
    room.save(update_fields=["is_active"])
    record_audit(action="room.deactivated", entity_type="Room", entity_id=room.pk, reason="broken piano")

    report = rooms_service.ensure_rooms()

    room.refresh_from_db()
    assert not room.is_active
    assert report["held_out"] == ["5"]


def test_a_booking_that_predates_a_class_shows_as_booked_not_class(frozen, student, other_student, rooms):
    booked = helpers.advance_booking(student, rooms[0], slots.slot_start_for(NEXT_TUESDAY, 13))
    assert booked.ok, booked.code
    # The timetable changes after the reservation: the booking stands (D-28).
    factories.make_weekly_block(rooms[0], weekday=1, start_hour=13, end_hour=15, reason="HARMONY")

    mine = cell_for(availability.public_grid(NEXT_TUESDAY, clock.now(), user=student), rooms[0], 13)
    theirs = cell_for(availability.public_grid(NEXT_TUESDAY, clock.now(), user=other_student), rooms[0], 13)
    unbooked = cell_for(availability.public_grid(NEXT_TUESDAY, clock.now(), user=student), rooms[0], 14)

    assert mine.state == availability.SlotState.TAKEN and mine.is_mine
    assert mine.block_reason == "HARMONY"
    assert theirs.state == availability.SlotState.TAKEN and not theirs.is_mine
    assert unbooked.state == availability.SlotState.CLOSED


def test_dropping_a_room_from_the_timetable_clears_its_blocks(frozen, db):
    rooms_service.ensure_rooms()
    room = Room.objects.get(number="304")
    factories.make_weekly_block(room, weekday=2, start_hour=10, end_hour=11)
    assert WeeklyBlock.objects.filter(room=room).count() == 6

    report = rooms_service.ensure_rooms(weekly_blocks={})

    assert not WeeklyBlock.objects.exists()
    assert report["blocks_cleared"] == 12
