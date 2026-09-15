"""Shared pytest fixtures.

V3 section 12: timing tests use a controlled clock, and concurrency tests use
independent PostgreSQL connections with synchronization barriers rather than
sequential loops.
"""

from __future__ import annotations

import os
import threading
from datetime import timedelta

import pytest

from core.services import clock
from core.services.policy import current_policy
from tests import factories

# A Monday, so the default weekly timetable is open.
BASE_MOMENT = factories.bangkok(2026, 9, 14, 10, 40)


@pytest.fixture
def tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Bangkok")


@pytest.fixture
def frozen(db):
    """Freeze the authoritative clock at a known Bangkok moment."""
    with clock.frozen_clock(BASE_MOMENT) as moment:
        yield moment


@pytest.fixture
def rooms(db):
    return factories.make_rooms(9)


@pytest.fixture
def student(db):
    return factories.make_user()


@pytest.fixture
def other_student(db):
    return factories.make_user()


@pytest.fixture
def staff_user(db):
    return factories.make_user(
        username="staff66000001",
        email="staff1@student.chula.ac.th",
        is_operational_staff=True,
        name="เจ้าหน้าที่ ทดสอบ",
    )


@pytest.fixture
def policy(db):
    return current_policy()


@pytest.fixture
def barrier():
    """Factory for a synchronized start across independent connections."""

    def _make(parties: int):
        return threading.Barrier(parties)

    return _make


@pytest.fixture
def run_concurrently():
    """Run callables on separate threads and collect (result, exception) pairs.

    Each worker opens its own database connection because threads do not share
    one; this is what makes the race tests meaningful.
    """
    from django.db import connections

    def _run(funcs):
        results = [None] * len(funcs)
        errors = [None] * len(funcs)

        def wrapper(index, func):
            try:
                results[index] = func()
            except Exception as exc:
                errors[index] = exc
            finally:
                connections.close_all()

        threads = [threading.Thread(target=wrapper, args=(index, func)) for index, func in enumerate(funcs)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results, errors

    return _run


@pytest.fixture
def minutes():
    return timedelta(minutes=1)


# --- Real-browser fixtures (V3 section 12 / A23-A26, A29) ----------------------
# The helpers live in tests/browserlib.py; the fixtures have to live here so
# pytest can find them. Both are opt-in: a test that does not ask for them never
# starts a browser.


@pytest.fixture(scope="session", autouse=True)
def _playwright_leaves_its_loop_running(request):
    """Stand Django's async-context guard down, for browser runs only.

    Playwright's sync API executes its own event loop *in the test's thread* and
    calls ``asyncio._set_running_loop()`` on the way out; nothing clears it again.
    From the first Playwright call onward, ``asyncio.get_running_loop()`` succeeds
    while no async code is actually running, so Django's ``async_unsafe`` refuses
    every ORM call that follows - including pytest-django's end-of-test flush.

    Every view in this project is synchronous, so the guard is not protecting
    anything here. It is switched off only when the session actually collected a
    browser test, which keeps it live for the rest of the suite.
    """
    if not any(item.get_closest_marker("browser") for item in request.session.items):
        yield
        return

    previous = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = previous


@pytest.fixture
def browser_clock():
    """Pin the authoritative clock for every thread, including the live server.

    ``frozen`` is thread-local and therefore invisible to the thread that answers
    HTTP requests, so browser tests need the process-wide variant.
    """
    from tests.browserlib import MONDAY, ServedClock

    clock.freeze_process_clock(MONDAY)
    try:
        yield ServedClock()
    finally:
        clock.freeze_process_clock(None)


@pytest.fixture
def watched(page):
    """A Playwright page that records console, page and 5xx errors."""
    from tests.browserlib import WatchedPage

    return WatchedPage(page=page).watch()
