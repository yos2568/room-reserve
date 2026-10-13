"""The master's conference room is reservable only by its listed people and staff (D-42).

Everyone on the room's access list (matched by account email, ignoring case)
may reserve it; every other student sees it but cannot reserve it or walk in;
staff keep full control. All names and addresses here are synthetic.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from core.models import Booking, Room, RoomMember
from core.services import availability, clock, slots
from core.services import rooms as rooms_service
from core.services.errors import Code
from core.services.room_access import member_room_ids, parse_pasted_rows
from tests import factories, helpers

pytestmark = pytest.mark.django_db

TOMORROW = date(2026, 9, 15)


@pytest.fixture
def conference(db):
    rooms_service.ensure_rooms()
    return Room.objects.get(number="ป.โท")


@pytest.fixture
def listed(conference):
    user = factories.make_user(username="6900000135", email="Listed.Master@Example.com")
    RoomMember.objects.create(room=conference, email="listed.master@example.com", name="นิสิต ทดสอบ")
    return user


def _reserve(user, room, hour=10):
    return helpers.advance_booking(user, room, slots.slot_start_for(TOMORROW, hour))


def test_a_listed_student_reserves_it_without_approval(frozen, conference, listed):
    outcome = _reserve(listed, conference)
    assert outcome.ok
    assert Booking.objects.get(user=listed).status == Booking.Status.SCHEDULED


def test_a_student_not_on_the_list_cannot_reserve_it(frozen, conference, listed, student):
    outcome = _reserve(student, conference)
    assert not outcome.ok
    assert outcome.code == Code.ROOM_MEMBERS_ONLY
    assert not Booking.objects.exists()


def test_staff_reserve_it_from_the_timetable_and_the_staff_screen(client, frozen, conference, staff_user):
    assert _reserve(staff_user, conference).ok
    client.force_login(staff_user)
    response = client.post(
        reverse("core:staff_create_booking"),
        {"room": conference.pk, "date": "2026-10-14", "start_hour": 13, "end_hour": 16, "title": "Seminar"},
    )
    assert response.status_code == 302
    assert Booking.objects.filter(room=conference, user=staff_user).count() == 4


def test_nobody_walks_in(conference, listed):
    with clock.frozen_clock(factories.bangkok(2026, 9, 15, 10, 5)):
        outcome = helpers.use_now(listed, conference)
    assert not outcome.ok
    assert outcome.code == Code.MEMBERS_ROOM_NO_WALK_IN


def test_the_timetable_offers_it_only_to_listed_people(frozen, conference, listed, student):
    def cell_for(user):
        grid = availability.public_grid(TOMORROW, frozen, user=user)
        row = next(row for row in grid["rows"] if row["room"].pk == conference.pk)
        return next(cell for cell in row["cells"] if cell.local_start.hour == 10)

    assert cell_for(listed).can_reserve
    assert not cell_for(student).can_reserve
    assert cell_for(student).state == availability.SlotState.RESTRICTED


def test_the_room_is_never_offered_as_a_walk_in_cell(conference, student):
    with clock.frozen_clock(factories.bangkok(2026, 9, 15, 10, 5)) as moment:
        cell = availability.current_slot_cell(conference, moment, user=student)
    assert cell.state == availability.SlotState.FREE_NOW
    assert not cell.can_use_now


def test_other_rooms_are_unchanged_for_listed_people(frozen, conference, listed):
    assert member_room_ids(listed) == {conference.pk}
    assert _reserve(listed, Room.objects.get(number="1")).ok


def test_rows_pasted_from_excel_are_read_with_names_and_emails():
    pasted = (
        "\tรายชื่อนิสิตปี1  ภาคการศึกษาต้น\n"
        "ลำดับที่\tชื่อ-นามสกุล\tE -mail\n"
        "1\tนางสาว ทดสอบ หนึ่ง\tOne@Example.com\n"
        "2\tนาย ทดสอบ สอง \ttwo@example.org\n"
        "3\tซ้ำ\tone@example.com\n"
        "\n"
    )
    entries, skipped = parse_pasted_rows(pasted)
    assert entries == [("นางสาว ทดสอบ หนึ่ง", "one@example.com"), ("นาย ทดสอบ สอง", "two@example.org")]
    assert len(skipped) == 2


def test_staff_add_and_remove_people_on_the_access_screen(client, frozen, conference, staff_user):
    client.force_login(staff_user)
    response = client.post(
        reverse("core:staff_add_room_members", args=[conference.pk]),
        {"rows": "1\tนิสิต ทดสอบ\tperson@example.com\nheader without email"},
    )
    assert response.status_code == 302
    member = RoomMember.objects.get(room=conference)
    assert (member.email, member.name) == ("person@example.com", "นิสิต ทดสอบ")

    page = client.get(reverse("core:staff_room_access"))
    assert "person@example.com" in page.content.decode()

    client.post(reverse("core:staff_remove_room_member", args=[conference.pk, member.pk]))
    assert not RoomMember.objects.exists()


def test_students_cannot_change_the_list(client, frozen, conference, student):
    client.force_login(student)
    assert client.get(reverse("core:staff_room_access")).status_code == 403
    response = client.post(
        reverse("core:staff_add_room_members", args=[conference.pk]),
        {"rows": f"1\tMe\t{student.email}"},
    )
    assert response.status_code == 403
    assert not RoomMember.objects.exists()


def test_removing_someone_keeps_a_booking_already_made(frozen, conference, listed):
    assert _reserve(listed, conference).ok
    RoomMember.objects.filter(room=conference).delete()
    assert Booking.objects.get(user=listed).status == Booking.Status.SCHEDULED
    assert not _reserve(listed, conference, hour=14).ok


# --- D-43: registering with an address from a room access list ----------------


def _register(institutional_id, email, name="นิสิต ปีสอง"):
    from core.services import identity

    return identity.start_registration(institutional_id=institutional_id, email=email, name=name)


def test_a_listed_gmail_may_register_but_waits_for_staff(frozen, conference):
    from core.services import identity

    RoomMember.objects.create(room=conference, email="second.year@gmail.com", name="นิสิต ปีสอง")
    result = _register("6800000235", "Second.Year@Gmail.com")
    user, _ = identity.verify_email(result.raw_token)
    user.refresh_from_db()
    assert user.email == "second.year@gmail.com"
    assert user.email_is_verified
    assert user.eligibility == user.Eligibility.PENDING
    assert not _reserve(user, conference).ok


def test_an_unlisted_gmail_is_still_refused(frozen, conference):
    from core.services.errors import OperationRejected

    with pytest.raises(OperationRejected) as refused:
        _register("6800000235", "stranger@gmail.com")
    assert refused.value.outcome.code == Code.DOMAIN_NOT_ALLOWED


def test_a_gmail_listed_for_a_retired_room_is_refused(frozen, conference):
    from core.services.errors import OperationRejected

    RoomMember.objects.create(room=conference, email="second.year@gmail.com")
    Room.objects.filter(pk=conference.pk).update(is_active=False)
    with pytest.raises(OperationRejected):
        _register("6800000235", "second.year@gmail.com")


def test_staff_see_the_listed_name_and_approve_then_the_student_books(client, frozen, conference, staff_user):
    from core.services import identity

    RoomMember.objects.create(room=conference, email="second.year@gmail.com", name="นางสาว ชื่อในรายชื่อ")
    result = _register("6800000235", "second.year@gmail.com", name="ชื่อที่พิมพ์เอง")
    user, _ = identity.verify_email(result.raw_token)

    client.force_login(staff_user)
    page = client.get(reverse("core:staff_today")).content.decode()
    assert "นางสาว ชื่อในรายชื่อ" in page
    assert "6800000235" in page

    response = client.post(
        reverse("core:staff_decide_eligibility", args=[user.pk]),
        {"decision": "APPROVED", "reason_choice": "room_list_checked", "reason": ""},
    )
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.eligibility == user.Eligibility.APPROVED
    assert user.eligibility_reason == "On a room access list; student ID checked by staff"
    assert _reserve(user, conference).ok
