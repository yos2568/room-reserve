"""Room provisioning, room audience and teaching-timetable configuration.

One place decides what a configured room looks like, so the seed commands that
create the rooms and the staff screen that changes them cannot disagree.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from core.models import AuditEvent, Room, RoomAdministrator, RoomAllowedCategory, User, WeeklyBlock

from .audit import record_audit


def set_audience(*, room: Room, scope: str, categories) -> dict:
    """Set who may reserve a room, replacing any previous list.

    Returns a change description for the audit trail. An empty ``categories`` with
    a ``LISTED`` scope is allowed and means *nobody* may reserve it — that is the
    fail-closed default, not an error.
    """
    before_scope = room.reservation_scope
    before = sorted(row.category for row in room.allowed_categories.all())

    room.reservation_scope = scope
    room.save(update_fields=["reservation_scope"])

    wanted = sorted(set(categories))
    RoomAllowedCategory.objects.filter(room=room).exclude(category__in=wanted).delete()
    for category in wanted:
        RoomAllowedCategory.objects.get_or_create(room=room, category=category)

    return {
        "scope_before": before_scope,
        "scope_after": scope,
        "categories_before": before,
        "categories_after": wanted,
    }


def update_profile(
    ctx, *, room: Room, capacity: int, equipment: str, requires_approval: bool | None = None
) -> Room:
    """Update public room-profile metadata with an auditable staff action."""
    from .errors import Code, OperationRejected
    from .refs import reload_for_update

    if not isinstance(capacity, int) or not 1 <= capacity <= 100:
        raise OperationRejected(Code.INVALID_INPUT)
    equipment = "\n".join(line.strip() for line in (equipment or "").splitlines() if line.strip())
    if len(equipment) > 1000:
        raise OperationRejected(Code.INVALID_INPUT)

    room = reload_for_update(room)
    before = {
        "capacity": room.capacity,
        "equipment": room.equipment,
        "requires_approval": room.requires_approval,
    }
    room.capacity = capacity
    room.equipment = equipment
    if requires_approval is not None:
        room.requires_approval = bool(requires_approval)
    room.save(update_fields=["capacity", "equipment", "requires_approval"])
    ctx.audit(
        action="room.profile_updated",
        entity_type="Room",
        entity_id=room.pk,
        actor=ctx.actor,
        changes={
            "before": before,
            "after": {
                "capacity": capacity,
                "equipment": equipment,
                "requires_approval": room.requires_approval,
            },
        },
    )
    return room


def can_manage_room(user, room: Room) -> bool:
    """Global staff may manage every room; room admins only their assignment."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser or user.is_operational_staff:
        return True
    return RoomAdministrator.objects.filter(user=user, room=room, room__is_active=True).exists()


def set_administrator(ctx, *, room: Room, user: User, add: bool):
    """Grant or revoke a room-scoped administrator assignment."""
    from .errors import Code, OperationRejected

    if not user.is_active:
        raise OperationRejected(Code.INVALID_INPUT)
    assignment = RoomAdministrator.objects.filter(room=room, user=user).first()
    if add:
        if assignment is None:
            assignment = RoomAdministrator.objects.create(room=room, user=user, granted_by=ctx.actor)
            changed = "added"
        else:
            changed = "unchanged"
    else:
        if assignment is None:
            changed = "unchanged"
        else:
            assignment.delete()
            changed = "removed"
    ctx.audit(
        action=f"room.administrator_{changed}",
        entity_type="Room",
        entity_id=room.pk,
        actor=ctx.actor,
        changes={"user_id": user.pk, "user": user.institutional_id, "room": room.number},
    )
    return {"room_id": room.pk, "user_id": user.pk, "changed": changed}


def _held_out_by_staff(room: Room) -> bool:
    """Whether staff took this room out of service and nobody has put it back.

    Configuration may retire a room and bring it back, but a room staff
    deactivated (a broken piano, a leak) stays out until a person decides
    otherwise: a routine ``seed_rooms`` run must not undo that decision.
    """
    latest = (
        AuditEvent.objects.filter(
            entity_type="Room",
            entity_id=str(room.pk),
            action__in=("room.deactivated", "room.reactivated"),
        )
        .order_by("-occurred_at", "-pk")
        .values_list("action", flat=True)
        .first()
    )
    return latest == "room.deactivated"


# Fields the room-admin screen edits (update_profile). Settings seed them, but
# once staff have changed a room's profile, their values win over a re-seed.
STAFF_PROFILE_FIELDS = ("capacity", "equipment", "requires_approval")


def _profile_managed_by_staff(room: Room) -> bool:
    return AuditEvent.objects.filter(
        entity_type="Room", entity_id=str(room.pk), action="room.profile_updated"
    ).exists()


def set_weekly_blocks(*, room: Room, entries) -> None:
    """Replace a room's recurring blocks with the configured entries.

    Settings are the source of truth for the teaching timetable, so a change
    there reaches an existing database on the next seed run. Entries are trusted
    configuration, exactly like ``ROOM_OVERRIDES``.
    """
    room.weekly_blocks.all().delete()
    WeeklyBlock.objects.bulk_create(WeeklyBlock(room=room, **entry) for entry in entries)


@transaction.atomic
def ensure_rooms(
    *, count: int | None = None, overrides: dict | None = None, weekly_blocks: dict | None = None
) -> dict:
    """Create or refresh the configured rooms. Idempotent.

    The rooms are the numbered stalls ``1..count`` plus any room named in
    ``overrides`` — the teaching rooms 303 and 304 carry building numbers rather
    than stall numbers. Anything not overridden is a general room that any
    eligible student may reserve. A room outside the configured set is
    deactivated rather than deleted, because bookings may reference it; history
    stays intact and the room leaves the grid.
    """
    count = settings.ROOM_COUNT if count is None else count
    overrides = settings.ROOM_OVERRIDES if overrides is None else overrides
    weekly_blocks = settings.ROOM_WEEKLY_BLOCKS if weekly_blocks is None else weekly_blocks

    numbers = [str(number) for number in range(1, count + 1)]
    numbers += [key for key in overrides if key not in numbers]

    created = 0
    held_out = []
    for index, number in enumerate(numbers, start=1):
        config = overrides.get(number, {})
        room, was_created = Room.objects.get_or_create(
            number=number,
            defaults={
                "label": config.get("label", f"ห้องซ้อม {number}"),
                "position": config.get("position", index),
            },
        )
        created += int(was_created)

        if not room.is_active:
            if _held_out_by_staff(room):
                held_out.append(number)
            else:
                # A room back in the configured set returns to the grid; retiring
                # hides a room, it does not bury it.
                room.is_active = True
                room.save(update_fields=["is_active"])
                record_audit(
                    action="room.reactivated",
                    entity_type="Room",
                    entity_id=room.pk,
                    actor_label="seed_rooms",
                    changes={"room": room.number},
                    reason="Room is in the configured set again.",
                )

        # Applied on every run, not only at creation, so a configuration change in
        # settings reaches an existing database without a migration.
        if config:
            profile_updates = {}
            staff_managed = _profile_managed_by_staff(room)
            for field in ("label", "capacity", "equipment", "requires_approval", "availability_only"):
                if staff_managed and field in STAFF_PROFILE_FIELDS:
                    continue
                if field in config and getattr(room, field) != config[field]:
                    setattr(room, field, config[field])
                    profile_updates[field] = config[field]
            if profile_updates:
                room.save(update_fields=list(profile_updates))
            set_audience(
                room=room,
                scope=config.get("reservation_scope", Room.ReservationScope.EVERYONE),
                categories=config.get("categories", ()),
            )

    for number, entries in weekly_blocks.items():
        room = Room.objects.filter(number=number).first()
        if room is not None:
            set_weekly_blocks(room=room, entries=entries)
    # Settings are the whole timetable: a room dropped from ROOM_WEEKLY_BLOCKS
    # (a semester with no classes there) loses its old blocks too.
    blocks_cleared, _ = WeeklyBlock.objects.exclude(room__number__in=list(weekly_blocks)).delete()

    retired = Room.objects.filter(is_active=True).exclude(number__in=numbers).update(is_active=False)

    return {
        "created": created,
        "total": Room.objects.count(),
        "retired": retired,
        "held_out": held_out,
        "blocks_cleared": blocks_cleared,
    }
