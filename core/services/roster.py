"""Roster import (V3 section 8).

Supports dry-run, atomic validation, duplicate detection and idempotent
application, and imports only the minimal fields. An import never silently
rebinds an existing identity: if a roster line contradicts what is already
stored for that institutional ID or email, the import is rejected instead of
overwriting it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from core.models import EligibleStudent

from . import clock, instruments
from .audit import record_audit
from .csvio import read_csv
from .identity import normalize_email

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("institutional_id", "email")


@dataclass
class RowError:
    line: int
    message: str


@dataclass
class ImportReport:
    rows_read: int = 0
    valid: int = 0
    created: int = 0
    unchanged: int = 0
    deactivated: int = 0
    errors: list[RowError] = field(default_factory=list)
    dry_run: bool = False
    # Every institutional ID that passed validation, so a full refresh can tell
    # which existing entries are absent and therefore stale.
    institutional_ids: list[str] = field(default_factory=list)
    # Instrument spellings no category covers. Not an error — the import still
    # works — but a student nobody has categorised cannot reserve an
    # instrument-specific room, so this has to be visible rather than swallowed.
    unrecognised_instruments: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        verb = "would be" if self.dry_run else "were"
        lines = [
            f"{self.rows_read} rows read; {self.valid} valid; "
            f"{self.created} {verb} created; {self.unchanged} unchanged; "
            f"{len(self.errors)} errors."
        ]
        if self.unrecognised_instruments:
            lines.append(
                f"{len(self.unrecognised_instruments)} instrument spelling(s) have no "
                f"category: {', '.join(self.unrecognised_instruments)}. Those students "
                f"cannot reserve an instrument-specific room until they are categorised."
            )
        return "\n".join(lines)


def import_roster_text(*, text: str, actor=None, dry_run: bool = False, batch: str = "") -> ImportReport:
    return import_roster_rows(rows=read_csv(text), actor=actor, dry_run=dry_run, batch=batch)


def import_roster_rows(
    *, rows: list[dict], actor=None, dry_run: bool = False, batch: str = ""
) -> ImportReport:
    report = ImportReport(rows_read=len(rows), dry_run=dry_run)

    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    seen_emails: set[str] = set()

    for index, row in enumerate(rows, start=2):  # line 1 is the header
        missing = [name for name in REQUIRED_FIELDS if not row.get(name)]
        if missing:
            report.errors.append(RowError(index, f"Missing required field(s): {', '.join(missing)}."))
            continue

        institutional_id = str(row["institutional_id"]).strip()
        email = normalize_email(str(row["email"]))

        try:
            validate_email(email)
        except ValidationError:
            report.errors.append(RowError(index, f"Not a valid email address: {email!r}."))
            continue

        if institutional_id in seen_ids:
            report.errors.append(
                RowError(index, f"Duplicate institutional ID inside this file: {institutional_id}.")
            )
            continue
        if email in seen_emails:
            report.errors.append(RowError(index, f"Duplicate email inside this file: {email}."))
            continue
        seen_ids.add(institutional_id)
        seen_emails.add(email)

        year = row.get("year") or ""
        year_value = None
        if year:
            try:
                year_value = int(year)
                if not (1 <= year_value <= 12):
                    raise ValueError
            except ValueError:
                report.errors.append(RowError(index, f"Year must be a number from 1 to 12, got {year!r}."))
                continue

        instrument = (row.get("instrument") or "").strip()
        cleaned.append(
            {
                "institutional_id": institutional_id,
                "email": email,
                "name_th": (row.get("name_th") or row.get("name") or "").strip(),
                "name_en": (row.get("name_en") or "").strip(),
                "program": (row.get("program") or "").strip(),
                "instrument": instrument,
                "instrument_category": instruments.categorise(instrument),
                "year": year_value,
            }
        )

    report.valid = len(cleaned)
    report.institutional_ids = [entry["institutional_id"] for entry in cleaned]
    report.unrecognised_instruments = instruments.unrecognised(entry["instrument"] for entry in cleaned)

    if report.errors:
        return report

    if dry_run:
        # A dry run must answer the two questions it is asked: how many rows would
        # be created, and would the import be refused. Returning early here used to
        # report "0 would be created" for any valid file, which reads as "nothing
        # to do" and makes the preflight worse than useless for a real roster.
        for entry in cleaned:
            conflict = _conflicting_entry(entry)
            if conflict is not None:
                report.errors.append(RowError(0, conflict))
                continue
            if EligibleStudent.objects.filter(institutional_id=entry["institutional_id"]).exists():
                report.unchanged += 1
            else:
                report.created += 1
        return report

    with transaction.atomic():
        for entry in cleaned:
            conflict = _conflicting_entry(entry)
            if conflict is not None:
                report.errors.append(RowError(0, conflict))
                # Raising aborts the transaction: imports are all-or-nothing.
                raise ValueError(conflict)
            created = _apply_entry(entry, batch=batch)
            report.created += int(created)
            report.unchanged += int(not created)

        record_audit(
            action="roster.imported",
            entity_type="EligibleStudent",
            entity_id=batch or "batch",
            actor=actor,
            changes={
                "rows_read": report.rows_read,
                "created": report.created,
                "unchanged": report.unchanged,
                "batch": batch,
            },
        )

    return report


def _conflicting_entry(entry: dict) -> str | None:
    """Detect a contradiction with stored data before any write happens."""
    by_id = EligibleStudent.objects.filter(institutional_id=entry["institutional_id"]).first()
    by_email = EligibleStudent.objects.filter(email=entry["email"]).first()

    if by_id is not None and by_id.email != entry["email"]:
        return (
            f"Institutional ID {entry['institutional_id']} is already on the roster with a "
            f"different email address; refusing to overwrite it."
        )
    if by_email is not None and by_email.institutional_id != entry["institutional_id"]:
        return (
            f"Email {entry['email']} is already on the roster under a different institutional "
            f"ID; refusing to rebind it."
        )
    return None


def _apply_entry(entry: dict, *, batch: str) -> bool:
    """Create or refresh a roster entry. Returns True when a row was created."""
    existing = EligibleStudent.objects.filter(institutional_id=entry["institutional_id"]).first()
    if existing is None:
        EligibleStudent.objects.create(
            **entry,
            is_active=True,
            import_batch=batch,
        )
        return True

    changed = []
    for field_name in ("name_th", "name_en", "program", "year"):
        value = entry[field_name]
        if value and getattr(existing, field_name) != value:
            setattr(existing, field_name, value)
            changed.append(field_name)

    # The instrument and its derived category move together, and only when the
    # file actually says something: a blank instrument column must not wipe a
    # category that was set deliberately.
    if entry["instrument"]:
        if existing.instrument != entry["instrument"]:
            existing.instrument = entry["instrument"]
            changed.append("instrument")
        if existing.instrument_category != entry["instrument_category"]:
            existing.instrument_category = entry["instrument_category"]
            changed.append("instrument_category")

    if not existing.is_active:
        existing.is_active = True
        changed.append("is_active")
    if changed:
        existing.updated_at = clock.now()
        existing.save(update_fields=[*changed, "updated_at"])
    return False


def deactivate_missing(*, keep_ids: set[str], actor=None, dry_run: bool = False) -> int:
    """Deactivate roster entries absent from a full refresh.

    Deliberately a separate, explicit action: a partial import must not
    deactivate the whole roster.
    """
    stale = EligibleStudent.objects.filter(is_active=True).exclude(institutional_id__in=keep_ids)
    count = stale.count()
    if dry_run or count == 0:
        return count

    with transaction.atomic():
        stale.update(is_active=False, updated_at=clock.now())
        record_audit(
            action="roster.deactivated",
            entity_type="EligibleStudent",
            entity_id="bulk",
            actor=actor,
            changes={"deactivated": count},
            reason="Absent from the full roster refresh.",
        )
    return count
