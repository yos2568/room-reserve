"""Room provisioning, room audience and teaching-timetable configuration.

One place decides what a configured room looks like, so the seed commands that
create the rooms and the staff screen that changes them cannot disagree.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from core.models import Room, RoomAllowedCategory, WeeklyBlock


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
            # A room back in the configured set returns to the grid; retiring
            # hides a room, it does not bury it.
            room.is_active = True
            room.save(update_fields=["is_active"])

        # Applied on every run, not only at creation, so a configuration change in
        # settings reaches an existing database without a migration.
        if config:
            if config.get("label") and room.label != config["label"]:
                room.label = config["label"]
                room.save(update_fields=["label"])
            set_audience(
                room=room,
                scope=config.get("reservation_scope", Room.ReservationScope.EVERYONE),
                categories=config.get("categories", ()),
            )

    for number, entries in weekly_blocks.items():
        room = Room.objects.filter(number=number).first()
        if room is not None:
            set_weekly_blocks(room=room, entries=entries)

    retired = Room.objects.filter(is_active=True).exclude(number__in=numbers).update(is_active=False)

    return {"created": created, "total": Room.objects.count(), "retired": retired}
