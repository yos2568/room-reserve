"""Daily quota, the upcoming-hours cap, and same/adjacent hour rules (V3 section 3.3, D-38).

    Status      quota charge   blocks same/adjacent hour
    SCHEDULED        1              yes
    IN_USE           1              yes
    COMPLETED        1              yes
    NO_SHOW          1              no (but the owner cannot reclaim that room/hour)
    CANCELLED        0              no

Quota and adjacency are not expressible as unique constraints alone, so every
mutation path must run these checks under the control lock.
"""

from __future__ import annotations

from datetime import timedelta

from core.models import BLOCKING_STATUSES, QUOTA_STATUSES, Booking

from . import clock
from .errors import Code, OperationRejected
from .policy import current_policy

SLOT = timedelta(minutes=60)


def quota_used(user, local_date) -> int:
    """Bookings charged against the daily quota for a Bangkok date."""
    return Booking.objects.filter(
        user=user,
        slot_date=local_date,
        status__in=list(QUOTA_STATUSES),
    ).count()


def blocking_bookings(user, slot_start, slot_end=None):
    """Bookings that overlap this request or its one-hour adjacency buffer."""
    slot_end = slot_end or slot_start + SLOT
    return Booking.objects.filter(
        user=user,
        status__in=list(BLOCKING_STATUSES),
        slot_start__lt=slot_end + SLOT,
        slot_end__gt=slot_start - SLOT,
    )


# Held hours for the upcoming cap (D-38): a booking counts until its hour ends.
UPCOMING_STATUSES = ("PENDING_APPROVAL", "SCHEDULED", "IN_USE")


def upcoming_hours(user, now) -> int:
    """Hours the student holds that have not finished yet, walk-ins included."""
    total = 0
    for booking in Booking.objects.filter(user=user, status__in=UPCOMING_STATUSES, slot_end__gt=now).only(
        "slot_start", "slot_end"
    ):
        remaining_from = max(booking.slot_start, now)
        seconds = (booking.slot_end - remaining_from).total_seconds()
        total += max(1, int((seconds + SLOT.total_seconds() - 1) // SLOT.total_seconds()))
    return total


def upcoming_remaining(user, now) -> int:
    return max(0, current_policy().max_upcoming_hours - upcoming_hours(user, now))


def no_show_reclaim(user, room, slot_start) -> bool:
    """True when the user forfeited exactly this room and hour as a no-show."""
    return Booking.objects.filter(
        user=user,
        room=room,
        slot_start=slot_start,
        status=Booking.Status.NO_SHOW,
    ).exists()


def assert_quota_and_adjacency(
    user, slot_start, *, room=None, local_date=None, now=None, duration_hours: int = 1
) -> None:
    """Raise a clean rejection when quota, the upcoming cap or adjacency forbids a new booking."""
    if local_date is None:
        local_date = clock.local_date(slot_start)
    if now is None:
        now = clock.now()

    policy = current_policy()

    slot_end = slot_start + SLOT * duration_hours
    if blocking_bookings(user, slot_start, slot_end).exists():
        raise OperationRejected(Code.ADJACENCY_CONFLICT)

    if quota_used(user, local_date) >= policy.daily_quota:
        raise OperationRejected(Code.QUOTA_EXCEEDED)

    if upcoming_hours(user, now) + duration_hours > policy.max_upcoming_hours:
        raise OperationRejected(Code.UPCOMING_LIMIT, limit=policy.max_upcoming_hours)

    if room is not None and no_show_reclaim(user, room, slot_start):
        raise OperationRejected(Code.NO_SHOW_RECLAIM)


def quota_remaining(user, local_date) -> int:
    policy = current_policy()
    return max(0, policy.daily_quota - quota_used(user, local_date))
