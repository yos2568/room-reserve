"""Effective calendar: closures, date overrides and the weekly timetable.

Precedence (V3 section 3.1): an explicit room or all-room closure wins, then a
date override, then the weekly timetable. Intersecting any part of an hourly slot
with a closure blocks new reservations and walk-ins for that whole slot; an
opening override never defeats an explicit closure.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Q

from core.models import CalendarOverride, Closure

from . import clock, slots
from .errors import Code, OperationRejected


def day_hours(local_date: date) -> tuple[int, int] | None:
    """Opening hours for a local date, or None when the day is closed."""
    override = CalendarOverride.objects.filter(local_date=local_date).first()
    if override is not None:
        if not override.is_open:
            return None
        return (override.open_hour, override.close_hour)
    return slots.default_opening_hours(local_date)


def active_closures(slot_start, slot_end, room):
    """Unrevoked closures whose interval intersects the slot and room scope."""
    return Closure.objects.filter(
        revoked_at__isnull=True,
        starts_at__lt=slot_end,
        ends_at__gt=slot_start,
    ).filter(Q(room__isnull=True) | Q(room=room))


def is_slot_closed(slot_start, room) -> bool:
    slot_end = slots.slot_end_for(slot_start)
    return active_closures(slot_start, slot_end, room).exists()


def closure_reason(slot_start, room) -> str:
    slot_end = slots.slot_end_for(slot_start)
    closure = active_closures(slot_start, slot_end, room).first()
    return closure.reason if closure else ""


def assert_slot_open(slot_start, room) -> None:
    """Raise a clean rejection when the slot is not bookable on the calendar."""
    if not room.is_active:
        raise OperationRejected(Code.CLOSED)

    local_date = clock.local_date(slot_start)
    hours = day_hours(local_date)
    if hours is None:
        raise OperationRejected(Code.CLOSED)

    open_hour, close_hour = hours
    local_hour = clock.local_time(slot_start).hour
    if not (open_hour <= local_hour < close_hour):
        raise OperationRejected(Code.CLOSED)

    if is_slot_closed(slot_start, room):
        raise OperationRejected(Code.CLOSED)


def bookable_slot_starts(local_date: date, room) -> list:
    """Slot starts that a new booking could target on a date.

    Calendar only: quota, adjacency and account state are per-user and are not
    applied here. Returns an empty list when the day is closed.
    """
    hours = day_hours(local_date)
    if hours is None or not room.is_active:
        return []
    open_hour, close_hour = hours
    starts = list(slots.day_slot_starts(local_date, open_hour, close_hour))
    if not starts:
        return []
    if not active_closures(starts[0], slots.slot_end_for(starts[-1]), room).exists():
        return starts
    return [start for start in starts if not is_slot_closed(start, room)]


def closures_for_range(start, end):
    return Closure.objects.filter(
        revoked_at__isnull=True, starts_at__lt=end, ends_at__gt=start
    ).select_related("room")
