"""Priority 2 product improvements: details, profiles, week view and stats."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import AuditEvent, Booking, Notification
from core.services import availability, clock, slots
from core.services import booking as booking_service
from core.services.errors import OperationOutcome
from core.services.protocol import run_operation
from tests import factories

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()


def advance_with_details(student, room, details):
    slot_start = slots.slot_start_for(DAY, 11)
    payload = booking_service.build_payload(room, slot_start, details)
    return run_operation(
        actor=student,
        operation="advance_booking",
        payload=payload,
        body=lambda ctx: OperationOutcome.success(
            **booking_service.create_advance_booking(ctx, room=room, slot_start=slot_start)
        ),
    )


def test_booking_details_are_saved_and_emailed(frozen, student, rooms):
    details = {
        "title": "Quartet rehearsal",
        "purpose": "Work on the second movement",
        "participant_names": "A, B, C",
    }
    outcome = advance_with_details(student, rooms[0], details)

    assert outcome.ok, outcome.code
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    assert booking.title == details["title"]
    assert booking.purpose == details["purpose"]
    assert booking.participant_names == details["participant_names"]
    notice = Notification.objects.get(kind="booking_confirmation")
    assert notice.payload["participant_names"] == "A, B, C"


def test_future_booking_details_edit_records_audit_and_change_email(frozen, student, rooms):
    created = advance_with_details(student, rooms[0], {"title": "Old title"})
    booking = Booking.objects.get(pk=created.data["booking_id"])
    details = {
        "title": "New title",
        "purpose": "Updated purpose",
        "participant_names": "D, E",
    }
    payload = booking_service.build_update_payload(booking, details)
    outcome = run_operation(
        actor=student,
        operation="update_booking_details",
        payload=payload,
        body=lambda ctx: OperationOutcome.success(
            **booking_service.update_booking_details(ctx, booking=booking, details=details)
        ),
    )

    assert outcome.ok, outcome.code
    booking.refresh_from_db()
    assert booking.details_version == 1
    assert booking.title == "New title"
    assert AuditEvent.objects.filter(action="booking.updated", entity_id=str(booking.pk)).exists()
    changed = Notification.objects.get(kind="booking_changed")
    assert changed.payload["details_version"] == 1
    assert changed.payload["title"] == "New title"


def test_room_profile_filters_are_applied_to_the_availability_grid(frozen, student, rooms):
    rooms[0].capacity = 4
    rooms[0].equipment = "Piano\nMusic stand"
    rooms[0].save(update_fields=["capacity", "equipment"])
    rooms[1].capacity = 2
    rooms[1].equipment = "Piano"
    rooms[1].save(update_fields=["capacity", "equipment"])

    grid = availability.public_grid(
        DAY,
        clock.now(),
        user=student,
        capacity_min=4,
        equipment_query="piano",
    )

    assert [row["room"].pk for row in grid["rows"]] == [rooms[0].pk]


def test_week_view_and_staff_stats_are_real_pages(frozen, student, rooms, staff_user):
    client = Client()
    assert client.get(reverse("core:week")).status_code == 200

    client.force_login(staff_user)
    response = client.get(reverse("core:staff_stats"))
    assert response.status_code == 200
    assert response.context["totals"]["total"] == 0
