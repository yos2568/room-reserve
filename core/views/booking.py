"""Student booking views: reserve, use now, check in, cancel.

Every mutation is a POST with CSRF and an idempotency key, so a double submit or
a browser retry returns the recorded result rather than creating a second
booking (V3 sections 5 and 9).
"""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.models import Booking, RecurringReservation, Room
from core.services import availability, clock, instruments, sanctions, slots, suggest
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


def _qr_window_state(booking: Booking, moment) -> str:
    """Which message the QR landing page should show, computed for display only.

    The service re-checks every rule under the lock on POST; this only decides
    which honest sentence to print before the student commits.
    """
    if booking.status == Booking.Status.IN_USE:
        return "already"
    if booking.status != Booking.Status.SCHEDULED:
        return "finished"
    if moment < booking.slot_start:
        return "early"
    if booking.deadline is not None and moment >= booking.deadline:
        return "closed"
    return "open"


@login_required
@require_http_methods(["GET", "POST"])
def checkin_qr(request, token: str):
    """Landing page for the QR code carried by the confirmation email (D-31).

    Scanning brings the student to one booking's check-in page. The token binds
    the link to that reservation, sign-in binds the confirmation to its owner,
    and the confirm button keeps check-in a deliberate act — the code is a
    convenience, never a proof of presence.
    """
    booking = get_object_or_404(Booking.objects.select_related("room", "user"), checkin_token=token)
    if booking.user_id != request.user.pk:
        # A token is that student's key: another account learns nothing here.
        raise Http404

    outcome = None
    if request.method == "POST":
        payload = checkin_service.build_payload(booking)
        outcome = run_view_operation(
            request=request,
            operation="check_in",
            payload=payload,
            key=operation_key(request),
            body=lambda ctx: _success(**checkin_service.check_in(ctx, booking=booking, room=booking.room)),
        )
        add_outcome_message(request, outcome)
        if outcome.ok:
            return redirect("core:my_bookings")
        booking.refresh_from_db()

    return render(
        request,
        "core/checkin_qr.html",
        {
            "booking": booking,
            "room": booking.room,
            "window_state": _qr_window_state(booking, now()),
            "policy": current_policy(),
            "moment": now(),
            "operation_key": _new_key(),
            "outcome": outcome,
        },
        status=409 if outcome is not None and not outcome.ok else 200,
    )


def _new_key() -> str:
    return str(uuid.uuid4())


def _success(**data) -> OperationOutcome:
    return OperationOutcome.success(**data)


def _grid_url(slot_start) -> str:
    return f"{reverse('core:home')}?date={clock.local_date(slot_start).isoformat()}"


def _ics_escape(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _may_reserve(room: Room, user) -> bool:
    return not room.availability_only and room.may_be_reserved_by(instruments.category_for_user(user))


def _booking_details(request) -> dict[str, str]:
    return booking_service.normalise_details(
        {
            "title": request.POST.get("title"),
            "purpose": request.POST.get("purpose"),
            "participant_names": request.POST.get("participant_names"),
        }
    )


def _booking_options(request) -> dict[str, object]:
    return {
        "repeat_weekly": request.POST.get("repeat_weekly") == "on",
        "repeat_until": (request.POST.get("repeat_until") or "").strip(),
    }


@login_required
@require_http_methods(["GET", "POST"])
def book_slot(request, room_id: int, slot: str):
    """Future reservation: confirmation page on GET, creation on POST."""
    room = get_object_or_404(Room, pk=room_id)
    slot_start = _decode_slot_or_404(slot)
    moment = now()
    policy = current_policy()

    if room.availability_only:
        messages.warning(
            request,
            "ห้องนี้แสดงสถานะเพื่อดูข้อมูลเท่านั้น ไม่เปิดให้จองหรือเข้าใช้ / "
            "This room is for availability viewing only; reservations and walk-ins are not available.",
        )
        return redirect(_grid_url(slot_start))
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
            "details": {"title": "", "purpose": "", "participant_names": ""},
            "repeat_weekly": False,
            "repeat_until": "",
            "recurring_max_weeks": settings.RECURRING_MAX_WEEKS,
        },
        status=409 if outcome is not None and not outcome.ok else 200,
    )


@login_required
@require_POST
def confirm_booking(request, room_id: int, slot: str):
    """Create the reservation, with POST revalidation and stale-hour handling."""
    room = get_object_or_404(Room, pk=room_id)
    slot_start = _decode_slot_or_404(slot)
    details = _booking_details(request)
    options = _booking_options(request)
    payload = booking_service.build_payload(room, slot_start, details, **options)

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
    # A refusal that other rooms could still satisfy names them here, computed
    # fresh from the same services the grid uses — never a cached guess.
    alternatives = []
    if outcome.code in {Code.SLOT_TAKEN, Code.CLASS_IN_SESSION, Code.ADJACENCY_CONFLICT}:
        alternatives = suggest.for_slot(slot_start, now(), user=request.user)
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
            "alternatives": alternatives,
            "details": details,
            **options,
            "recurring_max_weeks": settings.RECURRING_MAX_WEEKS,
        },
        status=409,
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_booking(request, pk: int):
    """Edit descriptive details for a future booking owned by the student."""
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk, user=request.user)
    outcome = None
    details = (
        _booking_details(request)
        if request.method == "POST"
        else {
            "title": booking.title,
            "purpose": booking.purpose,
            "participant_names": booking.participant_names,
        }
    )
    if request.method == "POST":
        payload = booking_service.build_update_payload(booking, details)
        outcome = run_view_operation(
            request=request,
            operation="update_booking_details",
            payload=payload,
            key=operation_key(request),
            body=lambda ctx: _success(
                **booking_service.update_booking_details(ctx, booking=booking, details=details)
            ),
        )
        if outcome.ok:
            add_outcome_message(
                request,
                outcome,
                success_message="รายละเอียดการจองถูกบันทึกแล้ว / Booking details updated.",
            )
            return redirect("core:my_bookings")
        booking.refresh_from_db()

    return render(
        request,
        "core/edit_booking.html",
        {
            "booking": booking,
            "details": details,
            "operation_key": _new_key(),
            "outcome": outcome,
            "moment": now(),
        },
        status=409 if outcome is not None and not outcome.ok else 200,
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
    # Rooms this student could still walk into for the rest of the hour.
    free_now = suggest.suggestions(clock.local_date(now()), now(), user=request.user).free_now
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
            "free_now": free_now,
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
    recurring = RecurringReservation.objects.filter(
        user=request.user,
        status=RecurringReservation.Status.ACTIVE,
        end_date__gte=clock.local_date(moment),
    ).select_related("room")

    return render(
        request,
        "core/my_bookings.html",
        {
            "upcoming": upcoming,
            "history": history,
            "recurring": recurring,
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
@require_GET
def booking_ics(request, pk: int):
    """Download one private booking as a standards-compatible calendar event."""
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk, user=request.user)
    if booking.status in {Booking.Status.CANCELLED, Booking.Status.REJECTED}:
        raise Http404
    start = booking.slot_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    end = booking.slot_end.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    stamp = now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    summary = f"{booking.room}"
    if booking.title:
        summary = f"{booking.title} — {summary}"
    description = "\n".join(
        value
        for value in (
            booking.purpose,
            f"Participants: {booking.participant_names}" if booking.participant_names else "",
            f"Booking reference: {booking.pk}",
        )
        if value
    )
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//RoomReserve//Practice Room Booking//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:booking-{booking.pk}@roomreserve",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        "LOCATION:Faculty of Fine and Applied Arts practice room",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    response = HttpResponse("\r\n".join(lines) + "\r\n", content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="roomreserve-{booking.pk}.ics"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def move_booking(request, pk: int):
    """Move a booking to a different room at the same hour, atomically.

    The alternatives shown on GET are advisory only. The service re-checks every
    rule under the control lock, so a room that goes while the page is open is
    refused there rather than here, and the student keeps the room they had.
    """
    booking = get_object_or_404(Booking.objects.select_related("room"), pk=pk, user=request.user)
    moment = now()

    movable = (
        booking.status in {Booking.Status.SCHEDULED, Booking.Status.PENDING_APPROVAL}
        and moment < booking.slot_start
        and booking.recurrence_id is None
    )

    def alternatives():
        if not movable:
            return []
        return suggest.for_slot(booking.slot_start, moment, user=request.user, exclude_booking_id=booking.pk)

    outcome = None
    if request.method == "POST":
        raw_room_id = request.POST.get("room_id") or ""
        room = get_object_or_404(Room, pk=int(raw_room_id) if raw_room_id.isdigit() else 0, is_active=True)
        outcome = run_view_operation(
            request=request,
            operation="move_booking",
            payload=booking_service.build_move_payload(booking, room),
            key=operation_key(request),
            body=lambda ctx: _success(**booking_service.move_booking(ctx, booking=booking, room=room)),
        )
        if outcome.ok:
            add_outcome_message(
                request,
                outcome,
                success_message="ย้ายห้องเรียบร้อยแล้ว / Your booking moved to the new room.",
            )
            return redirect("core:my_bookings")
        booking.refresh_from_db()

    return render(
        request,
        "core/move_booking.html",
        {
            "booking": booking,
            "movable": movable,
            "rooms": alternatives(),
            "operation_key": _new_key(),
            "outcome": outcome,
            "moment": moment,
        },
        status=409 if outcome is not None and not outcome.ok else 200,
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
def cancel_recurring(request, pk: int):
    recurrence = get_object_or_404(booking_service.RecurringReservation, pk=pk, user=request.user)
    outcome = run_view_operation(
        request=request,
        operation="cancel_recurring_reservation",
        payload={"recurrence_id": pk, "reason": (request.POST.get("reason") or "").strip()},
        key=operation_key(request),
        body=lambda ctx: _success(
            **booking_service.cancel_recurring_reservation(
                ctx, recurrence=recurrence, reason=(request.POST.get("reason") or "").strip()
            )
        ),
    )
    add_outcome_message(
        request,
        outcome,
        success_message="รายการจองรายสัปดาห์ถูกยกเลิกแล้ว / Weekly reservations cancelled.",
    )
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
