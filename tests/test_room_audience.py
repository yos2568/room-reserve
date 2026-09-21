"""The instrument-specific room (V3 extension, recorded as a deviation).

The room is restricted for **reserving only**. Once an hour has started and nobody
holds it, every eligible student may walk in — that is what keeps a restricted
room from being dead capacity whenever the piano and percussion students are not
using it.

The rule lives in one place (``eligibility.assert_may_use_room``) and deliberately
does not cover check-in, so a booking made before the room was restricted stays
valid.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Booking, InstrumentCategory
from core.services import availability, clock, slots
from tests import factories, helpers

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()
CURRENT_HOUR = 10
NEXT_HOUR = 11


@pytest.fixture
def big_room(db):
    return factories.make_restricted_room()


@pytest.fixture
def general_room(db):
    return factories.make_rooms(1)[0]


def student_in(category: str):
    """A student whose roster row carries the given instrument category."""
    spelling = {
        InstrumentCategory.PIANO: "เปียโน",
        InstrumentCategory.PERCUSSION: "เครื่องตี",
        InstrumentCategory.STRINGS: "ไวโอลิน",
        InstrumentCategory.VOICE: "ขับร้อง",
        InstrumentCategory.WOODWIND: "ฟลูต",
    }[category]
    student = factories.make_user()
    factories.make_roster_entry(user=student, instrument=spelling)
    return student


def cell_for(grid, room, slot_start):
    for row in grid["rows"]:
        if row["room"].pk == room.pk:
            for cell in row["cells"]:
                if cell.slot_start == slot_start:
                    return cell
    raise AssertionError(f"no cell for {room} at {slot_start}")


# --- Reserving -----------------------------------------------------------------


@pytest.mark.parametrize("allowed", [InstrumentCategory.PIANO, InstrumentCategory.PERCUSSION])
def test_the_room_can_be_reserved_by_piano_and_percussion_students(frozen, big_room, allowed):
    student = student_in(allowed)

    outcome = helpers.advance_booking(student, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert outcome.ok, outcome.code
    assert Booking.objects.filter(user=student, room=big_room).count() == 1


@pytest.mark.parametrize(
    "category",
    [InstrumentCategory.STRINGS, InstrumentCategory.VOICE, InstrumentCategory.WOODWIND],
)
def test_everyone_else_is_refused_and_nothing_is_written(frozen, big_room, category):
    student = student_in(category)

    outcome = helpers.advance_booking(student, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert not outcome.ok
    assert outcome.code == "room_not_for_instrument"
    assert Booking.objects.count() == 0, "a refused reservation must persist nothing"


def test_a_student_with_no_category_is_refused(frozen, big_room):
    """Approved by staff, no roster row: nothing to match the room against."""
    student = factories.make_user()

    outcome = helpers.advance_booking(student, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert not outcome.ok
    assert outcome.code == "room_not_for_instrument"


def test_an_uncategorised_spelling_is_refused(frozen, big_room):
    student = factories.make_user()
    factories.make_roster_entry(user=student, instrument="ปี่ใน")

    outcome = helpers.advance_booking(student, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert not outcome.ok
    assert outcome.code == "room_not_for_instrument"


def test_a_restricted_room_with_no_categories_lets_nobody_reserve_it(frozen, db):
    """Fails closed: an empty list is a mistake, not an invitation."""
    room = factories.make_restricted_room(categories=())
    student = student_in(InstrumentCategory.PIANO)

    outcome = helpers.advance_booking(student, room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert not outcome.ok
    assert outcome.code == "room_not_for_instrument"


def test_a_general_room_is_unaffected(frozen, general_room):
    """The rule is per room: general rooms still work exactly as before."""
    student = factories.make_user()

    outcome = helpers.advance_booking(student, general_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert outcome.ok, outcome.code


# --- Walking in ----------------------------------------------------------------


def test_anyone_may_walk_in_once_the_hour_has_started(frozen, big_room):
    """The release valve, and the reason a restricted room is not dead capacity."""
    student = student_in(InstrumentCategory.STRINGS)
    with clock.frozen_clock(factories.bangkok(2026, 9, 14, CURRENT_HOUR, 20)):
        outcome = helpers.use_now(student, big_room)

    assert outcome.ok, outcome.code
    booking = Booking.objects.get(user=student)
    assert booking.status == Booking.Status.IN_USE
    assert booking.slot_end == slots.slot_start_for(DAY, CURRENT_HOUR + 1), (
        "the walk-in still ends at the hour boundary"
    )


def test_a_walk_in_is_still_refused_while_a_reservation_holds_the_room(frozen, big_room):
    """Open to everyone is not the same as open while somebody holds it."""
    owner = student_in(InstrumentCategory.PIANO)
    other = student_in(InstrumentCategory.VOICE)
    helpers.advance_booking(owner, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    with clock.frozen_clock(factories.bangkok(2026, 9, 14, NEXT_HOUR, 5)):
        outcome = helpers.use_now(other, big_room)

    assert not outcome.ok
    assert outcome.code == "reservation_held"


def test_a_piano_student_who_reserved_can_still_check_in(frozen, big_room):
    student = student_in(InstrumentCategory.PIANO)
    helpers.advance_booking(student, big_room, slots.slot_start_for(DAY, NEXT_HOUR))
    booking = Booking.objects.get(user=student)

    with clock.frozen_clock(factories.bangkok(2026, 9, 14, NEXT_HOUR, 5)):
        outcome = helpers.check_in(student, booking)

    assert outcome.ok, outcome.code
    booking.refresh_from_db()
    assert booking.status == Booking.Status.IN_USE


def test_a_booking_made_before_the_room_was_restricted_still_checks_in(frozen, big_room):
    """Restricting a room is not a reason to invalidate somebody's booking."""
    student = student_in(InstrumentCategory.VOICE)
    booking = factories.make_booking(
        student, big_room, day=DAY, hour=NEXT_HOUR, status=Booking.Status.SCHEDULED
    )

    with clock.frozen_clock(factories.bangkok(2026, 9, 14, NEXT_HOUR, 5)):
        outcome = helpers.check_in(student, booking)

    assert outcome.ok, outcome.code


# --- The grid ------------------------------------------------------------------


def test_the_grid_says_restricted_to_a_student_who_cannot_reserve_it(frozen, big_room):
    student = student_in(InstrumentCategory.STRINGS)
    grid = availability.public_grid(DAY, clock.now(), user=student)

    cell = cell_for(grid, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert cell.state == availability.SlotState.RESTRICTED
    assert cell.can_reserve is False
    assert cell.state != availability.SlotState.CLOSED, "it is not closed; it is kept for others"


def test_the_grid_offers_the_room_to_a_piano_student(frozen, big_room):
    student = student_in(InstrumentCategory.PIANO)
    grid = availability.public_grid(DAY, clock.now(), user=student)

    cell = cell_for(grid, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert cell.state == availability.SlotState.BOOKABLE
    assert cell.can_reserve is True


def test_the_grid_shows_restricted_to_an_anonymous_visitor(frozen, big_room):
    """Signed out, nobody may reserve anything, so the room says why."""
    grid = availability.public_grid(DAY, clock.now(), user=None)

    cell = cell_for(grid, big_room, slots.slot_start_for(DAY, NEXT_HOUR))

    assert cell.state == availability.SlotState.RESTRICTED


def test_the_current_free_hour_is_open_to_everyone_including_the_grid(frozen, big_room):
    student = student_in(InstrumentCategory.STRINGS)
    grid = availability.public_grid(DAY, clock.now(), user=student)

    cell = cell_for(grid, big_room, slots.slot_start_for(DAY, CURRENT_HOUR))

    assert cell.state == availability.SlotState.FREE_NOW
    assert cell.can_use_now is True


def test_the_grid_state_reaches_the_page_without_rendering_an_empty_cell(frozen, client, big_room):
    """The trap: a SlotState with no template branch renders nothing at all.

    The label is read back through gettext rather than hard-coded, so this asserts
    both halves: the state reaches the template, and the copy is translated rather
    than silently falling back to English.
    """
    from django.utils.translation import gettext as translate

    student = student_in(InstrumentCategory.STRINGS)
    client.force_login(student)

    body = client.get(reverse("core:home")).content.decode("utf-8")

    label = translate("Piano & percussion only")
    assert label and label != "Piano & percussion only", "the label must be translated"
    assert label in body, "the restricted cell rendered nothing"
    assert f"/book/{big_room.pk}/" not in body, "no Reserve link for this viewer"


# --- Configuration -------------------------------------------------------------


def test_staff_can_reopen_a_restricted_room_and_restrict_it_again(frozen, client, staff_user, big_room):
    from core.models import AuditEvent

    client.force_login(staff_user)
    url = reverse("core:staff_set_room_audience", args=[big_room.pk])

    response = client.post(url, {"reservation_scope": "EVERYONE"})
    assert response.status_code == 302

    big_room.refresh_from_db()
    assert big_room.is_restricted is False
    assert big_room.allowed_category_values == []

    client.post(url, {"reservation_scope": "LISTED", "categories": ["PIANO", "PERCUSSION"]})
    big_room.refresh_from_db()
    assert sorted(big_room.allowed_category_values) == ["PERCUSSION", "PIANO"]

    assert AuditEvent.objects.filter(action="room.audience_changed").count() == 2


def test_restricting_a_room_does_not_cancel_a_booking_already_made(frozen, client, staff_user):
    """The rule is not retroactive, and staff changing it must not punish anyone."""
    other_room = factories.make_rooms(1)[0]
    student = student_in(InstrumentCategory.VOICE)
    booking = factories.make_booking(
        student, other_room, day=DAY, hour=NEXT_HOUR, status=Booking.Status.SCHEDULED
    )

    client.force_login(staff_user)
    client.post(
        reverse("core:staff_set_room_audience", args=[other_room.pk]),
        {"reservation_scope": "LISTED", "categories": ["PIANO"]},
    )

    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED

    with clock.frozen_clock(factories.bangkok(2026, 9, 14, NEXT_HOUR, 5)):
        outcome = helpers.check_in(student, booking)

    assert outcome.ok, "the existing booking survives and still checks in"
