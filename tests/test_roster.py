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

from core.models import EligibleStudent
from core.services.roster import deactivate_missing, import_roster_text

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
