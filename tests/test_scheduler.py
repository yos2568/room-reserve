"""The scheduler tick, heartbeat monitoring and service incidents (A28).

Three properties are pinned here. A tick that overlaps another is *skipped*, not
queued. A failed mail provider is visible to staff and monitoring without ever
failing process liveness, so a broken SMTP server cannot start a restart loop.
And a recorded incident exempts or corrects the penalties of the students it
affected, without silently backdating attendance.

These tests deliberately run the tick against the real wall clock. Reconciliation
fixtures are therefore placed on a date already in the past; the alternative (a
frozen clock) would leave the outbox nothing due and prove nothing about mail.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connections
from django.test import Client
from django.utils import timezone

from core.models import Booking, JobHeartbeat, Notification, ServiceIncident, Suspension, Violation
from core.services import clock, incidents, outbox, scheduler, slots
from core.services.errors import Code
from core.services.protocol import run_operation
from core.services.reconcile import reconcile
from tests import factories, helpers

pytestmark = pytest.mark.django_db

# Yesterday relative to the real wall clock, so overdue work is genuinely overdue.
PAST = factories.bangkok(2026, 9, 14)
PAST_DAY = PAST.date()

BREAK_SMTP = "tests.test_outbox.BrokenBackend"


def at_past(hour: int, minute: int = 0):
    return (
        slots.slot_start_for(PAST_DAY, hour) if minute == 0 else factories.bangkok(2026, 9, 14, hour, minute)
    )


def staff_call(actor, operation, payload, func):
    return run_operation(actor=actor, operation=operation, payload=payload, body=helpers._body(func))


def make_incident(actor, *, starts_at, ends_at, rooms=(), reason="service outage", cancel_scheduled=True):
    return staff_call(
        actor,
        "staff_create_incident",
        {"reason": reason},
        lambda ctx: incidents.create_incident(
            ctx,
            starts_at=starts_at,
            ends_at=ends_at,
            rooms=list(rooms),
            reason=reason,
            cancel_scheduled=cancel_scheduled,
        ),
    )


def void_incident_violations(actor, incident, reason="service outage"):
    return staff_call(
        actor,
        "staff_void_incident_violations",
        {"incident": incident.pk},
        lambda ctx: incidents.void_incident_violations(ctx, incident=incident, reason=reason),
    )


# --- Tick overlap ---------------------------------------------------------------


def test_an_overlapping_tick_is_skipped_not_queued(db):
    """A second tick must not wait for the first; it steps aside."""
    holder = connections.create_connection("default")
    try:
        with holder.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [scheduler.SCHEDULER_LOCK_KEY])

        summary = scheduler.run_tick()

        assert summary["skipped"] is True
        assert summary["reason"] == "lock_held"
        assert not JobHeartbeat.objects.exists(), "a skipped tick writes no heartbeat"
    finally:
        with holder.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [scheduler.SCHEDULER_LOCK_KEY])
        holder.close()


def test_the_lock_is_released_so_the_next_tick_runs(db):
    first = scheduler.run_tick()
    second = scheduler.run_tick()

    assert first["skipped"] is False
    assert second["skipped"] is False, "the lock must be released in a finally block"


def test_a_failed_tick_still_releases_the_lock(db, settings, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("reconcile blew up")

    monkeypatch.setattr(scheduler, "reconcile", explode)
    with pytest.raises(RuntimeError):
        scheduler.run_tick()

    monkeypatch.undo()
    assert scheduler.run_tick()["skipped"] is False


# --- Heartbeat ------------------------------------------------------------------


def test_a_tick_records_a_heartbeat_with_a_compact_summary(db):
    summary = scheduler.run_tick()
    heartbeat = JobHeartbeat.objects.get(name=scheduler.HEARTBEAT_NAME)

    assert summary["skipped"] is False
    assert heartbeat.last_run_at is not None
    assert set(heartbeat.detail) >= {"no_shows", "mail_sent", "mail_failed", "outbox_enabled"}
    assert heartbeat.detail["outbox_enabled"] is True

    status = scheduler.heartbeat_status(timezone.now())
    assert status["heartbeat_stale"] is False


def test_a_tick_reconciles_overdue_bookings_and_delivers_mail(db):
    student = factories.make_user()
    rooms = factories.make_rooms(2)
    overdue = factories.make_booking(student, rooms[0], slot_start=at_past(9))

    summary = scheduler.run_tick()

    overdue.refresh_from_db()
    assert overdue.status == Booking.Status.NO_SHOW
    assert Violation.objects.filter(booking=overdue, kind=Violation.Kind.NO_SHOW).count() == 1
    assert summary["reconciled"]["no_shows"] >= 1
    assert summary["mail"]["sent"] >= 1
    assert Notification.objects.filter(kind="no_show", status=Notification.Status.SENT).exists()


def test_a_stale_heartbeat_and_an_outbox_backlog_are_reported_separately(db):
    moment = timezone.now()
    JobHeartbeat.objects.update_or_create(
        name=scheduler.HEARTBEAT_NAME,
        defaults={"last_run_at": moment - timedelta(hours=2), "detail": {}},
    )

    status = scheduler.heartbeat_status(moment)
    assert status["heartbeat_stale"] is True
    assert status["outbox_stale"] is False, "nothing is queued, so there is no backlog"

    outbox.enqueue(
        kind="eligibility_decision",
        recipient=factories.make_user(),
        dedupe_key="stale-row",
        payload={"decision": "APPROVED", "reason": "fixture"},
        due_at=moment - timedelta(hours=1),
    )

    status = scheduler.heartbeat_status(moment)
    assert status["outbox_stale"] is True
    assert status["oldest_outbox"].dedupe_key == "stale-row"
    assert status["outbox_age_seconds"] >= 3600


def test_no_heartbeat_at_all_is_reported_as_stale_not_healthy(db):
    status = scheduler.heartbeat_status(timezone.now())
    assert status["heartbeat"] is None
    assert status["heartbeat_stale"] is True


# --- Mail outage ----------------------------------------------------------------


def test_a_mail_outage_is_visible_and_never_rolls_back_reconciliation(db, settings):
    """The whole point of sending outside the control transaction."""
    settings.EMAIL_BACKEND = BREAK_SMTP
    student = factories.make_user()
    rooms = factories.make_rooms(1)
    overdue = factories.make_booking(student, rooms[0], slot_start=at_past(9))

    summary = scheduler.run_tick()

    overdue.refresh_from_db()
    assert overdue.status == Booking.Status.NO_SHOW, "the state change must survive a mail failure"
    assert Violation.objects.filter(booking=overdue).count() == 1

    assert summary["mail"]["failed"] >= 1
    failed = Notification.objects.filter(status=Notification.Status.PENDING).first()
    assert failed is not None
    assert failed.attempts >= 1
    assert failed.last_error, "the reason must be visible to staff"

    heartbeat = JobHeartbeat.objects.get(name=scheduler.HEARTBEAT_NAME)
    assert heartbeat.detail["mail_failed"] >= 1, "monitoring sees the outage in the heartbeat"


def test_liveness_and_readiness_stay_healthy_during_a_mail_outage(db, settings):
    """A broken provider must not fail process liveness and cause restart loops."""
    settings.EMAIL_BACKEND = BREAK_SMTP
    outbox.enqueue(
        kind="eligibility_decision",
        recipient=factories.make_user(),
        dedupe_key="outage-1",
        payload={"decision": "APPROVED", "reason": "fixture"},
    )

    summary = scheduler.run_tick()
    assert summary["mail"]["failed"] >= 1

    client = Client()
    assert client.get("/healthz/").status_code == 200
    ready = client.get("/readyz/")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ok"


def test_recovery_resends_the_backlog_after_the_provider_returns(db, settings):
    settings.EMAIL_BACKEND = BREAK_SMTP
    outbox.enqueue(
        kind="eligibility_decision",
        recipient=factories.make_user(),
        dedupe_key="recover-1",
        payload={"decision": "APPROVED", "reason": "fixture"},
    )
    scheduler.run_tick()
    assert Notification.objects.get(dedupe_key="recover-1").status == Notification.Status.PENDING

    # The provider comes back and the retry window has elapsed.
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    row = Notification.objects.get(dedupe_key="recover-1")
    row.next_attempt_at = timezone.now() - timedelta(seconds=1)
    row.save(update_fields=["next_attempt_at"])

    from core.services.notifications import drain

    assert drain()["sent"] == 1
    row.refresh_from_db()
    assert row.status == Notification.Status.SENT


# --- Service incidents -----------------------------------------------------------


def test_an_incident_cancels_scheduled_bookings_without_a_penalty(frozen, staff_user, rooms):
    student = factories.make_user()
    affected = factories.make_booking(student, rooms[0], slot_start=at_past(14))
    elsewhere = factories.make_booking(
        student, rooms[1], slot_start=slots.slot_start_for(PAST_DAY + timedelta(days=1), 10)
    )

    outcome = make_incident(
        staff_user,
        starts_at=at_past(13),
        ends_at=at_past(17),
        rooms=[rooms[0]],
        reason="broken window",
    )
    assert outcome.ok, outcome.code
    assert outcome.data["cancelled"] == 1

    affected.refresh_from_db()
    assert affected.status == Booking.Status.CANCELLED
    assert affected.cancel_reason == "SERVICE_INCIDENT"
    assert affected.late_cancel is False
    assert Violation.objects.filter(booking=affected).count() == 0

    elsewhere.refresh_from_db()
    assert elsewhere.status == Booking.Status.SCHEDULED, "a room-scoped incident is scoped"


def test_an_empty_room_scope_covers_every_room(frozen, staff_user, rooms):
    first = factories.make_booking(factories.make_user(), rooms[0], slot_start=at_past(14))
    second = factories.make_booking(factories.make_user(), rooms[5], slot_start=at_past(14))

    outcome = make_incident(staff_user, starts_at=at_past(13), ends_at=at_past(17), rooms=[])
    assert outcome.ok, outcome.code
    assert outcome.data["cancelled"] == 2

    for booking in (first, second):
        booking.refresh_from_db()
        assert booking.status == Booking.Status.CANCELLED


def test_an_incident_never_touches_a_session_already_in_use(frozen, staff_user, rooms):
    student = factories.make_user()
    in_use = factories.make_booking(student, rooms[0], slot_start=at_past(14), status=Booking.Status.IN_USE)

    outcome = make_incident(staff_user, starts_at=at_past(13), ends_at=at_past(17), rooms=[rooms[0]])
    assert outcome.ok, outcome.code
    assert outcome.data["cancelled"] == 0

    in_use.refresh_from_db()
    assert in_use.status == Booking.Status.IN_USE


def test_incident_rejects_a_missing_reason_or_an_invalid_interval(frozen, staff_user, rooms):
    without_reason = make_incident(
        staff_user, starts_at=at_past(13), ends_at=at_past(15), rooms=[rooms[0]], reason="  "
    )
    assert not without_reason.ok
    assert without_reason.code == Code.INVALID_INPUT

    backwards = make_incident(staff_user, starts_at=at_past(15), ends_at=at_past(13), rooms=[rooms[0]])
    assert not backwards.ok
    assert backwards.code == Code.INVALID_INPUT
    assert ServiceIncident.objects.count() == 0


def test_voiding_incident_violations_clears_the_strikes_without_backdating_attendance(
    frozen, staff_user, rooms
):
    """A recorded no-show stays a no-show; only its penalty is corrected."""
    student = factories.make_user()
    refused = factories.make_booking(student, rooms[0], slot_start=at_past(9))
    with clock.frozen_clock(factories.bangkok(2026, 9, 14, 10, 0)):
        reconcile(factories.bangkok(2026, 9, 14, 10, 0))

    refused.refresh_from_db()
    assert refused.status == Booking.Status.NO_SHOW
    violation = Violation.objects.get(booking=refused)

    outcome = make_incident(
        staff_user, starts_at=at_past(8), ends_at=at_past(12), rooms=[rooms[0]], reason="power cut"
    )
    assert outcome.ok, outcome.code

    voided = void_incident_violations(staff_user, ServiceIncident.objects.get(pk=outcome.data["incident_id"]))
    assert voided.ok, voided.code
    assert voided.data["voided"] == 1

    violation.refresh_from_db()
    assert violation.voided_at is not None
    assert violation.voided_by_id == staff_user.pk
    assert violation.incident_id == outcome.data["incident_id"]

    refused.refresh_from_db()
    assert refused.status == Booking.Status.NO_SHOW, "attendance is never silently restored"
    assert refused.checked_in_at is None


def test_a_voided_strike_no_longer_counts_towards_a_suspension(staff_user, rooms):
    """No frozen clock here: sanctions_under_review() reads the real clock, and
    the sanction must still be in force when it is queryable."""
    student = factories.make_user()
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        factories.make_booking(student, room, slot_start=at_past(hour))

    moment = factories.bangkok(2026, 9, 14, 13, 0)
    with clock.frozen_clock(moment):
        reconcile(moment)

    suspension = Suspension.objects.get(user=student)
    assert suspension.consumed_strikes.count() == 3

    outcome = make_incident(
        staff_user, starts_at=at_past(7), ends_at=at_past(13), rooms=[], reason="building power cut"
    )
    assert outcome.ok, outcome.code
    voided = void_incident_violations(staff_user, ServiceIncident.objects.get(pk=outcome.data["incident_id"]))
    assert voided.ok and voided.data["voided"] == 3

    # The sanction is not silently lifted: it is opened for staff review.
    suspension.refresh_from_db()
    assert suspension.lifted_at is None
    assert suspension.pk in voided.data["suspensions_to_review"]
    assert suspension.pk in set(incidents.sanctions_under_review().values_list("pk", flat=True))


def test_a_no_show_outside_the_incident_window_keeps_its_strike(frozen, staff_user, rooms):
    student = factories.make_user()
    inside = factories.make_booking(student, rooms[0], slot_start=at_past(9))
    outside = factories.make_booking(student, rooms[1], slot_start=at_past(16))

    moment = factories.bangkok(2026, 9, 14, 17, 0)
    with clock.frozen_clock(moment):
        reconcile(moment)

    outcome = make_incident(staff_user, starts_at=at_past(8), ends_at=at_past(12), rooms=[])
    assert outcome.ok, outcome.code
    voided = void_incident_violations(staff_user, ServiceIncident.objects.get(pk=outcome.data["incident_id"]))

    assert voided.data["voided"] == 1
    assert Violation.objects.get(booking=inside).voided_at is not None
    assert Violation.objects.get(booking=outside).voided_at is None


def test_incidents_are_listed_until_resolved(frozen, staff_user, rooms):
    outcome = make_incident(staff_user, starts_at=at_past(13), ends_at=at_past(15), rooms=[rooms[0]])
    assert outcome.ok, outcome.code
    assert incidents.open_incidents().count() == 1

    incident = ServiceIncident.objects.get(pk=outcome.data["incident_id"])
    incident.resolved_at = timezone.now()
    incident.resolution_note = "Network switch replaced."
    incident.save(update_fields=["resolved_at", "resolution_note"])

    assert incidents.open_incidents().count() == 0
