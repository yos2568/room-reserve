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
    capacity = models.PositiveSmallIntegerField(
        default=1,
        help_text=_("Approximate number of people the room can accommodate."),
    )
    equipment = models.TextField(
        blank=True,
        help_text=_("Comma-separated equipment or facilities available in this room."),
    )
    requires_approval = models.BooleanField(
        default=False,
        help_text=_("Reservations for this room must be approved by a room administrator."),
    )
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
    def equipment_items(self) -> list[str]:
        """Display equipment as readable chips without storing a second table."""
        return [item.strip() for item in self.equipment.replace("\n", ",").split(",") if item.strip()]

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


class RoomAdministrator(models.Model):
    """A named user who may manage one room's profile and approvals."""

    room = models.ForeignKey("core.Room", on_delete=models.CASCADE, related_name="administrators")
    user = models.ForeignKey("core.User", on_delete=models.CASCADE, related_name="room_administrations")
    granted_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="room_administrator_grants",
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["room", "user"]
        constraints = [
            models.UniqueConstraint(fields=["room", "user"], name="room_admin_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.user} → {self.room}"


class Weekday(models.IntegerChoices):
    """ISO weekday, matching ``date.weekday()``: Monday is 0."""

    MONDAY = 0, _("Monday")
    TUESDAY = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY = 3, _("Thursday")
    FRIDAY = 4, _("Friday")
    SATURDAY = 5, _("Saturday")
    SUNDAY = 6, _("Sunday")


class WeeklyBlock(models.Model):
    """A recurring weekly unavailability of one room: the teaching timetable.

    Rooms 303 and 304 are teaching rooms as well as practice rooms, so a class
    that meets in them every week makes those hours unreservable and unwalkable,
    exactly as a closure does, but recurring and keyed by weekday and hour range
    instead of by date. Check-in is deliberately untouched: a booking made before
    a schedule change stays valid, the same survivorship a room-audience change
    grants. An optional validity window bounds the block to a semester.
    """

    room = models.ForeignKey(
        "core.Room",
        on_delete=models.PROTECT,
        related_name="weekly_blocks",
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_hour = models.PositiveSmallIntegerField(help_text=_("Inclusive, e.g. 10 for 10:00."))
    end_hour = models.PositiveSmallIntegerField(
        help_text=_("Exclusive end hour: 12 means the block ends at 12:00.")
    )
    reason = models.CharField(max_length=200, help_text=_("Shown to students, e.g. the course name."))
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True, help_text=_("Inclusive; empty means no end."))
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("weekly room block")
        verbose_name_plural = _("weekly room blocks")
        ordering = ["room", "weekday", "start_hour"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_hour__gt=models.F("start_hour")),
                name="weekly_block_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(end_hour__lte=24),
                name="weekly_block_hours_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_from__isnull=True)
                | models.Q(valid_until__isnull=True)
                | models.Q(valid_until__gte=models.F("valid_from")),
                name="weekly_block_validity_window",
            ),
            models.UniqueConstraint(
                fields=["room", "weekday", "start_hour"],
                name="weekly_block_room_day_hour_unique",
            ),
        ]
        indexes = [models.Index(fields=["room", "weekday"], name="weekly_block_room_day_idx")]

    def __str__(self) -> str:
        return f"{self.room} {self.get_weekday_display()} {self.start_hour}:00–{self.end_hour}:00"


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
