"""Peak-hour limits and safe two-hour off-peak reservations."""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Booking, WeeklyBlock
from core.services import availability, clock, quota, slots
from core.services.errors import Code
from tests import factories, helpers

pytestmark = pytest.mark.django_db

TUESDAY = factories.bangkok(2026, 9, 15).date()
WEDNESDAY = factories.bangkok(2026, 9, 16).date()


@pytest.mark.parametrize("hour", [8, 16, 17, 18])
def test_two_hour_reservations_fit_only_inside_off_peak_windows(frozen, student, rooms, hour):
    outcome = helpers.advance_booking(
        student, rooms[0], slots.slot_start_for(TUESDAY, hour), duration_hours=2
    )

    assert outcome.ok, outcome.code
    booking = Booking.objects.get(user=student)
    assert booking.slot_end == slots.slot_start_for(TUESDAY, hour + 2)


@pytest.mark.parametrize("hour", [9, 10, 15, 19])
def test_two_hour_reservations_cannot_cross_peak_or_closing(frozen, student, rooms, hour):
    outcome = helpers.advance_booking(
        student, rooms[0], slots.slot_start_for(TUESDAY, hour), duration_hours=2
    )

    assert outcome.code == Code.INVALID_DURATION
    assert Booking.objects.count() == 0


def test_two_hour_reservation_checks_both_hours_against_the_timetable(frozen, student, rooms):
    WeeklyBlock.objects.create(
        room=rooms[0], weekday=TUESDAY.weekday(), start_hour=17, end_hour=18, reason="CLASS"
    )

    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 16), duration_hours=2)

    assert outcome.code == Code.CLASS_IN_SESSION
    assert Booking.objects.count() == 0


def test_two_hour_reservation_checks_for_room_conflict_in_second_hour(frozen, student, other_student, rooms):
    factories.make_booking(other_student, rooms[0], day=TUESDAY, hour=17)

    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 16), duration_hours=2)

    assert outcome.code == Code.SLOT_TAKEN


def test_two_hour_reservation_occupies_both_hourly_cells(frozen, student, rooms):
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 16), duration_hours=2)
    assert outcome.ok

    grid = availability.public_grid(TUESDAY, clock.now(), user=student)
    row = next(row for row in grid["rows"] if row["room"].pk == rooms[0].pk)
    second_hour = next(cell for cell in row["cells"] if cell.slot_start == slots.slot_start_for(TUESDAY, 17))

    assert second_hour.booking.slot_end == slots.slot_start_for(TUESDAY, 18)
    assert not second_hour.can_reserve


def test_two_hour_reservation_checks_user_overlap_and_adjacency_across_rooms(frozen, student, rooms):
    factories.make_booking(student, rooms[1], day=TUESDAY, hour=17)

    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 16), duration_hours=2)

    assert outcome.code == Code.ADJACENCY_CONFLICT


def test_two_hour_reservations_count_as_two_upcoming_hours_but_one_daily_booking(frozen, student, rooms):
    first = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 16), duration_hours=2)
    second = helpers.advance_booking(student, rooms[1], slots.slot_start_for(TUESDAY, 19))

    assert first.ok and second.ok
    assert quota.upcoming_hours(student, clock.now()) == 3
    assert Booking.objects.filter(user=student, slot_date=TUESDAY).count() == 2

    third = helpers.advance_booking(student, rooms[2], slots.slot_start_for(WEDNESDAY, 16), duration_hours=2)
    assert third.code == Code.UPCOMING_LIMIT


def test_upcoming_hours_fall_as_each_hour_of_a_two_hour_booking_finishes(frozen, student, rooms):
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 8), duration_hours=2)
    assert outcome.ok

    with clock.frozen_clock(factories.bangkok(2026, 9, 15, 9, 30)):
        assert quota.upcoming_hours(student, clock.now()) == 1


def test_confirmation_form_only_offers_durations_allowed_at_that_start(frozen, client, student, rooms):
    client.force_login(student)
    peak_slot = slots.encode_slot(slots.slot_start_for(TUESDAY, 11))
    offpeak_slot = slots.encode_slot(slots.slot_start_for(TUESDAY, 16))

    peak = client.get(reverse("core:book_slot", args=[rooms[0].pk, peak_slot]))
    offpeak = client.get(reverse("core:book_slot", args=[rooms[0].pk, offpeak_slot]))

    assert peak.status_code == offpeak.status_code == 200
    assert 'value="2"' not in peak.content.decode("utf-8")
    assert 'value="2"' in offpeak.content.decode("utf-8")


def test_room_door_finds_a_checked_in_two_hour_booking_during_its_second_hour(frozen, client, student, rooms):
    room = rooms[0]
    outcome = helpers.advance_booking(student, room, slots.slot_start_for(TUESDAY, 8), duration_hours=2)
    assert outcome.ok
    booking = Booking.objects.get(user=student)

    with clock.frozen_clock(factories.bangkok(2026, 9, 15, 8, 5)):
        assert helpers.check_in(student, booking).ok

    client.force_login(student)
    with clock.frozen_clock(factories.bangkok(2026, 9, 15, 9, 5)):
        page = client.get(reverse("core:room_qr", args=[room.pk]))

    assert page.status_code == 200
    assert page.context["my_booking"].pk == booking.pk
    assert page.context["checkin_state"] == "in_use"
