"""The staff booking timeline: every room down, the day's hours across (D-44).

One read-only picture of a day for staff: each booking is a bar placed on its
hour, beside the class hours and closures that also take a room. Hours nobody
holds are offered as gaps that open Book a room for that room and hour.
Unlike the public grid, it names who booked, so it is staff-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import Booking, Room

from . import clock, slots
from .availability import _closures_for, _room_kind
from .calendar import day_hours, weekly_blocks_for_date

# A booking's look on the timeline, by status.
_BOOKING_KIND = {
    Booking.Status.PENDING_APPROVAL: "pending",
    Booking.Status.SCHEDULED: "booked",
    Booking.Status.IN_USE: "inuse",
    Booking.Status.COMPLETED: "done",
    Booking.Status.NO_SHOW: "noshow",
}


@dataclass
class Bar:
    kind: str  # pending | booked | staff | inuse | done | noshow | class | closed
    start_hour: int
    end_hour: int
    label: str
    booking: Booking | None = None
    col_start: int = 0
    col_end: int = 0
    lane: int = 1  # 2: a booking drawn below a class or closure it overlaps


@dataclass
class TimelineRow:
    room: Room
    bars: list = field(default_factory=list)
    gaps: list = field(default_factory=list)  # (hour, col) a staff booking could start at
    has_lanes: bool = False


def day_timeline(local_date, now) -> dict:
    """Rows of bars for ``local_date``; ``hours`` is None on a closed day."""
    hours = day_hours(local_date)
    rooms = list(Room.objects.filter(is_active=True).order_by("position", "number"))
    if hours is None:
        return {"hours": None, "groups": [], "columns": [], "now_percent": None}
    open_hour, close_hour = hours
    room_ids = [room.pk for room in rooms]

    bookings = (
        Booking.objects.filter(slot_date=local_date, room_id__in=room_ids, status__in=list(_BOOKING_KIND))
        .select_related("user", "room")
        .order_by("slot_start")
    )
    blocks = weekly_blocks_for_date(local_date, room_ids)
    closures = _closures_for(local_date, room_ids)

    def place(bar: Bar) -> Bar | None:
        start, end = max(bar.start_hour, open_hour), min(bar.end_hour, close_hour)
        if start >= end:
            return None
        bar.start_hour, bar.end_hour = start, end
        bar.col_start, bar.col_end = start - open_hour + 1, end - open_hour + 1
        return bar

    rows = {room.pk: TimelineRow(room=room) for room in rooms}
    taken = {room.pk: set() for room in rooms}
    for room in rooms:
        for block in blocks.get(room.pk, []):
            bar = place(Bar("class", block.start_hour, block.end_hour, block.reason))
            if bar:
                rows[room.pk].bars.append(bar)
                taken[room.pk].update(range(bar.start_hour, bar.end_hour))
        for closure in closures.get(None, []) + closures.get(room.pk, []):
            start = clock.local_time(closure.starts_at)
            end = clock.local_time(closure.ends_at)
            start_hour = start.hour if clock.local_date(closure.starts_at) == local_date else 0
            end_hour = (
                (end.hour + (1 if end.minute else 0))
                if clock.local_date(closure.ends_at) == local_date
                else 24
            )
            bar = place(Bar("closed", start_hour, end_hour, closure.reason))
            if bar:
                rows[room.pk].bars.append(bar)
                taken[room.pk].update(range(bar.start_hour, bar.end_hour))
    # A no-show releases its hour, so a walk-in may follow it in the same room:
    # draw only the booking that holds the hour, never two bars on one cell.
    by_hour = {}
    for booking in bookings:
        key = (booking.room_id, booking.slot_start)
        if key not in by_hour or by_hour[key].status == Booking.Status.NO_SHOW:
            by_hour[key] = booking
    for booking in by_hour.values():
        hour = booking.local_start.hour
        kind = _BOOKING_KIND[booking.status]
        if kind in ("booked", "inuse") and booking.is_staff_booking:
            kind = "staff"
        if kind == "staff":
            label = booking.title or booking.user.display_name
        else:
            label = booking.user.display_name + (f" · {booking.title}" if booking.title else "")
        bar = place(Bar(kind, hour, hour + 1, label, booking=booking))
        if bar:
            row = rows[booking.room_id]
            # A booking made before a class or closure was added keeps its
            # hour; show both rather than painting one over the other.
            if hour in taken[booking.room_id]:
                bar.lane = 2
                row.has_lanes = True
            row.bars.append(bar)
            taken[booking.room_id].add(hour)

    current = clock.local_time(slots.floor_to_slot(now)).hour if clock.local_date(now) == local_date else None
    future_day = local_date > clock.local_date(now)
    for room in rooms:
        for hour in range(open_hour, close_hour):
            if hour in taken[room.pk]:
                continue
            if future_day or (current is not None and hour > current):
                rows[room.pk].gaps.append((hour, hour - open_hour + 1))

    now_percent = None
    if clock.local_date(now) == local_date:
        start = slots.slot_start_for(local_date, open_hour)
        span = (close_hour - open_hour) * 3600
        now_percent = max(0, min(100, round((now - start).total_seconds() * 100 / span)))

    groups = []
    for kind in ("teaching", "meeting", "practice"):
        members = [rows[room.pk] for room in rooms if _room_kind(room) == kind]
        if members:
            groups.append((kind, members))
    return {
        "hours": hours,
        "columns": list(range(open_hour, close_hour)),
        "groups": groups,
        "now_percent": now_percent,
    }
