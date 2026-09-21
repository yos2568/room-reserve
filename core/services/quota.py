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

from django.db.models import Q

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


def blocking_bookings(user, slot_start):
    """Bookings that block ``slot_start`` and its adjacent hours, across rooms."""
    return Booking.objects.filter(
        user=user,
        status__in=list(BLOCKING_STATUSES),
    ).filter(Q(slot_start=slot_start) | Q(slot_start=slot_start - SLOT) | Q(slot_start=slot_start + SLOT))


# Held hours for the upcoming cap (D-38): a booking counts until its hour ends.
UPCOMING_STATUSES = ("PENDING_APPROVAL", "SCHEDULED", "IN_USE")


def upcoming_hours(user, now) -> int:
    """Hours the student holds that have not finished yet, walk-ins included."""
    return Booking.objects.filter(user=user, status__in=UPCOMING_STATUSES, slot_end__gt=now).count()


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


def assert_quota_and_adjacency(user, slot_start, *, room=None, local_date=None, now=None) -> None:
    """Raise a clean rejection when quota, the upcoming cap or adjacency forbids a new booking."""
    if local_date is None:
        local_date = clock.local_date(slot_start)
    if now is None:
        now = clock.now()

    policy = current_policy()

    if blocking_bookings(user, slot_start).exists():
        raise OperationRejected(Code.ADJACENCY_CONFLICT)

    if quota_used(user, local_date) >= policy.daily_quota:
        raise OperationRejected(Code.QUOTA_EXCEEDED)

    if upcoming_hours(user, now) >= policy.max_upcoming_hours:
        raise OperationRejected(Code.UPCOMING_LIMIT, limit=policy.max_upcoming_hours)

    if room is not None and no_show_reclaim(user, room, slot_start):
        raise OperationRejected(Code.NO_SHOW_RECLAIM)


def quota_remaining(user, local_date) -> int:
    policy = current_policy()
    return max(0, policy.daily_quota - quota_used(user, local_date))
