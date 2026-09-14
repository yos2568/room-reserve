"""The common transaction protocol for every operational write.

Follows the V3 section 5 pseudo-code exactly:

    BEGIN transaction
      lock BookingControl with bounded wait
      capture authoritative now
      reconcile all due booking states, violations, sanction expiry, thresholds
      check the authenticated user's idempotency key and payload
      if a successful prior operation matches: return its result, no re-run
      validate the requested operation against reconciled state
      if validation rejects: record/return a clean rejection; COMMIT reconciliation
      else: apply the change inside a savepoint, write audit and outbox events
    COMMIT

The critical property is that a normal rejection does **not** roll back the due
no-show or suspension work that made the rejection correct. That is why the
mutation sits in a nested savepoint and rejections are returned rather than
raised out of the outer atomic block.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, OperationalError, connection, transaction
from django.utils import timezone

from core.models import BookingControl, OperationRequest

from . import clock
from . import reconcile as reconcile_module
from .errors import (
    Code,
    LockUnavailable,
    OperationOutcome,
    OperationRejected,
    ReconciliationBacklog,
)

logger = logging.getLogger(__name__)

# Transient failures worth one more attempt: serialization failure and deadlock.
_TRANSIENT_SQLSTATES = {"40001", "40P01"}
_MAX_TRANSIENT_RETRIES = 2


@dataclass
class OperationContext:
    """Everything a service body needs, captured under the lock."""

    now: datetime
    actor: object | None
    operation: str
    payload: dict
    key: str | None
    fingerprint: str
    reconciled: dict = field(default_factory=dict)
    correlation_id: object | None = None

    def audit(self, **kwargs):
        from .audit import record_audit

        return record_audit(correlation_id=self.correlation_id, **kwargs)


def fingerprint_payload(payload: dict) -> str:
    """Stable fingerprint of the request payload.

    Two different payloads under one key must not be treated as the same
    operation, or a replay could change the requested room (V3 section 5).
    """
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sqlstate(exc: BaseException) -> str | None:
    cause = exc
    for _ in range(4):
        state = getattr(cause, "sqlstate", None)
        if state:
            return state
        cause = getattr(cause, "__cause__", None)
        if cause is None:
            break
    return None


def _is_lock_timeout(exc: BaseException) -> bool:
    if _sqlstate(exc) == "55P03":  # lock_not_available
        return True
    text = str(exc).lower()
    return "lock timeout" in text or "could not obtain lock" in text


def _acquire_control_lock() -> BookingControl:
    """Take the shared first lock, with a bounded wait.

    The attempt is wrapped in a savepoint: a ``lock_timeout`` error aborts the
    surrounding transaction in PostgreSQL, and rolling back to the savepoint
    keeps the outer transaction usable instead of poisoning it.

    The singleton row is created if it is missing. The migration pre-seeds it, but
    a missing row would otherwise turn every operational write into a server
    error - for instance on a database restored without the seed, or in a test run
    that flushed the table.
    """
    timeout_ms = max(1, int(settings.BOOKING_LOCK_TIMEOUT_SECONDS * 1000))
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                # SET accepts no bind parameters; timeout_ms is an int we control.
                cursor.execute(f"SET LOCAL lock_timeout = '{timeout_ms}ms'")
            try:
                return BookingControl.objects.select_for_update().get(pk=1)
            except BookingControl.DoesNotExist:
                # get_or_create tolerates a concurrent create by re-selecting.
                BookingControl.objects.get_or_create(pk=1)
                return BookingControl.objects.select_for_update().get(pk=1)
    except OperationalError as exc:
        if _is_lock_timeout(exc):
            raise LockUnavailable from exc
        raise


def _classify_integrity_error(exc: IntegrityError, *, context: str) -> OperationOutcome:
    """Map a database constraint violation to a clean conflict result."""
    text = str(exc).lower()
    if "booking_room_slot_unique" in text:
        return OperationOutcome.reject(Code.SLOT_TAKEN)
    if "booking_user_slot_unique" in text:
        return OperationOutcome.reject(Code.ADJACENCY_CONFLICT)
    if "operation_request_user_operation_key_unique" in text:
        # A concurrent request with the same key won the race.
        return OperationOutcome.reject(Code.BUSY)
    logger.warning("Unhandled integrity error during %s: %s", context, exc)
    return OperationOutcome.reject(Code.SLOT_TAKEN)


def run_operation(
    *,
    actor,
    operation: str,
    payload: dict,
    body,
    key: str | None = None,
) -> OperationOutcome:
    """Run one operational write under the shared lock.

    ``body(ctx)`` returns an :class:`OperationOutcome`. To reject, it should raise
    :class:`OperationRejected`; a returned non-ok outcome also discards the
    savepoint so no partial mutation survives.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return _run_once(actor=actor, operation=operation, payload=payload, body=body, key=key)
        except OperationalError as exc:
            if _sqlstate(exc) in _TRANSIENT_SQLSTATES and attempt <= _MAX_TRANSIENT_RETRIES:
                logger.info(
                    "Transient %s during %s; retry %s/%s",
                    _sqlstate(exc),
                    operation,
                    attempt,
                    _MAX_TRANSIENT_RETRIES,
                )
                continue
            raise


def _run_once(*, actor, operation: str, payload: dict, body, key: str | None) -> OperationOutcome:
    from core.middleware import current_correlation_id

    correlation_id = current_correlation_id()

    with transaction.atomic():
        try:
            _acquire_control_lock()
        except LockUnavailable:
            logger.info("Control lock unavailable for %s; returning busy.", operation)
            return OperationOutcome.reject(Code.BUSY)

        now = clock.now()

        try:
            reconciled = reconcile_module.reconcile(now, actor=actor)
        except ReconciliationBacklog:
            # Fail closed rather than skipping stale rows to look fast.
            return OperationOutcome.reject(Code.BUSY)

        fingerprint = fingerprint_payload(payload)

        if key and actor is not None and getattr(actor, "pk", None):
            prior = OperationRequest.objects.filter(
                user=actor, operation=operation, key=key
            ).first()
            if prior is not None:
                if prior.payload_fingerprint != fingerprint:
                    return OperationOutcome.reject(Code.IDEMPOTENCY_PAYLOAD_MISMATCH)
                return OperationOutcome.replay(prior.result)

        ctx = OperationContext(
            now=now,
            actor=actor,
            operation=operation,
            payload=payload,
            key=key,
            fingerprint=fingerprint,
            reconciled=reconciled,
            correlation_id=correlation_id,
        )

        try:
            with transaction.atomic():
                outcome = body(ctx)
                if not outcome.ok:
                    # Discard any partial write; reconciliation is untouched.
                    transaction.set_rollback(True)
        except OperationRejected as exc:
            outcome = exc.outcome
        except IntegrityError as exc:
            outcome = _classify_integrity_error(exc, context=operation)

        if outcome.ok and key and actor is not None and getattr(actor, "pk", None):
            try:
                with transaction.atomic():
                    OperationRequest.objects.create(
                        user=actor,
                        operation=operation,
                        key=key,
                        payload_fingerprint=fingerprint,
                        result=outcome.as_record(),
                    )
            except IntegrityError:
                prior = OperationRequest.objects.filter(
                    user=actor, operation=operation, key=key
                ).first()
                if prior is not None and prior.payload_fingerprint == fingerprint:
                    return OperationOutcome.replay(prior.result)
                return OperationOutcome.reject(Code.IDEMPOTENCY_PAYLOAD_MISMATCH)

        return outcome


def prune_operation_requests(older_than_days: int = 2) -> int:
    """Housekeeping: idempotency records only need to outlive a browser retry."""
    cutoff = timezone.now() - timedelta(days=older_than_days)
    deleted, _ = OperationRequest.objects.filter(created_at__lt=cutoff).delete()
    return deleted
