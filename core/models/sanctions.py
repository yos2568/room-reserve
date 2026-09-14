"""Violations (strikes) and suspensions."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ViolationQuerySet(models.QuerySet):
    def eligible_strikes(self, now):
        """Strikes that currently count toward the next automatic suspension.

        Eligible means: not voided, flagged as a strike, not already consumed by a
        sanction, and inside its rolling window (V3 section 7).
        """
        return self.filter(
            voided_at__isnull=True,
            counts_as_strike=True,
            consumed_by__isnull=True,
            occurred_at__lte=now,
            expires_at__gt=now,
        )

    def unconsumed(self):
        return self.filter(voided_at__isnull=True, counts_as_strike=True, consumed_by__isnull=True)


class Violation(models.Model):
    """A recorded policy violation. For a no-show the occurrence is the deadline."""

    class Kind(models.TextChoices):
        NO_SHOW = "NO_SHOW", _("No-show")
        MANUAL = "MANUAL", _("Recorded by staff")

    user = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="violations")
    booking = models.ForeignKey(
        "core.Booking",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="violations",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices)
    occurred_at = models.DateTimeField(
        help_text=_("When the violation happened: the check-in deadline for a no-show."),
    )
    expires_at = models.DateTimeField(
        help_text=_("End of the rolling strike window, frozen at recording time."),
    )
    recorded_at = models.DateTimeField(default=timezone.now)
    counts_as_strike = models.BooleanField(default=True)
    actor = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="violations_recorded",
    )
    note = models.TextField(blank=True)

    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="violations_voided",
    )
    void_reason = models.TextField(blank=True)

    consumed_by = models.ForeignKey(
        "core.Suspension",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumed_strikes",
        help_text=_("The automatic suspension this strike triggered; set at most once."),
    )

    incident = models.ForeignKey(
        "core.ServiceIncident",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="violations",
    )

    objects = ViolationQuerySet.as_manager()

    class Meta:
        verbose_name = _("violation")
        verbose_name_plural = _("violations")
        ordering = ["-occurred_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["NO_SHOW", "MANUAL"]),
                name="violation_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("occurred_at")),
                name="violation_expiry_after_occurrence",
            ),
            models.CheckConstraint(
                condition=models.Q(voided_at__isnull=False) | models.Q(void_reason=""),
                name="violation_void_reason_requires_void",
            ),
            models.CheckConstraint(
                condition=~models.Q(kind="NO_SHOW") | models.Q(booking__isnull=False),
                name="violation_no_show_requires_booking",
            ),
            # A no-show violation is unique per booking.
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(kind="NO_SHOW"),
                name="violation_no_show_unique_per_booking",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "expires_at"],
                name="violation_user_expiry_idx",
            ),
            models.Index(fields=["user", "consumed_by"], name="violation_consumed_idx"),
            models.Index(fields=["occurred_at"], name="violation_occurred_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.kind} at {self.occurred_at:%Y-%m-%d %H:%M}"

    @property
    def is_void(self) -> bool:
        return self.voided_at is not None

    def is_eligible_strike(self, now) -> bool:
        return (
            self.voided_at is None
            and self.counts_as_strike
            and self.consumed_by_id is None
            and self.occurred_at <= now < self.expires_at
        )


class SuspensionQuerySet(models.QuerySet):
    def active(self, now):
        """Suspensions in force at ``now``.

        A null ``ends_at`` is an explicit indefinite staff suspension. Expiry
        restores eligibility purely by timestamp comparison, with no cron needed
        (V3 section 7).
        """
        return self.filter(lifted_at__isnull=True, starts_at__lte=now).filter(
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now)
        )


class Suspension(models.Model):
    """A sanction. Automatic sanctions always carry a required end timestamp."""

    class Source(models.TextChoices):
        AUTO = "AUTO", _("Automatic (strike threshold)")
        MANUAL = "MANUAL", _("Staff decision")

    user = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="suspensions")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Required for automatic sanctions; null means an indefinite staff suspension."),
    )
    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suspensions_lifted",
    )
    lift_reason = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    source = models.CharField(max_length=16, choices=Source.choices)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="suspensions_created",
    )
    start_notified_at = models.DateTimeField(null=True, blank=True)
    end_notified_at = models.DateTimeField(null=True, blank=True)

    objects = SuspensionQuerySet.as_manager()

    class Meta:
        verbose_name = _("suspension")
        verbose_name_plural = _("suspensions")
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(source__in=["AUTO", "MANUAL"]),
                name="suspension_source_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=models.F("starts_at")),
                name="suspension_end_after_start",
            ),
            # An automatic sanction always has an end: the seven-day default.
            models.CheckConstraint(
                condition=~models.Q(source="AUTO") | models.Q(ends_at__isnull=False),
                name="suspension_auto_requires_end",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "ends_at"], name="suspension_user_end_idx"),
            models.Index(fields=["lifted_at"], name="suspension_lifted_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} suspended from {self.starts_at:%Y-%m-%d %H:%M}"

    def is_active_at(self, now) -> bool:
        if self.lifted_at is not None or self.starts_at > now:
            return False
        return self.ends_at is None or now < self.ends_at

    @property
    def is_indefinite(self) -> bool:
        return self.ends_at is None
