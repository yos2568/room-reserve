"""Staff book rooms for the department, outside the student limits (D-41).

Operational staff and the maintainer may hold a room for several consecutive
hours and up to STAFF_BOOKING_HORIZON_DAYS ahead, confirmed at once. The
calendar still applies, an hour someone holds is never taken over, and a staff
hold is never a no-show. Staff see every upcoming booking on one page.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse

from core.models import Booking, Room, Violation
from core.services import clock, slots
from core.services import rooms as rooms_service
from core.services.reconcile import enqueue_due_reminders, reconcile
from tests import factories, helpers

pytestmark = pytest.mark.django_db

# BASE_MOMENT is Monday 14 September 2026, 10:40 in Bangkok.
A_MONTH_AHEAD = date(2026, 10, 14)  # a Wednesday, far outside the student window


@pytest.fixture
def all_rooms(db):
    rooms_service.ensure_rooms()
    return {room.number: room for room in Room.objects.all()}


def _book(client, room, day, start_hour, end_hour, title="Orchestra rehearsal"):
    return client.post(
        reverse("core:staff_create_booking"),
        {
            "room": room.pk,
            "date": day.isoformat(),
            "start_hour": start_hour,
            "end_hour": end_hour,
            "title": title,
        },
    )


def test_staff_book_several_hours_a_month_ahead(client, frozen, staff_user, all_rooms):
    client.force_login(staff_user)
    response = _book(client, all_rooms["1"], A_MONTH_AHEAD, 17, 20)
    assert response.status_code == 302
    held = list(Booking.objects.filter(room=all_rooms["1"]).order_by("slot_start"))
    assert [booking.local_start.hour for booking in held] == [17, 18, 19]
    for booking in held:
        assert booking.status == Booking.Status.SCHEDULED
        assert booking.is_staff_booking
        assert booking.user == staff_user
        assert booking.approved_by == staff_user
        assert booking.title == "Orchestra rehearsal"


def test_a_staff_booking_of_an_approval_room_needs_no_approval(client, frozen, staff_user, all_rooms):
    client.force_login(staff_user)
    _book(client, all_rooms["304"], A_MONTH_AHEAD, 17, 19)
    statuses = set(Booking.objects.filter(room=all_rooms["304"]).values_list("status", flat=True))
    assert statuses == {Booking.Status.SCHEDULED}


def test_staff_cannot_book_beyond_their_window(client, frozen, staff_user, all_rooms, settings):
    client.force_login(staff_user)
    too_far = clock.local_date(frozen) + timedelta(days=settings.STAFF_BOOKING_HORIZON_DAYS + 7)
    while too_far.weekday() >= 5:
        too_far += timedelta(days=1)
    response = _book(client, all_rooms["1"], too_far, 10, 12)
    assert response.status_code == 200
    assert not Booking.objects.exists()


def test_a_range_touching_a_students_hour_books_nothing(client, frozen, staff_user, student, all_rooms):
    tomorrow = date(2026, 9, 15)
    outcome = helpers.advance_booking(student, all_rooms["2"], slots.slot_start_for(tomorrow, 18))
    assert outcome.ok
    client.force_login(staff_user)
    response = _book(client, all_rooms["2"], tomorrow, 17, 20)
    assert response.status_code == 409
    assert not Booking.objects.filter(user=staff_user).exists()
    assert Booking.objects.get(user=student).status == Booking.Status.SCHEDULED


def test_class_hours_still_apply_to_staff(client, frozen, staff_user, all_rooms):
    # Room 301 has a class on Mondays 13:00-15:00.
    next_monday = date(2026, 9, 21)
    client.force_login(staff_user)
    response = _book(client, all_rooms["301"], next_monday, 12, 14)
    assert response.status_code != 302
    assert not Booking.objects.exists()


def test_end_must_follow_start(client, frozen, staff_user, all_rooms):
    client.force_login(staff_user)
    response = _book(client, all_rooms["1"], A_MONTH_AHEAD, 18, 18)
    assert response.status_code == 400
    assert not Booking.objects.exists()


def test_a_student_cannot_use_the_staff_booking_screen(client, frozen, student, all_rooms):
    client.force_login(student)
    assert client.get(reverse("core:staff_book_room")).status_code == 403
    assert _book(client, all_rooms["1"], A_MONTH_AHEAD, 17, 20).status_code == 403
    assert not Booking.objects.exists()


def test_staff_requesting_301_from_the_timetable_are_approved_at_once(frozen, staff_user, all_rooms):
    outcome = helpers.advance_booking(
        staff_user, all_rooms["301"], slots.slot_start_for(date(2026, 9, 15), 16)
    )
    assert outcome.ok
    booking = Booking.objects.get(user=staff_user)
    assert booking.status == Booking.Status.SCHEDULED
    assert booking.approved_by == staff_user


def test_a_students_301_request_still_waits_for_approval(frozen, student, all_rooms):
    outcome = helpers.advance_booking(student, all_rooms["301"], slots.slot_start_for(date(2026, 9, 15), 16))
    assert outcome.ok
    assert Booking.objects.get(user=student).status == Booking.Status.PENDING_APPROVAL


def test_a_staff_hold_is_never_a_no_show_and_completes_at_its_end(client, frozen, staff_user, all_rooms):
    client.force_login(staff_user)
    _book(client, all_rooms["1"], date(2026, 9, 15), 17, 19)
    assert enqueue_due_reminders(slots.slot_start_for(date(2026, 9, 15), 17) - timedelta(minutes=10)) == 0

    after_deadline = slots.slot_start_for(date(2026, 9, 15), 17) + timedelta(minutes=30)
    with clock.frozen_clock(after_deadline):
        reconcile(after_deadline)
    # The running hour is in use (staff never check in); the next is still ahead.
    statuses = dict(Booking.objects.values_list("slot_start", "status"))
    assert sorted(statuses.values()) == [Booking.Status.IN_USE, Booking.Status.SCHEDULED]
    assert not Violation.objects.exists()

    after_end = slots.slot_start_for(date(2026, 9, 15), 19) + timedelta(minutes=1)
    with clock.frozen_clock(after_end):
        reconcile(after_end)
    assert set(Booking.objects.values_list("status", flat=True)) == {Booking.Status.COMPLETED}
    assert not Violation.objects.exists()


def test_a_students_missed_hour_is_still_a_no_show(frozen, student, all_rooms):
    start = slots.slot_start_for(date(2026, 9, 15), 17)
    assert helpers.advance_booking(student, all_rooms["1"], start).ok
    with clock.frozen_clock(start + timedelta(minutes=30)):
        reconcile(start + timedelta(minutes=30))
    assert Booking.objects.get(user=student).status == Booking.Status.NO_SHOW


def test_upcoming_lists_students_and_staff_bookings(client, frozen, staff_user, student, all_rooms):
    assert helpers.advance_booking(student, all_rooms["3"], slots.slot_start_for(date(2026, 9, 15), 9)).ok
    assert helpers.advance_booking(student, all_rooms["304"], slots.slot_start_for(date(2026, 9, 15), 16)).ok
    client.force_login(staff_user)
    _book(client, all_rooms["1"], A_MONTH_AHEAD, 17, 18)

    response = client.get(reverse("core:staff_upcoming"))
    assert response.status_code == 200
    listed = {(booking.room.number, booking.user_id) for booking in response.context["bookings"]}
    assert listed == {("3", student.pk), ("304", student.pk), ("1", staff_user.pk)}

    filtered = client.get(reverse("core:staff_upcoming"), {"room": all_rooms["3"].pk})
    assert [booking.room.number for booking in filtered.context["bookings"]] == ["3"]


def test_today_points_to_waiting_room_requests(client, frozen, staff_user, student, all_rooms):
    assert helpers.advance_booking(student, all_rooms["304"], slots.slot_start_for(date(2026, 9, 15), 16)).ok
    client.force_login(staff_user)
    response = client.get(reverse("core:staff_today"))
    assert response.context["room_requests_waiting"] == 1
    assert reverse("core:room_admin") in response.content.decode()


def test_a_teacher_room_admin_does_not_get_staff_booking(client, frozen, all_rooms):
    teacher = factories.make_user(is_teacher=True)
    client.force_login(teacher)
    assert client.get(reverse("core:staff_book_room")).status_code == 403
