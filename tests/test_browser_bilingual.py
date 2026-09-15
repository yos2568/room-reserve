"""Bilingual, mobile, keyboard and no-JavaScript delivery (V3 A25).

A25: "Thai/English routes and email copy, Gregorian years, 360px mobile and
desktop layouts, keyboard, JS-disabled forms, slow network and stale refresh".

None of these can be established by reading a template: a layout claim needs a
layout engine, a keyboard claim needs the real focus order, and a no-JavaScript
claim needs JavaScript actually switched off.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from core.models import Booking, Notification
from core.services import notifications
from tests import browserlib, factories
from tests.browserlib import (
    FACTORY_PASSWORD,
    WatchedPage,
    grid_cell,
    mobile_grid_cell,
    sign_in,
    visit,
)

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

TODAY = browserlib.MONDAY_DATE
RESERVE_HOUR = 12


def test_both_language_routes_serve_the_same_page_in_their_own_language(
    rooms, live_server, browser_clock, watched
):
    """And the dates stay Gregorian, in Thai as well as English (ค.ศ., not พ.ศ.)."""
    page = watched.page

    page.goto(f"{live_server.url}/th/", wait_until="load")
    expect(page.locator("html")).to_have_attribute("lang", "th")
    thai_text = page.locator("main").inner_text()

    page.goto(f"{live_server.url}/en/", wait_until="load")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    english_text = page.locator("main").inner_text()

    assert thai_text != english_text, "the two routes must not serve the same copy"

    for text in (thai_text, english_text):
        assert "2026" in text, "the Gregorian year is used"
        assert "2569" not in text, "never the Buddhist year"

    watched.assert_clean()


def test_the_email_copy_is_written_in_both_languages_and_carries_its_link(rooms, student, frozen):
    """A25 asks for email copy in both languages; the outbox renders per recipient."""
    from django.conf import settings

    bodies = {}
    for language in ("th", "en"):
        # An unsaved row is enough: render() reads the kind, language and recipient.
        notification = Notification(
            kind=notifications.KIND_EMAIL_VERIFICATION,
            recipient=student,
            dedupe_key="browser-copy-check",
            payload={},
            language=language,
        )
        subject, body = notifications.render(notification, {"token": "browser-token", "recipient": student})
        bodies[language] = (subject, body)

        assert subject.strip(), f"the {language} subject must not be empty"
        assert "browser-token" in body, "the link travels with the message"
        assert settings.SITE_BASE_URL.rstrip("/") in body

    assert bodies["th"] != bodies["en"], "the two languages must not render identical copy"


def test_the_360px_layout_is_a_usable_control_surface_not_a_shrunken_table(
    rooms, live_server, browser_clock, student, browser
):
    """A phone gets the card list, can complete a booking, and never scrolls sideways."""
    base = live_server.url
    context = browser.new_context(viewport={"width": 360, "height": 740})
    page = context.new_page()
    mobile = WatchedPage(page=page).watch()

    try:
        sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
        visit(page, base, "")

        assert not browserlib.grid_table(page).is_visible(), "a 360px screen must not show the table"
        assert page.locator("#availability-region section").first.is_visible()

        cell = mobile_grid_cell(page, rooms[0], RESERVE_HOUR)
        expect(cell.locator("a")).to_have_count(1)
        cell.locator("a").first.click()
        page.locator("form[action*='/confirm/'] button[type=submit]").click()
        expect(page).to_have_url(re.compile(r"/my-bookings/"))

        assert Booking.objects.filter(user=student, status=Booking.Status.SCHEDULED).count() == 1

        overflow = page.evaluate(
            "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - window.innerWidth"
        )
        assert overflow <= 1, f"the mobile layout overflows horizontally by {overflow}px"

        mobile.assert_clean()
    finally:
        context.close()


def test_a_reservation_can_be_made_with_the_keyboard_alone(
    rooms, live_server, browser_clock, student, watched
):
    """Tab to a Reserve control and press Enter. No pointer anywhere."""
    page = watched.page
    base = live_server.url
    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    visit(page, base, "")

    page.keyboard.press("Tab")
    skip = page.locator("body > a").first
    assert skip.evaluate("el => el === document.activeElement"), "the skip link is the first stop"
    assert skip.is_visible(), "the skip link must become visible when focused"

    reached = False
    for _ in range(200):
        page.keyboard.press("Tab")
        href = page.evaluate("document.activeElement && document.activeElement.getAttribute('href')")
        if href and "/book/" in href:
            reached = True
            break

    assert reached, "a Reserve control must be reachable by Tab"
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/book/\d+/\d{8}T\d{4}/"))

    watched.assert_clean()


def test_the_reserve_and_cancel_flow_works_with_javascript_disabled(
    rooms, live_server, browser_clock, student, browser
):
    """A25: "JS-disabled forms". Every mutation is a plain form for this reason."""
    base = live_server.url
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    plain = WatchedPage(page=page).watch()

    try:
        sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
        visit(page, base, "")

        cell = grid_cell(page, rooms[0], RESERVE_HOUR)
        expect(cell.locator("a")).to_have_count(1)
        cell.locator("a").first.click()
        page.locator("form[action*='/confirm/'] button[type=submit]").click()
        expect(page).to_have_url(re.compile(r"/my-bookings/"))

        booking = Booking.objects.get(user=student)
        assert booking.status == Booking.Status.SCHEDULED

        page.fill(f"#reason-{booking.pk}", "no javascript")
        page.locator(f"form:has(#reason-{booking.pk}) button[type=submit]").click()
        expect(page.locator(f"#reason-{booking.pk}")).to_have_count(0)

        booking.refresh_from_db()
        assert booking.status == Booking.Status.CANCELLED

        plain.assert_clean()
    finally:
        context.close()


def test_the_grid_refresh_replaces_the_region_instead_of_nesting_it(
    rooms, live_server, browser_clock, student, watched
):
    """A25: "stale refresh". The 30-second refresh must swap the region, not stack it.

    The refresh target *is* the region, so an innerHTML swap nests a second
    element with the same id inside the first, and every later lookup then
    addresses the stale outer copy.
    """
    page = watched.page
    base = live_server.url
    sign_in(page, base, student.institutional_id, FACTORY_PASSWORD)
    visit(page, base, "")
    assert page.locator("#availability-region").count() == 1

    # Someone starts using another room while this page is open.
    walker = factories.make_user(username="66009000077", email="refresh@student.chula.ac.th")
    factories.make_booking(walker, rooms[1], day=TODAY, hour=RESERVE_HOUR, status=Booking.Status.IN_USE)

    for _ in range(3):
        page.evaluate("window.htmx.trigger('#availability-region', 'refresh-availability')")
        page.wait_for_timeout(400)

    assert page.locator("#availability-region").count() == 1, "the refresh nested the region"
    expect(grid_cell(page, rooms[1], RESERVE_HOUR).locator("a")).to_have_count(0)

    # The refresh must not retrigger itself: a region that reloads on its own
    # response would hammer the server for as long as the page stays open.
    refreshes = [url for url in watched.requests if "/availability/" in url]
    assert len(refreshes) <= 8, f"the refresh is looping: {len(refreshes)} requests"

    watched.assert_clean()
