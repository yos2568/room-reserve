"""Rooms, closures and calendar overrides."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Room(models.Model):
    """One of the nine upright-piano practice rooms."""

    number = models.CharField(_("room number"), max_length=32, unique=True)
    label = models.CharField(_("label"), max_length=120, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    position = models.PositiveSmallIntegerField(default=0, help_text=_("Display order."))
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("room")
        verbose_name_plural = _("rooms")
        ordering = ["position", "number"]
        indexes = [models.Index(fields=["is_active"], name="room_active_idx")]

    def __str__(self) -> str:
        return self.label or f"Room {self.number}"


class Closure(models.Model):
    """An explicit closure of one room or of every room.

    Precedence: an explicit closure wins over a date override, which wins over the
    weekly timetable. Intersecting any part of an hourly slot with a closure
    blocks new reservations and walk-ins for that slot (V3 section 3.1).
    """

    room = models.ForeignKey(
        "core.Room",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="closures",
        help_text=_("Leave empty to close every room."),
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.TextField()
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="closures_created",
    )
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="closures_revoked",
    )
    revoked_reason = models.TextField(blank=True)
    # Set when staff confirmed the preview under the shared lock, so a closure
    # that was created with an acknowledgement can be audited afterwards.
    acknowledged_in_use = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("closure")
        verbose_name_plural = _("closures")
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="closure_end_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["starts_at", "ends_at"], name="closure_bounds_idx"),
            models.Index(fields=["room", "starts_at"], name="closure_room_start_idx"),
        ]

    def __str__(self) -> str:
        scope = self.room or _("all rooms")
        return f"{scope} {self.starts_at:%Y-%m-%d %H:%M}–{self.ends_at:%H:%M}"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class CalendarOverride(models.Model):
    """A date-specific opening or closing rule, keyed by Bangkok local date."""

    local_date = models.DateField(unique=True)
    is_open = models.BooleanField(
        default=False,
        help_text=_("Unchecked closes the whole day (holiday)."),
    )
    open_hour = models.PositiveSmallIntegerField(null=True, blank=True)
    close_hour = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Exclusive end hour: the 20:00 close is stored as 20."),
    )
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        on_delete=models.SET_NULL,
        related_name="calendar_overrides_created",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("calendar override")
        verbose_name_plural = _("calendar overrides")
        ordering = ["-local_date"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(is_open=False)
                    | (
                        models.Q(open_hour__isnull=False)
                        & models.Q(close_hour__isnull=False)
                        & models.Q(close_hour__gt=models.F("open_hour"))
                    )
                ),
                name="calendar_override_hours_consistent",
            ),
            models.CheckConstraint(
                condition=models.Q(open_hour__isnull=True) | models.Q(open_hour__lte=23),
                name="calendar_override_open_hour_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(close_hour__isnull=True) | models.Q(close_hour__lte=24),
                name="calendar_override_close_hour_valid",
            ),
        ]

    def __str__(self) -> str:
        if self.is_open:
            return f"{self.local_date} open {self.open_hour}:00–{self.close_hour}:00"
        return f"{self.local_date} closed ({self.reason or 'holiday'})"
