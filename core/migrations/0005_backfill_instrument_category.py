"""Backfill the canonical instrument category on roster rows that already exist.

The mapping is copied here rather than imported from ``core.services.instruments``
on purpose: a migration has to keep working after the service is refactored, and a
migration that imports application code breaks the moment that code moves. The two
tables cannot drift apart silently — ``tests/test_instruments.py`` asserts that this
copy and ``CATEGORY_ALIASES`` are identical.

Reversing it would throw away information for no benefit, so the reverse is a no-op.
"""

from __future__ import annotations

import re
import unicodedata

from django.db import migrations

_WHITESPACE = re.compile(r"\s+")

# Frozen copy of the alias table at the time of this migration.
BACKFILL_ALIASES = {
    "เปียโน": "PIANO",
    "เปียโนไฟฟ้า": "PIANO",
    "เปียโนคลาสสิก": "PIANO",
    "piano": "PIANO",
    "เครื่องตี": "PERCUSSION",
    "เครื่องกระทบ": "PERCUSSION",
    "เพอร์คัสชัน": "PERCUSSION",
    "กลอง": "PERCUSSION",
    "กลองชุด": "PERCUSSION",
    "ระนาด": "PERCUSSION",
    "percussion": "PERCUSSION",
    "drums": "PERCUSSION",
    "ไวโอลิน": "STRINGS",
    "วิโอล่า": "STRINGS",
    "เชลโล": "STRINGS",
    "เชลโล่": "STRINGS",
    "ดับเบิลเบส": "STRINGS",
    "violin": "STRINGS",
    "viola": "STRINGS",
    "cello": "STRINGS",
    "double bass": "STRINGS",
    "ฟลูต": "WOODWIND",
    "โอโบ": "WOODWIND",
    "คลาริเน็ต": "WOODWIND",
    "บาสซูน": "WOODWIND",
    "แซ็กโซโฟน": "WOODWIND",
    "แซ็ก": "WOODWIND",
    "flute": "WOODWIND",
    "oboe": "WOODWIND",
    "clarinet": "WOODWIND",
    "bassoon": "WOODWIND",
    "saxophone": "WOODWIND",
    "ทรัมเป็ต": "BRASS",
    "ฮอร์น": "BRASS",
    "ทรอมโบน": "BRASS",
    "ทูบา": "BRASS",
    "trumpet": "BRASS",
    "horn": "BRASS",
    "trombone": "BRASS",
    "tuba": "BRASS",
    "ขับร้อง": "VOICE",
    "ขับร้องเพลงคลาสสิก": "VOICE",
    "ขับร้องคลาสสิก": "VOICE",
    "ร้องเพลง": "VOICE",
    "voice": "VOICE",
    "กีตาร์": "GUITAR",
    "กีต้าร์คลาสสิก": "GUITAR",
    "กีตาร์คลาสสิก": "GUITAR",
    "กีต้าร์": "GUITAR",
    "classical guitar": "GUITAR",
    "guitar": "GUITAR",
    "ฮาร์บ": "HARP",
    "harp": "HARP",
}


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", str(text))).strip()


def forwards(apps, schema_editor):
    EligibleStudent = apps.get_model("core", "EligibleStudent")
    for row in EligibleStudent.objects.all().iterator():
        category = BACKFILL_ALIASES.get(_normalise(row.instrument).casefold(), "UNKNOWN")
        if row.instrument_category != category:
            row.instrument_category = category
            row.save(update_fields=["instrument_category"])


def noop(apps, schema_editor):
    """Nothing to undo: the previous value carried no information."""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0004_room_reservation_scope_and_instrument_category"),
    ]

    operations = [
        migrations.RunPython(forwards, noop),
    ]
