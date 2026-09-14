"""Shared helpers for calling operations the way a view would."""

from __future__ import annotations

from core.models import Booking, Room
from core.services import booking as booking_service
from core.services import cancel as cancel_service
from core.services import checkin as checkin_service
from core.services import clock, slots
from core.services import walkin as walkin_service
from core.services.errors import OperationOutcome
from core.services.protocol import run_operation


def _body(func):
    """Wrap a service that returns a payload dict into an OperationOutcome."""

    def inner(ctx):
        return OperationOutcome.success(**func(ctx))

    return inner


def advance_booking(actor, room: Room, slot_start, key: str | None = None):
    payload = booking_service.build_payload(room, slot_start)
    return run_operation(
        actor=actor,
        operation="advance_booking",
        payload=payload,
        key=key,
        body=_body(lambda ctx: booking_service.create_advance_booking(ctx, room=room, slot_start=slot_start)),
    )


def use_now(actor, room: Room, key: str | None = None):
    slot_start = slots.floor_to_slot(clock.now())
    payload = walkin_service.build_payload(room, slot_start)
    return run_operation(
        actor=actor,
        operation="use_now",
        payload=payload,
        key=key,
        body=_body(lambda ctx: walkin_service.use_now(ctx, room=room)),
    )


def check_in(actor, booking: Booking, key: str | None = None, room=None):
    payload = checkin_service.build_payload(booking)
    return run_operation(
        actor=actor,
        operation="check_in",
        payload=payload,
        key=key,
        body=_body(lambda ctx: checkin_service.check_in(ctx, booking=booking, room=room)),
    )


def cancel(actor, booking: Booking, reason: str = "", key: str | None = None):
    payload = cancel_service.build_payload(booking, reason)
    return run_operation(
        actor=actor,
        operation="cancel_booking",
        payload=payload,
        key=key,
        body=_body(lambda ctx: cancel_service.cancel(ctx, booking=booking, reason=reason)),
    )
