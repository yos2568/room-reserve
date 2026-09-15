"""Transactional outbox: rollback, retry, leases and stale suppression (A22).

V3 section 10: an event row is inserted inside the state-change transaction, a
worker leases it, sends outside any lock, and retries with backoff. Delivery is
*at least once* - SMTP acceptance followed by a worker crash can still duplicate
a message, and the documentation says so rather than promising exactly-once.

Nothing here uses the frozen test clock for delivery timing. The outbox stamps
rows with ``timezone.now()`` at insert, so a test that drained at a frozen 2026
instant would find nothing due and prove nothing.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.db import transaction
from django.utils import timezone

from core.models import Booking, Invitation, Notification
from core.services import identity, notifications, outbox, slots
from tests import factories, helpers

pytestmark = pytest.mark.django_db

DAY = factories.bangkok(2026, 9, 14).date()


class BrokenBackend:
    """A mail provider that refuses the connection, for retry-path tests."""

    def __init__(self, **kwargs):
        pass

    def open(self):
        return None

    def close(self):
        return None

    def send_messages(self, email_messages):
        raise OSError("connection refused by the mail provider")


def target(hour: int = 11):
    return slots.slot_start_for(DAY, hour)


def queue(kind="eligibility_decision", recipient=None, key="k1", payload=None, due_at=None):
    """Queue one row. The default kind revalidates without a related object."""
    recipient = recipient or factories.make_user()
    notification, _ = outbox.enqueue(
        kind=kind,
        recipient=recipient,
        dedupe_key=key,
        payload=payload or {"decision": "APPROVED", "reason": "fixture"},
        due_at=due_at,
    )
    return notification


def reminder_for(booking, recipient=None):
    notification, _ = outbox.enqueue(
        kind="booking_reminder",
        recipient=recipient or booking.user,
        dedupe_key=f"booking_reminder:{booking.pk}",
        payload={
            "booking_id": booking.pk,
            "room_number": booking.room.number,
            "slot_start": booking.slot_start.isoformat(),
            "slot_end": booking.slot_end.isoformat(),
        },
    )
    return notification


# --- Enqueue -------------------------------------------------------------------


def test_duplicate_enqueue_is_idempotent(db):
    user = factories.make_user()

    first = queue(recipient=user, key="dedupe-1")
    second = queue(recipient=user, key="dedupe-1")

    assert first.pk == second.pk
    assert Notification.objects.filter(dedupe_key="dedupe-1").count() == 1


def test_a_repeated_enqueue_never_resets_a_retry_counter(db):
    user = factories.make_user()
    notification = queue(recipient=user, key="dedupe-2")

    # The attempt counter is advanced by leasing, not by failing.
    outbox.claim_due(timezone.now())
    notification.refresh_from_db()
    assert notification.attempts == 1
    outbox.mark_failed(notification, "boom")
    notification.refresh_from_db()
    assert notification.last_error == "boom"

    queue(recipient=user, key="dedupe-2")
    notification.refresh_from_db()
    assert notification.attempts == 1, "a duplicate enqueue must not restart delivery"
    assert notification.last_error == "boom"
    assert Notification.objects.filter(dedupe_key="dedupe-2").count() == 1


def test_an_enqueue_inside_a_rolled_back_transaction_leaves_no_row(db):
    user = factories.make_user()

    with pytest.raises(RuntimeError), transaction.atomic():
        queue(recipient=user, key="rolled-back")
        raise RuntimeError("the state change failed")

    assert not Notification.objects.filter(dedupe_key="rolled-back").exists()


def test_a_rejected_booking_rolls_back_its_confirmation(frozen, student, other_student, rooms):
    """The real path: a refused operation must not queue mail."""
    factories.make_booking(student, rooms[0], slot_start=target(11))
    factories.make_booking(student, rooms[1], slot_start=target(13))

    before = Notification.objects.count()
    outcome = helpers.advance_booking(student, rooms[2], target(15))

    assert not outcome.ok
    assert Notification.objects.count() == before
    assert not Notification.objects.filter(kind="booking_confirmation").exists()


def test_the_outbox_row_is_written_in_the_same_transaction_as_the_booking(frozen, student, rooms):
    outcome = helpers.advance_booking(student, rooms[0], target(11))
    assert outcome.ok, outcome.code

    notification = Notification.objects.get(kind="booking_confirmation")
    assert notification.payload["booking_id"] == outcome.data["booking_id"]
    assert notification.status == Notification.Status.PENDING


# --- Leases --------------------------------------------------------------------


def test_claim_due_leases_rows_and_skips_a_live_lease(db):
    notification = queue(key="lease-1")
    now = timezone.now()

    claimed = outbox.claim_due(now, lease_seconds=60)
    assert [row.pk for row in claimed] == [notification.pk]

    notification.refresh_from_db()
    assert notification.attempts == 1
    assert notification.lease_token is not None
    assert notification.lease_until > now
    assert notification.is_leased(now) is True

    # A second worker must not take the same lease.
    assert outbox.claim_due(now, lease_seconds=60) == []


def test_an_expired_lease_is_recoverable(db):
    """A worker that dies mid-delivery must not strand the row."""
    notification = queue(key="lease-2")
    start = timezone.now()

    outbox.claim_due(start, lease_seconds=30)
    notification.refresh_from_db()
    first_token = notification.lease_token

    still_leased = start + timedelta(seconds=29)
    assert outbox.claim_due(still_leased, lease_seconds=30) == []

    after_expiry = start + timedelta(seconds=31)
    recovered = outbox.claim_due(after_expiry, lease_seconds=30)
    assert [row.pk for row in recovered] == [notification.pk]

    notification.refresh_from_db()
    assert notification.attempts == 2
    assert notification.lease_token != first_token, "a re-lease must issue a fresh token"


def test_claim_due_respects_the_batch_limit(db):
    for index in range(5):
        queue(key=f"batch-{index}")

    claimed = outbox.claim_due(timezone.now(), limit=2)
    assert len(claimed) == 2


def test_rows_due_later_are_not_claimed_early(db):
    user = factories.make_user()
    queue(recipient=user, key="future", due_at=timezone.now() + timedelta(hours=1))

    assert outbox.claim_due(timezone.now()) == []


# --- Delivery, backoff and exhaustion ------------------------------------------


def test_a_successful_drain_sends_and_marks_sent(db):
    notification = queue(key="send-1")

    summary = notifications.drain()

    assert summary == {"claimed": 1, "sent": 1, "failed": 0, "stale": 0}
    notification.refresh_from_db()
    assert notification.status == Notification.Status.SENT
    assert notification.sent_at is not None
    assert notification.lease_until is None and notification.lease_token is None

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [notification.recipient.email]
    assert mail.outbox[0].extra_headers["X-RoomReserve-Event"] == "send-1"


def test_a_failed_delivery_backs_off_and_stays_pending(db, settings):
    settings.EMAIL_BACKEND = "tests.test_outbox.BrokenBackend"
    notification = queue(key="fail-1")

    summary = notifications.drain()

    assert summary["failed"] == 1
    notification.refresh_from_db()
    assert notification.status == Notification.Status.PENDING
    assert notification.attempts == 1
    assert "connection refused" in notification.last_error
    assert notification.next_attempt_at > timezone.now()
    assert notification.lease_until is None, "a failed row must not hold its lease forever"


def test_repeated_failures_exhaust_the_row_and_stop_retrying(db, settings):
    settings.EMAIL_BACKEND = "tests.test_outbox.BrokenBackend"
    notification = queue(key="fail-2")

    for _ in range(settings.OUTBOX_MAX_ATTEMPTS):
        notification.refresh_from_db()
        notification.next_attempt_at = timezone.now() - timedelta(seconds=1)
        notification.save(update_fields=["next_attempt_at"])
        notifications.drain()

    notification.refresh_from_db()
    assert notification.status == Notification.Status.EXHAUSTED
    assert notification.attempts == settings.OUTBOX_MAX_ATTEMPTS
    assert notification.last_error, "the reason stays visible for staff"

    # An exhausted row is not retried again.
    assert notifications.drain()["claimed"] == 0


def test_a_failure_does_not_stop_the_rest_of_the_batch(db, settings):
    good = queue(key="good-row")
    bad = queue(key="bad-row")

    original = notifications.deliver

    def flaky(notification, now):
        if notification.pk == bad.pk:
            raise OSError("only this one fails")
        return original(notification, now)

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    notifications.deliver = flaky
    try:
        summary = notifications.drain()
    finally:
        notifications.deliver = original

    assert summary["claimed"] == 2
    assert summary["sent"] == 1 and summary["failed"] == 1
    good.refresh_from_db()
    bad.refresh_from_db()
    assert good.status == Notification.Status.SENT
    assert bad.status == Notification.Status.PENDING


def test_outbox_disabled_leaves_rows_queued(db, settings):
    settings.OUTBOX_ENABLED = False
    notification = queue(key="disabled-1")

    summary = notifications.drain()

    assert summary["disabled"] is True
    notification.refresh_from_db()
    assert notification.status == Notification.Status.PENDING
    assert notification.attempts == 0


def test_mail_provider_errors_cannot_inject_lines_into_logs_or_the_staff_page(db, settings):
    settings.EMAIL_BACKEND = "tests.test_outbox.BrokenBackend"
    notification = queue(key="inject-1")

    outbox.mark_failed(notification, "550 rejected\nX-Injected: yes\r\nsecond line")

    notification.refresh_from_db()
    assert "\n" not in notification.last_error
    assert "\r" not in notification.last_error

    long_error = "x" * 5000
    outbox.mark_failed(notification, long_error)
    notification.refresh_from_db()
    assert len(notification.last_error) <= 400


# --- Stale suppression ---------------------------------------------------------


def test_a_reminder_for_a_cancelled_booking_is_cancelled_not_sent(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=target(11))
    notification = reminder_for(booking)

    helpers.cancel(student, booking)
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED

    # Drain at the real wall clock: outbox rows are stamped with timezone.now(),
    # not with the frozen test clock.
    summary = notifications.drain(timezone.now())

    assert summary["stale"] == 1
    notification.refresh_from_db()
    assert notification.status == Notification.Status.CANCELLED

    # The cancellation notice itself is still delivered; the dead reminder is not.
    # Asserted on the event header rather than the subject, so the check does not
    # depend on the recipient's language.
    assert len(mail.outbox) == 1
    assert mail.outbox[0].extra_headers["X-RoomReserve-Event"] == f"booking_cancelled:{booking.pk}"


def test_a_reminder_after_the_slot_has_started_is_cancelled(db):
    booking = factories.make_booking(factories.make_user(), factories.make_rooms(1)[0], slot_start=target(11))
    notification = reminder_for(booking)

    # Deliver "after" the slot started, which makes the reminder pointless.
    after_start = booking.slot_start + timedelta(minutes=1)
    summary = notifications.drain(max(after_start, timezone.now()))

    assert summary["stale"] == 1
    notification.refresh_from_db()
    assert notification.status == Notification.Status.CANCELLED


def test_a_superseded_invitation_generation_is_never_retried(db):
    """Resending a verification link must not deliver the dead one."""
    result = identity.start_registration(
        institutional_id="66003000001",
        email="generation@student.chula.ac.th",
        name="ทดสอบ ระบบ",
    )
    user = result.invitation.user
    stale = Notification.objects.get(dedupe_key=f"email_verification:{result.invitation.pk}")

    _, fresh_token = identity.resend_verification(user)
    fresh = identity.find_invitation(fresh_token, Invitation.Purpose.EMAIL_VERIFICATION)

    summary = notifications.drain()

    assert summary["stale"] == 1, "the superseded link is dropped"
    assert summary["sent"] == 1, "the current link is delivered"
    stale.refresh_from_db()
    assert stale.status == Notification.Status.CANCELLED
    assert Notification.objects.get(dedupe_key=f"email_verification:{fresh.pk}").status == (
        Notification.Status.SENT
    )

    delivered = mail.outbox[0].body
    assert fresh_token in delivered
    assert result.raw_token not in delivered


def test_a_used_invitation_is_not_delivered_by_a_retry(db):
    result = identity.start_registration(
        institutional_id="66003000002",
        email="used@student.chula.ac.th",
        name="ทดสอบ ระบบ",
    )
    notification = Notification.objects.get(dedupe_key=f"email_verification:{result.invitation.pk}")

    identity.verify_email(result.raw_token)
    summary = notifications.drain()

    assert summary["stale"] == 1
    notification.refresh_from_db()
    assert notification.status == Notification.Status.CANCELLED


def test_a_cancelled_booking_confirmation_is_not_sent(frozen, student, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=target(11))
    notification, _ = outbox.enqueue(
        kind="booking_confirmation",
        recipient=student,
        dedupe_key=f"booking_confirmation:{booking.pk}",
        payload={"booking_id": booking.pk},
    )

    helpers.cancel(student, booking)
    summary = notifications.drain(timezone.now())

    assert summary["stale"] == 1
    notification.refresh_from_db()
    assert notification.status == Notification.Status.CANCELLED


# --- Monitoring signal ---------------------------------------------------------


def test_oldest_actionable_exposes_the_backlog_age(db):
    assert outbox.oldest_actionable(timezone.now()) is None

    notification = queue(key="backlog-1", due_at=timezone.now() - timedelta(minutes=15))
    oldest = outbox.oldest_actionable(timezone.now())

    assert oldest.pk == notification.pk

    notifications.drain()
    assert outbox.oldest_actionable(timezone.now()) is None, "a drained outbox is not a backlog"


def test_deliver_is_refused_when_the_outbox_is_disabled(db, settings):
    settings.OUTBOX_ENABLED = False
    notification = queue(key="refuse-1")

    with pytest.raises(RuntimeError):
        notifications.deliver(notification, timezone.now())


def test_booking_reminders_are_enqueued_for_the_lead_window(frozen, student, rooms):
    """The scheduler's reminder pass is idempotent across repeated ticks."""
    from core.services.reconcile import enqueue_due_reminders

    soon = factories.make_booking(student, rooms[0], slot_start=target(11))
    far = factories.make_booking(student, rooms[1], slot_start=slots.slot_start_for(DAY, 18))

    first = enqueue_due_reminders(frozen)
    second = enqueue_due_reminders(frozen)

    assert first == 1, "only the booking inside the 30-minute lead window is queued"
    assert second == 0, "a second tick must not queue it again"
    assert Notification.objects.filter(dedupe_key=f"booking_reminder:{soon.pk}").exists()
    assert not Notification.objects.filter(dedupe_key=f"booking_reminder:{far.pk}").exists()
