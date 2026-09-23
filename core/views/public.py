"""Public views: the availability grid and the printable QR landing page.

V3 section 9 requires the public grid to contain no personal fields in HTML or
JSON, and to expose ``Reserve`` for future slots and ``Use now`` for the current
one. Everything here is GET-only and writes nothing.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from core.models import BLOCKING_STATUSES, Booking, Room
from core.services import availability, clock, slots, suggest
from core.services import checkin as checkin_service
from core.services.eligibility import active_suspension
from core.services.errors import OperationOutcome
from core.services.policy import current_policy

from ._helpers import add_outcome_message, now, operation_key, run_view_operation


def _parse_date(raw: str | None, default: date) -> date:
    if not raw:
        return default
    try:
        parsed = date.fromisoformat(raw)
    except (TypeError, ValueError):
        return default
    # Viewing runs through the published last date. Booking stays on the horizon.
    today = clock.local_date(clock.now())
    if slots.clamp_view_date(parsed, today) is None:
        return default
    return parsed


def _grid_context(request, local_date: date, *, include_suggestions: bool = True) -> dict:
    moment = now()
    user = request.user if request.user.is_authenticated else None
    capacity_min = _parse_capacity(request.GET.get("capacity"))
    equipment_query = (request.GET.get("equipment") or "").strip()[:80]
    room_number = (request.GET.get("room") or "").strip()[:12]
    grid = availability.public_grid(
        local_date,
        moment,
        user=user,
        capacity_min=capacity_min,
        equipment_query=equipment_query,
        room_number=room_number,
    )
    today = clock.local_date(moment)
    policy = current_policy()
    last = slots.calendar_last_date(today)

    return {
        "grid": grid,
        "suggestions": (
            suggest.suggestions(
                local_date,
                moment,
                user=user,
                capacity_min=capacity_min,
                equipment_query=equipment_query,
                room_number=room_number,
            )
            if include_suggestions
            else None
        ),
        "local_date": local_date,
        "today_date": today,
        "calendar": slots.month_page(local_date, today),
        "is_today": local_date == today,
        "prev_date": local_date - timedelta(days=1) if local_date > today else None,
        "next_date": local_date + timedelta(days=1) if local_date < last else None,
        "moment": moment,
        "policy": policy,
        "closed_reason": _day_closed_reason(local_date),
        "capacity_min": capacity_min,
        "equipment_query": equipment_query,
        "selected_room": room_number,
        "room_options": Room.objects.filter(is_active=True).order_by("position", "number"),
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


def _board_hour(raw: str | None, moment) -> int:
    """Return a safe hourly choice for the room-selection board."""
    try:
        hour = int(raw or "")
    except (TypeError, ValueError):
        hour = clock.local_time(moment).hour
    if not settings.OPENING_SLOT_START_HOUR <= hour <= settings.LAST_SLOT_START_HOUR:
        return settings.OPENING_SLOT_START_HOUR
    return hour


@require_GET
def room_board(request):
    """Show every room at one selected date and hour, like a seat picker."""
    moment = now()
    local_date = _parse_date(request.GET.get("date"), clock.local_date(moment))
    selected_hour = _board_hour(request.GET.get("time"), moment)
    user = request.user if request.user.is_authenticated else None
    grid = availability.public_grid(local_date, moment, user=user)
    rows = []
    for row in grid["rows"]:
        cell = next(
            (cell for cell in row["cells"] if clock.local_time(cell.slot_start).hour == selected_hour),
            None,
        )
        rows.append({"room": row["room"], "cell": cell})

    today = clock.local_date(moment)
    last = slots.calendar_last_date(today)
    return render(
        request,
        "core/room_board.html",
        {
            "rows": rows,
            "local_date": local_date,
            "today_date": today,
            "calendar": slots.month_page(local_date, today),
            "selected_hour": selected_hour,
            "time_options": range(settings.OPENING_SLOT_START_HOUR, settings.LAST_SLOT_START_HOUR + 1),
            "is_today": local_date == today,
            "prev_date": local_date - timedelta(days=1) if local_date > today else None,
            "next_date": local_date + timedelta(days=1) if local_date < last else None,
        },
    )


def _day_closed_reason(local_date: date) -> str:
    from core.services.calendar import day_hours

    if day_hours(local_date) is None:
        return "closed"
    return ""


@require_GET
def grid(request):
    """The whole availability grid: configured rooms by twelve hourly slots."""
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
    today = clock.local_date(moment)
    selected = _parse_date(request.GET.get("date"), today)
    last = slots.calendar_last_date(today)
    monday = selected - timedelta(days=selected.weekday())
    prev_anchor = monday - timedelta(days=7)
    if prev_anchor + timedelta(days=6) < today:
        prev_week = None
    elif prev_anchor < today:
        prev_week = today
    else:
        prev_week = prev_anchor
    next_anchor = monday + timedelta(days=7)
    next_week = next_anchor if next_anchor <= last else None
    week_days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        context = _grid_context(request, day, include_suggestions=False)
        week_days.append(
            {
                "date": day,
                "is_today": day == today,
                "in_range": today <= day <= last,
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
            "local_date": selected,
            "calendar": slots.month_page(selected, today),
            "prev_week": prev_week,
            "next_week": next_week,
            "moment": moment,
            "capacity_min": _parse_capacity(request.GET.get("capacity")),
            "equipment_query": (request.GET.get("equipment") or "").strip()[:80],
            "selected_room": (request.GET.get("room") or "").strip()[:12],
            "room_options": Room.objects.filter(is_active=True).order_by("position", "number"),
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


# How far ahead the door page mentions the student's next booking in this room,
# so arriving a little early reads "opens at 14:00", not "not available".
DOOR_UPCOMING_NOTICE = timedelta(minutes=60)


def _door_checkin_state(booking: Booking | None, moment) -> str:
    """Why the door page does or does not offer check-in for this hour's booking."""
    if booking is None:
        return "none"
    if booking.status == Booking.Status.IN_USE:
        return "in_use"
    if booking.status == Booking.Status.PENDING_APPROVAL:
        return "pending"
    if booking.status == Booking.Status.NO_SHOW:
        return "closed"
    if booking.deadline is None:
        return "none"
    if moment < booking.slot_start:
        return "not_open"
    if moment >= booking.deadline:
        return "closed"
    return "ready"


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
            status__in=[
                Booking.Status.SCHEDULED,
                Booking.Status.IN_USE,
                Booking.Status.PENDING_APPROVAL,
                Booking.Status.NO_SHOW,
            ],
        )
        .select_related("room")
        .first()
    )
    checkin_state = _door_checkin_state(my_booking, moment)

    # Nothing for this door this hour: say where the student should be instead,
    # so a scan never ends on an unexplained "not available".
    other_room_booking = upcoming_booking = None
    if my_booking is None:
        other_room_booking = (
            Booking.objects.filter(
                user=user,
                slot_start=cell.slot_start,
                status__in=[Booking.Status.SCHEDULED, Booking.Status.IN_USE],
            )
            .exclude(room=room)
            .select_related("room")
            .first()
        )
        upcoming_booking = (
            Booking.objects.filter(
                user=user,
                room=room,
                slot_start__gt=moment,
                slot_start__lte=moment + DOOR_UPCOMING_NOTICE,
                status__in=[Booking.Status.SCHEDULED, Booking.Status.PENDING_APPROVAL],
            )
            .order_by("slot_start")
            .first()
        )

    suspension = active_suspension(user, moment)

    return render(
        request,
        "core/room_qr.html",
        {
            "room": room,
            "cell": cell,
            "my_booking": my_booking,
            "checkin_state": checkin_state,
            "can_check_in": checkin_state == "ready",
            "other_room_booking": other_room_booking,
            "upcoming_booking": upcoming_booking,
            "upcoming_minutes": (
                max(1, math.ceil((upcoming_booking.slot_start - moment).total_seconds() / 60))
                if upcoming_booking
                else None
            ),
            "remaining_minutes": slots.remaining_minutes(moment, cell.slot_end),
            "short_warning": slots.remaining_minutes(moment, cell.slot_end) < 5,
            "suspension": suspension,
            "policy": policy,
            "moment": moment,
            "operation_key": _new_key(),
        },
    )


@login_required
@require_POST
def room_check_in(request, room_id: int, pk: int):
    """Check in from the printed door QR: the only self-service check-in path (D-37).

    The booking must be the signed-in student's and must be for this door's room;
    the service re-checks ownership, room and the check-in window under the lock.
    A photographed poster still works from anywhere, so this is a declaration
    backed by staff spot checks, not proof of presence.
    """
    room = get_object_or_404(Room, pk=room_id)
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk, user=request.user)

    outcome = run_view_operation(
        request=request,
        operation="check_in",
        payload=checkin_service.build_payload(booking),
        key=operation_key(request),
        body=lambda ctx: OperationOutcome.success(
            **checkin_service.check_in(ctx, booking=booking, room=room)
        ),
    )
    add_outcome_message(request, outcome)
    return redirect("core:my_bookings")


def _new_key() -> str:
    import uuid

    return str(uuid.uuid4())
