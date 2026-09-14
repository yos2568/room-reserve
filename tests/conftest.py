"""Shared pytest fixtures.

V3 section 12: timing tests use a controlled clock, and concurrency tests use
independent PostgreSQL connections with synchronization barriers rather than
sequential loops.
"""

from __future__ import annotations

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

        threads = [
            threading.Thread(target=wrapper, args=(index, func)) for index, func in enumerate(funcs)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results, errors

    return _run


@pytest.fixture
def minutes():
    return timedelta(minutes=1)
