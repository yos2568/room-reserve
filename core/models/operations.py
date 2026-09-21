"""Audit trail, transactional outbox, idempotency records and job heartbeat."""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AuditEvent(models.Model):
    """Append-only record of an operational change.

    V3 section 4: "append-only through app permissions". The model therefore
    refuses updates and deletes outright, and no staff screen exposes either.
    """

    actor = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    actor_label = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Used for non-user actors such as the scheduler tick."),
    )
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        verbose_name = _("audit event")
        verbose_name_plural = _("audit events")
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"], name="audit_entity_idx"),
            models.Index(fields=["action", "occurred_at"], name="audit_action_idx"),
            models.Index(fields=["occurred_at"], name="audit_occurred_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}:{self.entity_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit events are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events are append-only and cannot be deleted.")


class Notification(models.Model):
    """A transactional outbox row. Inserted inside the state-change transaction."""

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        SENT = "SENT", _("Sent")
        EXHAUSTED = "EXHAUSTED", _("Exhausted")
        CANCELLED = "CANCELLED", _("Cancelled")

    kind = models.CharField(max_length=48)
    recipient = models.ForeignKey(
        "core.User",
        on_delete=models.PROTECT,
        related_name="notifications",
        null=True,
        blank=True,
    )
    recipient_email = models.EmailField(blank=True)
    language = models.CharField(max_length=5, default="th")
    payload = models.JSONField(default=dict)
    dedupe_key = models.CharField(max_length=191, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(default=timezone.now)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["PENDING", "SENT", "EXHAUSTED", "CANCELLED"]),
                name="notification_status_valid",
            ),
        ]
        indexes = [
            # Drives "oldest actionable outbox item" monitoring.
            models.Index(fields=["status", "next_attempt_at"], name="notif_due_idx"),
            models.Index(fields=["lease_until"], name="notif_lease_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} -> {self.recipient_id} [{self.status}]"

    def is_leased(self, now) -> bool:
        return self.lease_until is not None and self.lease_until > now


class OperationRequest(models.Model):
    """Idempotency record: one authenticated user, one operation, one key."""

    user = models.ForeignKey(
        "core.User",
        on_delete=models.PROTECT,
        related_name="operation_requests",
    )
    operation = models.CharField(max_length=64)
    key = models.CharField(max_length=128)
    payload_fingerprint = models.CharField(max_length=64)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("operation request")
        verbose_name_plural = _("operation requests")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "operation", "key"],
                name="operation_request_user_operation_key_unique",
            ),
        ]
        indexes = [models.Index(fields=["created_at"], name="opreq_created_idx")]

    def __str__(self) -> str:
        return f"{self.operation}:{self.key} by {self.user_id}"


class JobHeartbeat(models.Model):
    """Last successful run marker for a scheduled job.

    Monitoring alerts when the heartbeat is older than the configured threshold,
    and separately when the oldest actionable outbox item is older than its
    threshold. These are independent of HTTP liveness so that a failed mail
    provider cannot make process liveness fail and trigger restart loops
    (V3 sections 6 and 11).
    """

    name = models.CharField(max_length=64, unique=True)
    last_run_at = models.DateTimeField(default=timezone.now)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = _("job heartbeat")
        verbose_name_plural = _("job heartbeats")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} @ {self.last_run_at:%Y-%m-%d %H:%M:%S}"
