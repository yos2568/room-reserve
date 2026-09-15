"""Calendar precedence, date overrides and frozen policy values (A19).

V3 section 3.1 fixes the precedence order: an explicit room or all-room closure
wins, then a date override, then the weekly timetable. Section 8 adds the second
half of A19: changing a policy value creates a *new* version and must never
rewrite the values already stored on a booking, and the operational models are
not reachable through a raw Django admin form.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError

from core.models import Booking, CalendarOverride, Closure, PolicyVersion, User, Violation
from core.services import calendar, clock, maintenance, slots
from core.services.errors import Code
from core.services.policy import create_policy_version, current_policy
from core.services.protocol import run_operation
from core.services.reconcile import reconcile
from tests import factories, helpers

pytestmark = pytest.mark.django_db

# 2026-09-14 (the frozen fixture moment) is a Monday.
TUESDAY = date(2026, 9, 15)
SATURDAY = date(2026, 9, 19)


def staff_call(actor, operation, payload, func):
    """Run a staff service the way the staff views do: under the shared lock."""
    return run_operation(
        actor=actor,
        operation=operation,
        payload=payload,
        body=helpers._body(func),
    )


def set_override(actor, local_date, *, is_open, open_hour=None, close_hour=None, reason="fixture"):
    return staff_call(
        actor,
        "staff_set_calendar_override",
        {"local_date": local_date.isoformat(), "is_open": is_open},
        lambda ctx: {
            "override_id": maintenance.set_calendar_override(
                ctx,
                local_date=local_date,
                is_open=is_open,
                open_hour=open_hour,
                close_hour=close_hour,
                reason=reason,
            ).pk
        },
    )


def make_closure(actor, *, room, starts_at, ends_at, reason="fixture", acknowledged=False):
    return staff_call(
        actor,
        "staff_create_closure",
        {"room": getattr(room, "pk", None), "start": starts_at.isoformat()},
        lambda ctx: {
            "closure_id": maintenance.create_closure(
                ctx,
                room=room,
                starts_at=starts_at,
                ends_at=ends_at,
                reason=reason,
                acknowledged_in_use=acknowledged,
            ).pk
        },
    )


# --- The weekly timetable baseline ---------------------------------------------


def test_weekly_timetable_is_monday_to_friday_0800_to_2000(frozen):
    assert calendar.day_hours(TUESDAY) == (8, 20)
    assert calendar.day_hours(SATURDAY) is None, "weekends are closed by default"


def test_weekend_booking_rejected_without_an_override(frozen, student, rooms):
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(SATURDAY, 11))
    assert not outcome.ok
    assert outcome.code == Code.CLOSED


# --- Date overrides ------------------------------------------------------------


def test_opening_override_opens_a_normally_closed_saturday(frozen, student, staff_user, rooms):
    assert set_override(staff_user, SATURDAY, is_open=True, open_hour=10, close_hour=16).ok

    inside = helpers.advance_booking(student, rooms[0], slots.slot_start_for(SATURDAY, 11))
    assert inside.ok, inside.code

    before = helpers.advance_booking(student, rooms[1], slots.slot_start_for(SATURDAY, 9))
    assert not before.ok and before.code == Code.CLOSED

    # close_hour is exclusive: the 16:00 slot starts at the closing hour.
    after = helpers.advance_booking(student, rooms[2], slots.slot_start_for(SATURDAY, 16))
    assert not after.ok and after.code == Code.CLOSED


def test_closed_date_override_blocks_a_normally_open_weekday(frozen, student, staff_user, rooms):
    assert set_override(staff_user, TUESDAY, is_open=False, reason="public holiday").ok
    assert calendar.day_hours(TUESDAY) is None

    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 11))
    assert not outcome.ok
    assert outcome.code == Code.CLOSED


def test_override_can_shorten_a_weekday(frozen, student, staff_user, rooms):
    assert set_override(staff_user, TUESDAY, is_open=True, open_hour=8, close_hour=12).ok

    assert helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 11)).ok
    late = helpers.advance_booking(student, rooms[1], slots.slot_start_for(TUESDAY, 12))
    assert not late.ok and late.code == Code.CLOSED


def test_replacing_an_override_replaces_rather_than_duplicates(frozen, staff_user):
    first = set_override(staff_user, TUESDAY, is_open=True, open_hour=8, close_hour=12)
    second = set_override(staff_user, TUESDAY, is_open=True, open_hour=8, close_hour=18)
    assert first.ok and second.ok
    assert first.data["override_id"] == second.data["override_id"]
    assert CalendarOverride.objects.filter(local_date=TUESDAY).count() == 1
    assert calendar.day_hours(TUESDAY) == (8, 18)


# --- Closure precedence --------------------------------------------------------


def test_explicit_all_room_closure_defeats_an_opening_override(frozen, student, staff_user, rooms):
    assert set_override(staff_user, TUESDAY, is_open=True, open_hour=8, close_hour=20).ok
    closure = make_closure(
        staff_user,
        room=None,
        starts_at=slots.slot_start_for(TUESDAY, 10),
        ends_at=slots.slot_start_for(TUESDAY, 12),
        reason="maintenance",
    )
    assert closure.ok, closure.code

    blocked = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 11))
    assert not blocked.ok, "an opening override must never defeat an explicit closure"
    assert blocked.code == Code.CLOSED

    # Outside the closure the override still applies.
    assert helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 13)).ok


def test_room_scoped_closure_blocks_only_that_room(frozen, student, staff_user, rooms):
    assert make_closure(
        staff_user,
        room=rooms[1],
        starts_at=slots.slot_start_for(TUESDAY, 10),
        ends_at=slots.slot_start_for(TUESDAY, 12),
        reason="piano repair",
    ).ok

    blocked = helpers.advance_booking(student, rooms[1], slots.slot_start_for(TUESDAY, 11))
    assert not blocked.ok and blocked.code == Code.CLOSED

    allowed = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 11))
    assert allowed.ok, allowed.code


def test_closure_intersecting_any_part_of_the_hour_blocks_the_whole_hour(frozen, student, staff_user, rooms):
    """A 11:30-11:45 closure blocks the 11:00 booking (V3 section 3.1)."""
    assert make_closure(
        staff_user,
        room=rooms[0],
        starts_at=factories.bangkok(2026, 9, 15, 11, 30),
        ends_at=factories.bangkok(2026, 9, 15, 11, 45),
        reason="fire drill",
    ).ok

    assert calendar.is_slot_closed(slots.slot_start_for(TUESDAY, 11), rooms[0]) is True
    outcome = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 11))
    assert not outcome.ok
    assert outcome.code == Code.CLOSED


def test_closure_cancels_scheduled_bookings_without_penalty(frozen, student, staff_user, rooms):
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(TUESDAY, 14))
    outcome = make_closure(
        staff_user,
        room=rooms[0],
        starts_at=slots.slot_start_for(TUESDAY, 14),
        ends_at=slots.slot_start_for(TUESDAY, 16),
        reason="electrical work",
    )
    assert outcome.ok, outcome.code

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancel_reason == "CLOSURE"
    assert booking.late_cancel is False
    assert Violation.objects.filter(booking=booking).count() == 0, "no penalty for a closure"


def test_in_use_session_is_flagged_for_review_and_needs_acknowledgement(db, student, staff_user, rooms):
    moment = factories.bangkok(2026, 9, 14, 14, 20)
    in_use = factories.make_booking(
        student,
        rooms[0],
        slot_start=slots.slot_start_for(TUESDAY - timedelta(days=1), 14),
        status=Booking.Status.IN_USE,
    )
    with clock.frozen_clock(moment):
        refused = make_closure(
            staff_user,
            room=rooms[0],
            starts_at=factories.bangkok(2026, 9, 14, 14, 0),
            ends_at=factories.bangkok(2026, 9, 14, 15, 0),
            reason="gas leak",
        )
        assert not refused.ok
        assert refused.data["in_use_count"] == 1

        accepted = make_closure(
            staff_user,
            room=rooms[0],
            starts_at=factories.bangkok(2026, 9, 14, 14, 0),
            ends_at=factories.bangkok(2026, 9, 14, 15, 0),
            reason="gas leak",
            acknowledged=True,
        )
        assert accepted.ok, accepted.code

    in_use.refresh_from_db()
    assert in_use.status == Booking.Status.IN_USE, "an in-use session is never silently completed"
    assert in_use.needs_review is True
    assert "gas leak" in in_use.review_note


def test_revoked_closure_makes_the_slot_bookable_but_does_not_resurrect_bookings(
    frozen, student, staff_user, rooms
):
    booking = factories.make_booking(student, rooms[0], slot_start=slots.slot_start_for(TUESDAY, 14))
    created = make_closure(
        staff_user,
        room=rooms[0],
        starts_at=slots.slot_start_for(TUESDAY, 14),
        ends_at=slots.slot_start_for(TUESDAY, 16),
        reason="false alarm",
    )
    assert created.ok, created.code

    revoked = staff_call(
        staff_user,
        "staff_revoke_closure",
        {"closure": created.data["closure_id"]},
        lambda ctx: {
            "closure_id": maintenance.revoke_closure(
                ctx,
                closure=Closure.objects.get(pk=created.data["closure_id"]),
                reason="cleared",
            ).pk
        },
    )
    assert revoked.ok, revoked.code

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED, "reopening does not resurrect history"

    # The slot itself is reusable again for a new reservation.
    replacement = helpers.advance_booking(student, rooms[1], slots.slot_start_for(TUESDAY, 14))
    assert replacement.ok, replacement.code

    second = staff_call(
        staff_user,
        "staff_revoke_closure",
        {"closure": created.data["closure_id"]},
        lambda ctx: {
            "closure_id": maintenance.revoke_closure(
                ctx, closure=Closure.objects.get(pk=created.data["closure_id"]), reason="again"
            ).pk
        },
    )
    assert not second.ok
    assert second.code == Code.TERMINAL_STATUS


# --- Frozen policy values ------------------------------------------------------


def test_changing_grace_does_not_alter_stored_deadlines(frozen, student, other_student, rooms):
    """A later policy edit must not rewrite a deadline already promised."""
    booked = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 11))
    assert booked.ok, booked.code
    booking = Booking.objects.get(pk=booked.data["booking_id"])
    original_deadline = booking.deadline
    assert original_deadline == slots.slot_start_for(TUESDAY, 11) + timedelta(minutes=15)

    v2 = create_policy_version(
        snapshot={"checkin_grace_minutes": 45},
        label="Longer grace",
        note="Grace extended for the exam period.",
    )
    assert v2.version == 2
    assert current_policy().version == 2

    booking.refresh_from_db()
    assert booking.deadline == original_deadline, "a stored deadline must never move"
    assert booking.policy_version_id != v2.pk

    # A booking created after the change stores the new value.
    later = helpers.advance_booking(other_student, rooms[1], slots.slot_start_for(TUESDAY, 13))
    assert later.ok, later.code
    fresh = Booking.objects.get(pk=later.data["booking_id"])
    assert fresh.deadline == slots.slot_start_for(TUESDAY, 13) + timedelta(minutes=45)
    assert fresh.policy_version_id == v2.pk


def test_stored_deadline_governs_reconciliation_not_the_new_policy(frozen, student, other_student, rooms):
    """The behavioural half of the previous test: the old grace still applies."""
    booked = helpers.advance_booking(student, rooms[0], slots.slot_start_for(TUESDAY, 11))
    booking = Booking.objects.get(pk=booked.data["booking_id"])

    create_policy_version(snapshot={"checkin_grace_minutes": 45}, label="Longer grace")

    # 11:20 is past the stored 11:15 deadline but inside the new 45-minute grace.
    late_enough = factories.bangkok(2026, 9, 15, 11, 20)
    with clock.frozen_clock(late_enough):
        reconcile(late_enough)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.NO_SHOW


def test_activated_policy_versions_are_immutable(frozen, student, rooms):
    policy = current_policy()
    policy.snapshot = {"daily_quota": 99}
    with pytest.raises(ValidationError):
        policy.save()
    with pytest.raises(ValidationError):
        policy.delete()


def test_geometry_is_not_staff_editable(frozen):
    """Slot geometry is fixed by the deployment, never by a submitted snapshot."""
    policy = create_policy_version(
        snapshot={"slot_minutes": 30, "opening_hour": 3, "last_slot_hour": 23, "horizon_days": 10},
        label="Attempted geometry change",
    )
    assert policy.snapshot["slot_minutes"] == 60
    assert policy.snapshot["opening_hour"] == 8
    assert policy.snapshot["last_slot_hour"] == 19
    assert policy.snapshot["horizon_days"] == 10, "editable values still apply"


@pytest.mark.parametrize(
    "snapshot",
    [
        {"daily_quota": 0},
        {"daily_quota": 99},
        {"checkin_grace_minutes": 60},
        {"strike_threshold": 0},
        {"horizon_days": 0},
        {"institution_email_domain": "@student.chula.ac.th"},
        {"institution_email_domain": ""},
    ],
)
def test_invalid_policy_values_are_rejected(frozen, snapshot):
    from django.core.exceptions import ValidationError as DjangoValidationError

    with pytest.raises(DjangoValidationError):
        create_policy_version(snapshot=snapshot, label="invalid")


def test_operational_models_are_not_reachable_through_a_raw_admin_form(frozen):
    """Staff screens are the only path: no core model is registered in the admin."""
    registered_core_models = [
        model._meta.label for model in admin.site._registry if model._meta.app_label == "core"
    ]
    assert registered_core_models == [], (
        f"a raw admin form would bypass the shared lock and the rule services: {registered_core_models}"
    )
    for model in (Booking, User, PolicyVersion, CalendarOverride, Closure):
        assert not admin.site.is_registered(model)
