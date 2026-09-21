"""Transactional outbox: enqueue inside the state change, deliver outside locks.

V3 section 10: events are inserted in the state-change transaction; a worker
leases due rows, sends outside any lock, retries with backoff and exposes failure
to staff. Delivery is *at least once*: SMTP acceptance followed by a worker crash
can still produce a duplicate, and the docs say so rather than promising
exactly-once mail.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Notification

logger = logging.getLogger(__name__)

# Characters that would let a mail-provider error inject lines into a log or the
# staff screen are stripped, and the message is bounded.
_MAX_ERROR_LENGTH = 400


def sanitize_error(error: object) -> str:
    text = str(error).replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    return text[:_MAX_ERROR_LENGTH]


def enqueue(
    *,
    kind: str,
    recipient,
    dedupe_key: str,
    payload: dict | None = None,
    due_at=None,
    language: str | None = None,
    recipient_email: str = "",
) -> tuple[Notification, bool]:
    """Insert one outbox row. Repeated calls with the same key are a no-op.

    Must be called inside the same transaction as the state change it describes,
    so that a rolled-back booking never sends mail and a committed booking always
    has its event queued.

    Returns ``(notification, created)``. ``created`` is False when an earlier
    call already queued this key, which is what lets a repeated reminder pass
    report how much new work it actually produced.
    """
    now = timezone.now()
    notification, created = Notification.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "kind": kind,
            "recipient": recipient,
            "recipient_email": recipient_email,
            "language": language or getattr(recipient, "locale", None) or "th",
            "payload": payload or {},
            "due_at": due_at or now,
            "next_attempt_at": due_at or now,
        },
    )
    if not created:
        logger.debug("Outbox event %s already queued; skipping.", dedupe_key)
    return notification, created


def claim_due(now, limit: int | None = None, lease_seconds: int | None = None) -> list[Notification]:
    """Lease a batch of due rows using row locks only.

    This deliberately takes no BookingControl lock: outbox work happens outside
    the control transaction (V3 section 5). Rows whose previous lease expired are
    reclaimable, which is what makes a worker restart safe.
    """
    limit = limit or settings.OUTBOX_BATCH_SIZE
    lease_seconds = lease_seconds or settings.OUTBOX_LEASE_SECONDS

    with transaction.atomic():
        rows = list(
            Notification.objects.select_for_update(skip_locked=True)
            .filter(status=Notification.Status.PENDING, next_attempt_at__lte=now)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
            .order_by("next_attempt_at", "id")[:limit]
        )
        lease_until = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.lease_until = lease_until
            row.lease_token = uuid.uuid4()
            row.attempts += 1
            row.save(update_fields=["lease_until", "lease_token", "attempts"])
    return rows


def mark_sent(notification: Notification, now=None) -> None:
    now = now or timezone.now()
    notification.status = Notification.Status.SENT
    notification.sent_at = now
    notification.lease_until = None
    notification.lease_token = None
    notification.last_error = ""
    notification.save(update_fields=["status", "sent_at", "lease_until", "lease_token", "last_error"])


def mark_failed(notification: Notification, error: object, now=None) -> None:
    """Apply backoff, or move to EXHAUSTED once the attempt budget is spent."""
    now = now or timezone.now()
    notification.last_error = sanitize_error(error)

    if notification.attempts >= settings.OUTBOX_MAX_ATTEMPTS:
        notification.status = Notification.Status.EXHAUSTED
        notification.lease_until = None
        notification.lease_token = None
        notification.save(update_fields=["status", "last_error", "lease_until", "lease_token"])
        logger.warning(
            "Outbox event %s exhausted after %s attempts: %s",
            notification.dedupe_key,
            notification.attempts,
            notification.last_error,
        )
        return

    backoff = settings.OUTBOX_BACKOFF_MINUTES
    index = min(notification.attempts - 1, len(backoff) - 1)
    delay = backoff[max(index, 0)]
    notification.next_attempt_at = now + timedelta(minutes=delay)
    notification.lease_until = None
    notification.lease_token = None
    notification.save(update_fields=["next_attempt_at", "last_error", "lease_until", "lease_token"])


def cancel(notification: Notification, reason: str = "") -> None:
    notification.status = Notification.Status.CANCELLED
    notification.last_error = sanitize_error(reason)
    notification.lease_until = None
    notification.lease_token = None
    notification.save(update_fields=["status", "last_error", "lease_until", "lease_token"])


def oldest_actionable(now):
    """Oldest still-pending outbox row, used by the age monitoring check."""
    return Notification.objects.filter(status=Notification.Status.PENDING).order_by("next_attempt_at").first()
