"""Policy version resolution and creation.

Existing bookings keep the policy values stored on the row, so editing policy
later cannot retroactively invalidate an already-permitted check-in, and strike
windows already recorded keep their frozen expiry (V3 sections 7 and 8).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from core.models import PolicyVersion

# The fixed hourly geometry is NOT part of an editable policy snapshot.
EDITABLE_POLICY_FIELDS = (
    "horizon_days",
    "daily_quota",
    "max_upcoming_hours",
    "checkin_grace_minutes",
    "strike_window_days",
    "strike_threshold",
    "auto_suspension_days",
    "reminder_lead_minutes",
    "institution_email_domain",
)


def snapshot_from_settings() -> dict[str, Any]:
    return {
        "horizon_days": settings.HORIZON_DAYS,
        "daily_quota": settings.DAILY_QUOTA,
        "max_upcoming_hours": settings.MAX_UPCOMING_HOURS,
        "checkin_grace_minutes": settings.CHECKIN_GRACE_MINUTES,
        "strike_window_days": settings.STRIKE_WINDOW_DAYS,
        "strike_threshold": settings.STRIKE_THRESHOLD,
        "auto_suspension_days": settings.AUTO_SUSPENSION_DAYS,
        "reminder_lead_minutes": settings.REMINDER_LEAD_MINUTES,
        "institution_email_domain": settings.INSTITUTION_EMAIL_DOMAIN,
        # Recorded for the audit trail; not editable.
        "slot_minutes": settings.SLOT_MINUTES,
        "opening_hour": settings.OPENING_SLOT_START_HOUR,
        "last_slot_hour": settings.LAST_SLOT_START_HOUR,
    }


def current_policy() -> PolicyVersion:
    """The active policy version, created from settings on first use."""
    policy = PolicyVersion.objects.order_by("-version").first()
    if policy is not None:
        return policy
    try:
        with transaction.atomic():
            return PolicyVersion.objects.create(
                version=1,
                label="Initial policy from deployment defaults",
                snapshot=snapshot_from_settings(),
                note="Created automatically from environment defaults.",
            )
    except IntegrityError:
        # Another process created it first; the unique version column serialises us.
        return PolicyVersion.objects.order_by("-version").first()


def create_policy_version(
    *, snapshot: dict[str, Any], actor=None, label: str = "", note: str = ""
) -> PolicyVersion:
    """Create the next immutable policy version from a validated snapshot."""
    validated = _validate(snapshot, base=snapshot_from_settings())
    latest = PolicyVersion.objects.order_by("-version").first()
    next_version = (latest.version + 1) if latest else 1
    return PolicyVersion.objects.create(
        version=next_version,
        label=label,
        snapshot=validated,
        created_by=actor,
        note=note,
    )


def _validate(snapshot: dict[str, Any], *, base: dict[str, Any]) -> dict[str, Any]:
    """Reject anything that is not a sane, editable policy value."""
    from django.core.exceptions import ValidationError

    merged = dict(base)
    for field in EDITABLE_POLICY_FIELDS:
        if field in snapshot:
            merged[field] = snapshot[field]

    def positive_int(name: str, *, minimum: int, maximum: int) -> None:
        value = merged.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or not (minimum <= value <= maximum):
            raise ValidationError({name: f"Must be a whole number between {minimum} and {maximum}."})

    positive_int("horizon_days", minimum=1, maximum=60)
    positive_int("daily_quota", minimum=1, maximum=10)
    positive_int("max_upcoming_hours", minimum=1, maximum=20)
    positive_int("checkin_grace_minutes", minimum=1, maximum=59)
    positive_int("strike_window_days", minimum=1, maximum=365)
    positive_int("strike_threshold", minimum=1, maximum=10)
    positive_int("auto_suspension_days", minimum=1, maximum=90)
    positive_int("reminder_lead_minutes", minimum=1, maximum=180)

    domain = merged.get("institution_email_domain")
    if not isinstance(domain, str) or not domain or "@" in domain:
        raise ValidationError({"institution_email_domain": "Must be a bare domain name."})

    # Geometry is never taken from the submitted snapshot.
    merged["slot_minutes"] = settings.SLOT_MINUTES
    merged["opening_hour"] = settings.OPENING_SLOT_START_HOUR
    merged["last_slot_hour"] = settings.LAST_SLOT_START_HOUR
    return merged
