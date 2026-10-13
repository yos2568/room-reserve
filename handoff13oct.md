# Handoff — Room Reserve · 13 October 2026

**Repository:** `https://github.com/yos2568/room-reserve`
**Previous handoff:** `handoff24sep.md` (superseded by this file)
**Branch:** `main` at `0c5fc81` (PR #8 merged 12 Oct). This session's work is on
`claude/intelligent-gates-iw6824`, open as **PR #10** (not merged). **PR #9** (room 303
approvals and two-hour bookings, from another session) is also open and **conflicts with
PR #10** — read "Merge order" before merging either.
**Production:** `https://roomreserve.yos.in.th` (Hostinger VPS, Docker Manager project `roomreserve`)

## Honest status

`NOT_DEPLOYED`. Everything below is on PR #10 only. A second, independent review (13 Oct) found six issues; all are fixed (see "Second review" below). Production still runs the image from
before PR #8 unless someone deployed #8 since 24 September; check `APP_IMAGE` in Docker
Manager. PR #10 passes 614 tests locally (PostgreSQL 16 and Chromium), and GitHub CI is green on
`8128219` ("Lint, tests, and verification gate" and "Build and publish immutable image").

## What PR #10 contains (8 commits, 57 files, +3,137 / −83)

| Commit | Decision | What |
|---|---|---|
| `9313e6f` | — | Clean-ups from reviewing PR #8. A booking chosen before registering is forgotten after 1 hour (shared computers). An off-site `next` is dropped. An eligibility decision always needs a listed reason. A failed decision on an already-decided account says so. The staff menu group uses an explicit page map. |
| `7eb2e8e` | D-41 | Staff **Book a room** (several hours, up to 90 days ahead, no approval, no check-in). Staff bookings are never no-shows. **Upcoming bookings** page. Today links to Room approvals. |
| `e88f63f` | D-42 | Master's conference room **`ป.โท` (ห้องประชุม ป.โท)**: members-only, reservable by its access list and staff, no walk-ins. Staff **Room access lists** screen (paste rows from Excel). |
| `474b090`, `78dcd34` | D-43 | An email on a room access list may register, and is **auto-approved** once the email is confirmed (owner's choice). Staff **Correct student ID** action. |
| `d4270c2`, `6c921e6` | D-44 | Home page opens with a **"right now" board** (every room's state, today's strip, one action). Staff **Booking timeline** (rooms × hours, click a bar to act on it). |
| `8128219` | D-41…D-44 | Fixes from a code review: staff no-show strikes, roster-ID squatting, the self-declared instrument on auto-approved accounts, ID correction on staff accounts, the board not refreshing, overlapping timeline bars, and staff holding two rooms at once (migration 0018). |

Full details: `docs/decisions.md` D-41 to D-44 plus the "Review follow-up" entry, and the PR #10 description.

### The admin's four reported issues (12 Oct, from the staff member using the `maintainer` login)

| Reported (Thai) | Cause | Resolution |
|---|---|---|
| admin จองวันล่วงหน้าไม่ได้ | Staff booked through the student flow (2-day window) | Book a room: up to 90 days (`STAFF_BOOKING_HORIZON_DAYS`) |
| เลือก 17:00–20:00 ไม่ได้ | Student rules: 1 hour, no adjacent hours, 2 a day | Book a room: any consecutive range within opening hours |
| ที่นิสิตขอจองมาไม่ขึ้นในระบบของ admin | Requests appear only on Room approvals; there was no page for future bookings | Upcoming bookings page, Booking timeline, Today card linking to approvals |
| admin ก็ขึ้นรออนุมัติห้อง | Staff requests waited like a student's | Staff bookings of approval rooms are approved as made |

## Second review (13 Oct, independent reviewer)

| Finding | Status |
|---|---|
| Staff-set instrument ignored on room-list accounts | Fixed: new field `declared_category_confirmed` (migration 0019) |
| Timeline wrong on shortened/longer opening days | Fixed: CSS for 1–24 columns |
| Last bookable staff date rejected afternoon hours | Fixed: horizon counts whole dates |
| Conference-room refusal gave piano/percussion text | Fixed: "on its list" message; posters hide the walk-in line where walk-ins are not allowed |
| Booking hidden behind a later class/closure on the timeline | Fixed: drawn on a second line |
| Staff hold in progress can be cancelled and released | Fixed (owner's decision): in use from the start of its hour; End early instead of Cancel. **Tell the admin (ดิว).** |
| Thai copy polish (“ว่างอีกครั้งเวลา … น.”, “ขอจองเวลา … น.”, “จองเวลา … น.”, “ตารางห้องรายชั่วโมง”) | Applied |

## Merge order: PR #9 and PR #10 conflict

A trial merge (`git merge-tree`) of the two branches conflicts in 9 files:
`core/services/availability.py`, `core/services/booking.py`, `core/services/suggest.py`,
`core/templates/core/room_profile.html`, `docs/decisions.md`, `docs/policy.md`,
`locale/th/LC_MESSAGES/django.po` / `.mo`, `tests/test_weekly_blocks.py`.

Three of these are not just text conflicts:

1. **Migration numbers collide.** Both add `0016_…` on top of `0015`: #9 has
   `0016_booking_duration_two_hours`, #10 has `0016_booking_is_staff_booking`, `0017`, `0018`, `0019`.
   Whichever merges second must renumber its migrations to follow the other's, with the
   dependency updated. Never merge both and leave two `0016` leaves.
2. **Decision numbers collide.** #9 calls its decision **D-41** too. Renumber the second one
   (and every "D-4x" reference in its code comments) when merging.
3. **Behaviour overlaps.** #9 lets students hold **two-hour bookings** (one row with
   `slot_end = start + 2h`, in off-peak hours) and opens 303 to every category with approval.
   Several parts of #10 assume every booking row is one hour:
   - The staff timeline (`core/services/timeline.py`) places a bar at `local_start.hour` for one hour.
   - The home board strip and the "free until / free again" lines are built from hourly grid cells.
   - Staff Book a room creates one row per hour, under the old one-hour constraint.

   After merging both, the timeline must use `slot_end` for the bar's end, and the board
   and staff holds need checking against two-hour rows.

**Recommendation:** merge **#10 first** (bigger, and it touches more shared code). Then update
#9 on top of it: renumber its migration to `0020`, its decision to D-45, resolve the
conflicts, and fix the timeline's bar length. Ask Claude to do that update and run the full
suite before merging #9. The owner should first confirm #9's rules (303 open to all
categories with approval; two-hour off-peak bookings) are still wanted alongside D-41's
staff booking.

## Deploy procedure (unchanged from 24 Sep, plus new steps)

1. Merge the PR on GitHub; wait for the main CI run to publish the image.
2. Get the digest from the CI log line `pushing manifest for ghcr.io/yos2568/room-reserve:sha-<commit>@sha256:<digest>`.
3. hPanel → Docker Manager → **roomreserve** → Manage → set `APP_IMAGE` → **Save and deploy**.
4. **Migrations (required for #10):** `docker exec roomreserve-web-1 python manage.py migrate --noinput`
   (0016 staff-booking flag, 0017 members-only room + access list, 0018 staff holds may overlap,
   0019 staff-confirmed instrument).
5. **Room settings (required for #10):** `docker exec roomreserve-web-1 python manage.py seed_rooms`
   creates room `ป.โท`.
6. Optional env: `STAFF_BOOKING_HORIZON_DAYS` (default 90). No other new variables.

## After deploying PR #10 (owner / admin tasks)

1. **Conference room details.** Staff → Configuration → room `ป.โท`: set the real capacity
   (seeded as **20**, a guess) and equipment. If the room has a real building number, tell
   Claude; the room code `ป.โท` is used on the lobby board and posters.
2. **Paste the master's list.** Staff → งานประจำวัน → **รายชื่อผู้มีสิทธิ์ใช้ห้อง**: copy all
   rows from the department's spreadsheet (both sheets) and paste them in. A local test of
   the real file found 21 people and skipped 4 title/header rows. **The list is not in the
   repository**: the uploaded spreadsheet was deleted from the cloud session.
3. **Tell the master's students how to register.**
   - Year 1: their `@student.chula.ac.th` address.
   - Year 2: the Gmail/Outlook address on the list, plus their own student ID.
   - Accounts are approved once the email is confirmed.
   - If a year-2 student types a wrong ID, use Staff → Users → Manage → **แก้ไขรหัสนิสิต**;
     the student then signs in with the corrected ID.
4. **Give the admin their own account.** The staff member who reported the issues is using the
   `maintainer` superuser login. Invite them as operational staff (Staff → Invitations) so
   the audit log shows who did what, and keep `maintainer` for the owner.
5. **Reprint door posters** for the new room (Staff → Posters).

## Carried forward from 24 September (status not re-checked this session)

These were open on 24 Sep and nothing in this session touched them. Confirm their state:
- Tighten DMARC at z.com to `p=quarantine` (owner).
- Delete the old `roomreserve@yos.in.th` mailbox in Mailcow (owner).
- Invite staff/teachers; name the room 301 approver (Configuration → room 301 → room administrator).

## Design work

A design canvas with mock-ups (admin dashboard, booking timeline, student phone home, room
page, home status page) is at `https://claude.ai/artifact/6zAka3KdqvDqv5oVNCcc1E`. It is
private to the owner's account.

- **Built so far:** the home status board and the staff timeline.
- **Not yet built:**
  - the student phone home (limit bars "hours held 2/4", status cards with explanations);
  - the admin dashboard home (needs-action queue, summary tiles, busy-hours heatmap);
  - room pages with 30-day statistics.

## Known limitations and follow-ups (not started)

- **Thai user manual** (`docs/manual/`) does not yet cover Book a room, Upcoming bookings,
  Booking timeline, Room access lists, the conference room, or the new home board. Its
  screenshots predate the redesign.
- **Staff bookings show one bar per hour** on the timeline (each hour is its own row, so it can
  be cancelled separately). Merging adjacent staff bars visually is a possible polish.
- **Short timeline bars truncate names** ("ปวีณา ร...."). The full name is in the tooltip and the side panel.
- **Home page queries:** `room_access.member_room_ids` and the grid are computed more than
  once per home-page request (found in review; harmless at this scale, worth caching per
  request later).
- **Floor-plan image** does not show room `ป.โท` (location unknown).
- **No-JavaScript home page:** the board and timetable refresh via htmx; without JS the
  page is correct but static until reloaded (same as before).
- `Booking.checkin_token` leftover column decision is still pending (from 24 Sep).

## Verification done this session

- `ruff check`, `ruff format --check`, `manage.py check`, `makemigrations --check`: clean.
- Full suite including browser tests: **614 passed** (PostgreSQL 16, Chromium).
- Migrations 0016–0018 applied cleanly to a scratch database migrated to `0015`.
- Screens checked in a real browser, in Thai, at 390 px, 1366 px and 1440 px: no horizontal overflow.
- A code review of the whole branch: nine findings fixed in `8128219`, one (repeated queries) left as above.

## Working notes for the next session

- **This session ran in a Claude Code cloud container**, not on the owner's Mac. To run the
  suite there:
  - Start PostgreSQL 16 locally (`initdb` + `pg_ctl`) with a `roomreserve` superuser role.
  - Install `requirements/base.txt` + `dev.txt` into a venv.
  - The pinned Playwright expects `chromium_headless_shell-1234`, but the container ships
    build 1194; a symlink to the installed headless shell made the browser tests run.
  - `gettext` must be installed for `msgfmt`.
- **Translations:** `makemessages` reformats the whole `.po` file (a large noisy diff). New
  Thai entries were appended by hand and compiled with
  `msgfmt -o locale/th/LC_MESSAGES/django.mo locale/th/LC_MESSAGES/django.po`. Check for
  gaps by running `makemessages` on a copy, then restoring the original.
- **CSS:** run `npm run build:css` after adding Tailwind classes to a template. The new
  board and timeline styles are plain CSS at the end of `tailwind/input.css`, including
  generated `.tl-s*`, `.tl-e*`, `.now-at-*` and `.past-to-*` classes.
- **Personal data:** the master's student list and the roster are never committed. Tests use
  synthetic names and `example.com` addresses.
