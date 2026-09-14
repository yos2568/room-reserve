"""Authoritative time.

V3 section 3.1: "Obtain authoritative time after acquiring the operation lock;
use that captured time consistently." Services therefore receive ``now`` as a
parameter; only the operation protocol calls :func:`now`.

The frozen-clock override exists for tests and is gated on ``ALLOW_TEST_CLOCK``,
which production settings force to False. Nothing in the URL map can reach it, so
a public deployment cannot move the clock (V3 section 3.1).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import local
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

BANGKOK = ZoneInfo("Asia/Bangkok")

_state = local()


def now() -> datetime:
    """Current UTC-aware time, or the frozen value when a test clock is active."""
    frozen = getattr(_state, "frozen", None)
    if frozen is not None and settings.ALLOW_TEST_CLOCK:
        return frozen
    return timezone.now()


@contextmanager
def frozen_clock(moment: datetime):
    """Freeze time for the duration of the block. Test/development only."""
    if not settings.ALLOW_TEST_CLOCK:
        raise RuntimeError(
            "The test clock is disabled. It may only be used in test or development "
            "infrastructure (ALLOW_TEST_CLOCK=True), never in production."
        )
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, BANGKOK)
    previous = getattr(_state, "frozen", None)
    _state.frozen = moment.astimezone(ZoneInfo("UTC"))
    try:
        yield _state.frozen
    finally:
        _state.frozen = previous


def advance(delta: timedelta) -> None:
    """Move a frozen clock forward. Requires an active frozen clock."""
    if not settings.ALLOW_TEST_CLOCK:
        raise RuntimeError("The test clock is disabled.")
    current = getattr(_state, "frozen", None)
    if current is None:
        raise RuntimeError("No frozen clock is active; use frozen_clock() first.")
    _state.frozen = current + delta


def is_frozen() -> bool:
    return getattr(_state, "frozen", None) is not None and settings.ALLOW_TEST_CLOCK


def freeze_from_environment() -> datetime | None:
    """Freeze the clock from ``FROZEN_CLOCK`` when test infrastructure allows it.

    This exists so browser tests and manual walkthroughs can run against fixed
    times, which V3 section 12 requires: "Fixed-time browser fixtures live only in
    an isolated test app." It is inert unless ``ALLOW_TEST_CLOCK`` is on, which
    production settings force to False, and it is not reachable from any URL.
    """
    if not settings.ALLOW_TEST_CLOCK:
        return None

    raw = os.environ.get("FROZEN_CLOCK", "").strip()
    if not raw:
        return None

    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None

    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, BANGKOK)
    _state.frozen = moment.astimezone(ZoneInfo("UTC"))
    return _state.frozen


def local_date(moment: datetime):
    """Bangkok local date for an aware datetime."""
    return moment.astimezone(BANGKOK).date()


def local_time(moment: datetime):
    return moment.astimezone(BANGKOK)


def utc(moment: datetime) -> datetime:
    return moment.astimezone(ZoneInfo("UTC"))
