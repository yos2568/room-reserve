"""Members-only rooms: who is on a room's access list (D-42).

A members-only room may be reserved only by the people its access list names,
matched by the email address on their account, and by staff. Nobody walks in.
The list is kept by staff on the room's access screen, never in code or
settings, so names and addresses stay out of the repository.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from core.models import Room, RoomMember

from .errors import Code, OperationRejected

# A cell copied from a spreadsheet: tab-separated, or comma/semicolon in a CSV.
_CELL_SPLIT = re.compile(r"[\t,;]")


def is_booking_staff(user) -> bool:
    """Operational staff and the maintainer: may approve, and book outside student limits."""
    return bool(
        getattr(user, "is_active", False)
        and (getattr(user, "is_superuser", False) or getattr(user, "is_operational_staff", False))
    )


def member_room_ids(user) -> frozenset:
    """The members-only rooms this viewer may reserve. Staff may reserve them all."""
    if user is None or not getattr(user, "pk", None):
        return frozenset()
    if is_booking_staff(user):
        return frozenset(Room.objects.filter(members_only=True).values_list("pk", flat=True))
    email = (getattr(user, "email", "") or "").strip().lower()
    if not email:
        return frozenset()
    return frozenset(RoomMember.objects.filter(email=email).values_list("room_id", flat=True))


def _is_email(value: str) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


def parse_pasted_rows(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Read ``(name, email)`` pairs from rows pasted out of a spreadsheet.

    Each line needs one email address; the name is the first other cell that is
    not just a number (the sequence column). A line without any email address —
    a title or a header row — is skipped and reported back, never guessed at.
    """
    entries: dict[str, str] = {}
    skipped: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = [cell.strip() for cell in _CELL_SPLIT.split(line) if cell.strip()]
        emails = [cell for cell in cells if _is_email(cell)]
        if len(emails) != 1:
            skipped.append(line)
            continue
        email = emails[0].lower()
        name = next(
            (" ".join(cell.split()) for cell in cells if cell != emails[0] and not cell.isdigit()),
            "",
        )
        entries.setdefault(email, name[:200])
    return [(name, email) for email, name in entries.items()], skipped


def add_members(ctx, *, room: Room, entries: list[tuple[str, str]]) -> dict:
    """Add people to a members-only room's list. Someone already listed is left as is."""
    if not room.members_only:
        raise OperationRejected(Code.INVALID_INPUT)
    if not entries:
        raise OperationRejected(Code.INVALID_INPUT)
    added = []
    for name, email in entries:
        _, created = RoomMember.objects.get_or_create(
            room=room, email=email.strip().lower(), defaults={"name": name, "added_by": ctx.actor}
        )
        if created:
            added.append(email)
    ctx.audit(
        action="room.members_added",
        entity_type="Room",
        entity_id=room.pk,
        actor=ctx.actor,
        changes={"room": room.number, "added": added, "already_listed": len(entries) - len(added)},
    )
    return {"room_id": room.pk, "added": len(added), "already_listed": len(entries) - len(added)}


def remove_member(ctx, *, room: Room, member_id: int) -> dict:
    member = RoomMember.objects.filter(room=room, pk=member_id).first()
    if member is None:
        raise OperationRejected(Code.NOT_FOUND)
    email = member.email
    member.delete()
    ctx.audit(
        action="room.member_removed",
        entity_type="Room",
        entity_id=room.pk,
        actor=ctx.actor,
        changes={"room": room.number, "removed": email},
    )
    return {"room_id": room.pk, "removed": email}
