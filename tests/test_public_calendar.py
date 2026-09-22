"""The public timetable opens through 31 December 2030.

Booking stays inside the horizon. A date past the last published day snaps back
to today instead of opening.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.test import Client

from core.services import slots

pytestmark = pytest.mark.django_db


def test_month_page_stops_on_31_december_2030(frozen):
    today = date(2026, 9, 14)
    page = slots.month_page(date(2030, 12, 31), today)

    assert page["month_start"] == date(2030, 12, 1)
    assert page["next_month"] is None
    assert page["prev_month"] == date(2030, 11, 1)
    assert page["last"] == date(2030, 12, 31)
    selected = [day for week in page["weeks"] for day in week if day["is_selected"]]
    assert selected[0]["date"] == date(2030, 12, 31)
    assert selected[0]["selectable"] is True


def test_the_grid_opens_31_december_2030_and_refuses_the_next_day(frozen, rooms):
    client = Client()

    opened = client.get("/en/", {"date": "2030-12-31"})
    assert opened.status_code == 200
    body = opened.content.decode()
    assert "December 2030" in body
    assert 'aria-current="date"' in body
    assert "2030-12-31" in body
    assert 'max="2030-12-31"' in body
    assert "Too far ahead" in body
    assert "/book/" not in body

    snapped = client.get("/en/", {"date": "2031-01-01"})
    snapped_body = snapped.content.decode()
    assert "September 2026" in snapped_body
    assert "January 2031" not in snapped_body


def test_choose_room_and_week_view_reach_the_last_date(frozen, rooms):
    client = Client()

    board = client.get("/en/choose-room/", {"date": "2030-12-31", "time": "10"})
    assert board.status_code == 200
    assert "2030-12-31" in board.content.decode()

    week = client.get("/en/week/", {"date": "2030-12-31"})
    week_body = week.content.decode()
    assert week.status_code == 200
    assert "December 2030" in week_body
    assert "Next week" not in week_body
