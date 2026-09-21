"""Regressions for the defects found reviewing handoff21sep.md.

Each test pins one fix: approval rooms refuse walk-ins, view-only and approval
rooms are never suggested as walk-ins, seed_rooms keeps a staff room-profile
edit, and a room move email names both rooms. (The weekly-repeat cap test was
retired with weekly repeat itself, D-38.)
"""

from __future__ import annotations

import pytest

from core.models import Notification, Room
from core.services import availability, clock, notifications, slots, suggest
from core.services import booking as booking_service
from core.services import rooms as rooms_service
from core.services.errors import Code, OperationOutcome
from core.services.protocol import run_operation
from tests import factories, helpers

pytestmark = pytest.mark.django_db

# The frozen moment is Monday 2026-09-14 10:40, so hour 10 is in progress.
DAY = factories.bangkok(2026, 9, 14).date()


def _current_cell(room, user):
    return availability.current_slot_cell(room, clock.now(), user=user)


# --- Approval rooms: no walk-in path around staff -----------------------------------


def test_an_approval_room_refuses_a_walk_in(frozen, student, rooms):
    room = rooms[0]
    room.requires_approval = True
    room.save(update_fields=["requires_approval"])

    outcome = helpers.use_now(student, room)

    assert not outcome.ok
    assert outcome.code == Code.APPROVAL_ROOM_NO_WALK_IN


def test_an_approval_room_offers_no_use_now_button(frozen, student, rooms):
    room = rooms[0]
    room.requires_approval = True
    room.save(update_fields=["requires_approval"])

    cell = _current_cell(room, student)

    assert cell.state == availability.SlotState.FREE_NOW  # it is free; just not walkable
    assert not cell.can_use_now
    assert _current_cell(rooms[1], student).can_use_now


# --- Suggestions never offer a room the student cannot use --------------------------


def test_free_now_skips_view_only_and_approval_rooms(frozen, student, rooms):
    view_only, approval, ordinary = rooms[0], rooms[1], rooms[2]
    view_only.availability_only = True
    view_only.save(update_fields=["availability_only"])
    approval.requires_approval = True
    approval.save(update_fields=["requires_approval"])

    offered = {card.room.pk for card in suggest._free_now(DAY, clock.now(), user=student)}

    assert view_only.pk not in offered
    assert approval.pk not in offered
    assert ordinary.pk in offered


def test_refusal_alternatives_skip_a_view_only_room(frozen, student, rooms):
    view_only = rooms[0]
    view_only.availability_only = True
    view_only.save(update_fields=["availability_only"])

    alternatives = suggest.for_slot(slots.slot_start_for(DAY, 15), clock.now(), user=student)

    assert view_only not in alternatives
    assert alternatives, "the other rooms are still offered"


# --- seed_rooms keeps what staff set on the room-admin screen -----------------------


def test_seed_rooms_keeps_a_staff_room_profile_edit(frozen, db, staff_user):
    rooms_service.ensure_rooms()
    room = Room.objects.get(number="303")
    assert room.requires_approval, "settings mark 303 as an approval room"

    outcome = run_operation(
        actor=staff_user,
        operation="room_profile_update",
        payload={"room": room.pk, "requires_approval": False},
        body=lambda ctx: OperationOutcome.success(
            room_id=rooms_service.update_profile(
                ctx, room=room, capacity=6, equipment="Upright piano", requires_approval=False
            ).pk
        ),
    )
    assert outcome.ok, outcome.code

    rooms_service.ensure_rooms()

    room.refresh_from_db()
    assert room.requires_approval is False
    assert room.capacity == 6
    assert room.equipment == "Upright piano"


# --- A room move says the room moved ------------------------------------------------


def test_a_room_move_email_names_both_rooms(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=DAY, hour=15)

    outcome = run_operation(
        actor=student,
        operation="move_booking",
        payload=booking_service.build_move_payload(booking, rooms[1]),
        body=lambda ctx: OperationOutcome.success(
            **booking_service.move_booking(ctx, booking=booking, room=rooms[1])
        ),
    )
    assert outcome.ok, outcome.code

    notification = Notification.objects.get(kind="booking_changed", recipient=student)
    context = notifications.revalidate(notification, clock.now())
    _subject, body = notifications.render(notification, context)

    assert str(rooms[0]) in body
    assert str(rooms[1]) in body
