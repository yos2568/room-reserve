"""Outcome and error vocabulary shared by every operational service.

A *rejection* is a normal, expected result: it is returned, never raised out of
the outer transaction, so that the reconciliation work which made the rejection
correct still commits (V3 section 5). Only genuinely unexpected failures raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.utils.translation import gettext_lazy as _


class Code:
    """Machine-readable result codes. Messages are resolved at render time."""

    OK = "ok"
    BUSY = "busy"

    # Slot and calendar
    SLOT_TAKEN = "slot_taken"
    ADJACENCY_CONFLICT = "adjacency_conflict"
    QUOTA_EXCEEDED = "quota_exceeded"
    OUTSIDE_HORIZON = "outside_horizon"
    SLOT_IN_PAST = "slot_in_past"
    SLOT_ELAPSED = "slot_elapsed"
    CLOSED = "closed"
    CLASS_IN_SESSION = "class_in_session"
    ROOM_NOT_FOR_INSTRUMENT = "room_not_for_instrument"
    INVALID_SLOT = "invalid_slot"
    STALE_HOUR = "stale_hour"
    NO_SHOW_RECLAIM = "no_show_reclaim"

    # Account state
    NOT_ELIGIBLE = "not_eligible"
    SUSPENDED = "suspended"
    PENDING_APPROVAL = "pending_approval"
    EMAIL_UNVERIFIED = "email_unverified"
    INACTIVE_ACCOUNT = "inactive_account"
    TEACHER_READ_ONLY = "teacher_read_only"

    # Lifecycle
    NOT_OWNER = "not_owner"
    WRONG_ROOM = "wrong_room"
    CHECK_IN_NOT_OPEN = "check_in_not_open"
    CHECK_IN_CLOSED = "check_in_closed"
    ALREADY_CHECKED_IN = "already_checked_in"
    TERMINAL_STATUS = "terminal_status"
    CANCEL_AFTER_START = "cancel_after_start"
    RESERVATION_HELD = "reservation_held"

    # Identity
    DUPLICATE_ACCOUNT = "duplicate_account"
    INVALID_TOKEN = "invalid_token"
    TOKEN_EXPIRED = "token_expired"
    TOKEN_USED = "token_used"
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"
    ROSTER_MISMATCH = "roster_mismatch"

    # Protocol
    IDEMPOTENCY_PAYLOAD_MISMATCH = "idempotency_payload_mismatch"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"


_MESSAGES = {
    Code.OK: _("Done."),
    Code.BUSY: _("The system is busy. Please try again in a moment."),
    Code.SLOT_TAKEN: _("That room and hour has just been taken."),
    Code.ADJACENCY_CONFLICT: _("You already have a booking in this hour or an adjacent hour."),
    Code.QUOTA_EXCEEDED: _("You have reached your limit of bookings for that day."),
    Code.OUTSIDE_HORIZON: _("That date is further ahead than booking allows."),
    Code.SLOT_IN_PAST: _("That time has already started. Use “Use now” if the room is free."),
    Code.SLOT_ELAPSED: _("That hour has already finished."),
    Code.CLOSED: _("That room is closed for the selected time."),
    Code.CLASS_IN_SESSION: _("A class meets in that room at that hour. Choose another hour or another room."),
    Code.ROOM_NOT_FOR_INSTRUMENT: _(
        "That room can only be reserved by piano and percussion students. Once the "
        "hour starts, anyone may use it if it is still free."
    ),
    Code.INVALID_SLOT: _("That is not a bookable hour."),
    Code.STALE_HOUR: _(
        "The hour changed while this page was open. Check the current slot and confirm again."
    ),
    Code.NO_SHOW_RECLAIM: _(
        "You cannot use this room for the hour you missed. Another room is still possible."
    ),
    Code.NOT_ELIGIBLE: _("Your account is not eligible to book practice rooms."),
    Code.SUSPENDED: _("Booking is suspended on this account."),
    Code.PENDING_APPROVAL: _("Your account is still waiting for staff approval."),
    Code.EMAIL_UNVERIFIED: _("Verify your email address before booking."),
    Code.INACTIVE_ACCOUNT: _("This account is inactive. Please contact the department."),
    Code.TEACHER_READ_ONLY: _(
        "Teacher accounts are read-only: they can view schedules but cannot reserve rooms."
    ),
    Code.NOT_OWNER: _("That booking belongs to another account."),
    Code.WRONG_ROOM: _("That booking is for a different room."),
    Code.CHECK_IN_NOT_OPEN: _("Check-in opens at the start of the hour."),
    Code.CHECK_IN_CLOSED: _("The check-in window has closed for this booking."),
    Code.ALREADY_CHECKED_IN: _("This booking is already checked in."),
    Code.TERMINAL_STATUS: _("That booking has already finished and cannot be changed."),
    Code.CANCEL_AFTER_START: _("A booking cannot be cancelled after it has started."),
    Code.RESERVATION_HELD: _("Another student still holds this room for the current hour."),
    Code.DUPLICATE_ACCOUNT: _("Those details are already registered. Please sign in instead."),
    Code.INVALID_TOKEN: _("That link is not valid."),
    Code.TOKEN_EXPIRED: _("That link has expired. Request a new one."),
    Code.TOKEN_USED: _("That link has already been used. Request a new one."),
    Code.DOMAIN_NOT_ALLOWED: _("Use your institutional email address."),
    Code.ROSTER_MISMATCH: _("Your details were not found on the department roster."),
    Code.IDEMPOTENCY_PAYLOAD_MISMATCH: _(
        "This request conflicts with an earlier submission. Reload the page and try again."
    ),
    Code.INVALID_INPUT: _("Please check the form and try again."),
    Code.NOT_FOUND: _("That item could not be found."),
    Code.RATE_LIMITED: _("Too many attempts. Please wait a moment and try again."),
}

# Rejections that mean "the request was understood but refused".
_REJECTION_STATUS = {
    Code.BUSY: 503,
    Code.RATE_LIMITED: 429,
    Code.IDEMPOTENCY_PAYLOAD_MISMATCH: 409,
    Code.SLOT_TAKEN: 409,
    Code.ADJACENCY_CONFLICT: 409,
    Code.QUOTA_EXCEEDED: 409,
    Code.ROOM_NOT_FOR_INSTRUMENT: 409,
    Code.NOT_FOUND: 404,
    Code.INVALID_INPUT: 400,
}


def message_for(code: str) -> str:
    return _MESSAGES.get(code, _("That action could not be completed."))


def status_for(code: str, default: int = 200) -> int:
    return _REJECTION_STATUS.get(code, default)


@dataclass
class OperationOutcome:
    """The result of an operational service call.

    ``data`` must stay JSON-serialisable: it is persisted on the idempotency row
    and replayed verbatim when the same key is retried, possibly long after the
    booking's status has changed.
    """

    ok: bool
    code: str
    data: dict[str, Any] = field(default_factory=dict)
    replayed: bool = False

    @classmethod
    def success(cls, code: str = Code.OK, **data) -> OperationOutcome:
        return cls(ok=True, code=code, data=data)

    @classmethod
    def reject(cls, code: str, **data) -> OperationOutcome:
        return cls(ok=False, code=code, data=data)

    @classmethod
    def replay(cls, data: dict[str, Any]) -> OperationOutcome:
        """Return a previously recorded successful result without redoing effects."""
        return cls(ok=True, code=data.get("code", Code.OK), data=data.get("data", {}), replayed=True)

    def as_record(self) -> dict[str, Any]:
        return {"code": self.code, "data": self.data}

    @property
    def http_status(self) -> int:
        return status_for(self.code)

    @property
    def message(self) -> str:
        return message_for(self.code)


class OperationRejected(Exception):
    """Internal control-flow signal. Caught inside the transaction, never escaping."""

    def __init__(self, code: str, **data):
        super().__init__(code)
        self.outcome = OperationOutcome.reject(code, **data)


class LockUnavailable(Exception):
    """The shared control row could not be locked within the bounded wait."""


class ReconciliationBacklog(Exception):
    """Too much due work to reconcile inside the request budget; fail closed."""
