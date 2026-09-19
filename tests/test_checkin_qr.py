"""The QR check-in link carried by the confirmation email (D-31).

An advance reservation gets a secret token; the confirmation and reminder emails
embed a QR code for that token's landing page. Scanning it brings the owner to a
deliberate confirm button — the same check-in the rest of the app performs, under
the same window rules. The token binds the link to the booking, sign-in binds the
action to the owner, and none of it is a proof of presence.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Booking, Notification, User
from core.services import clock, notifications, slots
from tests import factories, helpers

pytestmark = pytest.django_db if False else pytest.mark.django_db

TOMORROW = factories.bangkok(2026, 9, 15).date()
_counter = {"n": 0}


def notification_stub(recipient: User, kind: str, payload: dict) -> Notification:
    _counter["n"] += 1
    return Notification.objects.create(
        kind=kind,
        recipient=recipient,
        payload=payload,
        dedupe_key=f"test-qr-{_counter['n']}",
    )


# --- Tokens and emails -----------------------------------------------------------


def test_advance_booking_gets_a_unique_token(frozen, student, rooms):
    first = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TOMORROW, 13))
    second = helpers.advance_booking(student, rooms[1], slots.slot_start_for(TOMORROW, 16))

    b1 = Booking.objects.get(pk=first.data["booking_id"])
    b2 = Booking.objects.get(pk=second.data["booking_id"])
    assert b1.checkin_token and b2.checkin_token
    assert b1.checkin_token != b2.checkin_token


def test_walk_in_has_no_token(frozen, student, rooms):
    outcome = helpers.use_now(student, rooms[0])

    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    assert booking.checkin_token is None


def test_confirmation_context_carries_the_checkin_path(frozen, student, rooms):
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TOMORROW, 13))
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    notification = notification_stub(
        student, kind=notifications.KIND_BOOKING_CONFIRMATION, payload={"booking_id": booking.pk}
    )

    context = notifications.revalidate(notification, clock.now())

    assert context["checkin_token"] == booking.checkin_token


def test_confirmation_email_contains_link_and_qr(frozen, student, rooms):
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TOMORROW, 13))
    booking = Booking.objects.get(pk=outcome.data["booking_id"])
    notification = notification_stub(
        student, kind=notifications.KIND_BOOKING_CONFIRMATION, payload={"booking_id": booking.pk}
    )

    context = notifications.revalidate(notification, clock.now())
    subject, text = notifications.render(notification, context)
    html = notifications.render_html(notification, context)

    path = reverse("core:checkin_qr", args=[booking.checkin_token])
    assert path in text
    assert html is not None
    assert path in html
    assert "data:image/png;base64," in html
    assert "qr_data_uri" not in html  # the placeholder is fully substituted


def test_other_kinds_get_no_html(frozen, student, rooms):
    notification = notification_stub(
        student, kind=notifications.KIND_ELIGIBILITY_DECISION, payload={"decision": "APPROVED"}
    )

    context = notifications.revalidate(notification, clock.now())
    assert notifications.render_html(notification, context) is None


# --- The landing page -------------------------------------------------------------


def test_anonymous_scan_is_sent_to_sign_in(client, db):
    response = client.get("/th/check-in/some-token/")
    assert response.status_code == 302
    assert "/login/" in response.url


def test_token_of_another_account_is_invisible(client, db, student, other_student, rooms):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client.force_login(other_student)

    response = client.get(f"/th/check-in/{booking.checkin_token}/")

    assert response.status_code == 404


def test_owner_sees_not_open_before_the_hour(client, db, student, rooms):
    from core.services import clock as clock_service

    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client.force_login(student)
    with clock_service.frozen_clock(factories.bangkok(2026, 9, 15, 10, 0)):
        response = client.get(f"/th/check-in/{booking.checkin_token}/")

    assert response.status_code == 200
    assert response.context["window_state"] == "early"


def test_owner_confirms_within_the_window(frozen, student, rooms, client):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client.force_login(student)
    from core.services import clock as clock_service

    with clock_service.frozen_clock(factories.bangkok(2026, 9, 15, 13, 5)):
        response = client.post(f"/th/check-in/{booking.checkin_token}/")

    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == Booking.Status.IN_USE


def test_double_confirm_reports_already_checked_in(frozen, student, rooms, client):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13, status=Booking.Status.IN_USE)
    client.force_login(student)
    from core.services import clock as clock_service

    with clock_service.frozen_clock(factories.bangkok(2026, 9, 15, 13, 5)):
        response = client.post(f"/th/check-in/{booking.checkin_token}/")

    assert response.status_code == 409


def test_confirm_after_the_deadline_refuses(frozen, student, rooms, client):
    booking = factories.make_booking(student, rooms[0], day=TOMORROW, hour=13)
    client.force_login(student)
    from core.services import clock as clock_service

    with clock_service.frozen_clock(factories.bangkok(2026, 9, 15, 13, 20)):
        response = client.post(f"/th/check-in/{booking.checkin_token}/")

    assert response.status_code == 409
    booking.refresh_from_db()
    # The protocol's reconciliation runs first: an expired reservation is a
    # no-show — room released, strike recorded — before the refusal is shown.
    assert booking.status == Booking.Status.NO_SHOW
