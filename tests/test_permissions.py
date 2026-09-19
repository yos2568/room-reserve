"""Permissions, CSRF and redirect safety (A21).

V3 section 8 keeps three roles apart: students, the ten operational staff
accounts, and the technical maintainer. Only the maintainer is a superuser, and
the staff surface offers no route that can create one. Everything here is
attacked from the outside - with the real URL map and the real middleware stack -
because permission bugs live in the wiring, not in the service bodies.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from core.models import User, Violation
from core.services import csvio, identity, maintenance, slots
from core.services.errors import Code
from core.services.protocol import run_operation
from tests import factories, helpers

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()
PASSWORD = "synthetic-password-123"

STAFF_GET_ROUTES = [
    "core:staff_today",
    "core:staff_users",
    "core:staff_closures",
    "core:staff_calendar",
    "core:staff_incidents",
    "core:staff_roster",
    "core:staff_roster_export",
    "core:staff_invitations",
    "core:staff_audit",
    "core:staff_outbox",
    "core:staff_stats",
    "core:staff_stats_export",
    "core:staff_posters",
    "core:staff_poster_sheet",
    "core:staff_policy",
    "core:staff_quota_lookup",
    "core:staff_admin",
]

MAINTAINER_URL = "/maintainer/"


def target(hour: int = 11):
    return slots.slot_start_for(DAY, hour)


@pytest.fixture(autouse=True)
def clean_rate_limit_cache():
    """Rate-limit counters live in a process-wide cache; start each test clean."""
    cache.clear()
    yield
    cache.clear()


# --- Roles ---------------------------------------------------------------------


def test_operational_staff_are_not_superusers(db):
    staff = factories.make_user(is_operational_staff=True)

    assert staff.is_operational_staff is True
    assert staff.is_superuser is False
    assert staff.is_staff is False


def test_a_staff_invitation_cannot_grant_superuser(db):
    maintainer = factories.make_user(is_operational_staff=True)

    invited, _, _ = identity.invite_account(
        institutional_id="66002000001",
        email="staff2@student.chula.ac.th",
        name="เจ้าหน้าที่ สอง",
        actor=maintainer,
        staff=True,
    )

    assert invited.is_operational_staff is True
    assert invited.is_superuser is False, "no service path may create a second superuser"
    assert invited.is_staff is False


def test_superuser_requires_the_explicit_manager_call(db):
    """is_operational_staff=True is not enough to become a superuser."""
    user = User.objects.create_user(
        username="66002000002",
        email="nearly@student.chula.ac.th",
        password=PASSWORD,
        is_operational_staff=True,
    )
    assert user.is_superuser is False

    with pytest.raises(ValueError):
        User.objects.create_superuser(
            username="66002000003",
            email="bad@student.chula.ac.th",
            password=PASSWORD,
            is_superuser=False,
        )


def test_staff_cannot_deactivate_the_maintainer_account(frozen):
    maintainer = User.objects.create_superuser(
        username="66002000004", email="maintain@student.chula.ac.th", password=PASSWORD
    )
    staff = factories.make_user(is_operational_staff=True)

    def body(ctx):
        return {"count": len(maintenance.deactivate_account(ctx, user=maintainer, reason="mistake"))}

    outcome = run_operation(
        actor=staff,
        operation="staff_deactivate_account",
        payload={"user": maintainer.pk},
        body=helpers._body(body),
    )
    assert not outcome.ok
    assert outcome.code == Code.INVALID_INPUT

    maintainer.refresh_from_db()
    assert maintainer.is_active is True


# --- Staff surface -------------------------------------------------------------


@pytest.mark.parametrize("route", STAFF_GET_ROUTES)
def test_a_student_is_refused_every_staff_route(frozen, student, route):
    client = Client()
    client.force_login(student)

    response = client.get(reverse(route))
    assert response.status_code == 403, f"{route} returned {response.status_code}"


@pytest.mark.parametrize("route", STAFF_GET_ROUTES)
def test_an_anonymous_visitor_is_sent_to_login(frozen, route):
    response = Client().get(reverse(route))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_operational_staff_can_reach_the_staff_surface(frozen, staff_user):
    client = Client()
    client.force_login(staff_user)
    assert client.get(reverse("core:staff_today")).status_code == 200


def test_operational_staff_cannot_reach_the_maintainer_admin(frozen, staff_user):
    client = Client()
    client.force_login(staff_user)

    response = client.get(MAINTAINER_URL)
    assert response.status_code == 302, "the admin is not a staff surface"
    assert "login" in response["Location"]


def test_a_student_posting_to_a_staff_action_changes_nothing(frozen, student, other_student):
    client = Client()
    client.force_login(student)

    response = client.post(
        reverse("core:staff_record_violation", args=[other_student.pk]),
        {"user": other_student.pk, "note": "forged by a student"},
    )
    assert response.status_code == 403
    assert Violation.objects.count() == 0


# --- Other students' data ------------------------------------------------------


def test_a_student_cannot_cancel_another_students_booking(frozen, student, other_student, rooms):
    booking = factories.make_booking(other_student, rooms[0], slot_start=target(11))

    client = Client()
    client.force_login(student)
    response = client.post(reverse("core:cancel_booking", args=[booking.pk]), {"reason": "not mine"})

    assert response.status_code == 404, "existence of another account's booking is not confirmed"
    booking.refresh_from_db()
    assert booking.status == "SCHEDULED"


def test_a_student_cannot_check_in_another_students_booking(frozen, student, other_student, rooms):
    booking = factories.make_booking(other_student, rooms[0], slot_start=target(11))

    client = Client()
    client.force_login(student)
    response = client.post(reverse("core:check_in", args=[booking.pk]))

    assert response.status_code == 404
    booking.refresh_from_db()
    assert booking.status == "SCHEDULED"


def test_a_student_cannot_export_staff_data(frozen, student):
    client = Client()
    client.force_login(student)

    assert client.get(reverse("core:staff_roster_export")).status_code == 403
    assert client.get(reverse("core:staff_stats_export")).status_code == 403


# --- CSRF and redirects --------------------------------------------------------


def test_csrf_is_enforced_on_every_student_mutation(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=target(11))

    client = Client(enforce_csrf_checks=True)
    client.force_login(student)

    response = client.post(reverse("core:cancel_booking", args=[booking.pk]), {"reason": "x"})
    assert response.status_code == 403, "a token-less POST must be refused"

    booking.refresh_from_db()
    assert booking.status == "SCHEDULED"


def test_csrf_is_enforced_on_staff_mutations(frozen, staff_user, student):
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff_user)

    response = client.post(
        reverse("core:staff_record_violation", args=[student.pk]),
        {"user": student.pk, "note": "no token"},
    )
    assert response.status_code == 403
    assert Violation.objects.count() == 0


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/phish",
        "//evil.example/phish",
        "http://evil.example",
    ],
)
def test_login_next_cannot_redirect_off_host(frozen, student, hostile):
    response = Client().post(
        reverse("core:login"),
        {"institutional_id": student.username, "password": PASSWORD, "next": hostile},
    )

    assert response.status_code == 302
    location = response["Location"]
    assert "evil.example" not in location, location
    assert location == reverse("core:my_bookings")


def test_login_next_accepts_a_local_path(frozen, student):
    response = Client().post(
        reverse("core:login"),
        {
            "institutional_id": student.username,
            "password": PASSWORD,
            "next": reverse("core:my_bookings"),
        },
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("core:my_bookings")


def test_login_accepts_the_id_and_the_account_email(frozen, student):
    """One field, two identifiers (D-34): the ID for students, the account's
    email for faculty. A password is still required either way, and an email
    that belongs to nobody fails exactly like a wrong password."""
    client = Client()

    by_id = client.post(
        reverse("core:login"),
        {"institutional_id": student.username, "password": PASSWORD},
    )
    assert by_id.status_code == 302

    client.logout()
    by_email = client.post(
        reverse("core:login"),
        {"institutional_id": student.email, "password": PASSWORD},
    )
    assert by_email.status_code == 302

    client.logout()
    unknown = client.post(
        reverse("core:login"),
        {"institutional_id": "nobody@student.chula.ac.th", "password": PASSWORD},
    )
    assert unknown.status_code == 401


def test_repeated_failed_logins_are_rate_limited(frozen, student):
    client = Client()
    payload = {"institutional_id": student.username, "password": "wrong-password"}

    for _ in range(8):
        assert client.post(reverse("core:login"), payload).status_code == 401

    throttled = client.post(reverse("core:login"), payload)
    assert throttled.status_code == 429

    # The correct password is also refused while the window is open: the limit is
    # on the identifier, not on the guess.
    assert (
        client.post(
            reverse("core:login"),
            {"institutional_id": student.username, "password": PASSWORD},
        ).status_code
        == 429
    )


def test_correct_credentials_are_not_blocked_by_another_accounts_attempts(frozen, student, other_student):
    """A campus address is shared, so one careless user must not lock everyone out."""
    client = Client()
    for _ in range(8):
        client.post(reverse("core:login"), {"institutional_id": student.username, "password": "nope"})
    assert (
        client.post(
            reverse("core:login"), {"institutional_id": student.username, "password": PASSWORD}
        ).status_code
        == 429
    )

    fresh = Client()
    response = fresh.post(
        reverse("core:login"),
        {"institutional_id": other_student.username, "password": PASSWORD},
    )
    assert response.status_code == 302


# --- Export safety -------------------------------------------------------------


def test_csv_export_neutralises_formula_payloads(db):
    hostile_name = '=HYPERLINK("http://evil.example","click")'
    body = csvio.write_csv(["name", "note"], [[hostile_name, "@SUM(1+1)"]])

    assert "'=HYPERLINK" in body
    assert "'@SUM" in body
    assert "\n=HYPERLINK" not in body


def test_roster_export_of_a_hostile_name_is_safe(frozen, staff_user):
    factories.make_roster_entry(
        institutional_id="66002000005",
        email="hostile@student.chula.ac.th",
        name_th="=cmd|'/c calc'!A0",
    )

    client = Client()
    client.force_login(staff_user)
    response = client.get(reverse("core:staff_roster_export"))

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "'=cmd" in body


def test_reason_is_required_for_staff_mutations(frozen, staff_user, student):
    """A mutation without a recorded reason is refused, not silently applied."""
    client = Client()
    client.force_login(staff_user)

    response = client.post(
        reverse("core:staff_record_violation", args=[student.pk]),
        {"user": student.pk, "note": "   "},
    )
    # Staff forms answer with a redirect plus a message rather than a status code.
    assert response.status_code == 302
    assert Violation.objects.count() == 0


def test_a_student_cannot_promote_themselves_through_a_forged_post(frozen, student):
    client = Client()
    client.force_login(student)
    for route in ("core:staff_record_violation", "core:staff_decide_eligibility"):
        args = [student.pk]
        response = client.post(
            reverse(route, args=args),
            {"user": student.pk, "decision": "APPROVED", "is_superuser": "on", "is_operational_staff": "on"},
        )
        assert response.status_code == 403

    student.refresh_from_db()
    assert student.is_superuser is False
    assert student.is_operational_staff is False


def test_an_expired_session_does_not_grant_staff_access(frozen, staff_user):
    """Re-authentication is required; a stale cookie is not a permission."""
    client = Client()
    client.force_login(staff_user)
    session = client.session
    session.set_expiry(timedelta(seconds=-1))
    session.save()

    response = client.get(reverse("core:staff_today"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_staff_operations_record_the_authenticated_actor_not_a_posted_id(frozen, staff_user):
    """The actor is request.user; a client-supplied id is never trusted."""
    from core.models import AuditEvent

    client = Client()
    client.force_login(staff_user)
    victim = factories.make_user()
    impostor = factories.make_user()

    client.post(
        reverse("core:staff_record_violation", args=[victim.pk]),
        {"user": victim.pk, "note": "forged actor", "actor": impostor.pk},
    )

    violation = Violation.objects.get()
    assert violation.actor_id == staff_user.pk
    event = AuditEvent.objects.filter(action="violation.recorded").first()
    assert event is not None
    assert event.actor_id == staff_user.pk
