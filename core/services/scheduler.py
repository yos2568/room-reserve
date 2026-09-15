"""The scheduled tick (V3 sections 6 and 11).

``tick`` performs lifecycle reconciliation, due reminder enqueue, outbox drain and
a heartbeat. Two properties matter:

* A **non-blocking** advisory lock (``pg_try_advisory_lock``) means an overlapping
  tick is skipped rather than queued. The lock is released in a ``finally`` block,
  and PostgreSQL also drops it if the connection dies, so a crash cannot wedge the
  scheduler.
* Outbox work happens outside the control transaction. A failed mail provider
  therefore cannot make process liveness fail and trigger restart loops; it
  surfaces on the staff page and in the heartbeat detail instead.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from core.models import BookingControl, JobHeartbeat

from . import clock
from . import outbox as outbox_service
from .notifications import drain
from .reconcile import enqueue_due_reminders, reconcile

logger = logging.getLogger(__name__)

# Arbitrary but fixed application key: 'room' in hex.
SCHEDULER_LOCK_KEY = 0x726F6F6D

HEARTBEAT_NAME = "tick"


def _try_lock() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [SCHEDULER_LOCK_KEY])
        return bool(cursor.fetchone()[0])


def _release_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [SCHEDULER_LOCK_KEY])


def run_tick(now=None) -> dict:
    """Run one tick. Returns a summary; safe to call repeatedly."""
    if not _try_lock():
        logger.info("Another tick holds the advisory lock; skipping this one.")
        return {"skipped": True, "reason": "lock_held"}

    try:
        summary = {"skipped": False}
        moment = now or clock.now()

        with transaction.atomic():
            # Same first lock as every other operational write, so a tick cannot
            # interleave with a booking.
            BookingControl.objects.select_for_update().get(pk=1)
            captured = clock.now() if now is None else now
            # Use the time captured under the lock, not the pre-lock value.
            summary["reconciled"] = reconcile(captured, actor=None)
            summary["reminders"] = enqueue_due_reminders(captured)
            moment = captured

        # Outside the control transaction: SMTP must never hold the booking lock.
        # Drained against the current wall clock rather than the moment captured
        # before reconciliation: an event this tick just queued is stamped with
        # ``timezone.now()``, which is later than that captured moment, so using
        # the captured moment would make every tick wait one further minute
        # before delivering its own work.
        summary["mail"] = drain(now if now is not None else timezone.now())

        JobHeartbeat.objects.update_or_create(
            name=HEARTBEAT_NAME,
            defaults={"last_run_at": moment, "detail": _summarise(summary)},
        )
        return summary
    finally:
        try:
            _release_lock()
        except Exception:
            logger.warning("Could not release the scheduler advisory lock.", exc_info=True)


def _summarise(summary: dict) -> dict:
    """A compact, non-personal heartbeat detail for monitoring."""
    reconciled = summary.get("reconciled", {}) or {}
    mail = summary.get("mail", {}) or {}
    return {
        "no_shows": reconciled.get("no_shows", 0),
        "completions": reconciled.get("completions", 0),
        "suspensions": reconciled.get("suspensions", 0),
        "lifts": reconciled.get("lifts", 0),
        "reminders": summary.get("reminders", 0),
        "mail_claimed": mail.get("claimed", 0),
        "mail_sent": mail.get("sent", 0),
        "mail_failed": mail.get("failed", 0),
        "outbox_enabled": settings.OUTBOX_ENABLED,
    }


def heartbeat_status(now):
    """Monitoring values: heartbeat age and oldest actionable outbox item."""
    heartbeat = JobHeartbeat.objects.filter(name=HEARTBEAT_NAME).first()
    age = None if heartbeat is None else (now - heartbeat.last_run_at).total_seconds()
    oldest = outbox_service.oldest_actionable(now)
    outbox_age = None if oldest is None else (now - oldest.next_attempt_at).total_seconds()
    return {
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": age,
        "heartbeat_stale": age is None or age > settings.SCHEDULER_HEARTBEAT_STALE_SECONDS,
        "oldest_outbox": oldest,
        "outbox_age_seconds": outbox_age,
        "outbox_stale": outbox_age is not None and outbox_age > settings.OUTBOX_AGE_WARN_SECONDS,
    }
