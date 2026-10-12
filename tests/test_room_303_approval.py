"""Room 303 is requestable by everyone, with a piano/percussion fast track."""

from __future__ import annotations

import uuid

import pytest
from django.urls import reverse

from core.models import Booking, InstrumentCategory, Room
from core.services import clock, slots
from core.services import rooms as rooms_service
from tests import factories, helpers

pytestmark = pytest.mark.django_db

TUESDAY = factories.bangkok(2026, 9, 15).date()


@pytest.fixture
def room303(db):
    rooms_service.ensure_rooms()
    return Room.objects.get(number="303")


def student_in(category: str | None):
    student = factories.make_user()
    if category:
        spelling = {
            InstrumentCategory.PIANO: "เปียโน",
            InstrumentCategory.PERCUSSION: "เครื่องตี",
            InstrumentCategory.STRINGS: "ไวโอลิน",
        }[category]
        factories.make_roster_entry(user=student, instrument=spelling)
    return student


@pytest.mark.parametrize(
    ("category", "expected_status"),
    [
        (InstrumentCategory.PIANO, Booking.Status.SCHEDULED),
        (InstrumentCategory.PERCUSSION, Booking.Status.SCHEDULED),
        (InstrumentCategory.STRINGS, Booking.Status.PENDING_APPROVAL),
        (None, Booking.Status.PENDING_APPROVAL),
    ],
)
def test_every_eligible_category_can_request_but_only_piano_and_percussion_are_fast_tracked(
    frozen, room303, category, expected_status
):
    student = student_in(category)

    outcome = helpers.advance_booking(student, room303, slots.slot_start_for(TUESDAY, 11))

    assert outcome.ok, outcome.code
    booking = Booking.objects.get(user=student, room=room303)
    assert booking.status == expected_status


def test_a_non_piano_student_can_still_walk_in_when_room_303_is_free(frozen, room303):
    student = student_in(InstrumentCategory.STRINGS)

    with clock.frozen_clock(factories.bangkok(2026, 9, 14, 10, 40)):
        outcome = helpers.use_now(student, room303)

    assert outcome.ok, outcome.code
    booking = Booking.objects.get(user=student, room=room303)
    assert booking.status == Booking.Status.IN_USE
    assert booking.slot_end == slots.slot_start_for(factories.bangkok(2026, 9, 14).date(), 11)


def test_other_category_sees_duration_and_approval_guidance_and_can_be_approved(
    frozen, client, staff_user, room303
):
    student = student_in(InstrumentCategory.STRINGS)
    slot = slots.encode_slot(slots.slot_start_for(TUESDAY, 16))
    client.force_login(student)

    page = client.get(reverse("core:book_slot", args=[room303.pk, slot]))

    assert page.status_code == 200
    html = page.content.decode("utf-8")
    assert 'name="duration_hours"' in html
    assert 'value="2"' in html
    assert page.context["approval_required"] is True

    response = client.post(
        reverse("core:confirm_booking", args=[room303.pk, slot]),
        {"operation_key": str(uuid.uuid4()), "duration_hours": "2"},
    )
    assert response.status_code == 302
    booking = Booking.objects.get(user=student, room=room303)
    assert booking.status == Booking.Status.PENDING_APPROVAL
    assert booking.slot_end == slots.slot_start_for(TUESDAY, 18)

    client.force_login(staff_user)
    approved = client.post(
        reverse("core:staff_decide_booking_approval", args=[booking.pk]),
        {"operation_key": str(uuid.uuid4()), "decision": "APPROVED"},
    )
    assert approved.status_code == 302
    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED
