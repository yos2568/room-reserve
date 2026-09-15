"""Room provisioning and room audience configuration.

One place decides what a room's reservation audience is, so the seed commands that
create the rooms and the staff screen that changes them cannot disagree about what
a configured room looks like.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from core.models import Room, RoomAllowedCategory


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


@transaction.atomic
def ensure_rooms(*, count: int | None = None, overrides: dict | None = None) -> dict:
    """Create or refresh the configured rooms. Idempotent.

    ``overrides`` maps a room number to its configuration; anything not overridden
    is a general room that any eligible student may reserve. A room beyond the
    configured count is left alone rather than deleted, because bookings may
    reference it.
    """
    count = settings.ROOM_COUNT if count is None else count
    overrides = settings.ROOM_OVERRIDES if overrides is None else overrides

    created = 0
    for number in range(1, count + 1):
        config = overrides.get(str(number), {})
        room, was_created = Room.objects.get_or_create(
            number=str(number),
            defaults={
                "label": config.get("label", f"ห้องซ้อม {number}"),
                "position": number,
            },
        )
        created += int(was_created)

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

    return {"created": created, "total": Room.objects.count()}
