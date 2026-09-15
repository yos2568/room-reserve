"""Shared machinery for the real-browser acceptance tests (V3 A23-A26, A29).

Two things make driving a real browser against this app possible:

* pytest-django's ``live_server`` fixture, which answers requests on its own
  thread while sharing one PostgreSQL database with the test. That is why these
  tests are transactional — the server has to see committed rows.
* A **process-wide** clock freeze. ``clock.frozen_clock`` is thread-local, so a
  freeze set by the test never reaches the server thread; ``freeze_process_clock``
  exists for exactly this case. V3 section 12: "Fixed-time browser fixtures live
  only in an isolated test app."

Nothing here asserts on translated copy, because the same flow has to be driven in
Thai and English. Locators use element ids, roles, and positional structure that
the templates guarantee; text is only asserted where the test is about the copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from core.models import Notification, User
from core.services import clock
from tests import factories

# Anchor moment for the whole browser suite: a Monday morning inside the
# timetable, one hour before the slot the journey tests book. Fixed rather than
# "now" so that opening hours, the check-in window and the quota date are all
# deterministic — the reason V3 asks for an isolated test app.
MONDAY = factories.bangkok(2026, 9, 14, 10, 40)
MONDAY_DATE = MONDAY.date()
OPENING_HOUR = 8
EVENING_HOUR = 19

# The hour the A23 journey reserves, cancels and re-reserves.
JOURNEY_HOUR = 11

# Long enough for the password validators, and not a known-common password.
PASSWORD = "browser-acceptance-passphrase-1"

# The default every ``factories.make_user`` account is created with.
FACTORY_PASSWORD = "synthetic-password-123"

# Chromium reports a missing favicon as a console error. It is not a product
# defect, and the app deliberately ships no favicon, so it is filtered rather
# than silently ignored: every other console error still fails the check.
BENIGN_CONSOLE = (re.compile(r"favicon", re.IGNORECASE),)

# Chromium words a failed response as "…responded with a status of 409 (Conflict)".
_RESOURCE_STATUS = re.compile(r"status of (\d{3})")


@dataclass
class ServedClock:
    """A clock the browser and the test thread share."""

    def at(self, hour: int, minute: int = 0, *, day_offset: int = 0) -> datetime:
        """Pin the clock to a Bangkok wall time on (or near) the anchor Monday."""
        date = MONDAY_DATE + timedelta(days=day_offset)
        moment = datetime(date.year, date.month, date.day, hour, minute, tzinfo=clock.BANGKOK)
        clock.freeze_process_clock(moment)
        return clock.now()

    def advance(self, **delta: int) -> datetime:
        clock.advance(timedelta(**delta))
        return clock.now()

    @property
    def moment(self) -> datetime:
        return clock.now()


@dataclass
class WatchedPage:
    """A browser page that records the failures a human would notice.

    A screenshot proves nothing about whether a flow worked, so the tests inspect
    visible state *and* assert that the browser reported no uncaught error and the
    server returned no 5xx while they did it.
    """

    page: Page
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    server_errors: list[str] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)

    def watch(self) -> WatchedPage:
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_pageerror)
        self.page.on("response", self._on_response)
        self.page.on("request", self._on_request)
        return self

    def _on_console(self, message: Any) -> None:
        if message.type == "error":
            self.console_errors.append(message.text)

    def _on_pageerror(self, error: Any) -> None:
        self.page_errors.append(str(error))

    def _on_response(self, response: Any) -> None:
        if response.status >= 500:
            self.server_errors.append(f"HTTP {response.status} {response.url}")

    def _on_request(self, request: Any) -> None:
        self.requests.append(request.url)

    @property
    def unexpected_console_errors(self) -> list[str]:
        return [
            text
            for text in self.console_errors
            if not any(pattern.search(text) for pattern in BENIGN_CONSOLE)
        ]

    def assert_clean(self, *, allow_console_status: tuple[int, ...] = ()) -> None:
        """Fail on any error the browser reported.

        A page that deliberately answers 4xx (a refused booking renders a 409 with
        an explanation) makes Chromium log "Failed to load resource". The test
        names the statuses it caused on purpose; everything else still fails.
        """
        assert self.page_errors == [], f"uncaught JavaScript errors: {self.page_errors}"
        assert self.server_errors == [], f"server errors: {self.server_errors}"

        allowed = {str(status) for status in allow_console_status}
        unexpected = []
        for text in self.unexpected_console_errors:
            match = _RESOURCE_STATUS.search(text)
            if match and match.group(1) in allowed:
                continue
            unexpected.append(text)
        assert unexpected == [], f"console errors: {unexpected}"

    def reload(self) -> None:
        self.page.reload(wait_until="load")


@pytest.fixture
def browser_clock() -> ServedClock:
    """Pin time for every thread, and release it when the test ends."""
    clock.freeze_process_clock(MONDAY)
    try:
        yield ServedClock()
    finally:
        clock.freeze_process_clock(None)


@pytest.fixture
def watched(page: Page) -> WatchedPage:
    """The `page` fixture with error and request recording attached."""
    return WatchedPage(page=page).watch()


# --- Locators ------------------------------------------------------------------


def grid_table(page: Page):
    """The desktop availability table.

    The template renders two grids — a table for wide screens and a card list for
    phones — and CSS hides one. Both are in the DOM, so every grid locator is
    scoped to the table to stay unambiguous.
    """
    return page.locator("#availability-region table").first


def grid_cell(page: Page, room, hour: int):
    """The wide-screen grid cell for one room at one hour.

    The row is found by its heading rather than by a hard-coded position, so the
    test does not silently address the wrong room if the grid order changes.
    """
    row = grid_table(page).locator("tbody tr").nth(grid_room_index(page, room))
    return row.locator("td").nth(hour - OPENING_HOUR)


def mobile_grid_cell(page: Page, room, hour: int):
    """The phone-layout cell for a room at an hour.

    The narrow layout is a card list, not a table, so it needs its own locator:
    the desktop cells are present in the DOM at 360px but hidden, and nothing
    clickable may be addressed through a hidden copy.
    """
    section = (
        page.locator("#availability-region section")
        .filter(has=page.get_by_role("heading", name=str(room), level=3))
        .first
    )
    return section.locator("li").nth(hour - OPENING_HOUR)


def grid_room_index(page: Page, room) -> int:
    """Display position of a room. The grid page must already be loaded."""
    heading = str(room)
    rows = grid_table(page).locator("tbody tr")
    for index in range(rows.count()):
        if rows.nth(index).locator("th").first.inner_text().strip() == heading:
            return index
    raise AssertionError(f"{heading!r} is not in the availability grid")


# --- Navigation helpers --------------------------------------------------------


def visit(page: Page, base_url: str, path: str, lang: str = "th") -> None:
    page.goto(f"{base_url}/{lang}/{path.lstrip('/')}", wait_until="load")


def submit_with_enter(page: Page, selector: str) -> None:
    """Submit a form the way a keyboard user would, without naming a button."""
    page.press(selector, "Enter")


def sign_in(page: Page, base_url: str, institutional_id: str, password: str, lang: str = "th") -> None:
    visit(page, base_url, "login/", lang)
    page.fill("#id_institutional_id", institutional_id)
    page.fill("#id_password", password)
    submit_with_enter(page, "#id_password")
    expect(page).not_to_have_url(re.compile(r"/login/"))


def sign_out(page: Page) -> None:
    page.locator("form[action*='/logout/'] button[type=submit]").first.click()
    expect(page.locator("form[action*='/logout/']")).to_have_count(0)


def register_student(
    page: Page, base_url: str, *, institutional_id: str, email: str, name: str, lang: str = "th"
):
    visit(page, base_url, "register/", lang)
    page.fill("#id_name", name)
    page.fill("#id_institutional_id", institutional_id)
    page.fill("#id_email", email)
    submit_with_enter(page, "#id_email")
    expect(page).to_have_url(re.compile(r"/register/done/"))


def verification_token(user: User) -> str:
    """The plaintext link is only ever in the outbox payload, never on the row."""
    note = Notification.objects.get(kind="email_verification", recipient=user)
    return note.payload["token"]


def set_password_from_link(page: Page, base_url: str, token: str, password: str, lang: str = "th") -> None:
    visit(page, base_url, f"verify/{token}/", lang)
    page.fill("#id_password", password)
    page.fill("#id_password_confirm", password)
    submit_with_enter(page, "#id_password_confirm")


def approve_pending_student(
    page: Page, base_url: str, applicant: User, reason: str, lang: str = "th"
) -> None:
    """Approve through the staff screen, not the service, so the UI is exercised."""
    visit(page, base_url, "staff/today/", lang)
    field = page.locator(f"#reason-{applicant.pk}")
    field.fill(reason)
    page.locator(f"form:has(#reason-{applicant.pk}) button[value=APPROVED]").click()
    expect(page.locator(f"#reason-{applicant.pk}")).to_have_count(0)


def reserve_from_grid(page: Page, base_url: str, room, hour: int, lang: str = "th") -> None:
    """Click Reserve in the grid, confirm, and land back on My bookings."""
    visit(page, base_url, "", lang)
    cell = grid_cell(page, room, hour)
    link = cell.locator("a")
    expect(link).to_have_count(1)
    link.first.click()
    expect(page).to_have_url(re.compile(r"/book/\d+/\d{8}T\d{4}/"))
    page.locator("form[action*='/confirm/'] button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/my-bookings/"))


def expand_details(page: Page) -> None:
    """Open every collapsed ``<details>`` on the page.

    The staff "Manage" controls live behind one, and Playwright refuses to type
    into an element that is not visible. A keyboard user would press Enter on the
    summary; this is the same thing in one step.
    """
    page.evaluate("document.querySelectorAll('details').forEach((d) => { d.open = true; })")


def visible_cell_text(page: Page, room_index: int, hour: int) -> str:
    return grid_cell(page, room_index, hour).inner_text().strip()
