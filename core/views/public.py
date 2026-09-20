"""Public views: the availability grid and the printable QR landing page.

V3 section 9 requires the public grid to contain no personal fields in HTML or
JSON, and to expose ``Reserve`` for future slots and ``Use now`` for the current
one. Everything here is GET-only and writes nothing.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from core.models import BLOCKING_STATUSES, Booking, Room
from core.services import availability, clock, slots, suggest
from core.services.eligibility import active_suspension
from core.services.policy import current_policy

from ._helpers import now


def _parse_date(raw: str | None, default: date) -> date:
    if not raw:
        return default
    try:
        parsed = date.fromisoformat(raw)
    except (TypeError, ValueError):
        return default
    # Never show a date outside the bookable horizon.
    policy = current_policy()
    today = clock.local_date(clock.now())
    latest = today + timedelta(days=policy.horizon_days)
    if parsed < today or parsed > latest:
        return default
    return parsed


def _grid_context(request, local_date: date, *, include_suggestions: bool = True) -> dict:
    moment = now()
    user = request.user if request.user.is_authenticated else None
    capacity_min = _parse_capacity(request.GET.get("capacity"))
    equipment_query = (request.GET.get("equipment") or "").strip()[:80]
    grid = availability.public_grid(
        local_date,
        moment,
        user=user,
        capacity_min=capacity_min,
        equipment_query=equipment_query,
    )
    today = clock.local_date(moment)

    days = []
    policy = current_policy()
    for offset in range(policy.horizon_days + 1):
        candidate = today + timedelta(days=offset)
        days.append(
            {
                "date": candidate,
                "is_selected": candidate == local_date,
                "is_today": candidate == today,
            }
        )

    return {
        "grid": grid,
        "suggestions": (
            suggest.suggestions(
                local_date,
                moment,
                user=user,
                capacity_min=capacity_min,
                equipment_query=equipment_query,
            )
            if include_suggestions
            else None
        ),
        "local_date": local_date,
        "days": days,
        "is_today": local_date == today,
        "prev_date": local_date - timedelta(days=1) if local_date > today else None,
        "next_date": local_date + timedelta(days=1)
        if local_date < today + timedelta(days=policy.horizon_days)
        else None,
        "moment": moment,
        "policy": policy,
        "closed_reason": _day_closed_reason(local_date),
        "capacity_min": capacity_min,
        "equipment_query": equipment_query,
        "capacity_options": _capacity_options(),
    }


def _parse_capacity(raw: str | None) -> int | None:
    try:
        value = int(raw or "")
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 100 else None


def _capacity_options() -> list[int]:
    configured = set(Room.objects.filter(is_active=True).values_list("capacity", flat=True))
    return sorted(configured | {1, 2, 4, 6, 8, 10})


def _day_closed_reason(local_date: date) -> str:
    from core.services.calendar import day_hours

    if day_hours(local_date) is None:
        return "closed"
    return ""


@require_GET
def grid(request):
    """The whole availability grid: nine rooms by twelve hourly slots."""
    moment = now()
    local_date = _parse_date(request.GET.get("date"), clock.local_date(moment))
    return render(request, "core/grid.html", _grid_context(request, local_date))


@require_GET
def lobby(request):
    """A privacy-safe, auto-refreshing room-status screen for a lobby display."""
    moment = now()
    rows = []
    for room in Room.objects.filter(is_active=True).order_by("position", "number"):
        cell = availability.current_slot_cell(room, moment)
        if cell.state in {availability.SlotState.FREE_NOW, availability.SlotState.BOOKABLE}:
            status_key = "available"
        elif cell.state == availability.SlotState.IN_USE:
            status_key = "in_use"
        elif cell.state in {
            availability.SlotState.HELD,
            availability.SlotState.TAKEN,
            availability.SlotState.COMPLETED,
        }:
            status_key = "reserved"
        elif cell.state == availability.SlotState.CLOSED and cell.block_reason and not cell.closed_reason:
            status_key = "class"
        else:
            status_key = "closed"

        next_booking = (
            Booking.objects.filter(
                room=room,
                slot_start__gt=moment,
                status__in=list(BLOCKING_STATUSES),
            )
            .order_by("slot_start")
            .first()
        )
        rows.append(
            {
                "room": room,
                "cell": cell,
                "status_key": status_key,
                "next_start": next_booking.local_start if next_booking else None,
            }
        )
    return render(request, "core/lobby.html", {"rows": rows, "moment": moment})


@require_GET
def availability_fragment(request):
    """HTMX target refreshed every 30 seconds. Focus is preserved client-side."""
    moment = now()
    local_date = _parse_date(request.GET.get("date"), clock.local_date(moment))
    return render(request, "core/partials/grid_table.html", _grid_context(request, local_date))


@require_GET
def week(request):
    """Seven-day view using the same cell derivation as the single-day grid."""
    moment = now()
    selected = _parse_date(request.GET.get("date"), clock.local_date(moment))
    monday = selected - timedelta(days=selected.weekday())
    week_days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        context = _grid_context(request, day, include_suggestions=False)
        week_days.append(
            {
                "date": day,
                "is_today": day == clock.local_date(moment),
                "grid": context["grid"],
            }
        )
    week_rows = []
    first_rows = week_days[0]["grid"]["rows"] if week_days else []
    for first_row in first_rows:
        room_id = first_row["room"].pk
        week_rows.append(
            {
                "room": first_row["room"],
                "days": [
                    next(
                        (row for row in day["grid"]["rows"] if row["room"].pk == room_id),
                        {"room": first_row["room"], "cells": []},
                    )
                    for day in week_days
                ],
            }
        )
    return render(
        request,
        "core/week.html",
        {
            "week_days": week_days,
            "week_rows": week_rows,
            "selected_date": selected,
            "prev_week": monday - timedelta(days=7),
            "next_week": monday + timedelta(days=7),
            "moment": moment,
            "capacity_min": _parse_capacity(request.GET.get("capacity")),
            "equipment_query": (request.GET.get("equipment") or "").strip()[:80],
            "capacity_options": _capacity_options(),
        },
    )


@require_GET
def room_profile(request, room_id: int):
    room = get_object_or_404(Room, pk=room_id, is_active=True)
    return render(
        request,
        "core/room_profile.html",
        {"room": room, "moment": now(), "policy": current_policy()},
    )


@login_required
@require_GET
def room_qr_landing(request, room_id: int):
    """The stable printed QR target for a room.

    Both the grid link and the QR lead to the same explicit "I am at this room"
    POST. The app accepts that declaration but cannot prove physical presence
    with a static URL, and says so (V3 section 9).
    """
    room = get_object_or_404(Room, pk=room_id)
    moment = now()
    user = request.user
    cell = availability.current_slot_cell(room, moment, user=user)

    policy = current_policy()
    my_booking = (
        Booking.objects.filter(
            user=user,
            room=room,
            slot_start=cell.slot_start,
            status__in=[Booking.Status.SCHEDULED, Booking.Status.IN_USE],
        )
        .select_related("room")
        .first()
    )

    can_check_in = (
        my_booking is not None
        and my_booking.status == Booking.Status.SCHEDULED
        and my_booking.deadline is not None
        and my_booking.slot_start <= moment < my_booking.deadline
    )

    suspension = active_suspension(user, moment)

    return render(
        request,
        "core/room_qr.html",
        {
            "room": room,
            "cell": cell,
            "my_booking": my_booking,
            "can_check_in": can_check_in,
            "remaining_minutes": slots.remaining_minutes(moment, cell.slot_end),
            "short_warning": slots.remaining_minutes(moment, cell.slot_end) < 5,
            "suspension": suspension,
            "policy": policy,
            "moment": moment,
            "operation_key": _new_key(),
        },
    )


def _new_key() -> str:
    import uuid

    return str(uuid.uuid4())
