"""Atomic room move: change the room, keep the hour, never lose the booking.

The reason this operation exists at all is that cancel-then-rebook has a window
in which the student holds nothing: the old room is released before the new one
is secured, and a competing request can take the target in between. Every test
below therefore checks the booking's state after a refusal, not only the
refusal itself.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import AuditEvent, Booking, Notification, RecurringReservation
from core.services import booking as booking_service
from core.services.errors import Code, OperationOutcome
from core.services.protocol import run_operation
from tests import factories

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()
HOUR = 11


def move(actor, booking, room, key=None):
    return run_operation(
        actor=actor,
        operation="move_booking",
        payload=booking_service.build_move_payload(booking, room),
        key=key,
        body=lambda ctx: OperationOutcome.success(
            **booking_service.move_booking(ctx, booking=booking, room=room)
        ),
    )


def scheduled(student, room):
    return factories.make_booking(student, room, day=DAY, hour=HOUR)


def test_move_changes_the_room_and_keeps_one_booking(frozen, student, rooms):
    booking = scheduled(student, rooms[0])

    outcome = move(student, booking, rooms[1])

    assert outcome.ok, outcome.code
    booking.refresh_from_db()
    assert booking.room_id == rooms[1].pk
    assert booking.status == Booking.Status.SCHEDULED
    # The identity of the reservation survives: same row, same check-in token.
    assert Booking.objects.filter(user=student, slot_date=DAY).count() == 1
    assert booking.pk == outcome.data["booking_id"]


def test_move_keeps_the_check_in_token_and_deadline(frozen, student, rooms):
    booking = scheduled(student, rooms[0])
    token_before = booking.checkin_token
    deadline_before = booking.deadline

    assert move(student, booking, rooms[1]).ok

    booking.refresh_from_db()
    assert booking.checkin_token == token_before, "a move must not invalidate a printed QR link"
    assert booking.deadline == deadline_before, "the hour did not change, so neither does the grace"


def test_a_taken_target_leaves_the_original_booking_untouched(frozen, student, other_student, rooms):
    """The property that makes this safer than cancel-then-rebook."""
    booking = scheduled(student, rooms[0])
    factories.make_booking(other_student, rooms[1], day=DAY, hour=HOUR)

    outcome = move(student, booking, rooms[1])

    assert not outcome.ok
    assert outcome.code == Code.SLOT_TAKEN
    booking.refresh_from_db()
    assert booking.room_id == rooms[0].pk, "the student must not be left holding nothing"
    assert booking.status == Booking.Status.SCHEDULED


def test_moving_to_the_same_room_is_refused(frozen, student, rooms):
    booking = scheduled(student, rooms[0])

    outcome = move(student, booking, rooms[0])

    assert not outcome.ok
    assert outcome.code == Code.SAME_ROOM
    booking.refresh_from_db()
    assert booking.room_id == rooms[0].pk


def test_only_the_owner_may_move_a_booking(frozen, student, other_student, rooms):
    booking = scheduled(student, rooms[0])

    outcome = move(other_student, booking, rooms[1])

    assert not outcome.ok
    assert outcome.code == Code.NOT_OWNER
    booking.refresh_from_db()
    assert booking.room_id == rooms[0].pk


def test_a_started_booking_cannot_be_moved(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=DAY, hour=HOUR, status=Booking.Status.IN_USE)

    outcome = move(student, booking, rooms[1])

    assert not outcome.ok
    assert outcome.code == Code.TERMINAL_STATUS
    booking.refresh_from_db()
    assert booking.room_id == rooms[0].pk


def test_a_cancelled_booking_cannot_be_moved(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], day=DAY, hour=HOUR, status=Booking.Status.CANCELLED)

    outcome = move(student, booking, rooms[1])

    assert not outcome.ok
    assert outcome.code == Code.TERMINAL_STATUS


def test_the_move_is_audited_and_notified(frozen, student, rooms):
    booking = scheduled(student, rooms[0])

    assert move(student, booking, rooms[1]).ok

    event = AuditEvent.objects.filter(action="booking.moved", entity_id=booking.pk).first()
    assert event is not None
    assert event.changes["from_room"] == rooms[0].number
    assert event.changes["to_room"] == rooms[1].number

    note = Notification.objects.filter(kind="booking_changed", recipient=student).first()
    assert note is not None
    assert note.payload["previous_room_number"] == rooms[0].number
    assert note.payload["room_number"] == rooms[1].number


def test_quota_is_not_consumed_by_a_move(frozen, student, rooms):
    """A move adds no booking, so a student at their quota may still move."""
    first = scheduled(student, rooms[0])
    # A second booking two hours away keeps adjacency out of it and puts the
    # student on the default quota of two for the day.
    factories.make_booking(student, rooms[0], day=DAY, hour=HOUR + 2)

    outcome = move(student, first, rooms[1])

    assert outcome.ok, "moving must not be refused for a quota the move does not change"
    first.refresh_from_db()
    assert first.room_id == rooms[1].pk
    assert Booking.objects.filter(user=student, slot_date=DAY).count() == 2


def test_adjacency_does_not_block_a_move(frozen, student, rooms):
    """The student's own neighbouring hour must not veto changing room."""
    booking = scheduled(student, rooms[0])
    factories.make_booking(student, rooms[2], day=DAY, hour=HOUR + 2)

    assert move(student, booking, rooms[1]).ok


def test_a_recurring_occurrence_is_refused(frozen, student, rooms):
    """RecurringReservation records the room, so one occurrence must not drift."""
    series = RecurringReservation.objects.create(
        user=student,
        room=rooms[0],
        weekday=DAY.weekday(),
        start_hour=HOUR,
        start_date=DAY,
        end_date=DAY,
        status=RecurringReservation.Status.ACTIVE,
    )
    booking = factories.make_booking(student, rooms[0], day=DAY, hour=HOUR, recurrence=series)

    outcome = move(student, booking, rooms[1])

    assert not outcome.ok
    assert outcome.code == Code.RECURRING_OCCURRENCE
    booking.refresh_from_db()
    assert booking.room_id == rooms[0].pk


def test_the_move_is_idempotent_under_one_key(frozen, student, rooms):
    booking = scheduled(student, rooms[0])

    first = move(student, booking, rooms[1], key="move-key-1")
    second = move(student, booking, rooms[1], key="move-key-1")

    assert first.ok and second.ok
    assert second.replayed, "a retried submit must replay, not refuse as SAME_ROOM"
    assert Booking.objects.filter(user=student, slot_date=DAY).count() == 1


# --- The view --------------------------------------------------------------


def signed_in(student):
    client = Client()
    client.force_login(student)
    return client


def move_url(booking):
    return reverse("core:move_booking", args=[booking.pk])


def test_the_picker_lists_other_free_rooms_and_never_the_current_one(frozen, student, rooms):
    booking = scheduled(student, rooms[0])

    response = signed_in(student).get(move_url(booking))

    assert response.status_code == 200
    offered = {room.pk for room in response.context["rooms"]}
    assert rooms[0].pk not in offered, "the room already held is not an alternative"
    assert rooms[1].pk in offered


def test_the_picker_omits_a_room_another_student_holds(frozen, student, other_student, rooms):
    booking = scheduled(student, rooms[0])
    factories.make_booking(other_student, rooms[1], day=DAY, hour=HOUR)

    response = signed_in(student).get(move_url(booking))

    offered = {room.pk for room in response.context["rooms"]}
    assert rooms[1].pk not in offered
    assert rooms[2].pk in offered


def test_posting_a_room_moves_the_booking_and_redirects(frozen, student, rooms):
    booking = scheduled(student, rooms[0])

    response = signed_in(student).post(
        move_url(booking), {"room_id": rooms[1].pk, "operation_key": "view-move-1"}
    )

    assert response.status_code == 302
    assert response["Location"].endswith(reverse("core:my_bookings"))
    booking.refresh_from_db()
    assert booking.room_id == rooms[1].pk


def test_a_lost_race_answers_409_and_keeps_the_booking(frozen, student, other_student, rooms):
    """The room went while the page was open; the student still has theirs."""
    booking = scheduled(student, rooms[0])
    factories.make_booking(other_student, rooms[1], day=DAY, hour=HOUR)

    response = signed_in(student).post(
        move_url(booking), {"room_id": rooms[1].pk, "operation_key": "view-move-2"}
    )

    assert response.status_code == 409
    assert response.context["outcome"].code == Code.SLOT_TAKEN
    booking.refresh_from_db()
    assert booking.room_id == rooms[0].pk


def test_another_students_booking_is_not_disclosed(frozen, student, other_student, rooms):
    booking = scheduled(other_student, rooms[0])

    response = signed_in(student).get(move_url(booking))

    assert response.status_code == 404


def test_an_anonymous_visitor_is_sent_to_sign_in(frozen, student, rooms):
    booking = scheduled(student, rooms[0])

    response = Client().get(move_url(booking))

    assert response.status_code == 302
    assert "/login/" in response["Location"]
