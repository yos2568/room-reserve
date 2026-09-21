"""Complaint delivery: private, fixed recipient, retryable and duplicate-safe."""

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from core.models import Notification
from core.services import notifications

pytestmark = pytest.mark.django_db


@pytest.fixture
def signed_in(client, student):
    cache.clear()
    client.force_login(student)
    return client


def submission(client, **extra):
    response = client.get(reverse("core:complaint"))
    return {
        "submission": response.context["form"].initial["submission"],
        "subject": "ปัญหาห้องซ้อม",
        "message": "The piano in the practice room needs repair.",
        **extra,
    }


def test_requires_login_and_csrf(client, student):
    assert client.get(reverse("core:complaint")).status_code == 302
    protected = Client(enforce_csrf_checks=True)
    protected.force_login(student)
    assert protected.post(reverse("core:complaint"), submission(protected)).status_code == 403
    assert not Notification.objects.exists()


def test_fixed_destination_reply_address_and_duplicate_submission(signed_in, student):
    data = submission(signed_in, recipient_email="attacker@example.org", reply_email="fake@example.org")
    for _ in range(2):
        assert signed_in.post(reverse("core:complaint"), data).status_code == 302
    note = Notification.objects.get(kind="complaint")
    assert note.recipient_email == "Sitanun.S@chula.ac.th"
    assert note.payload["reply_email"] == student.email
    assert notifications.drain()["sent"] == 1
    assert mail.outbox[0].to == ["Sitanun.S@chula.ac.th"]
    assert mail.outbox[0].reply_to == [student.email]
    assert data["message"] in mail.outbox[0].body
    assert note.payload["reference"] in mail.outbox[0].body


def test_invalid_form_and_token_enqueue_nothing(signed_in):
    assert signed_in.post(reverse("core:complaint"), submission(signed_in, message="")).status_code == 400
    assert signed_in.post(reverse("core:complaint"), submission(signed_in, submission="tampered")).status_code == 400
    assert not Notification.objects.exists()


def test_submission_token_is_bound_to_account(signed_in, other_student):
    data = submission(signed_in)
    signed_in.force_login(other_student)
    assert signed_in.post(reverse("core:complaint"), data).status_code == 400
    assert not Notification.objects.exists()


def test_rate_limit(signed_in):
    for _ in range(3):
        assert signed_in.post(reverse("core:complaint"), submission(signed_in)).status_code == 302
    response = signed_in.post(reverse("core:complaint"), submission(signed_in))
    assert response.status_code == 429
    assert response["Retry-After"] == "3600"
    assert Notification.objects.count() == 3


def test_failed_mail_remains_retryable(signed_in):
    signed_in.post(reverse("core:complaint"), submission(signed_in))
    with override_settings(EMAIL_BACKEND="tests.test_outbox.BrokenBackend"):
        assert notifications.drain()["failed"] == 1
    note = Notification.objects.get(kind="complaint")
    assert note.status == Notification.Status.PENDING
    assert note.attempts == 1
    assert note.last_error


def test_optional_room_and_message_escaping(signed_in, rooms):
    data = submission(signed_in, room=rooms[0].pk, subject="<script>alert(1)</script>", message="")
    response = signed_in.post(reverse("core:complaint"), data)
    assert b"<script>alert(1)</script>" not in response.content
    data["message"] = "The equipment needs checking."
    signed_in.post(reverse("core:complaint"), data)
    assert Notification.objects.get(kind="complaint").payload["room"] == str(rooms[0])
