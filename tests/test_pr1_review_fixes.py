"""Regressions for the ultrareview of PR #1.

Each test pins one fix: the maintainer-only room-admin grant obeys the staff
network allowlist, a details edit on a pending booking still emails the
student, approving a moved booking still sends its confirmation, the weekly
repeat's anchor obeys the booking horizon, and a malformed room id in a move is
a 404 rather than a 500.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from core.models import Booking, Notification, RoomAdministrator, User
from core.services import booking as booking_service
from core.services import clock, notifications, slots
from core.services.errors import Code, OperationOutcome
from core.services.protocol import run_operation
from tests import factories

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()


def _advance(student, room, slot_start, **options):
    payload = booking_service.build_payload(room, slot_start, {"title": "Practice"}, **options)
    return run_operation(
        actor=student,
        operation="advance_booking",
        payload=payload,
        body=lambda ctx: OperationOutcome.success(
            **booking_service.create_advance_booking(ctx, room=room, slot_start=slot_start)
        ),
    )


def _move(student, booking, room):
    return run_operation(
        actor=student,
        operation="move_booking",
        payload=booking_service.build_move_payload(booking, room),
        body=lambda ctx: OperationOutcome.success(
            **booking_service.move_booking(ctx, booking=booking, room=room)
        ),
    )


# --- 1. The room-admin grant sits behind the staff network boundary -----------------


def test_granting_room_admin_is_refused_off_the_staff_network(frozen, rooms, student):
    maintainer = User.objects.create_superuser(
        username="66002000009", email="maintainer9@student.chula.ac.th", password="Pw-for-tests-1!"
    )
    client = Client()
    client.force_login(maintainer)

    with override_settings(STAFF_ALLOWED_IPS=["203.0.113.10/32"]):
        response = client.post(
            reverse("core:staff_set_room_administrator", args=[rooms[0].pk]),
            {"user_id": student.pk, "action": "add", "operation_key": "grant-1"},
            REMOTE_ADDR="198.51.100.20",
        )

    assert response.status_code == 403
    assert not RoomAdministrator.objects.filter(room=rooms[0], user=student).exists()


# --- 2. Editing a pending booking's details still reaches the student ---------------


def test_a_details_edit_on_a_pending_booking_is_not_dropped_as_stale(frozen, student, rooms):
    room = rooms[0]
    room.requires_approval = True
    room.save(update_fields=["requires_approval"])
    created = _advance(student, room, slots.slot_start_for(DAY, 15))
    booking = Booking.objects.get(pk=created.data["booking_id"])
    assert booking.status == Booking.Status.PENDING_APPROVAL

    details = {"title": "New title", "purpose": "", "participant_names": ""}
    edited = run_operation(
        actor=student,
        operation="update_booking_details",
        payload=booking_service.build_update_payload(booking, details),
        body=lambda ctx: OperationOutcome.success(
            **booking_service.update_booking_details(ctx, booking=booking, details=details)
        ),
    )
    assert edited.ok, edited.code

    changed = Notification.objects.get(kind="booking_changed", recipient=student)
    context = notifications.revalidate(changed, clock.now())  # raised MessageStale before

    assert context["title"] == "New title"


# --- 3. Approval after a move into an approval room still confirms ------------------


def test_approving_a_moved_booking_sends_a_new_confirmation(frozen, student, staff_user, rooms):
    ordinary, approval_room = rooms[0], rooms[1]
    approval_room.requires_approval = True
    approval_room.save(update_fields=["requires_approval"])

    created = _advance(student, ordinary, slots.slot_start_for(DAY, 15))
    booking = Booking.objects.get(pk=created.data["booking_id"])
    assert Notification.objects.filter(kind="booking_confirmation", recipient=student).count() == 1

    moved = _move(student, booking, approval_room)
    assert moved.ok, moved.code
    booking.refresh_from_db()
    assert booking.status == Booking.Status.PENDING_APPROVAL

    RoomAdministrator.objects.create(room=approval_room, user=staff_user, granted_by=staff_user)
    client = Client()
    client.force_login(staff_user)
    response = client.post(
        reverse("core:staff_decide_booking_approval", args=[booking.pk]),
        {"decision": "APPROVED", "note": "ok", "operation_key": "approve-moved-1"},
    )
    assert response.status_code == 302

    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED
    # The creation-time confirmation plus the approval one: the second used to be
    # swallowed by the first's dedupe key.
    assert Notification.objects.filter(kind="booking_confirmation", recipient=student).count() == 2


# --- 4. A weekly series may not start beyond the horizon ----------------------------


def test_a_weekly_series_anchored_beyond_the_horizon_is_refused(frozen, student, rooms):
    far = DAY + timedelta(days=60)
    outcome = _advance(
        student,
        rooms[0],
        slots.slot_start_for(far, 15),
        repeat_weekly=True,
        repeat_until=(far + timedelta(days=7)).isoformat(),
    )

    assert not outcome.ok
    assert outcome.code == Code.OUTSIDE_HORIZON
    assert not Booking.objects.filter(user=student).exists()


def test_a_weekly_series_inside_the_horizon_still_repeats_past_it(frozen, student, rooms):
    with override_settings(RECURRING_MAX_WEEKS=3):
        outcome = _advance(
            student,
            rooms[0],
            slots.slot_start_for(DAY + timedelta(days=1), 15),
            repeat_weekly=True,
            repeat_until=(DAY + timedelta(days=15)).isoformat(),
        )

    assert outcome.ok, outcome.code
    assert Booking.objects.filter(user=student).count() == 3


# --- 5. A malformed room id is a 404 ------------------------------------------------


def test_a_move_with_a_non_numeric_room_id_is_a_404(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=DAY, hour=15)
    client = Client()
    client.force_login(student)

    response = client.post(
        reverse("core:move_booking", args=[booking.pk]),
        {"room_id": "abc", "operation_key": "move-bad-1"},
    )

    assert response.status_code == 404
