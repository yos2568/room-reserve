"""Concurrency races on independent PostgreSQL connections.

V3 section 12: "concurrency tests use independent PostgreSQL connections/processes
and synchronization barriers, not sequential loops". Each worker therefore opens
its own connection and the threads start together at a barrier.

These tests assert *invariants* rather than a single interleaving. Where two
outcomes are both legal, both are accepted; what must never happen is a
duplicated or contradictory persisted state, or an unhandled error.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.db import connections

from core.models import Booking, Closure, Suspension, User, Violation
from core.services import booking as booking_service
from core.services import checkin as checkin_service
from core.services import clock, identity, maintenance, slots
from core.services.errors import Code, OperationRejected
from core.services.protocol import run_operation
from tests import factories
from tests.helpers import _body

pytestmark = pytest.mark.django_db(transaction=True)

# Outcomes that are clean refusals rather than failures. BUSY is included because
# the shared control row has a bounded wait: under heavy contention a request may
# legitimately be told to retry instead of producing a definite answer.
CLEAN_REJECTIONS = {Code.SLOT_TAKEN, Code.ADJACENCY_CONFLICT, Code.QUOTA_EXCEEDED, Code.BUSY}


class Worker:
    """Runs one operation in its own thread with its own frozen clock."""

    def __init__(self, func, moment):
        self.func = func
        self.moment = moment
        self.outcome = None
        self.error = None

    def __call__(self):
        try:
            with clock.frozen_clock(self.moment):
                self.outcome = self.func()
        except Exception as exc:
            self.error = exc
        finally:
            connections.close_all()


def run_all(workers, timeout=90):
    """Start every worker at the same barrier and wait for all of them."""
    barrier = threading.Barrier(len(workers), timeout=timeout)

    def gated(worker):
        barrier.wait()
        worker()

    threads = [threading.Thread(target=gated, args=(worker,)) for worker in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=timeout)

    for worker in workers:
        assert worker.error is None, f"unhandled error in worker: {worker.error!r}"
    return workers


def book(actor, room, slot_start, key=None):
    return run_operation(
        actor=actor,
        operation="advance_booking",
        payload=booking_service.build_payload(room, slot_start),
        key=key,
        body=_body(lambda ctx: booking_service.create_advance_booking(ctx, room=room, slot_start=slot_start)),
    )


# A move loses either because the cell was taken or because the shared lock's
# bounded wait expired. BUSY does not release the room the student already holds.
MOVE_REJECTIONS = {Code.SLOT_TAKEN, Code.BUSY}


def move(actor, booking, room, key=None):
    return run_operation(
        actor=actor,
        operation="move_booking",
        payload=booking_service.build_move_payload(booking, room),
        key=key,
        body=_body(lambda ctx: booking_service.move_booking(ctx, booking=booking, room=room)),
    )


# --- A09: twenty users, one slot ----------------------------------------------


def test_twenty_users_race_for_one_slot():
    moment = factories.bangkok(2026, 9, 14, 10, 40)
    rooms = factories.make_rooms(9)
    room = rooms[0]
    slot_start = slots.slot_start_for(factories.bangkok(2026, 9, 14).date(), 11)

    users = [factories.make_user() for _ in range(20)]
    workers = [Worker(lambda u=user: book(u, room, slot_start), moment) for user in users]

    with clock.frozen_clock(moment):
        run_all(workers)

    successes = [w for w in workers if w.outcome.ok]
    assert len(successes) == 1, [w.outcome.code for w in workers]

    persisted = Booking.objects.filter(room=room, slot_start=slot_start).exclude(
        status=Booking.Status.CANCELLED
    )
    assert persisted.count() == 1
    assert persisted.first().pk == successes[0].outcome.data["booking_id"]

    for worker in workers:
        if not worker.outcome.ok:
            assert worker.outcome.code in CLEAN_REJECTIONS, worker.outcome.code


def test_single_use_activation_token_allows_only_one_concurrent_redemption():
    """A single activation link cannot set two passwords at the same time."""
    inviter = factories.make_user(
        username="staff-token-race",
        email="staff-token-race@student.chula.ac.th",
        is_operational_staff=True,
    )
    user, _invitation, raw_token = identity.invite_account(
        institutional_id="66009990001",
        email="token-race@student.chula.ac.th",
        name="Token Race",
        actor=inviter,
        staff=False,
    )
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def redeem(password):
        try:
            barrier.wait(timeout=10)
            identity.set_initial_password(
                user=User.objects.get(pk=user.pk),
                password=password,
                raw_token=raw_token,
                purpose="ACCOUNT_ACTIVATION",
            )
            results.append("success")
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=redeem, args=("a-long-enough-password-1",)),
        threading.Thread(target=redeem, args=("another-long-password-2",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert results == ["success"]
    assert len(errors) == 1
    assert isinstance(errors[0], OperationRejected)
    assert errors[0].outcome.code == Code.TOKEN_USED

    user.refresh_from_db()
    assert user.check_password("a-long-enough-password-1") or user.check_password("another-long-password-2")


# --- A10: one user, several slots, one allowance left -------------------------


def test_one_user_cannot_win_several_slots_with_one_allowance():
    """Daily quota is two; the user already holds one, so only one race can win."""
    moment = factories.bangkok(2026, 9, 14, 10, 40)
    rooms = factories.make_rooms(9)
    user = factories.make_user()
    today = factories.bangkok(2026, 9, 14).date()

    # Consume the first allowance with an existing booking at 13:00.
    factories.make_booking(user, rooms[0], slot_start=slots.slot_start_for(today, 13))

    # Four future, mutually non-adjacent targets, none adjacent to the existing
    # 13:00 booking, so the shared daily quota is the only real constraint.
    targets = [slots.slot_start_for(today, hour) for hour in (11, 15, 17, 19)]
    workers = [
        Worker(lambda s=start, i=index: book(user, rooms[i + 1], s), moment)
        for index, start in enumerate(targets)
    ]

    with clock.frozen_clock(moment):
        run_all(workers)

    successes = [w for w in workers if w.outcome.ok]
    assert len(successes) == 1, [w.outcome.code for w in workers]

    charged = Booking.objects.filter(user=user, slot_date=today).exclude(status=Booking.Status.CANCELLED)
    assert charged.count() == 2, "quota must be respected exactly at the boundary"

    for worker in workers:
        if not worker.outcome.ok:
            assert worker.outcome.code in CLEAN_REJECTIONS, worker.outcome.code


# --- A12: check-in races no-show reconciliation -------------------------------


def test_check_in_races_no_show_reconciliation():
    """Either the check-in wins or the no-show does; never both.

    The reconciliation runs through an unrelated booking on a different room, so
    it happens under the same shared control lock as the check-in - exactly how a
    scheduler tick and a request meet in production.
    """
    today = factories.bangkok(2026, 9, 14).date()
    slot_start = slots.slot_start_for(today, 11)
    rooms = factories.make_rooms(9)
    user = factories.make_user()
    neutral = factories.make_user()
    booking = factories.make_booking(user, rooms[0], slot_start=slot_start)
    deadline = booking.deadline
    assert deadline == slot_start + timedelta(minutes=15)

    just_before = deadline - timedelta(seconds=1)
    just_after = deadline + timedelta(seconds=1)

    def do_check_in():
        return run_operation(
            actor=user,
            operation="check_in",
            payload=checkin_service.build_payload(booking),
            body=_body(lambda ctx: checkin_service.check_in(ctx, booking=booking, room=rooms[0])),
        )

    def do_reconcile():
        # A neutral, unrelated booking whose transaction reconciles due state
        # first, under the shared lock.
        return book(neutral, rooms[1], slots.slot_start_for(today, 16))

    workers = [Worker(do_check_in, just_before), Worker(do_reconcile, just_after)]
    run_all(workers)

    booking.refresh_from_db()
    violations = Violation.objects.filter(booking=booking, kind=Violation.Kind.NO_SHOW)

    if booking.status == Booking.Status.IN_USE:
        assert violations.count() == 0, "a checked-in booking must not also be a no-show"
    else:
        assert booking.status == Booking.Status.NO_SHOW
        assert violations.count() == 1, "a no-show must record exactly one violation"


# --- A13: scheduler stopped ----------------------------------------------------


def test_expired_hold_is_cleared_by_the_next_mutation():
    """With no scheduler running, a request still reconciles and strikes once."""
    today = factories.bangkok(2026, 9, 14).date()
    moment = factories.bangkok(2026, 9, 14, 12, 30)
    rooms = factories.make_rooms(9)
    user = factories.make_user()

    # A hold whose grace expired at 11:15, and nobody ran the scheduler.
    stale = factories.make_booking(user, rooms[0], slot_start=slots.slot_start_for(today, 11))
    assert stale.deadline < moment

    with clock.frozen_clock(moment):
        # Any mutation reconciles first; this one is for a different room and time.
        outcome = book(user, rooms[5], slots.slot_start_for(today, 15))
        assert outcome.ok, outcome.code

    stale.refresh_from_db()
    assert stale.status == Booking.Status.NO_SHOW
    assert Violation.objects.filter(booking=stale).count() == 1

    # A second mutation must not record a second strike for the same booking.
    with clock.frozen_clock(moment + timedelta(minutes=1)):
        book(user, rooms[6], slots.slot_start_for(today, 17))
    assert Violation.objects.filter(booking=stale).count() == 1


def test_third_strike_in_one_request_applies_the_suspension():
    """A request that triggers the threshold must persist one suspension immediately."""
    today = factories.bangkok(2026, 9, 14).date()
    moment = factories.bangkok(2026, 9, 14, 12, 30)
    rooms = factories.make_rooms(9)
    user = factories.make_user()

    # Three separate expired holds, all confessed at once by the next request.
    for room, hour in zip(rooms[:3], (8, 10, 12), strict=False):
        stale = factories.make_booking(user, room, slot_start=slots.slot_start_for(today, hour))
        assert stale.deadline < moment

    with clock.frozen_clock(moment):
        # The mutation reconciles first: the three due holds become no-shows, the
        # third strike creates a suspension, and that sanction blocks the new
        # request - in the same transaction. A13's expected behaviour.
        outcome = book(user, rooms[8], slots.slot_start_for(today, 16))
    assert not outcome.ok
    assert outcome.code == Code.SUSPENDED, outcome.code

    assert Violation.objects.filter(user=user, kind=Violation.Kind.NO_SHOW).count() == 3
    suspensions = Suspension.objects.filter(user=user)
    assert suspensions.count() == 1, "three strikes must produce exactly one suspension"
    suspension = suspensions.first()
    assert suspension.ends_at == moment + timedelta(days=7)
    assert suspension.consumed_strikes.count() == 3


# --- A18: closure versus creation ---------------------------------------------


def test_closure_races_a_new_booking():
    """No booking may end up SCHEDULED inside a confirmed closure."""
    today = factories.bangkok(2026, 9, 14).date()
    moment = factories.bangkok(2026, 9, 14, 10, 40)
    rooms = factories.make_rooms(9)
    room = rooms[0]
    slot_start = slots.slot_start_for(today, 14)
    user = factories.make_user()
    actor = factories.make_user(is_operational_staff=True)

    def make_closure():
        return run_operation(
            actor=actor,
            operation="staff_create_closure",
            payload={"room": room.pk, "start": "14"},
            body=lambda ctx: _body(
                lambda c: {
                    "closure_id": maintenance.create_closure(
                        c,
                        room=room,
                        starts_at=slot_start,
                        ends_at=slot_start + timedelta(hours=2),
                        reason="race fixture",
                        acknowledged_in_use=True,
                    ).pk
                }
            )(ctx),
        )

    workers = [Worker(lambda: book(user, room, slot_start), moment), Worker(make_closure, moment)]
    run_all(workers)

    closure = Closure.objects.filter(room=room).first()
    assert closure is not None

    survivors = Booking.objects.filter(
        room=room,
        status=Booking.Status.SCHEDULED,
        slot_start__lt=closure.ends_at,
        slot_end__gt=closure.starts_at,
    )
    assert survivors.count() == 0, "a scheduled booking survived the closure"

    # Whatever happened, the booking is either refused or cancelled, never both
    # a live booking and a closed room.
    final = Booking.objects.filter(room=room).first()
    if final is not None:
        assert final.status in {Booking.Status.CANCELLED, Booking.Status.NO_SHOW}


# --- A30: repeated stress ------------------------------------------------------


@pytest.mark.slow
def test_repeated_contention_stress_has_no_invariant_breach():
    """Twenty rounds of contention with fresh fixtures, checking invariants each time."""
    today = factories.bangkok(2026, 9, 14).date()
    moment = factories.bangkok(2026, 9, 14, 10, 40)
    rooms = factories.make_rooms(9)

    for round_index in range(20):
        room = rooms[round_index % len(rooms)]
        slot_start = slots.slot_start_for(today, 15 + (round_index % 4))
        users = [factories.make_user() for _ in range(6)]
        workers = [Worker(lambda u=user, r=room, s=slot_start: book(u, r, s), moment) for user in users]

        with clock.frozen_clock(moment):
            run_all(workers)

        live = Booking.objects.filter(
            room=room, slot_start=slot_start, status__in=["SCHEDULED", "IN_USE", "COMPLETED"]
        )
        assert live.count() == 1, f"round {round_index}: {live.count()} bookings persisted"

        # No user may hold two bookings in the same hour anywhere.
        for user in users:
            assert (
                Booking.objects.filter(user=user, slot_start=slot_start)
                .exclude(status=Booking.Status.CANCELLED)
                .count()
                <= 1
            )


def _live(room, slot_start):
    return Booking.objects.filter(room=room, slot_start=slot_start).exclude(status=Booking.Status.CANCELLED)


# --- Concurrent moves: the loser keeps the room they already hold ------------


def test_two_students_race_to_move_into_the_same_room():
    moment = factories.bangkok(2026, 9, 14, 10, 40)
    rooms = factories.make_rooms(9)
    slot_start = slots.slot_start_for(factories.bangkok(2026, 9, 14).date(), 11)
    student_a = factories.make_user()
    student_b = factories.make_user()

    with clock.frozen_clock(moment):
        booking_a = factories.make_booking(student_a, rooms[0], slot_start=slot_start)
        booking_b = factories.make_booking(student_b, rooms[1], slot_start=slot_start)
        workers = [
            Worker(lambda: move(student_a, booking_a, rooms[2]), moment),
            Worker(lambda: move(student_b, booking_b, rooms[2]), moment),
        ]
        run_all(workers)

    successes = [worker for worker in workers if worker.outcome.ok]
    assert len(successes) == 1, [worker.outcome.code for worker in workers]
    for worker in workers:
        if not worker.outcome.ok:
            assert worker.outcome.code in MOVE_REJECTIONS, worker.outcome.code

    arrived = _live(rooms[2], slot_start)
    assert arrived.count() == 1

    for booking, original in ((booking_a, rooms[0]), (booking_b, rooms[1])):
        booking.refresh_from_db()
        if booking.pk == arrived.get().pk:
            continue
        assert booking.room_id == original.pk
        assert booking.status == Booking.Status.SCHEDULED

    for student in (student_a, student_b):
        held = Booking.objects.filter(user=student).exclude(status=Booking.Status.CANCELLED)
        assert held.count() == 1


def test_a_move_races_a_new_booking_for_the_same_cell():
    moment = factories.bangkok(2026, 9, 14, 10, 40)
    rooms = factories.make_rooms(9)
    slot_start = slots.slot_start_for(factories.bangkok(2026, 9, 14).date(), 11)
    student_a = factories.make_user()
    student_c = factories.make_user()

    with clock.frozen_clock(moment):
        booking_a = factories.make_booking(student_a, rooms[0], slot_start=slot_start)
        workers = [
            Worker(lambda: move(student_a, booking_a, rooms[1]), moment),
            Worker(lambda: book(student_c, rooms[1], slot_start), moment),
        ]
        run_all(workers)

    move_worker, book_worker = workers
    assert (move_worker.outcome.ok + book_worker.outcome.ok) == 1, [
        worker.outcome.code for worker in workers
    ]
    for worker in workers:
        if not worker.outcome.ok:
            assert worker.outcome.code in MOVE_REJECTIONS, worker.outcome.code

    holders = _live(rooms[1], slot_start)
    assert holders.count() == 1
    holder = holders.get()

    if not move_worker.outcome.ok:
        booking_a.refresh_from_db()
        assert booking_a.room_id == rooms[0].pk
        assert booking_a.status == Booking.Status.SCHEDULED
        assert holder.user_id == student_c.pk
    else:
        assert holder.user_id == student_a.pk
        assert (
            Booking.objects.filter(user=student_c, slot_start=slot_start)
            .exclude(status=Booking.Status.CANCELLED)
            .count()
            == 0
        )


def test_many_movers_one_free_room_keeps_every_loser_housed():
    moment = factories.bangkok(2026, 9, 14, 10, 40)
    rooms = factories.make_rooms(9)
    slot_start = slots.slot_start_for(factories.bangkok(2026, 9, 14).date(), 11)
    students = [factories.make_user() for _ in range(8)]

    with clock.frozen_clock(moment):
        bookings = [
            factories.make_booking(student, rooms[index], slot_start=slot_start)
            for index, student in enumerate(students)
        ]
        workers = [
            Worker(lambda actor=student, booking=booking: move(actor, booking, rooms[8]), moment)
            for student, booking in zip(students, bookings, strict=True)
        ]
        run_all(workers)

    successes = [worker for worker in workers if worker.outcome.ok]
    assert len(successes) == 1, [worker.outcome.code for worker in workers]
    for worker in workers:
        if not worker.outcome.ok:
            assert worker.outcome.code in MOVE_REJECTIONS, worker.outcome.code

    assert _live(rooms[8], slot_start).count() == 1

    for index, (worker, booking) in enumerate(zip(workers, bookings, strict=True)):
        if worker.outcome.ok:
            continue
        booking.refresh_from_db()
        assert booking.room_id == rooms[index].pk
        assert booking.status == Booking.Status.SCHEDULED

    assert (
        Booking.objects.filter(slot_start=slot_start).exclude(status=Booking.Status.CANCELLED).count()
        == 8
    )
