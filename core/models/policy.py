"""Policy versions and service incidents."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class PolicyVersion(models.Model):
    """An immutable, validated snapshot of the operational settings.

    Fixed hourly slot geometry is not part of the editable policy: V3 section 8
    states that "Fixed hourly geometry is not staff-editable." Changing quota,
    horizon, grace or strike rules creates a new version; existing bookings keep
    the values stored on the row, so a later edit cannot retroactively invalidate
    an already-permitted check-in.
    """

    version = models.PositiveIntegerField(unique=True)
    label = models.CharField(max_length=120, blank=True)
    snapshot = models.JSONField()
    activated_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="policy_versions_created",
    )
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = _("policy version")
        verbose_name_plural = _("policy versions")
        ordering = ["-version"]

    def __str__(self) -> str:
        return f"Policy v{self.version}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError(
                "Policy versions are immutable. Create a new version instead of "
                "editing an activated one (V3 section 8)."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Policy versions are immutable and cannot be deleted.")

    # Property names mirror the snapshot keys and the settings names, so there is
    # a single vocabulary across policy, settings and services.
    @property
    def daily_quota(self) -> int:
        return int(self.snapshot.get("daily_quota", 2))

    @property
    def horizon_days(self) -> int:
        return int(self.snapshot.get("horizon_days", 7))

    # The fixed hourly geometry, recorded in the snapshot for the audit trail.
    # Templates state the opening hours from these, so they have to resolve:
    # without them the grid and the posters silently render ":00–:00".
    @property
    def opening_hour(self) -> int:
        return int(self.snapshot.get("opening_hour", settings.OPENING_SLOT_START_HOUR))

    @property
    def closing_hour(self) -> int:
        return int(self.snapshot.get("last_slot_hour", settings.LAST_SLOT_START_HOUR)) + 1

    @property
    def checkin_grace_minutes(self) -> int:
        return int(self.snapshot.get("checkin_grace_minutes", 15))

    @property
    def strike_window_days(self) -> int:
        return int(self.snapshot.get("strike_window_days", 30))

    @property
    def strike_threshold(self) -> int:
        return int(self.snapshot.get("strike_threshold", 3))

    @property
    def auto_suspension_days(self) -> int:
        return int(self.snapshot.get("auto_suspension_days", 7))

    @property
    def reminder_lead_minutes(self) -> int:
        return int(self.snapshot.get("reminder_lead_minutes", 30))


class ServiceIncident(models.Model):
    """A recorded service failure or authorized room use that must not penalise students.

    Before release/no-show processing, matching scheduled bookings can be
    staff-cancelled without penalty. If no-shows were already recorded, staff
    voids the affected strikes and reviews any sanction; attendance is never
    silently backdated (V3 section 7).
    """

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    rooms = models.ManyToManyField(
        "core.Room",
        blank=True,
        related_name="incidents",
        help_text=_("Leave empty to cover every room."),
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="incidents_created",
    )
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        verbose_name = _("service incident")
        verbose_name_plural = _("service incidents")
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="incident_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["starts_at", "ends_at"], name="incident_bounds_idx")]

    def __str__(self) -> str:
        return f"Incident {self.starts_at:%Y-%m-%d %H:%M}–{self.ends_at:%H:%M}"

    def covers(self, slot_start, slot_end, room_id: int | None) -> bool:
        """True when the incident interval intersects the slot and room scope."""
        if slot_end <= self.starts_at or slot_start >= self.ends_at:
            return False
        if room_id is None:
            return True
        room_ids = {room.pk for room in self.rooms.all()}
        return not room_ids or room_id in room_ids
