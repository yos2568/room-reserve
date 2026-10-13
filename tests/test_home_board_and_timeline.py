"""The home page's "right now" board and the staff booking timeline (D-44).

BASE_MOMENT is Monday 14 September 2026, 10:40 in Bangkok; room 301 has a
class on Mondays 13:00–15:00.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.urls import reverse

from core.models import Booking, Room
from core.services import clock, slots
from core.services import rooms as rooms_service
from tests import factories, helpers

pytestmark = pytest.mark.django_db

MONDAY = date(2026, 9, 14)


@pytest.fixture
def all_rooms(db):
    rooms_service.ensure_rooms()
    return {room.number: room for room in Room.objects.all()}


def _card(response, number):
    return next(card for card in response.context["board"]["cards"] if card.room.number == number)


def test_home_leads_with_every_rooms_state_right_now(client, frozen, all_rooms, student):
    with clock.frozen_clock(factories.bangkok(2026, 9, 14, 9, 0)):
        assert helpers.advance_booking(student, all_rooms["2"], slots.slot_start_for(MONDAY, 10)).ok

    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    board = response.context["board"]
    assert len(board["cards"]) == Room.objects.filter(is_active=True).count()

    free = _card(response, "1")
    assert free.now == "free"
    assert free.now_cell.can_use_now
    assert reverse("core:use_now", args=[free.room.pk, free.now_cell.slot_key]) in response.content.decode()

    held = _card(response, "2")
    assert held.now == "busy"
    assert held.next_free.hour == 11

    classroom = _card(response, "301")
    assert classroom.kind == "teaching"
    assert [slot["kind"] for slot in classroom.strip][5:7] == ["class", "class"]  # 13:00 and 14:00
    assert board["counts"]["busy"] == 1


def test_the_free_filter_keeps_only_free_rooms(client, frozen, all_rooms, student):
    with clock.frozen_clock(factories.bangkok(2026, 9, 14, 9, 0)):
        assert helpers.advance_booking(student, all_rooms["2"], slots.slot_start_for(MONDAY, 10)).ok
    response = client.get(reverse("core:home"), {"show": "free"})
    shown = [card.room.number for _, cards in response.context["board_groups"] for card in cards]
    assert "2" not in shown
    assert "1" in shown


def test_the_board_is_only_for_todays_unfiltered_view(client, frozen, all_rooms):
    assert client.get(reverse("core:home"), {"date": "2026-09-15"}).context.get("board") is None
    assert client.get(reverse("core:home"), {"room": "1"}).context.get("board") is None


def test_there_is_no_board_outside_opening_hours(client, all_rooms):
    with clock.frozen_clock(factories.bangkok(2026, 9, 14, 21, 0)):
        response = client.get(reverse("core:home"))
    assert response.status_code == 200
    assert response.context.get("board") is None


def test_the_timeline_shows_bookings_classes_and_bookable_gaps(
    client, frozen, all_rooms, staff_user, student
):
    assert helpers.advance_booking(student, all_rooms["3"], slots.slot_start_for(MONDAY, 12)).ok
    client.force_login(staff_user)
    response = client.get(reverse("core:staff_timeline"), {"date": MONDAY.isoformat()})
    assert response.status_code == 200
    rows = {row.room.number: row for _, group in response.context["timeline"]["groups"] for row in group}

    booked = rows["3"].bars[0]
    assert (booked.kind, booked.start_hour, booked.booking.user) == ("booked", 12, student)
    assert any(bar.kind == "class" and bar.start_hour == 13 for bar in rows["301"].bars)
    gap_hours = [hour for hour, _ in rows["3"].gaps]
    assert 12 not in gap_hours and 10 not in gap_hours and 11 in gap_hours  # booked, started, free

    page = response.content.decode()
    assert student.display_name in page
    assert f"{reverse('core:staff_book_room')}?room={all_rooms['3'].pk}&date=2026-09-14&start_hour=11" in page


def test_selecting_a_request_offers_approval_that_returns_to_the_timeline(
    client, frozen, all_rooms, staff_user, student
):
    assert helpers.advance_booking(student, all_rooms["304"], slots.slot_start_for(MONDAY, 16)).ok
    request = Booking.objects.get(user=student)
    client.force_login(staff_user)
    url = reverse("core:staff_timeline")
    page = client.get(url, {"date": MONDAY.isoformat(), "booking": request.pk})
    assert page.context["selected"] == request
    assert reverse("core:staff_decide_booking_approval", args=[request.pk]) in page.content.decode()

    back = f"{url}?date={MONDAY.isoformat()}&booking={request.pk}"
    response = client.post(
        reverse("core:staff_decide_booking_approval", args=[request.pk]),
        {"decision": "APPROVED", "note": "", "next": back},
    )
    assert response.url == back
    request.refresh_from_db()
    assert request.status == Booking.Status.SCHEDULED


def test_an_empty_hour_prefills_book_a_room(client, frozen, all_rooms, staff_user):
    client.force_login(staff_user)
    response = client.get(
        reverse("core:staff_book_room"), {"room": all_rooms["6"].pk, "date": "2026-09-14", "start_hour": 17}
    )
    assert response.context["values"] == {
        "room": all_rooms["6"].pk,
        "date": "2026-09-14",
        "start_hour": 17,
        "end_hour": 18,
    }


def test_students_cannot_open_the_timeline(client, frozen, all_rooms, student):
    client.force_login(student)
    assert client.get(reverse("core:staff_timeline")).status_code == 403
