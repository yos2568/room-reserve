"""Priority 3: private calendar export, lobby status, approvals and series."""

from __future__ import annotations

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from core.models import AuditEvent, Booking, Notification, RecurringReservation, RoomAdministrator
from core.services import booking as booking_service
from core.services import slots
from core.services.errors import OperationOutcome
from core.services.protocol import run_operation
from tests import factories

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()


def create_booking(student, room, *, hour=11, **options):
    slot_start = slots.slot_start_for(DAY, hour)
    payload = booking_service.build_payload(room, slot_start, {"title": "Private rehearsal"}, **options)
    return run_operation(
        actor=student,
        operation="advance_booking",
        payload=payload,
        body=lambda ctx: OperationOutcome.success(
            **booking_service.create_advance_booking(ctx, room=room, slot_start=slot_start)
        ),
    )


def test_ics_export_is_private_and_calendar_compatible(frozen, student, other_student, rooms):
    booking = factories.make_booking(student, rooms[0], day=DAY, hour=11)
    client = Client()
    client.force_login(student)

    response = client.get(reverse("core:booking_ics", args=[booking.pk]))
    body = response.content.decode()
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in body
    assert f"UID:booking-{booking.pk}@roomreserve" in body
    assert student.email not in body

    client.force_login(other_student)
    assert client.get(reverse("core:booking_ics", args=[booking.pk])).status_code == 404


def test_lobby_status_does_not_expose_student_or_booking_details(frozen, student, rooms):
    factories.make_booking(
        student,
        rooms[0],
        day=DAY,
        hour=10,
        status=Booking.Status.IN_USE,
        title="Private title",
        purpose="Private purpose",
        participant_names="Private participant",
    )
    response = Client().get(reverse("core:lobby"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "In use" in body
    assert student.email not in body
    assert "Private title" not in body
    assert "Private participant" not in body


def test_room_approval_is_scoped_and_sends_confirmation_after_approval(frozen, student, staff_user, rooms):
    room = rooms[0]
    room.requires_approval = True
    room.save(update_fields=["requires_approval"])

    outcome = create_booking(student, room)
    assert outcome.ok, outcome.code
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    assert booking.status == Booking.Status.PENDING_APPROVAL
    assert Notification.objects.filter(kind="booking_pending").count() == 1

    RoomAdministrator.objects.create(room=room, user=staff_user, granted_by=staff_user)
    client = Client()
    client.force_login(staff_user)
    response = client.post(
        reverse("core:staff_decide_booking_approval", args=[booking.pk]),
        {"decision": "APPROVED", "note": "Confirmed for the pilot", "operation_key": "approve-1"},
    )
    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED
    assert Notification.objects.filter(kind="booking_confirmation", recipient=student).exists()
    assert AuditEvent.objects.filter(action="booking.approved", entity_id=str(booking.pk)).exists()


def test_recurring_reservations_materialise_and_cancel_future_occurrences(frozen, student, rooms):
    with override_settings(RECURRING_MAX_WEEKS=2):
        outcome = create_booking(
            student,
            rooms[0],
            hour=11,
            repeat_weekly=True,
            repeat_until="2026-09-21",
        )
    assert outcome.ok, outcome.code
    series = RecurringReservation.objects.get(pk=outcome.data["series_id"])
    assert series.occurrences.count() == 2
    assert series.occurrences.filter(status=Booking.Status.SCHEDULED).count() == 2

    client = Client()
    client.force_login(student)
    response = client.post(
        reverse("core:cancel_recurring", args=[series.pk]),
        {"reason": "Schedule changed", "operation_key": "series-cancel-1"},
    )
    assert response.status_code == 302
    series.refresh_from_db()
    assert series.status == RecurringReservation.Status.CANCELLED
    assert not series.occurrences.filter(status=Booking.Status.SCHEDULED).exists()
