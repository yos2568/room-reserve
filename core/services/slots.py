"""Slot geometry and the compact slot identifier used in URLs.

Every slot is ``[slot_start, slot_end)`` with an exact local-hour start and
``slot_end = slot_start + 60 minutes``. Valid default starts are 08:00 through
19:00 Bangkok; the day is closed at 20:00 (V3 section 3.1). The bounds are stored
as aware UTC timestamps.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

BANGKOK = ZoneInfo("Asia/Bangkok")
UTC = ZoneInfo("UTC")

SLOT_DELTA = timedelta(minutes=60)


def slot_start_for(local_date: date, hour: int) -> datetime:
    """UTC slot start for a Bangkok local date and hour."""
    return datetime.combine(local_date, time(hour=hour), tzinfo=BANGKOK).astimezone(UTC)


def slot_end_for(slot_start: datetime) -> datetime:
    return slot_start + SLOT_DELTA


def is_hour_aligned(moment: datetime) -> bool:
    """True when ``moment`` falls exactly on a Bangkok hour boundary.

    Asia/Bangkok has no daylight saving and a whole-hour offset, so this is
    equivalent to an exact UTC-hour boundary; the same reasoning backs the
    ``booking_slot_hour_aligned`` database constraint.
    """
    local = moment.astimezone(BANGKOK)
    return local.minute == 0 and local.second == 0 and local.microsecond == 0


def floor_to_slot(moment: datetime) -> datetime:
    """The start of the slot containing ``moment``."""
    local = moment.astimezone(BANGKOK)
    return local.replace(minute=0, second=0, microsecond=0).astimezone(UTC)


def encode_slot(slot_start: datetime) -> str:
    """Compact Bangkok-local identifier: ``YYYYMMDDTHHMM``."""
    local = slot_start.astimezone(BANGKOK)
    return local.strftime("%Y%m%dT%H%M")


def decode_slot(value: str) -> datetime:
    """Parse :func:`encode_slot` output. Raises ValueError on anything else."""
    try:
        local = datetime.strptime(value, "%Y%m%dT%H%M")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Not a slot identifier: {value!r}") from exc
    return local.replace(tzinfo=BANGKOK).astimezone(UTC)


def try_decode_slot(value: str) -> datetime | None:
    try:
        return decode_slot(value)
    except ValueError:
        return None


def day_slot_starts(local_date: date, open_hour: int | None = None, close_hour: int | None = None):
    """Every hourly slot start for a local date between the opening hours.

    ``close_hour`` is exclusive: the 20:00 close is passed as 20, so the last
    returned start is 19:00.
    """
    if open_hour is None:
        open_hour = settings.OPENING_SLOT_START_HOUR
    if close_hour is None:
        close_hour = settings.LAST_SLOT_START_HOUR + 1
    for hour in range(open_hour, close_hour):
        yield slot_start_for(local_date, hour)


def default_opening_hours(local_date: date) -> tuple[int, int] | None:
    """Default weekly timetable: Monday–Friday 08:00–20:00, weekends closed."""
    if local_date.weekday() >= 5:
        return None
    return (settings.OPENING_SLOT_START_HOUR, settings.LAST_SLOT_START_HOUR + 1)


def dates_within_horizon(now: datetime, horizon_days: int | None = None) -> list[date]:
    """Local dates bookable from ``now``: today through ``now + horizon_days``."""
    if horizon_days is None:
        horizon_days = settings.HORIZON_DAYS
    today = now.astimezone(BANGKOK).date()
    return [today + timedelta(days=offset) for offset in range(horizon_days + 1)]


def is_within_horizon(now: datetime, slot_start: datetime, horizon_days: int | None = None) -> bool:
    if horizon_days is None:
        horizon_days = settings.HORIZON_DAYS
    last_local_date = now.astimezone(BANGKOK).date() + timedelta(days=horizon_days)
    return slot_start.astimezone(BANGKOK).date() <= last_local_date


def format_local(moment: datetime, *, with_date: bool = True) -> str:
    """Human-readable Bangkok time, Gregorian year."""
    local = moment.astimezone(BANGKOK)
    if with_date:
        return local.strftime("%d/%m/%Y %H:%M")
    return local.strftime("%H:%M")


def remaining_minutes(now: datetime, slot_end: datetime) -> int:
    """Whole minutes left in the slot, never negative."""
    seconds = (slot_end - now).total_seconds()
    return max(0, int(seconds // 60))
