"""Effective calendar: closures, date overrides, the weekly timetable and classes.

Precedence (V3 section 3.1): an explicit room or all-room closure wins, then a
date override, then the weekly timetable. Intersecting any part of an hourly slot
with a closure blocks new reservations and walk-ins for that whole slot; an
opening override never defeats an explicit closure. A recurring ``WeeklyBlock``
(the teaching timetable, section D-28) sits in the weekly layer with the opening
hours: it too blocks reservations and walk-ins, and it never invalidates a
booking that already exists.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Q

from core.models import CalendarOverride, Closure, WeeklyBlock

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


def weekly_block_for(slot_start, room) -> WeeklyBlock | None:
    """The recurring block covering this slot, if any.

    The comparison is on local weekday and hour, so a block applies to every
    matching slot within its optional validity window. Slots are hour-aligned,
    so an hour comparison is exact: a block claims every slot it touches.
    """
    local_date = clock.local_date(slot_start)
    local_hour = clock.local_time(slot_start).hour
    return (
        WeeklyBlock.objects.filter(
            room=room,
            weekday=local_date.weekday(),
            start_hour__lte=local_hour,
            end_hour__gt=local_hour,
        )
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=local_date))
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=local_date))
        .first()
    )


def weekly_blocks_for_date(local_date: date, room_ids) -> dict[int, list[WeeklyBlock]]:
    """Blocks applying to ``local_date`` by weekday and validity, grouped by room id.

    Hour filtering is left to the caller, which knows the slot hours it renders;
    one query serves the whole grid.
    """
    blocks = (
        WeeklyBlock.objects.filter(room_id__in=room_ids, weekday=local_date.weekday())
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=local_date))
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=local_date))
    )
    by_room: dict[int, list[WeeklyBlock]] = {}
    for block in blocks:
        by_room.setdefault(block.room_id, []).append(block)
    return by_room


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

    block = weekly_block_for(slot_start, room)
    if block is not None:
        raise OperationRejected(Code.CLASS_IN_SESSION, room_id=room.pk, reason=block.reason)


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
    has_closures = active_closures(starts[0], slots.slot_end_for(starts[-1]), room).exists()
    has_blocks = WeeklyBlock.objects.filter(room=room, weekday=local_date.weekday()).exists()
    if not has_closures and not has_blocks:
        return starts
    return [
        start for start in starts if not is_slot_closed(start, room) and weekly_block_for(start, room) is None
    ]


def closures_for_range(start, end):
    return Closure.objects.filter(
        revoked_at__isnull=True, starts_at__lt=end, ends_at__gt=start
    ).select_related("room")
