"""Real-browser student journeys (V3 A23).

A23: "register/verify/approve/login → reserve → cancel → reserve → door check-in →
history, with fixture dates respecting quota."

Every step below is performed by Chromium against a live server, and each one is
confirmed twice: the visible page state, and the database row it was supposed to
produce. A screenshot would not establish either.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from core.models import Booking, User
from core.services import slots
from tests import browserlib
from tests.browserlib import (
    FACTORY_PASSWORD,
    JOURNEY_HOUR,
    PASSWORD,
    approve_pending_student,
    grid_cell,
    register_student,
    reserve_from_grid,
    set_password_from_link,
    sign_in,
    sign_out,
    verification_token,
    visit,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

STUDENT_ID = "66009000001"
STUDENT_EMAIL = "browser.student@student.chula.ac.th"
STUDENT_NAME = "สมชาย ทดสอบบราวเซอร์"


@pytest.fixture
def active_rooms(rooms):
    return rooms


def test_the_public_grid_renders_every_room_and_every_hour(active_rooms, live_server, browser_clock, watched):
    """The grid is the entry point for both reserving and walking in."""
    page = watched.page
    visit(page, live_server.url, "")

    table = browserlib.grid_table(page)
    expect(table.locator("tbody tr")).to_have_count(len(active_rooms))
    expect(table.locator("thead th")).to_have_count(12 + 1)  # room column + 12 hours

    # The morning hour under test offers a reservation, and it is a real link.
    cell = grid_cell(page, active_rooms[0], JOURNEY_HOUR)
    expect(cell.locator("a")).to_have_count(1)
    assert cell.locator("a").first.inner_text().strip() != ""

    watched.assert_clean()


def test_a23_student_journey_from_registration_through_history(
    active_rooms, live_server, browser_clock, staff_user, watched
):
    page = watched.page
    base = live_server.url

    # 1. Register -------------------------------------------------------------
    register_student(
        page,
        base,
        institutional_id=STUDENT_ID,
        email=STUDENT_EMAIL,
        name=STUDENT_NAME,
    )
    applicant = User.objects.get(username=STUDENT_ID)
    assert applicant.eligibility == User.Eligibility.PENDING
    assert applicant.email_is_verified is False

    # 2. Verify the emailed link and choose a password ------------------------
    set_password_from_link(page, base, verification_token(applicant), PASSWORD)
    expect(page).to_have_url(re.compile(r"/login/"))

    applicant.refresh_from_db()
    assert applicant.email_is_verified is True, "the link proves the address"
    assert applicant.eligibility == User.Eligibility.PENDING, (
        "this student is not on the roster, so staff must approve them"
    )

    # 3. Staff approve through the staff screen -------------------------------
    sign_in(page, base, staff_user.institutional_id, FACTORY_PASSWORD)
    approve_pending_student(page, base, applicant, reason="ตรวจสอบกับสำนักงานแล้ว")
    applicant.refresh_from_db()
    assert applicant.eligibility == User.Eligibility.APPROVED
    assert applicant.eligibility_decided_by_id == staff_user.pk
    sign_out(page)

    # 4. Sign in as the student ----------------------------------------------
    sign_in(page, base, STUDENT_ID, PASSWORD)
    expect(page).to_have_url(re.compile(r"/my-bookings/"))

    # 5. Reserve the 11:00 hour ----------------------------------------------
    reserve_from_grid(page, base, active_rooms[0], JOURNEY_HOUR)

    slot_start = slots.slot_start_for(browserlib.MONDAY_DATE, JOURNEY_HOUR)
    booking = Booking.objects.get(user=applicant, slot_start=slot_start)
    assert booking.status == Booking.Status.SCHEDULED
    assert booking.room_id == active_rooms[0].pk
    expect(page.locator(f"#reason-{booking.pk}")).to_have_count(1)

    # 6. Cancel it, and check the grid releases the hour ----------------------
    page.fill(f"#reason-{booking.pk}", "เปลี่ยนแผนการซ้อม")
    page.locator(f"form:has(#reason-{booking.pk}) button[type=submit]").click()
    expect(page.locator(f"#reason-{booking.pk}")).to_have_count(0)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED

    visit(page, base, "")
    expect(grid_cell(page, active_rooms[0], JOURNEY_HOUR).locator("a")).to_have_count(1)

    # 7. Reserve the same hour again — quota was restored by the cancellation -
    reserve_from_grid(page, base, active_rooms[0], JOURNEY_HOUR)
    rebooked = Booking.objects.get(user=applicant, slot_start=slot_start, status=Booking.Status.SCHEDULED)
    assert rebooked.status == Booking.Status.SCHEDULED
    assert rebooked.pk != booking.pk, "a new reservation, not the cancelled row"

    # 8. Door check-in from the printed QR landing page -----------------------
    door = f"{base}/th/r/{active_rooms[0].pk}/"
    browser_clock.at(JOURNEY_HOUR, 0)
    page.goto(door, wait_until="load")

    check_in_form = page.locator("form[action*='/check-in/']")
    expect(check_in_form).to_have_count(1)
    check_in_form.locator("button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/my-bookings/"))

    rebooked.refresh_from_db()
    assert rebooked.status == Booking.Status.IN_USE
    assert rebooked.checked_in_at is not None

    # The QR page now reports the room as in use rather than offering a form.
    page.goto(door, wait_until="load")
    expect(page.locator("form[action*='/check-in/']")).to_have_count(0)

    # 9. History once the hour has passed ------------------------------------
    browser_clock.at(JOURNEY_HOUR + 1, 5)
    visit(page, base, "my-bookings/")

    history = page.locator("table").last
    rows = history.locator("tbody tr")
    # Two rows, not one: the cancelled reservation is part of the student's record
    # too. It is also what the late-cancellation report reads.
    expect(rows).to_have_count(2)

    texts = [rows.nth(index).inner_text() for index in range(2)]
    assert all(str(active_rooms[0]) in text for text in texts)
    assert all(f"{JOURNEY_HOUR:02d}:00" in text for text in texts)
    assert Booking.objects.filter(user=applicant, status=Booking.Status.CANCELLED).count() == 1
    assert Booking.objects.filter(user=applicant, status=Booking.Status.IN_USE).count() == 1

    watched.assert_clean()


def test_the_daily_quota_is_enforced_in_the_browser(
    active_rooms, live_server, browser_clock, student, watched
):
    """A23 asks for fixture dates that respect the quota (policy default: two).

    The hours are two apart so that the adjacency rule (A08) is not what refuses
    the third request — the quota has to be the reason. The refusal must also
    leave nothing behind: a rejected request that still persisted a row would be
    the more dangerous failure.
    """
    page = watched.page
    base = live_server.url
    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)

    reserve_from_grid(page, base, active_rooms[0], JOURNEY_HOUR)
    reserve_from_grid(page, base, active_rooms[0], JOURNEY_HOUR + 2)
    assert Booking.objects.filter(user=student).count() == 2

    # A third hour is offered by the stale grid but must be refused by the server.
    visit(page, base, "")
    cell = grid_cell(page, active_rooms[0], JOURNEY_HOUR + 4)
    expect(cell.locator("a")).to_have_count(1)
    cell.locator("a").first.click()
    page.locator("form[action*='/confirm/'] button[type=submit]").click()

    expect(page.locator("[role=alert]").first).to_be_visible()
    assert Booking.objects.filter(user=student).count() == 2, "the refusal persisted nothing"

    # The refusal is rendered as a 409 on purpose, so Chromium logs it.
    watched.assert_clean(allow_console_status=(409,))
