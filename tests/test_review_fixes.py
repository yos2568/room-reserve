"""Regression tests for the issues a review of D-41..D-44 found.

BASE_MOMENT is Monday 14 September 2026, 10:40 in Bangkok.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

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
    assert booking.status == Booking.Status.IN_USE  # in use from the start of its hour, never a no-show
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


# --- Second review (13 Oct) -------------------------------------------------------


def test_an_instrument_staff_set_counts_for_a_room_listed_account(client, frozen, all_rooms, staff_user):
    user = _register_listed(all_rooms["ป.โท"], "6900000135", "listed@gmail.com", declared_category="PIANO")
    assert instruments.category_for_user(user) is None
    client.force_login(staff_user)
    client.post(
        reverse("core:staff_set_declared_category", args=[user.pk]),
        {"declared_category": "PIANO", "operation_key": "set-piano-1"},
    )
    user.refresh_from_db()
    assert instruments.category_for_user(user) == "PIANO"
    assert helpers.advance_booking(user, all_rooms["303"], slots.slot_start_for(TUESDAY, 10)).ok


def test_staff_may_book_any_hour_of_the_last_date_the_form_offers(
    client, frozen, all_rooms, staff_user, settings
):
    settings.STAFF_BOOKING_HORIZON_DAYS = 4  # the last date is Friday 18 September
    client.force_login(staff_user)
    page = client.get(reverse("core:staff_book_room"))
    assert page.context["max_date"] == date(2026, 9, 18)
    response = client.post(
        reverse("core:staff_create_booking"),
        {
            "room": all_rooms["304"].pk,
            "date": "2026-09-18",
            "start_hour": 17,
            "end_hour": 20,
            "title": "Recital",
        },
    )
    assert response.status_code == 302
    assert Booking.objects.filter(user=staff_user).count() == 3
    late = client.post(
        reverse("core:staff_create_booking"),
        {"room": all_rooms["304"].pk, "date": "2026-09-19", "start_hour": 10, "end_hour": 11},
    )
    assert late.status_code != 302


def test_a_student_not_on_the_list_is_told_why_the_conference_room_is_closed_to_them(
    client, frozen, all_rooms, student
):
    client.force_login(student)
    room = all_rooms["ป.โท"]
    url = reverse("core:book_slot", args=[room.pk, slots.encode_slot(slots.slot_start_for(TUESDAY, 10))])
    shown = [str(m) for m in client.get(url, follow=True).context["messages"]]
    assert any("on its list" in m or "รายชื่อ" in m for m in shown)
    assert not any("piano" in m.lower() or "เปียโน" in m for m in shown)


def test_the_timeline_fits_a_day_with_shortened_opening_hours(frozen, all_rooms):
    from core.models import CalendarOverride

    CalendarOverride.objects.create(local_date=TUESDAY, is_open=True, open_hour=10, close_hour=14)
    timeline = day_timeline(TUESDAY, clock.now())
    assert timeline["columns"] == [10, 11, 12, 13]
    css = Path("core/static/css/tailwind.css").read_text()
    assert ".timeline-cols-4" in css and ".tl-s24" in css and ".tl-e25" in css


def test_a_booking_under_a_later_closure_gets_its_own_line(frozen, all_rooms, student, staff_user):
    from core.models import Closure

    room = all_rooms["3"]
    assert helpers.advance_booking(student, room, slots.slot_start_for(TUESDAY, 11)).ok
    Closure.objects.create(
        room=room,
        starts_at=slots.slot_start_for(TUESDAY, 11),
        ends_at=slots.slot_start_for(TUESDAY, 12),
        reason="Tuning",
        created_by=staff_user,
    )
    rows = {row.room.pk: row for _, group in day_timeline(TUESDAY, clock.now())["groups"] for row in group}
    row = rows[room.pk]
    at_eleven = sorted((bar.kind, bar.lane) for bar in row.bars if bar.start_hour == 11)
    assert at_eleven == [("booked", 2), ("closed", 1)]
    assert row.has_lanes


def test_posters_offer_walk_in_only_where_walk_ins_are_allowed(client, frozen, all_rooms, staff_user):
    from django.utils import translation

    client.force_login(staff_user)
    with translation.override("en"):
        html = client.get(reverse("core:staff_poster_sheet")).content.decode()
    walk_in_rooms = Room.objects.filter(is_active=True, members_only=False, requires_approval=False).count()
    assert html.count("If the hour is free, confirm you are here to use it now.") == walk_in_rooms


# --- A staff booking in progress is protected like a checked-in session -----------


@pytest.fixture
def running_staff_hold(all_rooms, staff_user):
    """A staff booking of room 3 for 10:00-11:00 on Monday; the frozen time is 10:40."""
    return factories.make_booking(staff_user, all_rooms["3"], day=MONDAY, hour=10, is_staff_booking=True)


def test_a_staff_hold_is_in_use_once_its_hour_begins(frozen, running_staff_hold):
    assert running_staff_hold.in_progress
    reconcile(clock.now())
    running_staff_hold.refresh_from_db()
    assert running_staff_hold.status == Booking.Status.IN_USE


def test_a_staff_hold_in_progress_cannot_be_cancelled_and_resold(
    client, frozen, all_rooms, staff_user, other_student, running_staff_hold
):
    client.force_login(staff_user)
    client.post(
        reverse("core:staff_cancel_booking", args=[running_staff_hold.pk]),
        {"reason": "Rehearsal over", "operation_key": "cancel-running-1"},
    )
    running_staff_hold.refresh_from_db()
    assert running_staff_hold.status == Booking.Status.IN_USE
    assert not helpers.use_now(other_student, all_rooms["3"]).ok


def test_ending_a_staff_hold_early_keeps_its_hour_occupied(
    client, frozen, all_rooms, staff_user, other_student, running_staff_hold
):
    client.force_login(staff_user)
    client.post(
        reverse("core:staff_complete_early", args=[running_staff_hold.pk]),
        {"reason": "Rehearsal over", "operation_key": "end-running-1"},
    )
    running_staff_hold.refresh_from_db()
    assert running_staff_hold.status == Booking.Status.COMPLETED
    assert not helpers.use_now(other_student, all_rooms["3"]).ok


def test_a_closure_preview_counts_a_staff_hold_in_progress(frozen, all_rooms, running_staff_hold):
    from core.services.maintenance import preview_closure

    preview = preview_closure(all_rooms["3"], clock.now(), slots.slot_start_for(MONDAY, 12))
    assert preview["in_use"] == [running_staff_hold]
    assert preview["scheduled"] == []


def test_the_timeline_offers_end_early_not_cancel_for_a_staff_hold_in_progress(
    client, frozen, staff_user, running_staff_hold
):
    client.force_login(staff_user)
    page = client.get(
        reverse("core:staff_timeline"), {"date": MONDAY.isoformat(), "booking": running_staff_hold.pk}
    ).content.decode()
    assert reverse("core:staff_complete_early", args=[running_staff_hold.pk]) in page
    assert reverse("core:staff_cancel_booking", args=[running_staff_hold.pk]) not in page
