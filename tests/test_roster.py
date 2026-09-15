"""Roster import: validation, atomicity and the preflight (V3 section 8).

The roster decides who can register and be approved automatically, so a silent
problem here is expensive: a wrong email means a real student cannot self-register
and cannot be matched. This file had no tests at all before, despite the import
being the one path that writes other people's personal data in bulk.

Every ID, name and address below is **synthetic** (V3 section 10: "Keep test data
synthetic"). An earlier version of this file copied a real row out of the
department's spreadsheet, which is precisely what that rule forbids — the
department's roster is never a fixture.
"""

from __future__ import annotations

import pytest

from core.models import EligibleStudent, User
from core.services.roster import deactivate_missing, import_roster_text
from tests import factories

pytestmark = pytest.mark.django_db

HEADER = "institutional_id,email,name_th,instrument,year,program"


def csv_text(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


GOOD = "6699000001,6699000001@student.chula.ac.th,นายทดสอบ ระบบ,ดับเบิลเบส,1,ดุริยางคศิลป์ตะวันตก"
OTHER = "6699000002,6699000002@student.chula.ac.th,นางสาวทดสอบ สอง,เปียโน,2,ดุริยางคศิลป์ตะวันตก"


# --- The preflight -------------------------------------------------------------


def test_a_dry_run_reports_what_it_would_create_and_writes_nothing():
    """The summary says "would be created"; the count has to be real.

    It previously returned before counting, so every valid file reported
    "0 would be created" — which reads as "nothing to do" and makes the preflight
    useless for deciding whether to run it.
    """
    report = import_roster_text(text=csv_text(GOOD, OTHER), dry_run=True)

    assert report.ok
    assert report.valid == 2
    assert report.created == 2, "a valid file must not report nothing to do"
    assert report.unchanged == 0
    assert EligibleStudent.objects.count() == 0, "a dry run writes nothing"


def test_a_dry_run_counts_an_already_present_row_as_unchanged():
    import_roster_text(text=csv_text(GOOD))

    report = import_roster_text(text=csv_text(GOOD), dry_run=True)

    assert report.created == 0
    assert report.unchanged == 1


def test_a_dry_run_surfaces_a_conflict_instead_of_passing():
    """A preflight that cannot fail is not a preflight."""
    EligibleStudent.objects.create(
        institutional_id="6699000001",
        email="someone.else@student.chula.ac.th",
        name_th="ชื่อเดิม",
        is_active=True,
    )

    report = import_roster_text(text=csv_text(GOOD), dry_run=True)

    assert not report.ok
    assert report.errors
    assert "different email" in report.errors[0].message


# --- Writing -------------------------------------------------------------------


def test_an_import_creates_the_rows_and_is_idempotent():
    first = import_roster_text(text=csv_text(GOOD, OTHER), batch="batch-1")
    assert first.created == 2 and first.unchanged == 0

    second = import_roster_text(text=csv_text(GOOD, OTHER), batch="batch-1")
    assert second.created == 0 and second.unchanged == 2
    assert EligibleStudent.objects.count() == 2


def test_a_contradiction_with_a_stored_row_aborts_the_whole_file():
    """All-or-nothing: a partial roster is worse than none."""
    import_roster_text(text=csv_text(GOOD))
    before = EligibleStudent.objects.count()

    with pytest.raises(ValueError):
        import_roster_text(text=csv_text(GOOD.replace("6699000001@", "other@"), OTHER))

    assert EligibleStudent.objects.count() == before, "nothing may be written"
    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert stored.email == "6699000001@student.chula.ac.th"


def test_refreshing_a_name_updates_it_without_rebinding_the_email():
    import_roster_text(text=csv_text(GOOD))

    import_roster_text(text=csv_text(GOOD.replace("ทดสอบ", "ทดสอบ (แก้ไข)")))

    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert "แก้ไข" in stored.name_th


# --- Validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ("6699000009,,ไม่มีอีเมล,เปียโน,1,program", "Missing required field"),
        ("6699000009,not-an-address,ผิด,เปียโน,1,program", "Not a valid email"),
        ("6699000009,6699000009@student.chula.ac.th,ผิด,เปียโน,99,program", "1 to 12"),
    ],
)
def test_bad_rows_are_reported_and_nothing_is_written(row, expected):
    report = import_roster_text(text=csv_text(row))

    assert not report.ok
    assert any(expected in error.message for error in report.errors), report.errors
    assert EligibleStudent.objects.count() == 0


def test_duplicates_inside_one_file_are_rejected():
    duplicate_id = csv_text(GOOD, GOOD.replace("6699000002@", "different@"))

    report = import_roster_text(text=duplicate_id)

    assert not report.ok
    assert any(
        "Duplicate email" in error.message or "Duplicate institutional" in error.message
        for error in report.errors
    )


def test_headers_are_matched_case_insensitively_and_tolerate_spacing():
    text = " Institutional_ID ,  Email , NAME_TH ,instrument,year\n6699000001,6699000001@student.chula.ac.th,ชื่อ ทดสอบ,ไวโอลิน,3\n"

    report = import_roster_text(text=text)

    assert report.ok, report.errors
    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert stored.instrument == "ไวโอลิน"
    assert stored.year == 3


# --- Instrument categories ------------------------------------------------------


def test_the_import_derives_the_instrument_category():
    report = import_roster_text(text=csv_text(GOOD))

    assert report.ok, report.errors
    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert stored.instrument == "ดับเบิลเบส"
    assert stored.instrument_category == "STRINGS"


def test_an_unrecognised_spelling_is_reported_but_does_not_block_the_import():
    """The import must not fail over one odd spelling, and must not swallow it.

    A student nobody has categorised cannot reserve an instrument-specific room,
    so the fact has to reach a human.
    """
    report = import_roster_text(text=csv_text(GOOD.replace("ดับเบิลเบส", "ปี่ใน")))

    assert report.ok, "an unknown instrument is not a row error"
    assert report.unrecognised_instruments == ["ปี่ใน"]
    assert "ปี่ใน" in report.summary()
    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert stored.instrument_category == "UNKNOWN"


def test_refreshing_an_instrument_moves_the_category_with_it():
    import_roster_text(text=csv_text(GOOD))
    assert EligibleStudent.objects.get(institutional_id="6699000001").instrument_category == "STRINGS"

    import_roster_text(text=csv_text(GOOD.replace("ดับเบิลเบส", "เปียโน")))

    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert stored.instrument == "เปียโน"
    assert stored.instrument_category == "PIANO"


def test_a_blank_instrument_column_does_not_wipe_a_stored_category():
    """A roster export with the column missing must not silently uncategorise people."""
    import_roster_text(text=csv_text(GOOD))
    blank = GOOD.replace("ดับเบิลเบส", "")

    report = import_roster_text(text=csv_text(blank))

    assert report.ok, report.errors
    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert stored.instrument_category == "STRINGS", "the category survives a blank column"


# --- Deactivation --------------------------------------------------------------


def test_deactivating_missing_rows_is_separate_and_explicit():
    """A partial import must never wipe the roster, so this is its own action."""
    import_roster_text(text=csv_text(GOOD, OTHER))
    keep = import_roster_text(text=csv_text(GOOD), dry_run=True)

    removed = deactivate_missing(keep_ids={"6699000001"}, dry_run=True)
    assert removed == 1
    assert EligibleStudent.objects.filter(is_active=True).count() == 2, "a dry run writes nothing"

    deactivate_missing(keep_ids={"6699000001"})
    assert EligibleStudent.objects.get(institutional_id="6699000001").is_active is True
    assert EligibleStudent.objects.get(institutional_id="6699000002").is_active is False
    assert keep.valid == 1


def test_a_personal_address_is_stored_but_will_not_auto_approve():
    """Why the department's sheet needs care.

    The importer validates the *shape* of an address, not whether it is the
    institutional one. A roster row holding a personal address is stored happily
    and then cannot match a registration, so the student needs staff approval
    instead. This is the behaviour the acceptance work found in the real file.
    """
    report = import_roster_text(text=csv_text("6699000001,someone@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))

    assert report.ok, "the shape is valid, so the import accepts it"
    stored = EligibleStudent.objects.get(institutional_id="6699000001")
    assert stored.email == "someone@gmail.com"


# --- Correcting one address -----------------------------------------------------


def _correct(entry, email, reason="ยืนยันกับภาควิชาแล้ว", actor=None):
    """Run the correction the way a view does, through the protocol."""
    from core.services.errors import OperationOutcome
    from core.services.protocol import run_operation
    from core.services.roster import correct_email

    return run_operation(
        actor=actor,
        operation="staff_correct_roster_email",
        payload={"entry": entry.pk, "email": email},
        key=None,
        body=lambda ctx: OperationOutcome.success(
            **correct_email(ctx, entry=entry, email=email, reason=reason)
        ),
    )


def test_a_wrong_roster_address_can_be_corrected(staff_user):
    """The one thing the import refuses to do, done explicitly and with a trail.

    The department's list holds personal addresses for students who must register
    with their institutional one, and without this there is no way to remedy it.
    """
    from core.models import AuditEvent

    import_roster_text(text=csv_text("6699000001,personal@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))
    entry = EligibleStudent.objects.get(institutional_id="6699000001")

    outcome = _correct(entry, "6699000001@student.chula.ac.th", actor=staff_user)

    assert outcome.ok, outcome.code
    assert outcome.data["previous_email"] == "personal@gmail.com"
    assert outcome.data["changed"] is True
    entry.refresh_from_db()
    assert entry.email == "6699000001@student.chula.ac.th"

    event = AuditEvent.objects.get(action="roster.email_corrected")
    assert event.changes["from"] == "personal@gmail.com"
    assert event.changes["to"] == "6699000001@student.chula.ac.th"
    assert event.reason
    assert event.actor_id == staff_user.pk


def test_the_corrected_address_now_matches_a_registration(staff_user, frozen):
    """The point of the whole exercise: the student becomes approvable."""
    from core.services import identity

    factories_ok = import_roster_text(text=csv_text("6699000001,personal@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))
    assert factories_ok.ok
    entry = EligibleStudent.objects.get(institutional_id="6699000001")

    _correct(entry, "6699000001@student.chula.ac.th", actor=staff_user)

    # Registration rejects the personal domain, and auto-approval needs ID *and*
    # address to match the roster.
    result = identity.start_registration(
        institutional_id="6699000001",
        email="6699000001@student.chula.ac.th",
        name="ชื่อ ทดสอบ",
    )
    user, _ = identity.verify_email(result.raw_token)
    assert user.eligibility == User.Eligibility.APPROVED


def test_correcting_leaves_the_linked_account_alone(staff_user):
    """A roster correction is not a licence to change somebody's login identity."""
    student = factories.make_user(username="6699000001", email="6699000001@student.chula.ac.th")
    factories.make_roster_entry(user=student, instrument="เปียโน")
    entry = EligibleStudent.objects.get(institutional_id="6699000001")

    outcome = _correct(entry, "6699000001@student.chula.ac.th", actor=staff_user)

    assert outcome.ok, outcome.code
    assert outcome.data["changed"] is False, "the same address is a no-op, not an error"
    student.refresh_from_db()
    assert student.email == "6699000001@student.chula.ac.th"
    assert student.eligibility == User.Eligibility.APPROVED


def test_a_stale_link_is_reported_rather_than_revoked(staff_user):
    """When the account no longer agrees, say so — do not silently un-approve."""
    student = factories.make_user(username="6699000001", email="old@student.chula.ac.th")
    factories.make_roster_entry(user=student, instrument="เปียโน")
    entry = EligibleStudent.objects.get(institutional_id="6699000001")

    outcome = _correct(entry, "new@student.chula.ac.th", actor=staff_user)

    assert outcome.ok, outcome.code
    assert outcome.data["linked_account_id"] == student.pk
    assert outcome.data["linked_account_matches"] is False

    student.refresh_from_db()
    assert student.eligibility == User.Eligibility.APPROVED, "approval is not withdrawn"
    assert student.email == "old@student.chula.ac.th", "the account's own address is kept"


def test_an_address_held_by_another_student_is_refused(staff_user):
    import_roster_text(text=csv_text(GOOD, OTHER))
    entry = EligibleStudent.objects.get(institutional_id="6699000001")

    outcome = _correct(entry, "6699000002@student.chula.ac.th", actor=staff_user)

    assert not outcome.ok
    assert outcome.code == "duplicate_account"
    entry.refresh_from_db()
    assert entry.email == "6699000001@student.chula.ac.th"


@pytest.mark.parametrize("email", ["", "   ", "not-an-address"])
def test_a_bad_address_is_refused(staff_user, email):
    import_roster_text(text=csv_text(GOOD))
    entry = EligibleStudent.objects.get(institutional_id="6699000001")

    outcome = _correct(entry, email, actor=staff_user)

    assert not outcome.ok
    assert outcome.code == "invalid_input"
    entry.refresh_from_db()
    assert entry.email == "6699000001@student.chula.ac.th"


def test_a_reason_is_required(staff_user):
    import_roster_text(text=csv_text(GOOD))
    entry = EligibleStudent.objects.get(institutional_id="6699000001")

    outcome = _correct(entry, "elsewhere@student.chula.ac.th", reason="   ", actor=staff_user)

    assert not outcome.ok
    assert outcome.code == "invalid_input"
    entry.refresh_from_db()
    assert entry.email == "6699000001@student.chula.ac.th"


def test_staff_can_correct_an_address_from_the_roster_screen(client, staff_user):
    from django.urls import reverse

    import_roster_text(text=csv_text("6699000001,personal@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))
    entry = EligibleStudent.objects.get(institutional_id="6699000001")
    client.force_login(staff_user)

    response = client.post(
        reverse("core:staff_correct_roster_email", args=[entry.pk]),
        {"email": "6699000001@student.chula.ac.th", "reason": "ยืนยันกับภาควิชาแล้ว"},
    )

    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.email == "6699000001@student.chula.ac.th"


def test_a_student_cannot_correct_a_roster_address(client, student):
    from django.urls import reverse

    import_roster_text(text=csv_text("6699000001,personal@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))
    entry = EligibleStudent.objects.get(institutional_id="6699000001")
    client.force_login(student)

    response = client.post(
        reverse("core:staff_correct_roster_email", args=[entry.pk]),
        {"email": "6699000001@student.chula.ac.th", "reason": "x"},
    )

    assert response.status_code == 403
    entry.refresh_from_db()
    assert entry.email == "personal@gmail.com"


# --- The import's explicit opt-in -----------------------------------------------


def test_an_import_still_refuses_an_address_change_by_default():
    import_roster_text(text=csv_text("6699000001,old@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))

    with pytest.raises(ValueError):
        import_roster_text(text=csv_text("6699000001,new@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))

    assert EligibleStudent.objects.get(institutional_id="6699000001").email == "old@gmail.com"


def test_an_import_rewrites_addresses_only_when_explicitly_allowed():
    import_roster_text(text=csv_text("6699000001,old@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))

    report = import_roster_text(
        text=csv_text("6699000001,new@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"),
        allow_email_change=True,
    )

    assert report.ok, report.errors
    assert report.email_changed == 1, "the count must be visible, never silent"
    assert "rewritten" in report.summary()
    assert EligibleStudent.objects.get(institutional_id="6699000001").email == "new@gmail.com"

    from core.models import AuditEvent

    event = AuditEvent.objects.filter(action="roster.imported").latest("id")
    assert event.changes["email_changed"] == 1
    assert event.changes["allow_email_change"] is True


def test_the_opt_in_counts_rewrites_in_a_dry_run_without_writing():
    import_roster_text(text=csv_text("6699000001,old@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"))

    report = import_roster_text(
        text=csv_text("6699000001,new@gmail.com,ชื่อ ทดสอบ,เปียโน,1,program"),
        dry_run=True,
        allow_email_change=True,
    )

    assert report.email_changed == 1
    assert EligibleStudent.objects.get(institutional_id="6699000001").email == "old@gmail.com"


def test_the_opt_in_never_steals_another_students_address():
    """Correcting an address is allowed; taking someone else's identity is not.

    Row 6699000001 is offered 6699000002's address. That is a rebinding, and the
    opt-in does not cover it.
    """
    import_roster_text(text=csv_text(GOOD, OTHER))

    with pytest.raises(ValueError):
        import_roster_text(
            text=csv_text(GOOD.replace("6699000001@", "6699000002@")),
            allow_email_change=True,
        )

    assert EligibleStudent.objects.get(institutional_id="6699000001").email == (
        "6699000001@student.chula.ac.th"
    )
    assert EligibleStudent.objects.get(institutional_id="6699000002").email == (
        "6699000002@student.chula.ac.th"
    )


# --- Round trip ----------------------------------------------------------------


def test_the_staff_export_round_trips_through_the_importer(client, staff_user):
    """What staff download must be re-importable, or a refresh is a trap."""
    from django.urls import reverse

    import_roster_text(text=csv_text(GOOD, OTHER))
    client.force_login(staff_user)

    response = client.get(reverse("core:staff_roster_export"))
    assert response.status_code == 200
    exported = response.content.decode("utf-8-sig")

    report = import_roster_text(text=exported, dry_run=True)

    assert report.ok, report.errors
    assert report.valid == 2
    assert report.unchanged == 2, "an exported roster must import as already present"
    assert report.created == 0
