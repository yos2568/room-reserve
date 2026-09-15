"""Canonical instrument categories.

The roster's instrument column is free text and the department writes it however
it likes, so the mapping is load-bearing: a student whose spelling is not in the
table cannot reserve an instrument-specific room. These tests pin the real
spellings, including the pairs that differ by a single Thai mark, and keep the
migration's frozen copy in step with the service.
"""

from __future__ import annotations

import importlib

import pytest

from core.models import InstrumentCategory
from core.services import instruments
from tests import factories

# Every spelling that appears in the department's four-year roster, with the
# category it must produce. Kept verbatim — `กีตาร์` and `กีต้าร์คลาสสิก` differ by one
# mark, and so do `เชลโล` and `เชลโล่`, which is exactly why the table exists.
REAL_ROSTER_SPELLINGS = {
    "ขับร้อง": InstrumentCategory.VOICE,
    "ไวโอลิน": InstrumentCategory.STRINGS,
    "คลาริเน็ต": InstrumentCategory.WOODWIND,
    "เปียโน": InstrumentCategory.PIANO,
    "แซ็กโซโฟน": InstrumentCategory.WOODWIND,
    "ทรัมเป็ต": InstrumentCategory.BRASS,
    "ฮอร์น": InstrumentCategory.BRASS,
    "ฟลูต": InstrumentCategory.WOODWIND,
    "ทูบา": InstrumentCategory.BRASS,
    "เครื่องตี": InstrumentCategory.PERCUSSION,
    "กีตาร์": InstrumentCategory.GUITAR,
    "ทรอมโบน": InstrumentCategory.BRASS,
    "วิโอล่า": InstrumentCategory.STRINGS,
    "โอโบ": InstrumentCategory.WOODWIND,
    "ดับเบิลเบส": InstrumentCategory.STRINGS,
    "กีต้าร์คลาสสิก": InstrumentCategory.GUITAR,
    "บาสซูน": InstrumentCategory.WOODWIND,
    "เชลโล": InstrumentCategory.STRINGS,
    "แซ็ก": InstrumentCategory.WOODWIND,
    "เชลโล่": InstrumentCategory.STRINGS,
    "ฮาร์บ": InstrumentCategory.HARP,
    "ขับร้องเพลงคลาสสิก": InstrumentCategory.VOICE,
}


@pytest.mark.parametrize(("spelling", "expected"), sorted(REAL_ROSTER_SPELLINGS.items()))
def test_every_spelling_in_the_real_roster_categorises(spelling, expected):
    assert instruments.categorise(spelling) == expected.value


def test_the_variants_that_differ_by_one_mark_both_work():
    """เชลโล / เชลโล่ and กีตาร์ / กีต้าร์ are different strings, same instrument."""
    assert instruments.categorise("เชลโล") == instruments.categorise("เชลโล่")
    assert instruments.categorise("กีตาร์") == instruments.categorise("กีต้าร์คลาสสิก")
    assert instruments.categorise("แซ็ก") == instruments.categorise("แซ็กโซโฟน")


def test_the_piano_and_percussion_categories_are_exactly_what_the_room_needs():
    """The two categories the instrument-specific room lists."""
    piano = [s for s, c in REAL_ROSTER_SPELLINGS.items() if c == InstrumentCategory.PIANO]
    percussion = [s for s, c in REAL_ROSTER_SPELLINGS.items() if c == InstrumentCategory.PERCUSSION]
    assert piano == ["เปียโน"]
    assert percussion == ["เครื่องตี"]


@pytest.mark.parametrize("value", ["", None, "   ", "ไม่ทราบ", "theremin", "ปี่ใน"])
def test_an_unknown_spelling_is_unknown_rather_than_guessed(value):
    assert instruments.categorise(value) == InstrumentCategory.UNKNOWN.value
    assert instruments.is_recognised(value) is False


def test_whitespace_and_case_do_not_defeat_the_lookup():
    assert instruments.categorise("  เปียโน  ") == InstrumentCategory.PIANO.value
    assert instruments.categorise("Piano") == InstrumentCategory.PIANO.value
    assert instruments.categorise("ดับเบิล   เบส") == InstrumentCategory.UNKNOWN.value, (
        "a doubled space inside a Thai name is not the same spelling"
    )
    assert instruments.categorise("  ไวโอลิน ") == InstrumentCategory.STRINGS.value


def test_unrecognised_reports_each_distinct_spelling_once():
    values = ["เปียโน", "ไม่ทราบ", "ไม่ทราบ", "theremin", "", "ไวโอลิน"]

    assert instruments.unrecognised(values) == ["theremin", "ไม่ทราบ"]


# --- Resolving a signed-in student's category ----------------------------------


@pytest.mark.django_db
def test_a_linked_roster_row_gives_the_category():
    student = factories.make_user()
    factories.make_roster_entry(user=student, instrument="เปียโน")

    assert instruments.category_for_user(student) == InstrumentCategory.PIANO.value


@pytest.mark.django_db
def test_an_unlinked_account_has_no_category():
    """The normal state for a student approved by staff whose roster email never matched.

    Nothing may be reserved that needs a category, which is why the roster email
    fix matters for more than tidiness.
    """
    student = factories.make_user()
    factories.make_roster_entry(instrument="เปียโน", is_active=True)  # not linked

    assert instruments.category_for_user(student) is None


@pytest.mark.django_db
def test_an_inactive_roster_row_does_not_confer_a_category():
    student = factories.make_user()
    factories.make_roster_entry(user=student, instrument="เปียโน", is_active=False)

    assert instruments.category_for_user(student) is None


def test_an_anonymous_visitor_has_no_category():
    assert instruments.category_for_user(None) is None


# --- The migration's frozen copy ------------------------------------------------


def test_the_migration_alias_table_matches_the_service():
    """The backfill may not drift from the live table.

    The migration keeps its own copy on purpose — a migration must not import
    application code — so something has to notice when the two diverge. Without
    this, a spelling added to the service would silently stay UNKNOWN for every row
    the backfill touched.
    """
    # Imported by path: a module name may not start with a digit.
    backfill = importlib.import_module("core.migrations.0005_backfill_instrument_category")
    expected = {
        instruments.normalise(key).casefold(): value for key, value in instruments.CATEGORY_ALIASES.items()
    }

    assert expected == backfill.BACKFILL_ALIASES
