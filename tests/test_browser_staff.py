"""Real-browser staff workflows (V3 A24 staff half, plus A16/A17 ordering).

A24: "…staff closure and appeal workflows."

The appeal case matters beyond the screen. The student is told to contact the
department office, so the office has to be able to see the strikes behind a
sanction and void one. And voiding has to open a review rather than quietly end
the sanction: a strike that already caused a suspension cannot un-cause it
without a human decision.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from playwright.sync_api import expect

from core.models import Booking, Closure, Suspension, Violation
from core.services import sanctions, slots
from tests import browserlib, factories
from tests.browserlib import FACTORY_PASSWORD, grid_cell, sign_in, sign_out, visit

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

TODAY = browserlib.MONDAY_DATE
WEEK = timedelta(days=7)
STRIKE_WINDOW = timedelta(days=30)


def be_staff(page, base, staff_user) -> None:
    sign_in(page, base, staff_user.institutional_id, FACTORY_PASSWORD, lang="en")


def be_student(page, base, student) -> None:
    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)


def test_staff_close_a_room_range_and_the_booking_inside_it_is_cancelled(
    rooms, live_server, browser_clock, staff_user, student, watched
):
    """A18: preview, then confirm. The sessions inside the range are cancelled."""
    page = watched.page
    base = live_server.url
    booking = factories.make_booking(student, rooms[0], day=TODAY, hour=13)

    be_staff(page, base, staff_user)
    visit(page, base, "staff/closures/", lang="en")

    page.fill("#starts_at", "2026-09-14T13:00")
    page.fill("#ends_at", "2026-09-14T15:00")
    page.locator("form:has(#starts_at) button[type=submit]").click()

    # The preview has to say what it will affect, and change nothing.
    expect(page.locator("#reason")).to_be_visible()
    assert "1" in page.locator("main").inner_text(), "the preview counts the affected session"
    assert Closure.objects.count() == 0
    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED, "a preview changes nothing"

    page.fill("#reason", "Electrical maintenance")
    page.locator("form:has(#reason) button[type=submit]").click()

    expect(page.locator("form:has(#starts_at)")).to_have_count(1)
    assert Closure.objects.filter(reason="Electrical maintenance").count() == 1
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED

    # The student sees the hours as closed, not as bookable.
    sign_out(page)
    be_student(page, base, student)
    visit(page, base, "", lang="en")
    expect(grid_cell(page, rooms[0], 13).locator("a")).to_have_count(0)

    watched.assert_clean()


def test_voiding_a_strike_opens_a_review_instead_of_lifting_the_sanction(
    rooms, live_server, browser_clock, student, staff_user, watched
):
    """A16: the sanction survives the void; a human decides what happens next."""
    page = watched.page
    base = live_server.url
    moment = browser_clock.moment

    suspension = Suspension.objects.create(
        user=student,
        source=Suspension.Source.AUTO,
        starts_at=moment,
        ends_at=moment + WEEK,
        reason="Three strikes within 30 days.",
    )
    strike = Violation.objects.create(
        user=student,
        kind=Violation.Kind.MANUAL,
        occurred_at=moment,
        expires_at=moment + STRIKE_WINDOW,
        counts_as_strike=True,
        consumed_by=suspension,
        note="Reported by the office.",
    )

    be_staff(page, base, staff_user)
    visit(page, base, "staff/users/", lang="en")
    browserlib.expand_details(page)

    # Staff can see the strike behind the sanction before acting on the appeal.
    expect(page.locator(f"#void-{strike.pk}")).to_have_count(1)

    page.fill(f"#void-{strike.pk}", "The student was in a departmental rehearsal.")
    page.locator(f"form:has(#void-{strike.pk}) button[type=submit]").click()

    strike.refresh_from_db()
    suspension.refresh_from_db()
    assert strike.voided_at is not None
    assert suspension.lifted_at is None, "voiding a strike is not a decision to lift it"
    assert sanctions.pending_appeals(student), "the sanction is now under review"

    sign_out(page)
    be_student(page, base, student)
    visit(page, base, "my-bookings/")
    expect(page.locator("main")).to_be_visible()
    assert sanctions.pending_appeals(student)

    watched.assert_clean()


def test_a_lifted_suspension_restores_booking(
    rooms, live_server, browser_clock, student, staff_user, watched
):
    """A17: a suspension blocks new use, and lifting it restores booking."""
    page = watched.page
    base = live_server.url
    moment = browser_clock.moment

    suspension = Suspension.objects.create(
        user=student,
        source=Suspension.Source.MANUAL,
        starts_at=moment,
        ends_at=moment + WEEK,
        reason="ตรวจสอบโดยเจ้าหน้าที่",
    )

    # Blocked while it is in force.
    be_student(page, base, student)
    visit(page, base, "")
    cell = grid_cell(page, rooms[0], 12)
    expect(cell.locator("a")).to_have_count(0, timeout=10_000)
    # Hidden controls are not the security boundary: a saved URL must still
    # reject a suspended account when its confirmation is submitted.
    slot_key = slots.encode_slot(slots.slot_start_for(TODAY, 12))
    visit(page, base, f"book/{rooms[0].pk}/{slot_key}/")
    page.locator("form[action*='/confirm/'] button[type=submit]").click()
    expect(page.locator("[role=alert]").first).to_be_visible()
    assert Booking.objects.filter(user=student).count() == 0

    sign_out(page)
    be_staff(page, base, staff_user)
    visit(page, base, "staff/users/", lang="en")
    browserlib.expand_details(page)

    lift = page.locator(f"form[action*='/{suspension.pk}/lift/']")
    expect(lift).to_have_count(1)
    lift.locator("input[name=reason]").fill("Appeal upheld.")
    lift.locator("button[type=submit]").click()
    suspension.refresh_from_db()
    assert suspension.lifted_at is not None

    sign_out(page)
    be_student(page, base, student)
    visit(page, base, "")
    cell = grid_cell(page, rooms[0], 12)
    expect(cell.locator("a")).to_have_count(1, timeout=10_000)
    cell.locator("a").first.click()
    page.locator("form[action*='/confirm/'] button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/my-bookings/"))

    assert Booking.objects.filter(user=student, status=Booking.Status.SCHEDULED).count() == 1
    watched.assert_clean(allow_console_status=(409,))


def test_staff_correct_a_roster_address_from_the_roster_screen(
    rooms, live_server, browser_clock, staff_user, watched
):
    """The department's remedy for a roster address that a student cannot use.

    The importer refuses to change an address on an existing entry, so this screen
    is the only path — and it is the path the office will be told to use.
    """
    entry = factories.make_roster_entry(
        institutional_id="6699000123",
        email="personal.address@gmail.com",
        instrument="เปียโน",
    )

    page = watched.page
    base = live_server.url
    be_staff(page, base, staff_user)
    visit(page, base, "staff/roster/", lang="en")
    browserlib.expand_details(page)

    page.fill(f"#email-{entry.pk}", "6699000123@student.chula.ac.th")
    page.fill(f"#why-{entry.pk}", "Confirmed with the department")
    page.locator(f"form:has(#email-{entry.pk}) button[type=submit]").click()

    entry.refresh_from_db()
    assert entry.email == "6699000123@student.chula.ac.th"
    expect(page.locator("main")).to_contain_text("6699000123@student.chula.ac.th")

    watched.assert_clean()
