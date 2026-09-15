"""Real-browser walk-in and negative-state feedback (V3 A24, student half).

A24: "Real browser: current-slot walk-in after :15; wrong-room/too-late/full/
suspended/closed feedback".

Everything here happens *after* the :15 grace, because that is when a walk-in
becomes the interesting operation: the hour has started, a reservation that was
never checked in has lost its hold, and the room is either genuinely free or
genuinely gone. The staff half of A24 is in ``test_browser_staff.py``.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from core.models import Booking, Closure, Room, Suspension
from core.services import slots
from tests import browserlib, factories
from tests.browserlib import FACTORY_PASSWORD, grid_cell, sign_in, visit

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

WALK_IN_HOUR = 11
AFTER_GRACE = 20  # 11:20 — the check-in window has closed
LAST_MINUTES = 57  # 11:57 — three minutes left in the hour


def use_now_url(base: str, room: Room, hour: int = WALK_IN_HOUR) -> str:
    slot = slots.encode_slot(slots.slot_start_for(browserlib.MONDAY_DATE, hour))
    return f"{base}/th/use-now/{room.pk}/{slot}/"


def test_a_walk_in_after_the_grace_period_keeps_the_original_hour(
    rooms, live_server, browser_clock, student, watched
):
    """The session ends at the hour boundary — there is no extra hour (V3 s.4)."""
    page = watched.page
    base = live_server.url
    browser_clock.at(WALK_IN_HOUR, AFTER_GRACE)

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    visit(page, base, "")

    cell = grid_cell(page, rooms[0], WALK_IN_HOUR)
    expect(cell.locator("a")).to_have_count(1)
    cell.locator("a").first.click()

    expect(page).to_have_url(re.compile(r"/use-now/\d+/\d{8}T\d{4}/"))
    page.locator("form[action*='/use-now/'] button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/my-bookings/"))

    booking = Booking.objects.get(user=student)
    assert booking.room_id == rooms[0].pk
    assert booking.status == Booking.Status.IN_USE
    assert booking.source == Booking.Source.WALK_IN
    assert booking.slot_start == slots.slot_start_for(browserlib.MONDAY_DATE, WALK_IN_HOUR)
    assert booking.slot_end == slots.slot_start_for(browserlib.MONDAY_DATE, WALK_IN_HOUR + 1), (
        "the session ends at the hour boundary, not 60 minutes from the click"
    )
    assert booking.deadline is None, "a walk-in has no check-in deadline"

    watched.assert_clean()


def test_a_walk_in_in_the_last_minutes_says_so_before_it_starts(
    rooms, live_server, browser_clock, student, watched
):
    """Starting at :57 must warn, not let the student discover it a minute later."""
    page = watched.page
    base = live_server.url
    browser_clock.at(WALK_IN_HOUR, LAST_MINUTES)

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    page.goto(use_now_url(base, rooms[0]), wait_until="load")

    warning = page.locator("p[role=status]").first
    expect(warning).to_be_visible()
    assert warning.inner_text().strip() != ""

    page.locator("form[action*='/use-now/'] button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/my-bookings/"))

    assert Booking.objects.get(user=student).slot_end == slots.slot_start_for(
        browserlib.MONDAY_DATE, WALK_IN_HOUR + 1
    )
    watched.assert_clean()


def test_a_room_that_is_already_taken_offers_no_way_to_start_it(
    rooms, live_server, browser_clock, student, other_student, watched
):
    """The "full" feedback A24 asks for.

    A grid that has gone stale can still link here after someone else took the
    room. The page has to explain that rather than present a button that can only
    fail, and the grid has to agree on a refresh.
    """
    page = watched.page
    base = live_server.url
    browser_clock.at(WALK_IN_HOUR, AFTER_GRACE)
    factories.make_booking(
        other_student,
        rooms[1],
        day=browserlib.MONDAY_DATE,
        hour=WALK_IN_HOUR,
        status=Booking.Status.IN_USE,
    )

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    page.goto(use_now_url(base, rooms[1]), wait_until="load")

    expect(page.locator("form[action*='/use-now/']")).to_have_count(0)
    expect(page.locator("p[role=status]").first).to_be_visible()
    assert Booking.objects.filter(user=student).count() == 0

    visit(page, base, "")
    expect(grid_cell(page, rooms[1], WALK_IN_HOUR).locator("a")).to_have_count(0)

    watched.assert_clean()


def test_a_stale_check_in_button_is_refused_and_reconciles_the_no_show(
    rooms, live_server, browser_clock, student, watched
):
    """The "too late" feedback, driven the way it actually happens (A12/A13).

    A student opens My bookings inside the window and presses Check in after it
    has closed. The button is long gone on a fresh page — so this keeps the page
    open across the boundary and presses the stale one. The server must re-read
    the row under the lock, refuse, and reconcile the expired hold into a no-show.
    """
    page = watched.page
    base = live_server.url
    booking = factories.make_booking(student, rooms[2], day=browserlib.MONDAY_DATE, hour=WALK_IN_HOUR)
    browser_clock.at(WALK_IN_HOUR, 5)

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    visit(page, base, "my-bookings/")
    button = page.locator(f"form[action*='/{booking.pk}/check-in/'] button[type=submit]")
    expect(button).to_have_count(1)

    # Time passes with the page still open: the offer is now stale.
    browser_clock.at(WALK_IN_HOUR, AFTER_GRACE)
    button.click()
    expect(page).to_have_url(re.compile(r"/my-bookings/"))

    expect(page.locator("[role=alert]").first).to_be_visible()

    booking.refresh_from_db()
    assert booking.status == Booking.Status.NO_SHOW
    assert Booking.objects.filter(user=student, status=Booking.Status.IN_USE).count() == 0
    assert booking.violations.count() == 1, "one strike, recorded by the reconciliation"

    watched.assert_clean()


def test_the_student_cannot_reclaim_the_room_they_just_missed(
    rooms, live_server, browser_clock, student, watched
):
    """A05/A06: a no-show hour is released to others, not handed back to its owner."""
    page = watched.page
    base = live_server.url
    factories.make_booking(student, rooms[3], day=browserlib.MONDAY_DATE, hour=WALK_IN_HOUR)
    browser_clock.at(WALK_IN_HOUR, AFTER_GRACE)

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    page.goto(use_now_url(base, rooms[3]), wait_until="load")

    expect(page.locator("form[action*='/use-now/']")).to_have_count(0)
    assert Booking.objects.filter(user=student, status=Booking.Status.IN_USE).count() == 0

    watched.assert_clean()


def test_a_suspended_student_is_told_why_and_cannot_start_a_room(
    rooms, live_server, browser_clock, student, staff_user, watched
):
    """The "suspended" feedback: history and help stay; new use does not (A17)."""
    page = watched.page
    base = live_server.url
    browser_clock.at(WALK_IN_HOUR, AFTER_GRACE)

    Suspension.objects.create(
        user=student,
        source=Suspension.Source.MANUAL,
        starts_at=slots.slot_start_for(browserlib.MONDAY_DATE, 9),
        ends_at=slots.slot_start_for(browserlib.MONDAY_DATE, 18),
        reason="ตรวจสอบโดยเจ้าหน้าที่",
        created_by=staff_user,
    )

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)

    # My bookings still opens, and carries the sanction banner.
    visit(page, base, "my-bookings/")
    expect(page.locator("h1").first).to_be_visible()
    expect(page.locator("span.text-red-800").first).to_be_visible()

    page.goto(use_now_url(base, rooms[4]), wait_until="load")
    expect(page.locator("[role=alert]").first).to_be_visible()
    expect(page.locator("form[action*='/use-now/']")).to_have_count(0)
    assert Booking.objects.filter(user=student).count() == 0

    watched.assert_clean()


def test_a_closed_room_is_shown_as_closed_and_cannot_be_started(
    rooms, live_server, browser_clock, student, staff_user, watched
):
    """The "closed" feedback. A closure is scoped: the room next door still works."""
    page = watched.page
    base = live_server.url
    browser_clock.at(WALK_IN_HOUR, AFTER_GRACE)

    Closure.objects.create(
        room=rooms[5],
        starts_at=slots.slot_start_for(browserlib.MONDAY_DATE, WALK_IN_HOUR),
        ends_at=slots.slot_start_for(browserlib.MONDAY_DATE, WALK_IN_HOUR + 2),
        reason="ซ่อมแซมเปียโน",
        created_by=staff_user,
    )

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    visit(page, base, "")

    closed_cell = grid_cell(page, rooms[5], WALK_IN_HOUR)
    expect(closed_cell.locator("a")).to_have_count(0)
    assert closed_cell.inner_text().strip() != ""

    page.goto(use_now_url(base, rooms[5]), wait_until="load")
    expect(page.locator("form[action*='/use-now/']")).to_have_count(0)
    assert Booking.objects.filter(user=student).count() == 0

    visit(page, base, "")
    expect(grid_cell(page, rooms[6], WALK_IN_HOUR).locator("a")).to_have_count(1)

    watched.assert_clean()


def test_the_wrong_door_offers_no_check_in_for_another_rooms_booking(
    rooms, live_server, browser_clock, student, watched
):
    """The "wrong-room" feedback: only the booked room's page offers check-in."""
    page = watched.page
    base = live_server.url
    booking = factories.make_booking(student, rooms[7], day=browserlib.MONDAY_DATE, hour=WALK_IN_HOUR)
    browser_clock.at(WALK_IN_HOUR, 5)

    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)

    page.goto(f"{base}/th/r/{rooms[7].pk}/", wait_until="load")
    expect(page.locator(f"form[action*='/{booking.pk}/check-in/']")).to_have_count(1)

    page.goto(f"{base}/th/r/{rooms[8].pk}/", wait_until="load")
    expect(page.locator("form[action*='/check-in/']")).to_have_count(0)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED, "the wrong-door page changed nothing"

    watched.assert_clean()


# --- The instrument-specific tenth room -----------------------------------------


def student_with(spelling: str):
    """A student whose roster row carries the given instrument."""
    user = factories.make_user()
    factories.make_roster_entry(user=user, instrument=spelling)
    return user


def test_other_instruments_see_why_they_cannot_reserve_the_big_room(
    rooms, live_server, browser_clock, watched
):
    """The cell must say what it is, not look broken, and offer nothing to click."""
    big = factories.make_restricted_room()
    violinist = student_with("ไวโอลิน")

    page = watched.page
    base = live_server.url
    sign_in(page, base, violinist.institutional_id, FACTORY_PASSWORD)
    visit(page, base, "")

    cell = grid_cell(page, big, WALK_IN_HOUR)
    expect(cell.locator("a")).to_have_count(0)
    assert "เปียโน" in cell.inner_text(), "the label names who the room is kept for"

    # A general room in the same hour is still bookable, so nothing is globally broken.
    expect(grid_cell(page, rooms[0], WALK_IN_HOUR).locator("a")).to_have_count(1)

    # And the confirmation route refuses rather than presenting a doomed form.
    page.goto(f"{base}/th/book/{big.pk}/20260914T1100/", wait_until="load")
    expect(page.locator("#availability-region")).to_be_visible()
    assert Booking.objects.count() == 0

    watched.assert_clean()


def test_a_piano_student_reserves_the_big_room_through_the_grid(rooms, live_server, browser_clock, watched):
    big = factories.make_restricted_room()
    pianist = student_with("เปียโน")

    page = watched.page
    base = live_server.url
    sign_in(page, base, pianist.institutional_id, FACTORY_PASSWORD)

    browserlib.reserve_from_grid(page, base, big, WALK_IN_HOUR)

    booking = Booking.objects.get(user=pianist)
    assert booking.room_id == big.pk
    assert booking.status == Booking.Status.SCHEDULED

    watched.assert_clean()


def test_anyone_may_walk_into_the_big_room_once_the_hour_has_started(
    rooms, live_server, browser_clock, watched
):
    """Restricted for reserving, open to everyone once it is free and running."""
    big = factories.make_restricted_room()
    violinist = student_with("ไวโอลิน")

    page = watched.page
    base = live_server.url
    browser_clock.at(WALK_IN_HOUR, AFTER_GRACE)

    sign_in(page, base, violinist.institutional_id, FACTORY_PASSWORD)
    visit(page, base, "")

    cell = grid_cell(page, big, WALK_IN_HOUR)
    expect(cell.locator("a")).to_have_count(1)
    cell.locator("a").first.click()
    page.locator("form[action*='/use-now/'] button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/my-bookings/"))

    booking = Booking.objects.get(user=violinist)
    assert booking.room_id == big.pk
    assert booking.status == Booking.Status.IN_USE

    watched.assert_clean()
