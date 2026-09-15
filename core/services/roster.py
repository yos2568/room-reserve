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
from .errors import Code, OperationRejected
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
    # Roster addresses this import rewrote. Only ever non-zero when the caller
    # explicitly allowed it, and always reported: silently changing the address a
    # student will be matched against is the one thing an import must never hide.
    email_changed: int = 0

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
        if self.email_changed:
            lines.append(
                f"{self.email_changed} existing roster address(es) {verb} rewritten. "
                f"Check them against the department's confirmation before relying on "
                f"automatic approval."
            )
        if self.unrecognised_instruments:
            lines.append(
                f"{len(self.unrecognised_instruments)} instrument spelling(s) have no "
                f"category: {', '.join(self.unrecognised_instruments)}. Those students "
                f"cannot reserve an instrument-specific room until they are categorised."
            )
        return "\n".join(lines)


def import_roster_text(
    *,
    text: str,
    actor=None,
    dry_run: bool = False,
    batch: str = "",
    allow_email_change: bool = False,
) -> ImportReport:
    return import_roster_rows(
        rows=read_csv(text),
        actor=actor,
        dry_run=dry_run,
        batch=batch,
        allow_email_change=allow_email_change,
    )


def import_roster_rows(
    *,
    rows: list[dict],
    actor=None,
    dry_run: bool = False,
    batch: str = "",
    allow_email_change: bool = False,
) -> ImportReport:
    """Validate and apply a roster.

    ``allow_email_change`` is the explicit opt-in for rewriting the address on an
    entry that already exists. Without it a difference is a conflict and the whole
    file is refused, because an import must never silently rebind an identity. With
    it, an address that already belongs to a *different* institutional ID is still
    refused: that is a rebinding, not a correction.
    """
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
        # A dry run must answer the three questions it is asked: how many rows
        # would be created, would the import be refused, and how many existing
        # addresses would be rewritten. Returning early here used to report
        # "0 would be created" for any valid file, which reads as "nothing to do"
        # and makes the preflight worse than useless for a real roster.
        for entry in cleaned:
            conflict = _conflicting_entry(entry, allow_email_change=allow_email_change)
            if conflict is not None:
                report.errors.append(RowError(0, conflict))
                continue
            existing = EligibleStudent.objects.filter(institutional_id=entry["institutional_id"]).first()
            if existing is None:
                report.created += 1
            else:
                report.unchanged += 1
                if existing.email != entry["email"]:
                    report.email_changed += 1
        return report

    with transaction.atomic():
        for entry in cleaned:
            conflict = _conflicting_entry(entry, allow_email_change=allow_email_change)
            if conflict is not None:
                report.errors.append(RowError(0, conflict))
                # Raising aborts the transaction: imports are all-or-nothing.
                raise ValueError(conflict)
            created, address_rewritten = _apply_entry(entry, batch=batch)
            report.created += int(created)
            report.unchanged += int(not created)
            report.email_changed += int(address_rewritten)

        record_audit(
            action="roster.imported",
            entity_type="EligibleStudent",
            entity_id=batch or "batch",
            actor=actor,
            changes={
                "rows_read": report.rows_read,
                "created": report.created,
                "unchanged": report.unchanged,
                "email_changed": report.email_changed,
                "allow_email_change": allow_email_change,
                "batch": batch,
            },
        )

    return report


def _conflicting_entry(entry: dict, *, allow_email_change: bool = False) -> str | None:
    """Detect a contradiction with stored data before any write happens.

    Two different failures, deliberately treated differently:

    * the same institutional ID with a **different address** — a correction when the
      caller has opted in, a refused conflict otherwise;
    * an address that already belongs to a **different ID** — always refused, because
      that is rebinding an identity to another person, not correcting one.
    """
    by_id = EligibleStudent.objects.filter(institutional_id=entry["institutional_id"]).first()
    by_email = EligibleStudent.objects.filter(email=entry["email"]).first()

    if by_id is not None and by_id.email != entry["email"] and not allow_email_change:
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


def _apply_entry(entry: dict, *, batch: str) -> tuple[bool, bool]:
    """Create or refresh a roster entry.

    Returns ``(created, address_rewritten)``. The second is only ever True when the
    caller passed ``allow_email_change`` — ``_conflicting_entry`` has already
    refused the change otherwise, so reaching here with a different address means
    the rewrite was explicitly authorised.
    """
    existing = EligibleStudent.objects.filter(institutional_id=entry["institutional_id"]).first()
    if existing is None:
        EligibleStudent.objects.create(
            **entry,
            is_active=True,
            import_batch=batch,
        )
        return True, False

    changed = []
    address_rewritten = False
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

    if existing.email != entry["email"]:
        existing.email = entry["email"]
        changed.append("email")
        address_rewritten = True

    if not existing.is_active:
        existing.is_active = True
        changed.append("is_active")
    if changed:
        existing.updated_at = clock.now()
        existing.save(update_fields=[*changed, "updated_at"])
    return False, address_rewritten


def correct_email(ctx, *, entry: EligibleStudent, email: str, reason: str) -> dict:
    """Correct one roster entry's address, deliberately and with a trail.

    The import refuses to change an address on an existing entry, because it must
    never silently rebind an identity. That left no way to fix a roster that is
    simply wrong — which is the normal case: the department's own list can hold a
    personal address for a student who must register with their institutional one.
    This is the explicit path for that, one entry at a time, with a reason.

    Two things it deliberately does **not** do:

    * it does not touch the student's account, even when the row is linked to one.
      Their login identity is theirs, not something a roster correction rewrites;
    * it does not re-run eligibility. Approval is a staff decision recorded at a
      point in time, and withdrawing it because a spreadsheet was wrong would
      penalise the student for somebody else's mistake.

    The returned ``linked_account_matches`` says whether a linked account still
    agrees with the corrected address, so staff can see when an approval now rests
    on a stale match.
    """
    email = normalize_email(email)
    if not email:
        raise OperationRejected(Code.INVALID_INPUT, field="email")
    try:
        validate_email(email)
    except ValidationError as exc:
        raise OperationRejected(Code.INVALID_INPUT, field="email") from exc
    if not (reason or "").strip():
        raise OperationRejected(Code.INVALID_INPUT, field="reason")

    entry = EligibleStudent.objects.select_for_update().get(pk=entry.pk)
    previous = entry.email

    if previous == email:
        # Nothing to do. Deliberately not an error: re-submitting the same address is
        # a no-op, not a mistake worth a generic "check the form" message.
        return {
            "entry_id": entry.pk,
            "institutional_id": entry.institutional_id,
            "previous_email": previous,
            "email": email,
            "linked_account_id": getattr(entry.account, "pk", None),
            "linked_account_matches": bool(
                entry.account is not None and normalize_email(entry.account.email) == email
            ),
            "changed": False,
        }

    holder = EligibleStudent.objects.filter(email=email).exclude(pk=entry.pk).first()
    if holder is not None:
        # Refuse rather than steal another student's address: addresses are unique
        # because a registration is matched on ID *and* address together.
        raise OperationRejected(
            Code.DUPLICATE_ACCOUNT,
            email=email,
            held_by=holder.institutional_id,
        )

    entry.email = email
    entry.updated_at = clock.now()
    entry.save(update_fields=["email", "updated_at"])

    account = entry.account
    linked_account_matches = bool(account is not None and normalize_email(account.email) == email)

    ctx.audit(
        action="roster.email_corrected",
        entity_type="EligibleStudent",
        entity_id=entry.pk,
        actor=ctx.actor,
        changes={
            "institutional_id": entry.institutional_id,
            "from": previous,
            "to": email,
            "linked_account_id": getattr(account, "pk", None),
            "linked_account_matches": linked_account_matches,
        },
        reason=reason.strip(),
    )

    return {
        "entry_id": entry.pk,
        "institutional_id": entry.institutional_id,
        "previous_email": previous,
        "email": email,
        "linked_account_id": getattr(account, "pk", None),
        "linked_account_matches": linked_account_matches,
        "changed": True,
    }


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
