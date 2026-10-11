"""Regression coverage for onboarding continuity and audited staff decisions."""

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.urls import reverse

from core.models import AuditEvent, User
from core.services.policy import create_policy_version
from tests import factories

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("language", ["th", "en"])
def test_registration_shows_the_active_policy_domain(client, language):
    create_policy_version(snapshot={"institution_email_domain": "music.example.edu"})
    response = client.get(f"/{language}/register/")
    assert response.status_code == 200
    assert "music.example.edu" in response.content.decode()


def test_onboarding_retains_booking_through_registration_and_failed_login(client):
    target = "/en/book/1/20261012T0800/"
    response = client.get(reverse("core:register"), {"next": target})
    assert response.context["next"] == target
    response = client.post(
        reverse("core:register"),
        {
            "name": "Synthetic student",
            "institutional_id": "66001009999",
            "email": "onboarding@student.chula.ac.th",
            "next": target,
        },
    )
    assert response.status_code == 302
    response = client.get(response.url)
    assert response.context["next"] == target
    user = User.objects.get(username="66001009999")
    user.set_password("synthetic-password-123")
    user.save(update_fields=["password"])
    response = client.post(
        reverse("core:login"),
        {
            "institutional_id": user.username,
            "password": "wrong",
            "next": target,
        },
    )
    assert response.status_code == 401
    assert response.context["next"] == target
    response = client.post(
        reverse("core:login"),
        {
            "institutional_id": user.username,
            "password": "synthetic-password-123",
        },
    )
    assert response.url == target
    assert "onboarding_next" not in client.session


@pytest.mark.parametrize("candidate", ["https://evil.example/", "//evil.example/", "javascript:alert(1)"])
def test_onboarding_never_restores_an_external_destination(client, candidate):
    response = client.get(reverse("core:register"), {"next": candidate})
    assert response.context["next"] == reverse("core:my_bookings")
    assert "evil.example" not in client.session["onboarding_next"]


@pytest.mark.parametrize(
    "decision,choice,expected",
    [
        (User.Eligibility.APPROVED, "identity_confirmed", "Student identity confirmed by staff"),
        (User.Eligibility.REJECTED, "not_eligible", "Not eligible for department access"),
        (User.Eligibility.APPROVED, "other", "Confirmed with office"),
    ],
)
def test_staff_choice_is_saved_in_the_account_and_audit(
    client, frozen, staff_user, decision, choice, expected
):
    candidate = factories.make_user(eligibility=User.Eligibility.PENDING)
    client.force_login(staff_user)
    response = client.post(
        reverse("core:staff_decide_eligibility", args=[candidate.pk]),
        {
            "decision": decision,
            "reason_choice": choice,
            "reason": "Confirmed with office" if choice == "other" else "",
        },
    )
    assert response.status_code == 302
    candidate.refresh_from_db()
    assert candidate.eligibility == decision
    assert candidate.eligibility_reason == expected
    event = AuditEvent.objects.get(action=f"eligibility.{decision.lower()}", entity_id=str(candidate.pk))
    assert event.reason == expected


@pytest.mark.parametrize("choice,details", [("other", ""), ("not_eligible", "Preserve this note"), ("", "")])
def test_invalid_approval_preserves_fields_without_changing_user(client, frozen, staff_user, choice, details):
    candidate = factories.make_user(eligibility=User.Eligibility.PENDING)
    client.force_login(staff_user)
    response = client.post(
        reverse("core:staff_decide_eligibility", args=[candidate.pk]),
        {
            "decision": "APPROVED",
            "reason_choice": choice,
            "reason": details,
        },
    )
    assert response.status_code == 400
    candidate.refresh_from_db()
    assert candidate.eligibility == User.Eligibility.PENDING
    assert not AuditEvent.objects.filter(action="eligibility.approved", entity_id=str(candidate.pk)).exists()
    row = next(user for user in response.context["pending"] if user.pk == candidate.pk)
    assert row.approval_form.errors
    assert row.approval_form["reason"].value() == details


def test_login_registration_link_carries_the_selected_booking(client):
    target = "/en/book/1/20261012T0800/"
    response = client.get(reverse("core:login"), {"next": target})
    links = re.findall(r'href="([^"]+)"', response.content.decode())
    destinations = [urlsplit(html.unescape(link)) for link in links]
    assert any(
        url.path == reverse("core:register") and parse_qs(url.query).get("next") == [target]
        for url in destinations
    )
