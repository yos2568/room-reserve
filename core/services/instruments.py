"""Canonical instrument categories, derived from the roster's free text.

The department writes instruments however it likes — the 2569 roster has **22
distinct spellings across 74 students**, including the same instrument twice:

    กีตาร์  / กีต้าร์คลาสสิก      (guitar)
    เชลโล   / เชลโล่               (cello)
    แซ็ก    / แซ็กโซโฟน            (saxophone)
    ขับร้อง / ขับร้องเพลงคลาสสิก    (voice)

So a room's audience is never matched against that text. The text is preserved as
provenance and this module derives a canonical :class:`InstrumentCategory`.

**Nothing here guesses.** An unrecognised spelling becomes ``UNKNOWN``, which
cannot open a restricted room, and the roster import reports it so a human can
extend the table. Refusing a legitimate piano student because nobody has
categorised their spelling is bad; silently letting anyone into a restricted room
because the match failed is worse.
"""

from __future__ import annotations

import re
import unicodedata

from core.models import EligibleStudent, InstrumentCategory

_UNKNOWN = InstrumentCategory.UNKNOWN.value
_WHITESPACE = re.compile(r"\s+")

# Keyed by the normalised spelling. Extend it from what the import reports, not
# from imagination: `manage.py import_roster` lists anything it cannot place.
CATEGORY_ALIASES: dict[str, str] = {
    # Piano
    "เปียโน": InstrumentCategory.PIANO.value,
    "เปียโนไฟฟ้า": InstrumentCategory.PIANO.value,
    "เปียโนคลาสสิก": InstrumentCategory.PIANO.value,
    "piano": InstrumentCategory.PIANO.value,
    # Percussion
    "เครื่องตี": InstrumentCategory.PERCUSSION.value,
    "เครื่องกระทบ": InstrumentCategory.PERCUSSION.value,
    "เพอร์คัสชัน": InstrumentCategory.PERCUSSION.value,
    "กลอง": InstrumentCategory.PERCUSSION.value,
    "กลองชุด": InstrumentCategory.PERCUSSION.value,
    "ระนาด": InstrumentCategory.PERCUSSION.value,
    "percussion": InstrumentCategory.PERCUSSION.value,
    "drums": InstrumentCategory.PERCUSSION.value,
    # Strings
    "ไวโอลิน": InstrumentCategory.STRINGS.value,
    "วิโอล่า": InstrumentCategory.STRINGS.value,
    "เชลโล": InstrumentCategory.STRINGS.value,
    "เชลโล่": InstrumentCategory.STRINGS.value,
    "ดับเบิลเบส": InstrumentCategory.STRINGS.value,
    "violin": InstrumentCategory.STRINGS.value,
    "viola": InstrumentCategory.STRINGS.value,
    "cello": InstrumentCategory.STRINGS.value,
    "double bass": InstrumentCategory.STRINGS.value,
    # Woodwind
    "ฟลูต": InstrumentCategory.WOODWIND.value,
    "โอโบ": InstrumentCategory.WOODWIND.value,
    "คลาริเน็ต": InstrumentCategory.WOODWIND.value,
    "บาสซูน": InstrumentCategory.WOODWIND.value,
    "แซ็กโซโฟน": InstrumentCategory.WOODWIND.value,
    "แซ็ก": InstrumentCategory.WOODWIND.value,
    "flute": InstrumentCategory.WOODWIND.value,
    "oboe": InstrumentCategory.WOODWIND.value,
    "clarinet": InstrumentCategory.WOODWIND.value,
    "bassoon": InstrumentCategory.WOODWIND.value,
    "saxophone": InstrumentCategory.WOODWIND.value,
    # Brass
    "ทรัมเป็ต": InstrumentCategory.BRASS.value,
    "ฮอร์น": InstrumentCategory.BRASS.value,
    "ทรอมโบน": InstrumentCategory.BRASS.value,
    "ทูบา": InstrumentCategory.BRASS.value,
    "trumpet": InstrumentCategory.BRASS.value,
    "horn": InstrumentCategory.BRASS.value,
    "trombone": InstrumentCategory.BRASS.value,
    "tuba": InstrumentCategory.BRASS.value,
    # Voice
    "ขับร้อง": InstrumentCategory.VOICE.value,
    "ขับร้องเพลงคลาสสิก": InstrumentCategory.VOICE.value,
    "ขับร้องคลาสสิก": InstrumentCategory.VOICE.value,
    "ร้องเพลง": InstrumentCategory.VOICE.value,
    "voice": InstrumentCategory.VOICE.value,
    # Guitar and harp are their own families in this department.
    "กีตาร์": InstrumentCategory.GUITAR.value,
    "กีต้าร์คลาสสิก": InstrumentCategory.GUITAR.value,
    "กีตาร์คลาสสิก": InstrumentCategory.GUITAR.value,
    "กีต้าร์": InstrumentCategory.GUITAR.value,
    "classical guitar": InstrumentCategory.GUITAR.value,
    "guitar": InstrumentCategory.GUITAR.value,
    "ฮาร์บ": InstrumentCategory.HARP.value,
    "harp": InstrumentCategory.HARP.value,
}


def normalise(text: str | None) -> str:
    """The lookup key: NFC, no surrounding space, single internal spaces.

    Excel and hand-typed CSVs both produce double spaces and decomposed Thai
    vowels, which would otherwise defeat an exact match.
    """
    if not text:
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", str(text))).strip()


def categorise(text: str | None) -> str:
    """The canonical category for a free-text instrument, or ``UNKNOWN``."""
    return CATEGORY_ALIASES.get(normalise(text).casefold(), _UNKNOWN)


def is_recognised(text: str | None) -> bool:
    return categorise(text) != _UNKNOWN


def category_for_user(user) -> str | None:
    """The signed-in student's category, from their active roster entry.

    ``None`` when the account has no linked roster row — which is the normal state
    for a student approved by staff whose roster email never matched. A restricted
    room cannot be reserved in that case, so the roster link is load-bearing here.
    """
    if user is None or not getattr(user, "pk", None):
        return None
    row = (
        EligibleStudent.objects.filter(account_id=user.pk, is_active=True).only("instrument_category").first()
    )
    return row.instrument_category if row is not None else None


def unrecognised(values) -> list[str]:
    """Distinct instrument *spellings* that no category covers, normalised.

    Reported by the roster import and shown to staff, so the table grows from real
    data instead of somebody guessing at spellings.

    A blank instrument is not reported: it is missing data rather than an
    unrecognised spelling. It still categorises as ``UNKNOWN`` and so still fails
    closed, which is what matters for the room rule.
    """
    leftovers = {normalise(value) for value in values if normalise(value) and not is_recognised(value)}
    return sorted(leftovers)
