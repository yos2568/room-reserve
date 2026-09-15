"""Account eligibility, suspension and room-audience checks.

Used by every mutation so that a student can still sign in, read history and ask
for help while blocked, but cannot reserve, check in or walk in (V3 section 7).
"""

from __future__ import annotations

from core.models import Suspension, User

from . import instruments
from .errors import Code, OperationRejected


def active_suspension(user, now) -> Suspension | None:
    """The suspension in force for ``user`` at ``now``, if any.

    Expiry restores eligibility by timestamp comparison alone, with no cron.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    return Suspension.objects.active(now).filter(user_id=user.pk).first()


def is_suspended(user, now) -> bool:
    return active_suspension(user, now) is not None


def account_block_code(user) -> str | None:
    """The reason a signed-in account may not book, or None when it may."""
    if user is None or not getattr(user, "pk", None):
        return Code.NOT_ELIGIBLE
    if not user.is_active:
        return Code.INACTIVE_ACCOUNT
    if not user.email_is_verified:
        return Code.EMAIL_UNVERIFIED
    if user.eligibility == User.Eligibility.PENDING:
        return Code.PENDING_APPROVAL
    if user.eligibility != User.Eligibility.APPROVED:
        return Code.NOT_ELIGIBLE
    return None


def assert_can_operate(user, now) -> None:
    """Raise OperationRejected when the account may not create or use bookings."""
    block = account_block_code(user)
    if block:
        raise OperationRejected(block)
    if is_suspended(user, now):
        raise OperationRejected(Code.SUSPENDED)


def assert_may_use_room(user, room, now) -> None:
    """Raise unless this account may start or hold a booking in this room.

    Deliberately says nothing about the room's reservation audience: that applies
    to *reserving*, which is ``assert_may_reserve_room``. A walk-in is open to every
    eligible student once the hour has started and nobody holds the room, which is
    what keeps a restricted room from standing empty.
    """
    assert_can_operate(user, now)
    if not room.is_active:
        raise OperationRejected(Code.CLOSED)


def assert_may_reserve_room(user, room, now) -> None:
    """Raise unless this account may reserve this room in advance.

    The audience check lives here, alone, so reservation cannot disagree with
    itself about who may book a room. Not called by walk-in (open to everyone) and
    not called by check-in (a booking already made stays valid), which is why it is
    a separate function rather than a flag on the one above.
    """
    assert_may_use_room(user, room, now)

    if not room.may_be_reserved_by(instruments.category_for_user(user)):
        raise OperationRejected(
            Code.ROOM_NOT_FOR_INSTRUMENT,
            room_id=room.pk,
            allowed_categories=[row.category for row in room.allowed_categories.all()],
        )
