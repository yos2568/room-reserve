"""The authoritative clock, and the difference between a thread and a process.

The thread-local freeze is the right tool for a unit test but the wrong one for
anything that answers requests: Django runs views on worker threads, so a freeze
set by the caller never reaches them. V3 section 12 requires fixed-time browser
fixtures, and those only work if the server thread is pinned too.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from core.services import clock
from tests import factories

UTC = ZoneInfo("UTC")
MONDAY_1040 = factories.bangkok(2026, 9, 14, 10, 40)
MONDAY_1040_UTC = MONDAY_1040.astimezone(UTC)


@pytest.fixture(autouse=True)
def _release_process_clock():
    """Never leak a process-wide freeze into another test."""
    yield
    clock.freeze_process_clock(None)


def _read_clock_in_a_new_thread():
    """The value a view running on a worker thread would see."""
    seen = {}

    def worker():
        seen["moment"] = clock.now()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    return seen["moment"]


def test_thread_local_freeze_does_not_reach_another_thread():
    """Pins why a thread-local freeze cannot serve a browser test."""
    with clock.frozen_clock(MONDAY_1040):
        assert clock.now() == MONDAY_1040_UTC
        assert _read_clock_in_a_new_thread() != MONDAY_1040_UTC


def test_process_freeze_is_visible_from_another_thread():
    clock.freeze_process_clock(MONDAY_1040)

    assert clock.now() == MONDAY_1040_UTC
    assert _read_clock_in_a_new_thread() == MONDAY_1040_UTC


def test_process_freeze_is_released_with_none():
    clock.freeze_process_clock(MONDAY_1040)
    clock.freeze_process_clock(None)

    assert clock.is_frozen() is False
    assert _read_clock_in_a_new_thread() != MONDAY_1040_UTC


def test_a_thread_local_freeze_still_wins_over_the_process_freeze():
    clock.freeze_process_clock(MONDAY_1040)
    later = factories.bangkok(2026, 9, 14, 12, 0)

    with clock.frozen_clock(later):
        assert clock.now() == later.astimezone(UTC)

    assert clock.now() == MONDAY_1040_UTC


def test_advance_moves_the_process_clock_when_that_is_the_active_one():
    clock.freeze_process_clock(MONDAY_1040)
    clock.advance(timedelta(minutes=20))

    assert clock.now() == MONDAY_1040_UTC + timedelta(minutes=20)


def test_advance_without_any_freeze_is_an_error():
    clock.freeze_process_clock(None)
    with pytest.raises(RuntimeError):
        clock.advance(timedelta(minutes=1))


def test_the_process_clock_cannot_be_set_when_test_infrastructure_forbids_it(settings):
    """Production forces ALLOW_TEST_CLOCK off, so this entrance closes with it."""
    settings.ALLOW_TEST_CLOCK = False

    with pytest.raises(RuntimeError):
        clock.freeze_process_clock(MONDAY_1040)


def test_freeze_from_environment_pins_every_thread(settings, monkeypatch):
    settings.ALLOW_TEST_CLOCK = True
    monkeypatch.setenv("FROZEN_CLOCK", "2026-09-14T10:40:00+07:00")

    clock.freeze_from_environment()

    assert clock.now() == MONDAY_1040_UTC
    assert _read_clock_in_a_new_thread() == MONDAY_1040_UTC


def test_freeze_from_environment_is_inert_without_the_variable(settings, monkeypatch):
    settings.ALLOW_TEST_CLOCK = True
    monkeypatch.delenv("FROZEN_CLOCK", raising=False)

    assert clock.freeze_from_environment() is None
    assert clock.is_frozen() is False


def test_freeze_from_environment_is_inert_when_the_test_clock_is_forbidden(settings, monkeypatch):
    settings.ALLOW_TEST_CLOCK = False
    monkeypatch.setenv("FROZEN_CLOCK", "2026-09-14T10:40:00+07:00")

    assert clock.freeze_from_environment() is None
