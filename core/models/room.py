"""Rooms, closures and calendar overrides."""

from __future__ import annotations

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.identity import InstrumentCategory


class Room(models.Model):
    """A practice room. Most are general; the larger room is instrument-specific."""

    class ReservationScope(models.TextChoices):
        """Who may reserve the room *in advance*.

        A room set to ``LISTED`` with no categories lets **nobody** reserve it.
        That is deliberate: an empty list is far more likely to be a mistake than
        an intention to open the room to everyone, so the failure is closed.
        Everyone may still walk in once an hour has started and the room is free,
        which is what keeps a restricted room from being dead capacity.
        """

        EVERYONE = "EVERYONE", _("Any eligible student")
        LISTED = "LISTED", _("Only the instrument categories listed")

    number = models.CharField(_("room number"), max_length=32, unique=True)
    label = models.CharField(_("label"), max_length=120, blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    position = models.PositiveSmallIntegerField(default=0, help_text=_("Display order."))
    reservation_scope = models.CharField(
        _("who may reserve"),
        max_length=16,
        choices=ReservationScope.choices,
        default=ReservationScope.EVERYONE,
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("room")
        verbose_name_plural = _("rooms")
        ordering = ["position", "number"]
        indexes = [models.Index(fields=["is_active"], name="room_active_idx")]

    def __str__(self) -> str:
        return self.label or f"Room {self.number}"

    @property
    def is_restricted(self) -> bool:
        return self.reservation_scope == self.ReservationScope.LISTED

    @property
    def allowed_category_values(self) -> list[str]:
        """Categories permitted to reserve. Uses the prefetch cache when present."""
        return [row.category for row in self.allowed_categories.all()]

    def may_be_reserved_by(self, category: str | None) -> bool:
        """Whether a student in ``category`` may reserve this room in advance.

        Fails closed: an unlisted category, an empty category, or a room marked
        ``LISTED`` with nothing configured all answer no.
        """
        if not self.is_restricted:
            return True
        if not category:
            return False
        return any(row.category == category for row in self.allowed_categories.all())


class RoomAllowedCategory(models.Model):
    """One instrument category permitted to reserve a room.

    A separate row per category rather than a list field, so the constraint is in
    the database and the grid can prefetch it in one query.
    """

    room = models.ForeignKey(
        "core.Room",
        on_delete=models.CASCADE,
        related_name="allowed_categories",
    )
    category = models.CharField(max_length=32, choices=InstrumentCategory.choices)

    class Meta:
        verbose_name = _("room instrument category")
        verbose_name_plural = _("room instrument categories")
        constraints = [
            models.UniqueConstraint(
                fields=["room", "category"],
                name="room_allowed_category_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.room} · {self.get_category_display()}"


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
