"""Faculty accounts (D-34): invited by staff, sign in with email or ID, read-only.

A teacher account exists so faculty can see schedules today and grow into
permissions later. The account gate states the read-only reason in one place, so
every mutation refuses and every button hides without special cases. Roles are
never self-selected: only staff invitations create teacher accounts.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AuditEvent, User
from core.services import clock, slots
from core.services import identity as identity_service
from core.services.errors import Code
from tests import factories, helpers

pytestmark = pytest.mark.django_db

TOMORROW = factories.bangkok(2026, 9, 15).date()


def teacher_user() -> User:
    """A faculty account as an invitation leaves it: approved, verified, read-only."""
    return factories.make_user(is_teacher=True)


# --- The invitation ---------------------------------------------------------------


def test_staff_can_invite_a_teacher(frozen, staff_user):
    user, _, _ = identity_service.invite_account(
        institutional_id="669980001",
        email="advisor.c@chula.ac.th",
        name="อาจารย์ทดสอบ",
        actor=staff_user,
        staff=False,
        teacher=True,
    )

    assert user.is_teacher
    assert not user.is_operational_staff
    assert user.eligibility == User.Eligibility.APPROVED
    assert user.email_verified_at is not None
    event = AuditEvent.objects.filter(action="account.invited").latest("occurred_at")
    assert event.changes["teacher"] is True


def test_faculty_invitation_can_use_email_without_student_id(frozen, staff_user):
    user, _, _ = identity_service.invite_account(
        institutional_id="",
        email="faculty.without.id@chula.ac.th",
        name="อาจารย์ไม่มีรหัสนิสิต",
        actor=staff_user,
        staff=False,
        teacher=True,
    )

    assert user.username == user.email == "faculty.without.id@chula.ac.th"
    assert user.is_teacher
    assert user.declared_category == ""


def test_regular_staff_invitation_is_unchanged(frozen, staff_user):
    user, _, _ = identity_service.invite_account(
        institutional_id="669980002",
        email="office.c@chula.ac.th",
        name="เจ้าหน้าที่ทดสอบ",
        actor=staff_user,
        staff=True,
    )

    assert user.is_operational_staff
    assert not user.is_teacher


# --- Sign-in with email or ID ------------------------------------------------------


def test_teacher_signs_in_with_email(client, db):
    teacher = teacher_user()
    teacher.set_password("teacher-pass-1")
    teacher.save(update_fields=["password"])

    response = client.post(
        reverse("core:login"),
        {"institutional_id": teacher.email, "password": "teacher-pass-1"},
    )

    assert response.status_code == 302
    assert client.session.get("_auth_user_id") == str(teacher.pk)


def test_student_can_still_sign_in_with_their_id(client, db):
    student = factories.make_user()
    student.set_password("student-pass-1")
    student.save(update_fields=["password"])

    response = client.post(
        reverse("core:login"),
        {"institutional_id": student.username, "password": "student-pass-1"},
    )

    assert response.status_code == 302
    assert client.session.get("_auth_user_id") == str(student.pk)


# --- Read-only, everywhere ----------------------------------------------------------


def test_teacher_cannot_reserve(frozen, db):
    from core.services.errors import OperationRejected

    teacher = teacher_user()
    room = factories.make_rooms(1)[0]
    slot = slots.slot_start_for(TOMORROW, 13)

    outcome = helpers.advance_booking(teacher, room, slot)
    assert not outcome.ok
    assert outcome.code == Code.TEACHER_READ_ONLY

    with pytest.raises(OperationRejected) as excinfo:
        from core.services.eligibility import assert_can_operate

        assert_can_operate(teacher, clock.now())
    assert excinfo.value.outcome.code == Code.TEACHER_READ_ONLY


def test_teacher_cannot_walk_in(frozen, db):
    factories.make_rooms(1)

    outcome = helpers.use_now(teacher_user(), factories.make_rooms(1)[0])
    assert not outcome.ok


def test_teacher_sees_schedules_without_action_buttons(frozen, db):
    from core.services import suggest

    factories.make_rooms(1)
    today = factories.bangkok(2026, 9, 14).date()

    result = suggest.suggestions(today, clock.now(), user=teacher_user())

    assert result.free_now  # availability is fully visible
    assert not result.can_act  # but no buttons: read-only


def test_teacher_grid_card_states_the_read_only_reason(frozen, db):
    from django.test import Client

    teacher = teacher_user()
    teacher.set_password("teacher-pass-1")
    teacher.save(update_fields=["password"])
    client = Client()
    client.force_login(teacher)

    response = client.get("/th/")

    assert response.status_code == 200
    assert "อาจารย์".encode() in response.content or b"Teacher account" in response.content
