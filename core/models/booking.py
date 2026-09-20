"""The booking control row and the booking itself."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

BANGKOK = ZoneInfo("Asia/Bangkok")

BLOCKING_STATUSES = ("PENDING_APPROVAL", "SCHEDULED", "IN_USE", "COMPLETED")
QUOTA_STATUSES = ("PENDING_APPROVAL", "SCHEDULED", "IN_USE", "COMPLETED", "NO_SHOW")


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


class RecurringReservation(models.Model):
    """A weekly reservation series owned by one student.

    Occurrences are materialised as ordinary bookings so the existing conflict,
    quota, approval and cancellation rules remain the source of truth. The series
    row is only the user's durable explanation of why those bookings are related.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        CANCELLED = "CANCELLED", _("Cancelled")

    user = models.ForeignKey("core.User", on_delete=models.PROTECT, related_name="recurring_reservations")
    room = models.ForeignKey("core.Room", on_delete=models.PROTECT, related_name="recurring_reservations")
    weekday = models.PositiveSmallIntegerField()
    start_hour = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    title = models.CharField(max_length=120, blank=True)
    purpose = models.TextField(max_length=500, blank=True)
    participant_names = models.TextField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(weekday__gte=0, weekday__lte=6),
                name="recurring_weekday_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(start_hour__gte=0, start_hour__lte=23),
                name="recurring_start_hour_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="recurring_date_range_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["ACTIVE", "CANCELLED"]),
                name="recurring_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status"], name="recurring_user_status_idx"),
            models.Index(fields=["room", "start_date"], name="recurring_room_start_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.room} weekly {self.start_hour:02d}:00"

    def get_weekday_display(self):
        return (
            _("Monday"),
            _("Tuesday"),
            _("Wednesday"),
            _("Thursday"),
            _("Friday"),
            _("Saturday"),
            _("Sunday"),
        )[self.weekday]


class Booking(models.Model):
    """A reservation of one room for one fixed hourly slot.

    Slot identity is always room plus the *original* hourly start, even for a late
    walk-in: a walk-in at 08:45 still occupies the 08:00 slot and still blocks the
    09:00 slot (V3 sections 3.1 and 3.3).
    """

    class Status(models.TextChoices):
        PENDING_APPROVAL = "PENDING_APPROVAL", _("Awaiting room approval")
        SCHEDULED = "SCHEDULED", _("Scheduled")
        IN_USE = "IN_USE", _("In use")
        COMPLETED = "COMPLETED", _("Completed")
        NO_SHOW = "NO_SHOW", _("No-show")
        CANCELLED = "CANCELLED", _("Cancelled")
        REJECTED = "REJECTED", _("Not approved")

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
    recurrence = models.ForeignKey(
        "core.RecurringReservation",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="occurrences",
    )

    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="booking_approvals",
    )
    approval_note = models.TextField(blank=True)

    # Planning details are deliberately separate from the student's identity:
    # staff can understand what a room is being used for without exposing this
    # information on the public availability grid.
    title = models.CharField(max_length=120, blank=True)
    purpose = models.TextField(max_length=500, blank=True)
    participant_names = models.TextField(
        max_length=500, blank=True, help_text=_("Optional names of other participants.")
    )
    details_version = models.PositiveIntegerField(default=0, editable=False)

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
                condition=models.Q(status__in=[*BLOCKING_STATUSES, "NO_SHOW", "CANCELLED", "REJECTED"]),
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
            self.Status.REJECTED,
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
