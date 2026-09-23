"""The maintainer can hand a staff member a set-password link when email fails.

The link is shown once, never emailed, works once, and is limited to staff and
faculty accounts. Only a superuser may issue one.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from core.models import AuditEvent, Invitation, Notification
from core.services import identity
from core.services.errors import OperationRejected
from tests import factories

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Fresh-staff-pass-2026"


@pytest.fixture
def maintainer(db):
    return factories.make_user(is_superuser=True, is_staff=True)


@pytest.fixture
def staff_member(db):
    return factories.make_user(is_operational_staff=True)


def _issue(client, account):
    return client.post(reverse("core:staff_issue_password_link", args=[account.pk]))


def _link_path(body: str) -> str:
    match = re.search(r'value="https?://[^/"]+(/[^"]*/invite/[^"/]+/)"', body)
    assert match, "the page shows an absolute set-password link"
    return match.group(1)


def test_the_maintainer_gets_a_one_time_link_that_sets_the_password(maintainer, staff_member):
    client = Client()
    client.force_login(maintainer)

    response = _issue(client, staff_member)

    assert response.status_code == 200
    path = _link_path(response.content.decode())
    assert not Notification.objects.filter(recipient=staff_member).exists(), "nothing is emailed"

    visitor = Client()
    visitor.post(path, {"password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD})
    staff_member.refresh_from_db()
    assert staff_member.check_password(NEW_PASSWORD)

    again = visitor.get(path)
    assert again.status_code == 410, "the link works once"


def test_a_new_link_cancels_the_previous_one(maintainer, staff_member):
    client = Client()
    client.force_login(maintainer)

    first = _link_path(_issue(client, staff_member).content.decode())
    _issue(client, staff_member)

    assert Client().get(first).status_code == 410


def test_operational_staff_cannot_issue_links(staff_member):
    other = factories.make_user(is_operational_staff=True)
    client = Client()
    client.force_login(staff_member)

    response = _issue(client, other)

    assert response.status_code == 403
    assert not Invitation.objects.filter(user=other).exists()


def test_students_and_superusers_are_refused(maintainer):
    student = factories.make_user()
    other_admin = factories.make_user(is_superuser=True, is_staff=True)

    for account in (student, other_admin):
        with pytest.raises(OperationRejected):
            identity.issue_password_link(user=account, actor=maintainer)
    assert not Invitation.objects.filter(user__in=[student, other_admin]).exists()


def test_issuing_is_audited_without_the_token(maintainer, staff_member):
    client = Client()
    client.force_login(maintainer)

    path = _link_path(_issue(client, staff_member).content.decode())
    token = path.rstrip("/").rsplit("/", 1)[1]

    event = AuditEvent.objects.get(action="account.password_link_issued", entity_id=staff_member.pk)
    assert token not in str(event.changes)


def test_the_button_is_shown_to_the_maintainer_for_staff_only(maintainer, staff_member):
    student = factories.make_user()
    client = Client()
    client.force_login(maintainer)

    body = client.get(reverse("core:staff_users")).content.decode()

    assert reverse("core:staff_issue_password_link", args=[staff_member.pk]) in body
    assert reverse("core:staff_issue_password_link", args=[student.pk]) not in body
