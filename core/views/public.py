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

from core.models import Booking, Room
from core.services import availability, clock, slots
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


def _grid_context(request, local_date: date) -> dict:
    moment = now()
    user = request.user if request.user.is_authenticated else None
    grid = availability.public_grid(local_date, moment, user=user)
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
    }


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
def availability_fragment(request):
    """HTMX target refreshed every 30 seconds. Focus is preserved client-side."""
    moment = now()
    local_date = _parse_date(request.GET.get("date"), clock.local_date(moment))
    return render(request, "core/partials/grid_table.html", _grid_context(request, local_date))


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
