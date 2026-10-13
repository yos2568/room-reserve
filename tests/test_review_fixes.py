"""Regression tests for the issues a review of D-41..D-44 found.

BASE_MOMENT is Monday 14 September 2026, 10:40 in Bangkok.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.urls import reverse

from core.models import Booking, Room, RoomMember, User, Violation
from core.services import booking as booking_service
from core.services import clock, identity, instruments, slots
from core.services import rooms as rooms_service
from core.services.errors import OperationRejected
from core.services.protocol import run_operation
from core.services.reconcile import reconcile
from core.services.timeline import day_timeline
from tests import factories, helpers

pytestmark = pytest.mark.django_db

MONDAY = date(2026, 9, 14)
TUESDAY = date(2026, 9, 15)


@pytest.fixture
def all_rooms(db):
    rooms_service.ensure_rooms()
    return {room.number: room for room in Room.objects.all()}


def _register_listed(conference, institutional_id, email, **extra):
    RoomMember.objects.get_or_create(room=conference, email=email)
    result = identity.start_registration(
        institutional_id=institutional_id, email=email, name="นิสิต ทดสอบ", **extra
    )
    user, _ = identity.verify_email(result.raw_token)
    user.refresh_from_db()
    return user


# --- Sign-up from a room list ---------------------------------------------------


def test_a_room_listed_account_does_not_unlock_instrument_rooms_by_declaration(frozen, all_rooms):
    user = _register_listed(all_rooms["ป.โท"], "6900000135", "listed@gmail.com", declared_category="PIANO")
    assert user.eligibility == User.Eligibility.APPROVED
    assert instruments.category_for_user(user) is None
    assert not helpers.advance_booking(user, all_rooms["303"], slots.slot_start_for(TUESDAY, 10)).ok


def test_staff_approval_still_honours_a_declaration(frozen):
    user = factories.make_user(declared_category="PIANO", eligibility_reason="Identity confirmed by staff")
    assert instruments.category_for_user(user) == "PIANO"


def test_a_room_listed_address_cannot_take_an_id_the_roster_gives_to_someone_else(frozen, all_rooms):
    factories.make_roster_entry(institutional_id="6500000135", email="owner@student.chula.ac.th")
    RoomMember.objects.create(room=all_rooms["ป.โท"], email="squatter@gmail.com")
    with pytest.raises(OperationRejected):
        identity.start_registration(institutional_id="6500000135", email="squatter@gmail.com", name="x")


def test_an_account_listed_later_is_not_approved_under_a_roster_id(client, frozen, all_rooms, staff_user):
    factories.make_roster_entry(institutional_id="6500000135", email="owner@student.chula.ac.th")
    squatter = factories.make_user(
        username="6500000135", email="other@student.chula.ac.th", eligibility=User.Eligibility.PENDING
    )
    client.force_login(staff_user)
    client.post(
        reverse("core:staff_add_room_members", args=[all_rooms["ป.โท"].pk]),
        {"rows": "1\tx\tother@student.chula.ac.th"},
    )
    squatter.refresh_from_db()
    assert squatter.eligibility == User.Eligibility.PENDING


# --- Correcting a student ID ----------------------------------------------------


@pytest.mark.parametrize(
    "flags", [{"is_operational_staff": True}, {"is_superuser": True}, {"is_teacher": True}]
)
def test_staff_and_teacher_sign_ins_cannot_be_renamed(frozen, staff_user, flags):
    target = factories.make_user(**flags)
    with pytest.raises(OperationRejected):
        identity.correct_institutional_id(
            user=target, institutional_id="6800000999", reason="x", actor=staff_user
        )


@pytest.mark.parametrize("new_id", ["68 00000999", "68/00000999"])
def test_an_invalid_id_is_refused(frozen, staff_user, student, new_id):
    with pytest.raises(OperationRejected):
        identity.correct_institutional_id(user=student, institutional_id=new_id, reason="x", actor=staff_user)


def test_an_id_the_roster_gives_to_another_student_is_refused(frozen, staff_user, student):
    factories.make_roster_entry(institutional_id="6500000135", email="owner@student.chula.ac.th")
    with pytest.raises(OperationRejected):
        identity.correct_institutional_id(
            user=student, institutional_id="6500000135", reason="x", actor=staff_user
        )


def test_an_id_the_roster_lists_with_this_students_email_is_allowed(frozen, staff_user, student):
    factories.make_roster_entry(institutional_id="6500000135", email=student.email)
    identity.correct_institutional_id(
        user=student, institutional_id="6500000135", reason="typo", actor=staff_user
    )
    student.refresh_from_db()
    assert student.institutional_id == "6500000135"


# --- Staff bookings -------------------------------------------------------------


def test_a_staff_booking_from_the_timetable_is_never_a_no_show(frozen, all_rooms, staff_user):
    start = slots.slot_start_for(TUESDAY, 10)
    assert helpers.advance_booking(staff_user, all_rooms["304"], start).ok
    booking = Booking.objects.get(user=staff_user)
    assert booking.is_staff_booking
    with clock.frozen_clock(start + timedelta(minutes=30)):
        reconcile(start + timedelta(minutes=30))
    booking.refresh_from_db()
    assert booking.status == Booking.Status.SCHEDULED
    assert not Violation.objects.exists()


def _move(actor, booking, room):
    return run_operation(
        actor=actor,
        operation="move_booking",
        payload=booking_service.build_move_payload(booking, room),
        body=helpers._body(lambda ctx: booking_service.move_booking(ctx, booking=booking, room=room)),
    )


def test_staff_moving_into_an_approval_room_stay_approved(frozen, all_rooms, staff_user):
    assert helpers.advance_booking(staff_user, all_rooms["3"], slots.slot_start_for(TUESDAY, 10)).ok
    booking = Booking.objects.get(user=staff_user)
    assert _move(staff_user, booking, all_rooms["304"]).ok
    booking.refresh_from_db()
    assert (booking.status, booking.approved_by) == (Booking.Status.SCHEDULED, staff_user)


def test_a_students_move_into_an_approval_room_waits_with_no_stale_approval(
    frozen, all_rooms, student, staff_user
):
    assert helpers.advance_booking(student, all_rooms["304"], slots.slot_start_for(TUESDAY, 10)).ok
    booking = Booking.objects.get(user=student)
    Booking.objects.filter(pk=booking.pk).update(
        status=Booking.Status.SCHEDULED, approved_by=staff_user, approved_at=clock.now()
    )
    booking.refresh_from_db()
    assert _move(student, booking, all_rooms["301"]).ok
    booking.refresh_from_db()
    assert booking.status == Booking.Status.PENDING_APPROVAL
    assert booking.approved_by is None and booking.approved_at is None


def test_one_staff_member_may_hold_two_rooms_at_the_same_hour(client, frozen, all_rooms, staff_user):
    client.force_login(staff_user)
    for room in (all_rooms["301"], all_rooms["304"]):
        response = client.post(
            reverse("core:staff_create_booking"),
            {"room": room.pk, "date": "2026-09-15", "start_hour": 16, "end_hour": 18, "title": "Concert"},
        )
        assert response.status_code == 302
    assert Booking.objects.filter(user=staff_user).count() == 4


def test_a_student_still_holds_one_room_per_hour(frozen, all_rooms, student):
    assert helpers.advance_booking(student, all_rooms["1"], slots.slot_start_for(TUESDAY, 10)).ok
    assert not helpers.advance_booking(student, all_rooms["2"], slots.slot_start_for(TUESDAY, 10)).ok


# --- Timeline and home board ------------------------------------------------------


def test_a_walk_in_after_a_no_show_is_the_only_bar_for_that_hour(frozen, all_rooms, student, other_student):
    room = all_rooms["3"]
    factories.make_booking(student, room, day=MONDAY, hour=9, status=Booking.Status.NO_SHOW)
    walk_in = factories.make_booking(
        other_student,
        room,
        day=MONDAY,
        hour=9,
        status=Booking.Status.COMPLETED,
        source=Booking.Source.WALK_IN,
    )
    rows = {row.room.pk: row for _, group in day_timeline(MONDAY, clock.now())["groups"] for row in group}
    bars = [bar for bar in rows[room.pk].bars if bar.start_hour == 9]
    assert [bar.booking for bar in bars] == [walk_in]


def test_the_board_refreshes_on_its_own(client, frozen, all_rooms):
    page = client.get(reverse("core:home")).content.decode()
    assert f'hx-get="{reverse("core:now_board")}"' in page
    fragment = client.get(reverse("core:now_board"), {"show": "free"})
    assert fragment.status_code == 200
    assert fragment.context["board_show"] == "free"
    assert 'id="now"' in fragment.content.decode()


def test_the_board_fragment_is_empty_after_closing(client, all_rooms):
    with clock.frozen_clock(factories.bangkok(2026, 9, 14, 21, 0)):
        response = client.get(reverse("core:now_board"))
    assert response.status_code == 200
    assert response.content == b""


def test_the_refreshed_timetable_keeps_the_repeated_lists_hidden(client, frozen, all_rooms, student):
    client.force_login(student)
    fragment = client.get(reverse("core:availability")).content.decode()
    assert "Bookable later today" not in fragment and "หลังจากนี้" not in fragment
    assert client.get(reverse("core:availability")).context["board"] is None  # not built for the fragment
