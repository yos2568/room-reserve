"""The booking control row and the booking itself."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

BANGKOK = ZoneInfo("Asia/Bangkok")

BLOCKING_STATUSES = ("SCHEDULED", "IN_USE", "COMPLETED")
QUOTA_STATUSES = ("SCHEDULED", "IN_USE", "COMPLETED", "NO_SHOW")


class BookingControl(models.Model):
    """Singleton row used as the first lock for every operational write.

    V3 section 5: "Use one database BookingControl row as the first lock for every
    operational write ... This serializes short writes at this scale and avoids
    mismatched lock orders." Reads never lock this row.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("booking control")
        verbose_name_plural = _("booking control")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1),
                name="booking_control_singleton",
            ),
        ]

    def __str__(self) -> str:
        return "BookingControl"


class Booking(models.Model):
    """A reservation of one room for one fixed hourly slot.

    Slot identity is always room plus the *original* hourly start, even for a late
    walk-in: a walk-in at 08:45 still occupies the 08:00 slot and still blocks the
    09:00 slot (V3 sections 3.1 and 3.3).
    """

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", _("Scheduled")
        IN_USE = "IN_USE", _("In use")
        COMPLETED = "COMPLETED", _("Completed")
        NO_SHOW = "NO_SHOW", _("No-show")
        CANCELLED = "CANCELLED", _("Cancelled")

    class Source(models.TextChoices):
        ADVANCE = "ADVANCE", _("Advance reservation")
        WALK_IN = "WALK_IN", _("Walk-in / use now")

    user = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="bookings")
    room = models.ForeignKey("core.Room", on_delete=models.PROTECT, related_name="bookings")
    slot_start = models.DateTimeField()
    slot_end = models.DateTimeField()
    slot_date = models.DateField(help_text=_("Bangkok local date of slot_start."))
    deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Check-in deadline for an advance reservation; null for walk-ins."),
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    source = models.CharField(max_length=16, choices=Source.choices)

    created_at = models.DateTimeField(default=timezone.now)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="check_ins_performed",
    )
    # Secret per-reservation token (D-31): encodes the emailed QR target. Null for
    # walk-ins, which are already in use and have nothing to check in to.
    checkin_token = models.CharField(
        _("check-in token"),
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancellations_performed",
    )
    cancel_reason = models.TextField(blank=True)
    late_cancel = models.BooleanField(
        default=False,
        help_text=_("Cancelled less than 60 minutes before the slot started (reporting only)."),
    )
    actual_end = models.DateTimeField(null=True, blank=True)
    completion_reason = models.TextField(blank=True)

    # Set when a closure or deactivation overlapped a live session, so staff can
    # confirm what happened on site. Until it is resolved the session keeps its
    # historical slot occupancy and cannot be re-sold (V3 sections 6 and 8).
    needs_review = models.BooleanField(
        default=False,
        help_text=_("Pending on-site intervention by staff."),
    )
    review_note = models.TextField(blank=True)

    policy_version = models.ForeignKey(
        "core.PolicyVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    original_no_show = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="walk_ins_released",
        help_text=_("The forfeited booking whose remaining time this walk-in took."),
    )
    incident = models.ForeignKey(
        "core.ServiceIncident",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bookings",
    )

    class Meta:
        verbose_name = _("booking")
        verbose_name_plural = _("bookings")
        ordering = ["-slot_start"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[*BLOCKING_STATUSES, "NO_SHOW", "CANCELLED"]),
                name="booking_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source__in=["ADVANCE", "WALK_IN"]),
                name="booking_source_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(slot_end=models.F("slot_start") + timedelta(hours=1)),
                name="booking_slot_length_one_hour",
            ),
            # Asia/Bangkok is a whole-hour offset with no daylight saving, so an
            # exact local-hour boundary is also an exact UTC-hour boundary.
            models.CheckConstraint(
                condition=models.Q(slot_start__minute=0, slot_start__second=0),
                name="booking_slot_hour_aligned",
            ),
            # A walk-in is created already in use and is never SCHEDULED.
            models.CheckConstraint(
                condition=models.Q(source="ADVANCE") | ~models.Q(status="SCHEDULED"),
                name="booking_walkin_never_scheduled",
            ),
            # ADVANCE carries a check-in deadline; WALK_IN never does.
            models.CheckConstraint(
                condition=(
                    models.Q(source="ADVANCE", deadline__isnull=False)
                    | models.Q(source="WALK_IN", deadline__isnull=True)
                ),
                name="booking_deadline_matches_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(checked_in_at__isnull=True)
                    | (
                        models.Q(checked_in_at__gte=models.F("slot_start"))
                        & models.Q(checked_in_at__lt=models.F("slot_end"))
                    )
                ),
                name="booking_checkin_within_slot",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="CANCELLED", cancelled_at__isnull=False)
                    | (~models.Q(status="CANCELLED") & models.Q(cancelled_at__isnull=True))
                ),
                name="booking_cancellation_requires_metadata",
            ),
            models.CheckConstraint(
                condition=models.Q(cancel_reason="") | models.Q(cancelled_at__isnull=False),
                name="booking_cancel_reason_requires_cancellation",
            ),
            # Completed sessions and in-use/scheduled bookings reserve the slot.
            models.UniqueConstraint(
                fields=["room", "slot_start"],
                condition=models.Q(status__in=list(BLOCKING_STATUSES)),
                name="booking_room_slot_unique",
            ),
            # One user may not hold two rooms or two adjacent slots at one hour.
            models.UniqueConstraint(
                fields=["user", "slot_start"],
                condition=models.Q(status__in=list(BLOCKING_STATUSES)),
                name="booking_user_slot_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "slot_date"], name="booking_user_date_idx"),
            models.Index(
                fields=["status", "deadline"],
                name="booking_status_deadline_idx",
            ),
            models.Index(fields=["status", "slot_end"], name="booking_status_end_idx"),
            models.Index(fields=["room", "slot_date"], name="booking_room_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.room} {self.local_start:%Y-%m-%d %H:%M} [{self.status}]"

    def save(self, *args, **kwargs):
        # Keep the denormalised Bangkok date consistent with the stored start.
        local_start = self.slot_start.astimezone(BANGKOK)
        self.slot_date = local_start.date()
        super().save(*args, **kwargs)

    @property
    def local_start(self):
        return self.slot_start.astimezone(BANGKOK)

    @property
    def local_end(self):
        return self.slot_end.astimezone(BANGKOK)

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.Status.COMPLETED,
            self.Status.NO_SHOW,
            self.Status.CANCELLED,
        }

    @property
    def occupies_slot(self) -> bool:
        return self.status in BLOCKING_STATUSES

    @property
    def charges_quota(self) -> bool:
        return self.status in QUOTA_STATUSES

    def effective_end(self):
        """When the room is actually free again (read-only derivation)."""
        if self.status == self.Status.COMPLETED:
            return self.actual_end or self.slot_end
        return self.slot_end


def advance_deadline(slot_start, grace_minutes: int | None = None):
    """Check-in deadline for an advance reservation: start + grace."""
    if grace_minutes is None:
        grace_minutes = settings.CHECKIN_GRACE_MINUTES
    return slot_start + timedelta(minutes=grace_minutes)
