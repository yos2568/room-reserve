"""Declared instrument at registration (D-30).

A student declares an instrument family when registering. The declaration opens
restricted rooms only once staff have approved the account, and a linked roster
row always overrides it. Everyone else still sees the room's availability.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AuditEvent, User
from core.services import availability, clock, instruments, slots
from core.services import identity as identity_service
from core.services.errors import Code, OperationRejected
from tests import factories, helpers

pytestmark = pytest.mark.django_db

TOMORROW = factories.bangkok(2026, 9, 15).date()


def register_payload(institutional_id: str, declared: str) -> dict:
    return {
        "name": "ทดสอบ ประกาศ",
        "institutional_id": institutional_id,
        "email": f"{institutional_id}@student.chula.ac.th",
        "declared_category": declared,
    }


# --- Registration ---------------------------------------------------------------


def test_registration_persists_declared_category(db, client):
    response = client.post(reverse("core:register"), register_payload("669900001", "PIANO"))

    assert response.status_code == 302
    account = User.objects.get(username="669900001")
    assert account.declared_category == "PIANO"
    assert account.eligibility == User.Eligibility.PENDING


def test_registration_without_declaration_leaves_it_blank(db, client):
    payload = register_payload("669900002", "")
    del payload["declared_category"]

    response = client.post(reverse("core:register"), payload)

    assert response.status_code == 302
    assert User.objects.get(username="669900002").declared_category == ""


def test_registration_rejects_unknown_category(db, client):
    response = client.post(reverse("core:register"), register_payload("669900003", "TUBA"))

    assert response.status_code == 400


# --- Who the declaration lets in --------------------------------------------------


def test_approved_declaration_can_reserve_restricted_room(frozen, db):
    student = factories.make_user(declared_category="PIANO")
    room303 = factories.make_restricted_room()

    outcome = helpers.advance_booking(student, room303, slots.slot_start_for(TOMORROW, 13))

    assert outcome.ok, outcome.code


def test_pending_declaration_cannot_reserve(frozen, db):
    student = factories.make_user(declared_category="PIANO", eligibility=User.Eligibility.PENDING)
    room303 = factories.make_restricted_room()

    outcome = helpers.advance_booking(student, room303, slots.slot_start_for(TOMORROW, 13))

    # The account gate fires before the audience check; either way, no booking.
    assert not outcome.ok
    assert outcome.code in {Code.PENDING_APPROVAL, Code.NOT_ELIGIBLE, Code.ROOM_NOT_FOR_INSTRUMENT}


def test_unverified_declaration_cannot_book(frozen, db):
    student = factories.make_user(declared_category="PIANO", verified=False)
    room303 = factories.make_restricted_room()

    outcome = helpers.advance_booking(student, room303, slots.slot_start_for(TOMORROW, 13))

    assert not outcome.ok
    assert outcome.code == Code.EMAIL_UNVERIFIED


def test_non_piano_declaration_does_not_open_the_room(frozen, db):
    violinist = factories.make_user(declared_category="STRINGS")
    room303 = factories.make_restricted_room()

    assert instruments.category_for_user(violinist) == "STRINGS"
    assert not room303.may_be_reserved_by("STRINGS")

    outcome = helpers.advance_booking(violinist, room303, slots.slot_start_for(TOMORROW, 13))
    assert not outcome.ok
    assert outcome.code == Code.ROOM_NOT_FOR_INSTRUMENT


def test_roster_link_overrides_the_declaration(frozen, db):
    """A student who declared piano but rosters as strings is a strings student."""
    student = factories.make_user(declared_category="PIANO")
    factories.make_roster_entry(student, instrument="ไวโอลิน")  # links; STRINGS
    room303 = factories.make_restricted_room()

    assert instruments.category_for_user(student) == "STRINGS"
    outcome = helpers.advance_booking(student, room303, slots.slot_start_for(TOMORROW, 13))
    assert outcome.code == Code.ROOM_NOT_FOR_INSTRUMENT


# --- The grid ---------------------------------------------------------------------


def test_declared_piano_student_sees_room_303_as_bookable_on_grid(frozen, db):
    student = factories.make_user(declared_category="PIANO")
    factories.make_restricted_room()

    grid = availability.public_grid(TOMORROW, clock.now(), user=student)

    row303 = next(row for row in grid["rows"] if row["room"].number == "303")
    states = {cell.state for cell in row303["cells"]}
    assert availability.SlotState.BOOKABLE in states
    assert availability.SlotState.RESTRICTED not in states


def test_declared_other_student_still_sees_the_room_as_restricted(frozen, db):
    student = factories.make_user(declared_category="STRINGS")
    factories.make_restricted_room()

    grid = availability.public_grid(TOMORROW, clock.now(), user=student)

    row303 = next(row for row in grid["rows"] if row["room"].number == "303")
    states = {cell.state for cell in row303["cells"]}
    assert availability.SlotState.RESTRICTED in states
    assert availability.SlotState.BOOKABLE not in states


# --- The staff control ------------------------------------------------------------


def test_staff_can_set_declared_category_and_it_is_audited(frozen, staff_user, student):
    updated = identity_service.set_declared_category(
        user=student, declared_category="PERCUSSION", actor=staff_user
    )

    assert updated.declared_category == "PERCUSSION"
    assert AuditEvent.objects.filter(action="user.declared_category_set").exists()


def test_staff_can_clear_declared_category(frozen, staff_user, student):
    student.declared_category = "PIANO"
    student.save(update_fields=["declared_category"])

    updated = identity_service.set_declared_category(user=student, declared_category="", actor=staff_user)

    assert updated.declared_category == ""


def test_staff_view_rejects_unknown_category(frozen, staff_user, student):
    with pytest.raises(OperationRejected):
        identity_service.set_declared_category(user=student, declared_category="TUBA", actor=staff_user)
