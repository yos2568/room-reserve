"""Check-in happens only at the room's door QR (D-37).

The emailed QR and the My bookings button checked students in from anywhere, so
they are gone. What remains: the printed door poster opens the room page, and
its button checks in the signed-in student's booking for that room. A photo of
the poster still works remotely; staff spot checks are the backstop, and staff
can check a student in by hand when needed.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import NoReverseMatch, reverse

from core.models import Booking, Notification
from core.services import clock, notifications
from tests import factories

pytestmark = pytest.mark.django_db

TOMORROW = factories.bangkok(2026, 9, 15).date()
IN_WINDOW = factories.bangkok(2026, 9, 15, 13, 5)
AFTER_DEADLINE = factories.bangkok(2026, 9, 15, 13, 20)


def _door_check_in(client, room, booking, moment):
    with clock.frozen_clock(moment):
        return client.post(reverse("core:room_check_in", args=[room.pk, booking.pk]))


# --- The door is the way in ---------------------------------------------------------


def test_the_door_checks_in_the_owner_within_the_window(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client = Client()
    client.force_login(student)

    response = _door_check_in(client, rooms[0], booking, IN_WINDOW)

    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == Booking.Status.IN_USE


def test_the_wrong_rooms_door_does_not_check_in(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client = Client()
    client.force_login(student)

    _door_check_in(client, rooms[1], booking, IN_WINDOW)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED


def test_after_the_deadline_the_door_refuses(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client = Client()
    client.force_login(student)

    _door_check_in(client, rooms[0], booking, AFTER_DEADLINE)

    booking.refresh_from_db()
    assert booking.status != Booking.Status.IN_USE


def test_the_door_page_offers_check_in_during_the_window(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client = Client()
    client.force_login(student)

    with clock.frozen_clock(IN_WINDOW):
        body = client.get(reverse("core:room_qr", args=[rooms[0].pk])).content.decode()

    assert reverse("core:room_check_in", args=[rooms[0].pk, booking.pk]) in body


# --- The remote paths are gone ------------------------------------------------------


@pytest.mark.parametrize("name", ["check_in", "checkin_qr"])
def test_the_remote_check_in_routes_no_longer_exist(name):
    with pytest.raises(NoReverseMatch):
        reverse(f"core:{name}", args=[1])


def test_my_bookings_says_where_to_check_in_but_offers_no_button(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client = Client()
    client.force_login(student)

    with clock.frozen_clock(IN_WINDOW):
        body = client.get(reverse("core:my_bookings")).content.decode()

    assert "check-in/" not in body, "no check-in form or link on My bookings"
    assert reverse("core:room_qr", args=[rooms[0].pk]) not in body, "no remote link to the door page"
    assert booking.room.label in body


@pytest.mark.parametrize(
    "kind", [notifications.KIND_BOOKING_CONFIRMATION, notifications.KIND_BOOKING_REMINDER]
)
def test_booking_emails_carry_no_check_in_link(frozen, student, rooms, kind):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    notification = Notification.objects.create(
        kind=kind,
        recipient=student,
        payload={
            "booking_id": booking.pk,
            "slot_start": booking.slot_start.isoformat(),
            "slot_end": booking.slot_end.isoformat(),
            "deadline_local": "13:15",
        },
        dedupe_key=f"door-test-{kind}",
        language="en",
    )

    _subject, body = notifications.render(notification, notifications.revalidate(notification, clock.now()))

    assert "check-in/" not in body
    assert "/r/" not in body
    assert "scan the QR code on the room door" in body


# --- The door page explains why there is no check-in button -------------------------


def _door_page(client, room, moment):
    with clock.frozen_clock(moment):
        return client.get(reverse("core:room_qr", args=[room.pk])).content.decode()


def test_the_door_page_says_a_pending_booking_awaits_approval(frozen, student, rooms):
    factories.make_booking(student, rooms[0], day=TOMORROW, hour=13, status=Booking.Status.PENDING_APPROVAL)
    client = Client()
    client.force_login(student)

    body = _door_page(client, rooms[0], IN_WINDOW)

    assert 'data-checkin-state="pending"' in body
    assert 'data-checkin-state="ready"' not in body


@pytest.mark.parametrize("status", [Booking.Status.SCHEDULED, Booking.Status.NO_SHOW])
def test_the_door_page_says_check_in_has_closed(frozen, student, rooms, status):
    factories.make_booking(student, rooms[0], day=TOMORROW, hour=13, status=status)
    client = Client()
    client.force_login(student)

    body = _door_page(client, rooms[0], AFTER_DEADLINE)

    assert 'data-checkin-state="closed"' in body
    assert "13:15" in body, "the closing time is shown"


def test_the_door_page_says_when_check_in_opens_for_an_early_arrival(frozen, student, rooms):
    factories.make_booking(student, rooms[0], day=TOMORROW, hour=14)
    client = Client()
    client.force_login(student)

    body = _door_page(client, rooms[0], factories.bangkok(2026, 9, 15, 13, 50))

    assert 'data-checkin-state="not_open"' in body
    assert "14:00" in body and "14:15" in body
    assert "(in 10 min)" in body or "(อีก 10 นาที)" in body
    assert 'data-checkin-state="ready"' not in body


def test_the_door_page_points_to_the_room_actually_booked(frozen, student, rooms):
    factories.make_booking(student, rooms[1], day=TOMORROW, hour=13)
    client = Client()
    client.force_login(student)

    body = _door_page(client, rooms[0], IN_WINDOW)

    assert 'data-checkin-state="other_room"' in body
    assert str(rooms[1]) in body
