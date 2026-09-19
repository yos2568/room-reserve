"""Read-only availability derivation.

V3 section 6: "Read-only availability can derive expired holds as free and
elapsed use as completed. GET does not write." Nothing here calls ``.save()``,
and the public grid never exposes a student identity — a cell knows only its
state, plus an ``is_mine`` flag when the viewer is signed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from django.conf import settings

from core.models import BLOCKING_STATUSES, Booking, Closure, Room

from . import clock, instruments, slots
from .calendar import day_hours, weekly_blocks_for_date


class SlotState(StrEnum):
    PAST = "past"
    CLOSED = "closed"
    IN_USE = "in_use"
    HELD = "held"
    COMPLETED = "completed"
    FREE_NOW = "free_now"
    BOOKABLE = "bookable"
    # Free and bookable in principle, but not by *this* viewer: the room is
    # restricted to other instrument categories. Anyone may still walk in once the
    # hour has started and the room is free, so this state only ever applies to a
    # slot that has not begun.
    RESTRICTED = "restricted"
    TAKEN = "taken"
    OUTSIDE_HORIZON = "outside_horizon"


@dataclass
class SlotCell:
    room: Room
    slot_start: object
    slot_end: object
    state: SlotState
    booking: Booking | None = None
    is_mine: bool = False
    remaining_minutes: int = 0
    closed_reason: str = ""
    block_reason: str = ""

    @property
    def is_current(self) -> bool:
        return self.state in {SlotState.IN_USE, SlotState.HELD, SlotState.FREE_NOW, SlotState.COMPLETED}

    @property
    def slot_key(self) -> str:
        """Compact identifier used in booking and use-now URLs."""
        return slots.encode_slot(self.slot_start)

    @property
    def local_start(self):
        return clock.local_time(self.slot_start)

    @property
    def local_end(self):
        return clock.local_time(self.slot_end)

    @property
    def can_reserve(self) -> bool:
        return self.state == SlotState.BOOKABLE

    @property
    def can_use_now(self) -> bool:
        return self.state == SlotState.FREE_NOW

    @property
    def is_short(self) -> bool:
        return self.state == SlotState.FREE_NOW and self.remaining_minutes < 5


def _bookings_by_slot(local_date, room_ids):
    bookings = (
        Booking.objects.filter(slot_date=local_date, room_id__in=room_ids)
        .filter(status__in=list(BLOCKING_STATUSES))
        .select_related("room", "user")
    )
    lookup = {}
    for booking in bookings:
        lookup[(booking.room_id, booking.slot_start)] = booking
    return lookup


def _closures_for(local_date, room_ids):
    """Unrevoked closures intersecting the local date, grouped by room id (None = all)."""
    day_start = slots.slot_start_for(local_date, 0)
    day_end = day_start + timedelta(hours=24)
    closures = Closure.objects.filter(revoked_at__isnull=True, starts_at__lt=day_end, ends_at__gt=day_start)
    by_room: dict[int | None, list[Closure]] = {}
    for closure in closures:
        by_room.setdefault(closure.room_id, []).append(closure)
    return by_room


def room_day_cells(
    room: Room, local_date, now, *, user=None, closure_map=None, block_map=None, viewer_category=None
) -> list[SlotCell]:
    """Every slot cell for one room on one local date.

    ``viewer_category`` is resolved once by the caller rather than per cell, and it
    decides only whether a *future* slot shows as bookable or as restricted to
    somebody else's instrument.
    """
    closure_map = closure_map if closure_map is not None else _closures_for(local_date, [room.pk])
    block_map = block_map if block_map is not None else weekly_blocks_for_date(local_date, [room.pk])
    hours = day_hours(local_date)
    if hours is None:
        starts = list(slots.day_slot_starts(local_date))
        closed = True
    else:
        starts = list(slots.day_slot_starts(local_date, hours[0], hours[1]))
        closed = False

    bookings = _bookings_by_slot(local_date, [room.pk])
    user_id = getattr(user, "pk", None)
    horizon_last = clock.local_date(now) + timedelta(days=settings.HORIZON_DAYS)
    can_reserve_here = room.may_be_reserved_by(viewer_category)

    cells = []
    for slot_start in starts:
        slot_end = slots.slot_end_for(slot_start)
        booking = bookings.get((room.pk, slot_start))
        is_mine = booking is not None and booking.user_id == user_id
        reason = _closure_reason_for(room, slot_start, slot_end, closure_map)
        block_reason = _block_reason_for(room, slot_start, block_map)

        state = _classify(
            now=now,
            slot_start=slot_start,
            slot_end=slot_end,
            room_active=room.is_active,
            day_closed=closed,
            closure_reason=reason,
            block_reason=block_reason,
            booking=booking,
            horizon_last=horizon_last,
            can_reserve_here=can_reserve_here,
        )
        cells.append(
            SlotCell(
                room=room,
                slot_start=slot_start,
                slot_end=slot_end,
                state=state,
                booking=booking,
                is_mine=is_mine,
                remaining_minutes=slots.remaining_minutes(now, slot_end),
                closed_reason=reason,
                block_reason=block_reason,
            )
        )
    return cells


def _closure_reason_for(room, slot_start, slot_end, closure_map) -> str:
    for closure in closure_map.get(None, []):
        if closure.starts_at < slot_end and closure.ends_at > slot_start:
            return closure.reason
    for closure in closure_map.get(room.pk, []):
        if closure.starts_at < slot_end and closure.ends_at > slot_start:
            return closure.reason
    return ""


def _block_reason_for(room, slot_start, block_map) -> str:
    """The teaching-timetable reason for this slot, if a class claims the hour."""
    hour = clock.local_time(slot_start).hour
    for block in block_map.get(room.pk, []):
        if block.start_hour <= hour < block.end_hour:
            return block.reason
    return ""


def _classify(
    *,
    now,
    slot_start,
    slot_end,
    room_active,
    day_closed,
    closure_reason,
    booking,
    horizon_last,
    block_reason="",
    can_reserve_here=True,
):
    if slot_end <= now:
        return SlotState.PAST
    if not room_active or day_closed or closure_reason:
        return SlotState.CLOSED
    # A booking outranks a class block: a reservation made before the teaching
    # timetable changed stays valid (D-28), so the grid must show it, not "Class".
    if booking is not None:
        if booking.status == Booking.Status.COMPLETED:
            # Historical occupancy: a completed slot is never resold.
            return SlotState.COMPLETED
        if booking.status == Booking.Status.IN_USE:
            return SlotState.IN_USE
        if booking.deadline is not None and slot_start <= now < booking.deadline:
            return SlotState.HELD
        return SlotState.TAKEN
    if block_reason:
        return SlotState.CLOSED
    if slot_start <= now:
        # The hour has begun and nobody holds it. Open to everyone, whatever the
        # room's reservation audience: this is the release valve that keeps a
        # restricted room from standing empty.
        return SlotState.FREE_NOW
    if clock.local_date(slot_start) <= horizon_last:
        return SlotState.BOOKABLE if can_reserve_here else SlotState.RESTRICTED
    return SlotState.OUTSIDE_HORIZON


def public_grid(local_date, now, *, user=None) -> dict:
    """The whole grid: active rooms down, hourly slots across."""
    rooms = list(Room.objects.filter(is_active=True).prefetch_related("allowed_categories"))
    if not rooms:
        return {"rooms": [], "rows": [], "local_date": local_date}

    closure_map = _closures_for(local_date, [room.pk for room in rooms])
    block_map = weekly_blocks_for_date(local_date, [room.pk for room in rooms])
    hours = day_hours(local_date)
    if hours is None:
        column_starts = list(slots.day_slot_starts(local_date))
    else:
        column_starts = list(slots.day_slot_starts(local_date, hours[0], hours[1]))

    # Resolved once for the whole grid rather than once per cell.
    viewer_category = instruments.category_for_user(user)

    rows = []
    for room in rooms:
        cells = room_day_cells(
            room,
            local_date,
            now,
            user=user,
            closure_map=closure_map,
            block_map=block_map,
            viewer_category=viewer_category,
        )
        by_start = {cell.slot_start: cell for cell in cells}
        # Keep the column set stable even when the day is closed or shortened.
        rows.append(
            {
                "room": room,
                "cells": [
                    by_start.get(start)
                    or SlotCell(
                        room=room,
                        slot_start=start,
                        slot_end=slots.slot_end_for(start),
                        state=SlotState.CLOSED,
                    )
                    for start in column_starts
                ],
            }
        )

    return {
        "rooms": rooms,
        "rows": rows,
        "columns": column_starts,
        "local_date": local_date,
        "day_closed": hours is None,
    }


def my_upcoming(user, now, limit: int = 20):
    """Upcoming bookings for My bookings."""
    return (
        Booking.objects.filter(user=user, slot_end__gt=now)
        .exclude(status=Booking.Status.CANCELLED)
        .order_by("slot_start")
        .select_related("room")[:limit]
    )


def my_history(user, now, days: int = 60):
    """Displayed history window (a display limit, not a retention policy)."""
    cutoff = now - timedelta(days=days)
    return (
        Booking.objects.filter(user=user, slot_start__lt=now, slot_start__gte=cutoff)
        .order_by("-slot_start")
        .select_related("room")
    )


def current_slot_cell(room: Room, now, *, user=None) -> SlotCell:
    """The cell for the hour in progress, used by the QR landing page."""
    local_date = clock.local_date(now)
    slot_start = slots.floor_to_slot(now)
    cells = room_day_cells(
        room,
        local_date,
        now,
        user=user,
        viewer_category=instruments.category_for_user(user),
    )
    for cell in cells:
        if cell.slot_start == slot_start:
            return cell
    return SlotCell(
        room=room,
        slot_start=slot_start,
        slot_end=slots.slot_end_for(slot_start),
        state=SlotState.CLOSED,
    )
