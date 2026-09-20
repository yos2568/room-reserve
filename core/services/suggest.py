"""Deterministic room suggestions for the availability page and refusals.

The grid already derives every room's every slot state, so "which room should I
take?" is a ranking over data the server computed anyway — never a guess and
never a model (D-29). A suggestion must, in order:

1. be legal for this viewer: opening hours, no closure, no class hour
   (``WeeklyBlock``), and — for *future* slots — the room's reservation
   audience. The current hour is the walk-in exception: it is open to every
   eligible student whatever the audience, so it is surfaced as such.
2. not collide with the viewer's own bookings in the same or an adjacent hour.
3. fit the viewer's remaining daily quota; at zero nothing is suggested and the
   panel says the limit, not an empty box.

Within those rules the order is soonest hour first, then display position.
Every branch here is exercised by ``tests/test_suggest.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models import BLOCKING_STATUSES, Booking, Room

from . import availability, calendar, clock, instruments, quota, slots
from .eligibility import account_block_code, is_suspended
from .errors import OperationRejected


@dataclass(frozen=True)
class FreeNowCard:
    """One room the viewer could walk into for the rest of the current hour."""

    room: Room
    remaining_minutes: int
    slot_key: str


@dataclass(frozen=True)
class HourGroup:
    """Future hours on the selected date, each with the rooms still bookable."""

    slot_start: object
    slot_key: str
    rooms: list


@dataclass(frozen=True)
class Suggestions:
    free_now: list
    hours: list
    can_act: bool
    quota_remaining: int
    quota_exhausted: bool

    def __bool__(self) -> bool:
        return bool(self.free_now or self.hours)


def _viewer_can_act(user, now) -> bool:
    """Whether this viewer could act on a suggestion at all."""
    if user is None or not getattr(user, "pk", None):
        return False
    return account_block_code(user) is None and not is_suspended(user, now)


def _viewer_conflicts(user) -> set:
    """The slot starts this viewer already holds, plus their adjacent hours."""
    if user is None or not getattr(user, "pk", None):
        return set()
    mine = list(
        Booking.objects.filter(user=user, status__in=list(BLOCKING_STATUSES)).values_list(
            "slot_start", flat=True
        )
    )
    delta = slots.SLOT_DELTA
    conflicts = set()
    for start in mine:
        conflicts.update({start, start - delta, start + delta})
    return conflicts


def for_slot(slot_start, now, *, user) -> list:
    """Rooms still bookable by this viewer at one exact slot.

    Used when a booking is refused: the refusal names the rule, and this names
    the rooms that would have accepted the same hour — computed fresh, in the
    same request, from the same services the grid uses. The refused room itself
    is excluded even when a later state would allow it.
    """
    viewer_category = instruments.category_for_user(user)
    conflicts = _viewer_conflicts(user)
    rooms = list(Room.objects.filter(is_active=True).prefetch_related("allowed_categories"))

    found = []
    for room in rooms:
        if not room.may_be_reserved_by(viewer_category):
            continue
        try:
            calendar.assert_slot_open(slot_start, room)
        except OperationRejected:
            continue
        if slot_start <= now:
            continue
        if slot_start in conflicts:
            continue
        if room_has_blocker(room, slot_start):
            continue
        found.append(room)

    found.sort(key=lambda room: (room.position, room.number))
    return found


def room_has_blocker(room: Room, slot_start) -> bool:
    """Whether any blocking booking occupies this room and slot."""
    return Booking.objects.filter(
        room=room,
        slot_start=slot_start,
        status__in=list(BLOCKING_STATUSES),
    ).exists()


def _free_now(local_date, now, *, user, capacity_min: int | None = None, equipment_query: str = "") -> list:
    """Rooms walkable-into right now, longest remaining first.

    The walk-in rule ignores the reservation audience on purpose, so this list
    is filtered by the calendar, occupancy and the viewer's booking restrictions.
    Anonymous visitors see public availability without action buttons.
    """
    today = clock.local_date(now)
    if local_date != today:
        return []
    hours = calendar.day_hours(today)
    if hours is None or not hours[0] <= clock.local_time(now).hour < hours[1]:
        return []

    slot_start = slots.floor_to_slot(now)
    if slot_start in _viewer_conflicts(user):
        return []
    slot_end = slots.slot_end_for(slot_start)
    remaining = slots.remaining_minutes(now, slot_end)
    if remaining <= 0:
        return []

    occupied = set(
        Booking.objects.filter(slot_start=slot_start, status__in=list(BLOCKING_STATUSES)).values_list(
            "room_id", flat=True
        )
    )
    forfeited = set()
    if user is not None and getattr(user, "pk", None):
        forfeited = set(
            Booking.objects.filter(
                user=user, slot_start=slot_start, status=Booking.Status.NO_SHOW
            ).values_list("room_id", flat=True)
        )

    cards = []
    room_query = Room.objects.filter(is_active=True)
    if capacity_min:
        room_query = room_query.filter(capacity__gte=capacity_min)
    if equipment_query:
        room_query = room_query.filter(equipment__icontains=equipment_query)
    for room in room_query.order_by("position", "number"):
        if room.pk in occupied or room.pk in forfeited:
            continue
        if calendar.is_slot_closed(slot_start, room):
            continue
        if calendar.weekly_block_for(slot_start, room) is not None:
            continue
        cards.append(
            FreeNowCard(
                room=room,
                remaining_minutes=remaining,
                slot_key=slots.encode_slot(slot_start),
            )
        )
    return cards


def _hour_groups(
    local_date,
    now,
    *,
    user,
    capacity_min: int | None = None,
    equipment_query: str = "",
    limit: int = 4,
) -> list:
    """The next bookable hours on ``local_date``, with the rooms open in each.

    Derived from the same per-cell states the grid renders, so the panel cannot
    disagree with the table below it: a cell that is not ``BOOKABLE`` for this
    viewer is not suggested here either.
    """
    viewer_category = instruments.category_for_user(user)
    conflicts = _viewer_conflicts(user)
    grid = availability.public_grid(
        local_date,
        now,
        user=user,
        capacity_min=capacity_min,
        equipment_query=equipment_query,
    )

    by_slot: dict[object, list] = {}
    for row in grid["rows"]:
        room = row["room"]
        if not room.may_be_reserved_by(viewer_category):
            continue
        for cell in row["cells"]:
            if not cell.can_reserve or cell.slot_start <= now:
                continue
            if cell.slot_start in conflicts:
                continue
            by_slot.setdefault(cell.slot_start, []).append(room)

    groups = []
    for slot_start in sorted(by_slot)[:limit]:
        rooms = sorted(by_slot[slot_start], key=lambda room: (room.position, room.number))
        groups.append(
            HourGroup(
                slot_start=slot_start,
                slot_key=slots.encode_slot(slot_start),
                rooms=rooms,
            )
        )
    return groups


def suggestions(
    local_date,
    now,
    *,
    user,
    capacity_min: int | None = None,
    equipment_query: str = "",
) -> Suggestions:
    """Everything the dashboard panel needs, in one call."""
    remaining_quota = quota.quota_remaining(user, local_date) if user else None
    # Anonymous visitors see the public suggestions but never the quota note:
    # quota is per account, and there is no account to count.
    exhausted = remaining_quota is not None and remaining_quota <= 0
    return Suggestions(
        free_now=[]
        if exhausted
        else _free_now(
            local_date,
            now,
            user=user,
            capacity_min=capacity_min,
            equipment_query=equipment_query,
        ),
        hours=[]
        if exhausted
        else _hour_groups(
            local_date,
            now,
            user=user,
            capacity_min=capacity_min,
            equipment_query=equipment_query,
        ),
        can_act=_viewer_can_act(user, now),
        quota_remaining=max(0, remaining_quota or 0),
        quota_exhausted=exhausted,
    )
