# Handoff — Room Reserve V3 · 19 September 2026 (checkpoint 3)

**Project:** `/Volumes/Crucial2TB/All Codes/FAA/Room problem`
**Previous checkpoint:** `handoff15sep26.md` (superseded by this file)
**Newest decisions:** `docs/decisions.md` **D-28** (the real floor and the
teaching timetable) — read that first; it supersedes the old D-23 "tenth room".

## What this session changed

The department supplied the actual floor plan and the teaching timetable
(`ตารางห้อง อาคารศิลปกรรมชั้น3.pdf`, semester 1/2569), and the app now models the
real floor instead of a placeholder:

| Room | On the floor | In the app |
|---|---|---|
| Stalls 1–9 | Two rows (①–⑤, then ⑥–⑨ numbered right-to-left) | General rooms, unchanged |
| **303** | Large room, bottom-left of the plan | Piano & percussion may **reserve**; anyone may walk in. No class hours (the sheet has no A303 page) |
| **304** | `ห้องบรรยาย 1` (Lecture Room 1), bottom-right | General room, **blocked during its class hours** |
| 301 / 302 | Recital Hall / open area | Not in the app |
| ~~10~~ | Never existed — placeholder `ห้องซ้อมใหญ่` | Deactivated by `seed_rooms`, never deleted |

Room 304's class hours (from the sheet, **still to be confirmed with the
department** — D-28 records how they were derived):

- Monday 10:00–12:00 — Counterpoint (`อ.ดร.ปริญญา`)
- Tuesday 12:00–13:00 — Skill-Piano (`ผศ.ดร.รามสูร`)
- Thursday 10:00–12:00 — Harmony (`อ.ดร.ปริญญา`)

The sheet's note "Day: 28/9/69, 16/11/69" under Counterpoint looks like exam
dates; it is not modelled.

## The new mechanism: `WeeklyBlock`

A recurring (weekday, hour-range) block per room — migration `0006`, model in
`core/models/room.py`, configured in `ROOM_WEEKLY_BLOCKS` and applied by
`manage.py seed_rooms` exactly like room audiences. Optional `valid_from` /
`valid_until` bounds a block to a semester.

- Enforced in **one place**: `calendar.assert_slot_open`, which both
  `booking.validate_advance_target` and `walkin.use_now` already call. A blocked
  hour refuses reservations *and* walk-ins with the new code
  `class_in_session` ("มีการเรียนการสอนในห้องนี้…").
- The grid shows it as a **"Class"** cell (Thai "มีสอน") with the course name in
  the tooltip — the same "say why" doctrine as the piano/percussion chip.
- **Check-in is untouched**: a booking made before a schedule change still
  checks in (same survivorship as a room-audience change, D-26).
- An explicit closure still wins over a class hour.
- No staff screen for the timetable yet — a schedule change is a settings edit
  plus a seed run (runbook §9). Deliberate: a wrong schedule blocks a room for
  a semester.

## State of the code

- **372 tests pass** (359 before + 13 in `tests/test_weekly_blocks.py`), 28 in
  Chromium; `ruff check` and `ruff format --check` clean; `scripts/verify`
  result for this session is in the evidence directory it printed.
- Migration `0006_weeklyblock.py`; the Thai catalogue gained the new strings and
  one wrong translation was fixed ("Piano & percussion only" used to render as
  just "เปียโน").
- `scripts/verify`'s bootstrap now asserts the configured room set (9 stalls +
  override rooms = 11) and the configured weekly-block count.
- The uncommitted department spreadsheet (`รายชื่อนิสิตป.ตรี ทั้ง 4 ปี.xlsx`) is
  still deliberately untouched — the privacy question from the previous
  checkpoint (§6.1 there) is still open.

## Added later the same day: the suggestions dashboard (D-29)

"Which room should I take?" is answered by `core/services/suggest.py` — a
deterministic ranking over the same grid states, surfaced as a panel above the
grid ("Free right now" / "Bookable later today") and inside refusals ("other
rooms free at this hour"). D-29 records why a probabilistic recommender was
rejected. 13 further tests in `tests/test_suggest.py` (suite now 385; the
`scripts/verify` run quoted above predates the suggester unless re-run).

## Added later the same day: the declared instrument (D-30)

Registration now asks for an instrument family (`User.declared_category`,
migration 0007). A declared piano/percussion student can reserve room 303 once
staff approve the account; a linked roster row always overrides the declaration;
staff set/clear it on Staff → Users (audited `user.declared_category_set`).
Decision D-30 supersedes the "never self-declared" half of D-22 — made because
zero of the 9 real piano/percussion rows can currently link (personal emails).
13 further tests in `tests/test_declared_category.py` (suite now 398).

## Added later the same day: QR check-in from the email (D-31)

Advance reservations get a secret token (`Booking.checkin_token`, migration 0008,
backfilled); the confirmation and reminder emails embed a QR (segno data-URI in a
new HTML alternative) for `/th/check-in/<token>/` — one booking, owner-only,
explicit confirm, normal window rules, idempotent. 11 tests in
`tests/test_checkin_qr.py` (suite now 409).

## Added later the same day: the visual refresh (D-32)

The UI was modernised with patterns from GoogleChrome/modern-web-guidance: glass
sticky header (scroll-state "stuck" queries), scroll-driven card reveals,
`:user-invalid` fields, refined buttons/cards/chips — all plain CSS, progressive,
reduced-motion-gated. Cross-document view transitions were tried and REJECTED:
they hung the no-JS navigation guarantee (see the warning comment in
`tailwind/input.css` and D-32).

## Added later the same day: FAA identity + faculty accounts (D-33/D-34)

- Palette swapped to the faculty's own crimson (logo + building lattice), logo
  vendored at `static/img/faa-logo.png`, paper background. View transitions were
  rejected earlier (see D-32).
- **Faculty accounts**: `User.is_teacher` (migration 0009), created only by staff
  invitation (Invitations page now offers "Teacher (view-only)"); login accepts
  **email or student ID** in one field; teacher accounts are read-only via
  `TEACHER_READ_ONLY` in `account_block_code`. 8 tests in
  `tests/test_teacher_accounts.py` (suite now 417).

## What remains, in order

1. **Confirm room 304's class hours with the department** (and ask whether A303
   really has none). One settings edit + `seed_rooms` if they differ.
2. The previous checkpoint's list is unchanged and still stands: the 57
   corrected roster addresses, clearing demo logins, CI wiring for
   `scripts/verify`, A27/A30 leftovers, printing now **eleven** posters, Thai
   copy review, then deployment and the pilot.
3. Decide on the D-28 amendment of V3's "nine rooms" lines (owner's call, listed
   in `docs/acceptance-matrix.md`).

## Environment gotchas: unchanged

Everything in `handoff15sep26.md` §9 still applies (Docker first, BuildKit
fallback on exFAT, never export `DJANGO_SETTINGS_MODULE` around pytest, `msgfmt`
not `compilemessages`, `npm install --include=dev`, run scripts as `bash`).
