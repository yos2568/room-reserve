"""Student booking views: reserve, use now, check in, cancel.

Every mutation is a POST with CSRF and an idempotency key, so a double submit or
a browser retry returns the recorded result rather than creating a second
booking (V3 sections 5 and 9).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.models import Booking, Room
from core.services import availability, clock, instruments, sanctions, slots
from core.services import booking as booking_service
from core.services import cancel as cancel_service
from core.services import checkin as checkin_service
from core.services import walkin as walkin_service
from core.services.eligibility import active_suspension
from core.services.errors import Code, OperationOutcome, message_for
from core.services.policy import current_policy
from core.services.quota import quota_remaining, quota_used

from ._helpers import (
    add_outcome_message,
    now,
    operation_key,
    run_view_operation,
)


def _decode_slot_or_404(raw: str):
    slot_start = slots.try_decode_slot(raw)
    if slot_start is None:
        raise Http404("Unknown slot identifier")
    return slot_start


def _new_key() -> str:
    return str(uuid.uuid4())


def _success(**data) -> OperationOutcome:
    return OperationOutcome.success(**data)


def _grid_url(slot_start) -> str:
    return f"{reverse('core:home')}?date={clock.local_date(slot_start).isoformat()}"


def _may_reserve(room: Room, user) -> bool:
    return room.may_be_reserved_by(instruments.category_for_user(user))


@login_required
@require_http_methods(["GET", "POST"])
def book_slot(request, room_id: int, slot: str):
    """Future reservation: confirmation page on GET, creation on POST."""
    room = get_object_or_404(Room, pk=room_id)
    slot_start = _decode_slot_or_404(slot)
    moment = now()
    policy = current_policy()

    if not _may_reserve(room, request.user):
        # Never present a form that the server is certain to refuse. The grid
        # explains the same thing on the cell itself, with the reason.
        messages.warning(request, message_for(Code.ROOM_NOT_FOR_INSTRUMENT))
        return redirect(_grid_url(slot_start))

    if request.method == "POST":
        return confirm_booking(request, room_id=room_id, slot=slot)

    outcome = None
    return render(
        request,
        "core/book_confirm.html",
        {
            "room": room,
            "slot_start": slot_start,
            "slot_end": slots.slot_end_for(slot_start),
            "slot_key": slots.encode_slot(slot_start),
            "deadline": slot_start + timedelta(minutes=policy.checkin_grace_minutes),
            "policy": policy,
            "moment": moment,
            "quota_used": quota_used(request.user, clock.local_date(slot_start)),
            "quota_remaining": quota_remaining(request.user, clock.local_date(slot_start)),
            "operation_key": _new_key(),
            "outcome": outcome,
        },
    )


@login_required
@require_POST
def confirm_booking(request, room_id: int, slot: str):
    """Create the reservation, with POST revalidation and stale-hour handling."""
    room = get_object_or_404(Room, pk=room_id)
    slot_start = _decode_slot_or_404(slot)
    payload = booking_service.build_payload(room, slot_start)

    outcome = run_view_operation(
        request=request,
        operation="advance_booking",
        payload=payload,
        key=operation_key(request),
        body=lambda ctx: _success(
            **booking_service.create_advance_booking(ctx, room=room, slot_start=slot_start)
        ),
    )

    if outcome.ok:
        add_outcome_message(request, outcome, success_message="จองห้องเรียบร้อยแล้ว / Room booked.")
        return redirect("core:my_bookings")

    if outcome.code == Code.STALE_HOUR:
        # The hour changed while the page was open. Offer the current-slot action
        # instead of silently declaring attendance.
        messages.warning(request, outcome.message)
        return render(
            request,
            "core/book_stale.html",
            {
                "room": room,
                "slot_start": slot_start,
                "cell": availability.current_slot_cell(room, now(), user=request.user),
                "remaining_minutes": outcome.data.get("remaining_minutes", 0),
                "operation_key": _new_key(),
                "policy": current_policy(),
                "moment": now(),
            },
        )

    add_outcome_message(request, outcome)
    if outcome.code in {Code.ROOM_NOT_FOR_INSTRUMENT, Code.CLOSED}:
        # Neither refusal can be resolved by anything on this page, so send the
        # student back to the grid rather than re-offering the same button.
        return redirect(_grid_url(slot_start))
    return render(
        request,
        "core/book_confirm.html",
        {
            "room": room,
            "slot_start": slot_start,
            "slot_end": slots.slot_end_for(slot_start),
            "slot_key": slots.encode_slot(slot_start),
            "policy": current_policy(),
            "moment": now(),
            "quota_used": quota_used(request.user, clock.local_date(slot_start)),
            "quota_remaining": quota_remaining(request.user, clock.local_date(slot_start)),
            "operation_key": _new_key(),
            "outcome": outcome,
        },
        status=409,
    )


@login_required
@require_http_methods(["GET", "POST"])
def use_now(request, room_id: int, slot: str):
    """Current-slot use-now. Both the grid and the QR reach this POST."""
    room = get_object_or_404(Room, pk=room_id)
    requested_slot = _decode_slot_or_404(slot)
    moment = now()
    actual_slot = walkin_service.current_slot(moment)

    if request.method == "GET":
        cell = availability.current_slot_cell(room, moment, user=request.user)
        remaining = slots.remaining_minutes(moment, cell.slot_end)
        return render(
            request,
            "core/use_now_confirm.html",
            {
                "room": room,
                "cell": cell,
                "requested_slot": requested_slot,
                "remaining_minutes": remaining,
                "short_warning": remaining < walkin_service.SHORT_WARNING_MINUTES,
                "suspension": active_suspension(request.user, moment),
                "policy": current_policy(),
                "moment": moment,
                "quota_used": quota_used(request.user, clock.local_date(actual_slot)),
                "quota_remaining": quota_remaining(request.user, clock.local_date(actual_slot)),
                "operation_key": _new_key(),
            },
        )

    payload = walkin_service.build_payload(room, actual_slot)
    outcome = run_view_operation(
        request=request,
        operation="use_now",
        payload=payload,
        key=operation_key(request),
        body=lambda ctx: _success(**walkin_service.use_now(ctx, room=room)),
    )

    if outcome.ok:
        add_outcome_message(
            request,
            outcome,
            success_message="เริ่มใช้งานห้องแล้ว / Room is now in use.",
        )
        return redirect("core:my_bookings")

    add_outcome_message(request, outcome)
    cell = availability.current_slot_cell(room, now(), user=request.user)
    remaining = slots.remaining_minutes(now(), cell.slot_end)
    return render(
        request,
        "core/use_now_confirm.html",
        {
            "room": room,
            "cell": cell,
            "requested_slot": requested_slot,
            "remaining_minutes": remaining,
            "short_warning": remaining < walkin_service.SHORT_WARNING_MINUTES,
            "suspension": active_suspension(request.user, now()),
            "policy": current_policy(),
            "moment": now(),
            "quota_used": quota_used(request.user, clock.local_date(actual_slot)),
            "quota_remaining": quota_remaining(request.user, clock.local_date(actual_slot)),
            "operation_key": _new_key(),
            "outcome": outcome,
        },
        status=409,
    )


@login_required
@require_GET
def my_bookings(request):
    moment = now()
    today = clock.local_date(moment)
    upcoming = availability.my_upcoming(request.user, moment)
    history = availability.my_history(request.user, moment)

    return render(
        request,
        "core/my_bookings.html",
        {
            "upcoming": upcoming,
            "history": history,
            "moment": moment,
            "today": today,
            "quota_used": quota_used(request.user, today),
            "quota_remaining": quota_remaining(request.user, today),
            "policy": current_policy(),
            "suspension": active_suspension(request.user, moment),
            "strike_summary": sanctions.strike_summary(request.user, moment),
            "appeals": sanctions.pending_appeals(request.user),
            "operation_key": _new_key(),
        },
    )


@login_required
@require_POST
def cancel_booking(request, pk: int):
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk)
    if booking.user_id != request.user.pk:
        # Do not confirm the existence of another student's booking.
        raise Http404

    reason = (request.POST.get("reason") or "").strip()
    payload = cancel_service.build_payload(booking, reason)

    outcome = run_view_operation(
        request=request,
        operation="cancel_booking",
        payload=payload,
        key=operation_key(request),
        body=lambda ctx: _success(**cancel_service.cancel(ctx, booking=booking, reason=reason)),
    )
    add_outcome_message(request, outcome)
    return redirect("core:my_bookings")


@login_required
@require_POST
def check_in(request, pk: int):
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk)
    if booking.user_id != request.user.pk:
        raise Http404

    payload = checkin_service.build_payload(booking)
    outcome = run_view_operation(
        request=request,
        operation="check_in",
        payload=payload,
        key=operation_key(request),
        body=lambda ctx: _success(**checkin_service.check_in(ctx, booking=booking, room=booking.room)),
    )
    add_outcome_message(request, outcome)
    return redirect("core:my_bookings")
