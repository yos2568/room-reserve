"""Synthetic fixture builders.

V3 section 10: "Keep test data synthetic." Nothing here resembles a real student,
and the email domain is reserved for documentation use.
"""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

from core.models import (
    Booking,
    EligibleStudent,
    InstrumentCategory,
    Room,
    RoomAllowedCategory,
    User,
    Weekday,
    WeeklyBlock,
    advance_deadline,
)
from core.services import instruments, slots
from core.services.policy import current_policy

TEST_DOMAIN = "student.chula.ac.th"

_counter = {"n": 0}


def next_index() -> int:
    _counter["n"] += 1
    return _counter["n"]


def make_user(
    *,
    username: str | None = None,
    email: str | None = None,
    name: str = "ทดสอบ ระบบ",
    password: str = "synthetic-password-123",
    eligibility: str = User.Eligibility.APPROVED,
    verified: bool = True,
    is_active: bool = True,
    is_operational_staff: bool = False,
    locale: str = User.Locale.TH,
    **extra,
) -> User:
    index = next_index()
    username = username or f"6600{index:05d}"
    email = email or f"student{index:05d}@{TEST_DOMAIN}"
    user = User(
        username=username,
        email=email,
        name_th=name,
        name_en="Test Student",
        locale=locale,
        eligibility=eligibility,
        is_active=is_active,
        is_operational_staff=is_operational_staff,
        email_verified_at=timezone.now() if verified else None,
        **extra,
    )
    user.set_password(password)
    user.save()
    return user


def make_roster_entry(user: User | None = None, **overrides) -> EligibleStudent:
    index = next_index()
    data = {
        "institutional_id": overrides.pop("institutional_id", f"6600{index:05d}"),
        "email": overrides.pop("email", f"roster{index:05d}@{TEST_DOMAIN}"),
        "name_th": "ชื่อจากทะเบียน",
        "is_active": True,
    }
    if user is not None:
        data["institutional_id"] = user.institutional_id
        data["email"] = user.email
        data["account"] = user
    data.update(overrides)
    # Derived the same way the roster import derives it, so a test that writes an
    # instrument gets the category the real path would produce.
    if "instrument" in data and "instrument_category" not in data:
        data["instrument_category"] = instruments.categorise(data["instrument"])
    return EligibleStudent.objects.create(**data)


def make_restricted_room(
    number: str = "303",
    label: str = "ห้อง 303",
    categories: tuple[str, ...] = (InstrumentCategory.PIANO, InstrumentCategory.PERCUSSION),
    position: int = 10,
) -> Room:
    """A room only the listed instrument categories may reserve."""
    room, _ = Room.objects.get_or_create(
        number=number,
        defaults={"label": label, "position": position},
    )
    room.reservation_scope = Room.ReservationScope.LISTED
    room.save(update_fields=["reservation_scope"])
    for category in categories:
        RoomAllowedCategory.objects.get_or_create(room=room, category=category)
    return room


def make_weekly_block(
    room: Room,
    *,
    weekday: int = Weekday.MONDAY,
    start_hour: int = 10,
    end_hour: int = 12,
    reason: str = "COUNTERPOINT",
    valid_from=None,
    valid_until=None,
) -> WeeklyBlock:
    """One recurring teaching-timetable hour range on a room."""
    return WeeklyBlock.objects.create(
        room=room,
        weekday=weekday,
        start_hour=start_hour,
        end_hour=end_hour,
        reason=reason,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def make_rooms(count: int = 9) -> list[Room]:
    rooms = []
    for number in range(1, count + 1):
        room, _ = Room.objects.get_or_create(
            number=str(number),
            defaults={"label": f"ห้องซ้อม {number}", "position": number},
        )
        rooms.append(room)
    return rooms


def make_booking(
    user: User,
    room: Room,
    *,
    day=None,
    hour: int = 10,
    status: str = Booking.Status.SCHEDULED,
    source: str = Booking.Source.ADVANCE,
    **overrides,
) -> Booking:
    """Create a booking directly, bypassing policy checks, for test setup."""
    day = day or timezone.localdate()
    slot_start = overrides.pop("slot_start", None) or slots.slot_start_for(day, hour)
    slot_end = slots.slot_end_for(slot_start)

    policy = current_policy()
    deadline = None
    if source == Booking.Source.ADVANCE:
        deadline = advance_deadline(slot_start, policy.checkin_grace_minutes)

    defaults = {
        "slot_end": slot_end,
        "deadline": deadline,
        "status": status,
        "source": source,
        "policy_version": policy,
    }
    # Advance reservations carry the QR check-in token, exactly as the real
    # creation path does; walk-ins have nothing to check in to.
    if status == Booking.Status.IN_USE:
        defaults.setdefault("checked_in_at", slot_start)
    if status == Booking.Status.CANCELLED:
        defaults.setdefault("cancelled_at", timezone.now())
        defaults.setdefault("cancel_reason", "test fixture")
    if status == Booking.Status.COMPLETED:
        defaults.setdefault("checked_in_at", slot_start)
        defaults.setdefault("actual_end", slot_end)
    defaults.update(overrides)
    return Booking.objects.create(user=user, room=room, slot_start=slot_start, **defaults)


def bangkok(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """An aware Bangkok datetime, for readable test fixtures."""
    from zoneinfo import ZoneInfo

    return datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo("Asia/Bangkok"))
