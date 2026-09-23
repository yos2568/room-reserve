"""Room 301 is used only once a teacher or admin approves the request (D-39).

The maintainer names who approves 301 by making them its room administrator.
A teacher account stays read-only for its own bookings, but as 301's room
administrator it can approve or reject requests for that room and no other.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from core.models import Booking, Room, RoomAdministrator
from core.services import rooms as rooms_service
from core.services import slots
from tests import factories, helpers

pytestmark = pytest.mark.django_db

NEXT_TUESDAY = date(2026, 9, 15)


@pytest.fixture
def room_301(db):
    rooms_service.ensure_rooms()
    return Room.objects.get(number="301")


@pytest.fixture
def teacher(db):
    return factories.make_user(is_teacher=True)


def _request_301(student, room):
    outcome = helpers.advance_booking(student, room, slots.slot_start_for(NEXT_TUESDAY, 11))
    assert outcome.ok
    return Booking.objects.get(user=student, room=room)


def _decide(client, booking, decision):
    return client.post(
        reverse("core:staff_decide_booking_approval", args=[booking.pk]),
        {"decision": decision, "note": ""},
    )


def test_a_teacher_named_for_301_approves_a_request(frozen, student, room_301, teacher):
    RoomAdministrator.objects.create(room=room_301, user=teacher)
    booking = _request_301(student, room_301)
    client = Client()
    client.force_login(teacher)

    _decide(client, booking, "APPROVED")

    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED
    assert booking.approved_by == teacher


def test_the_named_teacher_sees_301_requests_on_the_room_admin_page(frozen, student, room_301, teacher):
    RoomAdministrator.objects.create(room=room_301, user=teacher)
    booking = _request_301(student, room_301)
    client = Client()
    client.force_login(teacher)

    body = client.get(reverse("core:room_admin")).content.decode()

    assert reverse("core:staff_decide_booking_approval", args=[booking.pk]) in body


def test_a_teacher_not_named_for_301_cannot_approve(frozen, student, room_301, teacher):
    booking = _request_301(student, room_301)
    client = Client()
    client.force_login(teacher)

    response = _decide(client, booking, "APPROVED")

    assert response.status_code == 403
    booking.refresh_from_db()
    assert booking.status == Booking.Status.PENDING_APPROVAL


def test_the_maintainer_can_approve_301(frozen, student, room_301):
    admin = factories.make_user(is_superuser=True, is_staff=True)
    booking = _request_301(student, room_301)
    client = Client()
    client.force_login(admin)

    _decide(client, booking, "APPROVED")

    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED


def test_301_cannot_be_walked_into(frozen, student, room_301):
    outcome = helpers.use_now(student, room_301)

    assert not outcome.ok
