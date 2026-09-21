# Handoff — Room Reserve V3 · 21 September 2026 (UI checkpoint 6)

**Project:** `/Volumes/Crucial2TB/All Codes/FAA/Room problem`  
**Normative product specification:** `roomreserveapp.v3.md`  
**Previous checkpoint:** `handoff19sep26.md`  
**Latest local preview:** `http://127.0.0.1:8004/th/?date=2026-09-21&preview=room10`

## Honest status

The application has a substantial visual refresh and is running locally. The latest
dashboard now follows the approved reference direction: compact FAA branding, a
navy header, a white availability workspace, timetable-first booking information,
and the new supplied illustrated floor plan below the schedule. The updated image
also confirms room 10 as an active numbered practice room. The historical database
row for room 10 was normalised from its old label “ห้องซ้อมใหญ่” to “ห้องซ้อม 10”
during the local seed.

The latest UI work is **not committed, pushed, or deployed**. The working tree
already contains earlier uncommitted Priority 2/3 UI work as well as this latest
header refinement. Do not deploy from this working tree until the diff is reviewed,
tests are rerun, unrelated files are separated, and a clean commit is created.

### Latest functional update after this checkpoint

- The room board is available at `/th/choose-room/` with date and hourly selection.
- Room 301 was added as an active, timetable-only room. Its first-semester class
  blocks come from `รายงานการใช้ห้อง-ดุริยางคศิลป์ตะวันตก-ต้น69.md`; approximate
  empty hours are visible but cannot be reserved or used as a walk-in.
- Rooms 303 and 304 now require administrator approval. Their requests become
  `PENDING_APPROVAL` and must be approved through the existing room-admin flow.
- Registration is separated into Student, Faculty, and Staff/administrator paths.
  Students use student ID and instrument selection. Faculty/staff/admin accounts
  are invitation-only and use email instead of a student ID; they cannot
  self-assign privileged roles.
- The room board no longer displays the unnecessary capacity line.

## 1. Product and specification

`roomreserveapp.v3.md` remains the sole product specification. Historical files
such as `roomreserveapp.md`, `roomreserveapp.v1.2.md`, and comparison documents may
provide context but must not override V3.

The most important current decisions are:

- **D-28:** the real third-floor layout contains numbered stalls, room 303, and room
  304. Room 303 is reservation-restricted for piano/percussion students, while
  room 304 is a general room.
- **D-35:** the updated floor plan adds active general room 10. The configured room
  set is now room 301, stalls 1–10, and rooms 303 and 304: thirteen active rooms
  total.
- **D-36:** room 301 is timetable-only; it shows free/class status but never offers
  reservation or walk-in actions. Rooms 303 and 304 require administrator approval.
- **Room 304 schedule:** Monday–Friday, 08:00–20:00, with only the confirmed
  recurring class spans blocked: Monday Counterpoint 10:00–12:00, Tuesday
  Skill-Piano 12:00–14:00, Thursday Harmony 10:00–12:00, and Friday Wind Pedagogy
  13:00–15:00. Empty hours remain reservable.
- **D-29:** room suggestions are deterministic and use the same availability state
  as the timetable.
- **D-30:** students may declare an instrument family at registration; a linked
  roster row remains authoritative.
- **D-31:** confirmation/reminder emails include owner-bound QR check-in links.
- **D-32:** visual enhancement uses progressive CSS and preserves no-JavaScript
  behavior and reduced-motion support.
- **D-33/D-34:** faculty identity, bilingual routes, invited faculty accounts, and
  read-only teacher accounts remain in scope.

## 2. Latest design update

The reference image is treated as a composition and hierarchy reference, not as a
replacement for the application's behavior.

### Desktop direction

- Compact dark-navy header with `FAA | Room Reserve` wordmark.
- The full faculty seal is no longer the dominant header element; this fixes the
  previous oversized-logo problem and keeps the page title and timetable visible.
- Primary navigation remains available, with contained horizontal scrolling when
  the available width is tight.
- Main page uses a pale blue/ice background and a white dashboard surface.
- “Practice room availability” is the visual focus, followed by date navigation,
  capacity/equipment filters, the legend, and the room timetable.
- Availability states retain explicit meaning: free, booked, class, and closed.
- The room map is a secondary orientation panel below the schedule, matching the
  supplied reference composition.

### Floor-plan asset

The canonical dashboard asset is the user-supplied 3D floor-plan image:

`core/static/img/floor-plan-dashboard.jpg`

It is used by `core/templates/core/grid.html` with descriptive alternative text
covering the fourteen room labels shown in the artwork: 301, 1–10, 302, 303,
and 304. Room 10 is now present in the live timetable as a general room. The older
`floor-plan.png` and `floor-plan-polished.png` files are currently unreferenced
untracked files; review them before the next commit rather than deleting them
blindly.

### Visual tokens

The current design tokens in `tailwind/input.css` use:

- Navy: `#153964` for the header and primary identity
- Ink navy: `#10264a` for headings and room names
- Ice paper: `#edf5fd` for the application background
- Crimson: `#c31749` / `#9b1238` for primary actions and booked states
- Cool line: `#d7e3ef` for borders and grid structure

The design follows the UI/UX Pro Max and frontend-design guidance used during the
refresh: content hierarchy first, compact branding, clear status colors, keyboard
focus visibility, comfortable touch targets, responsive layout, and restrained
motion.

## 3. Files changed for the latest UI work

- `core/templates/core/base.html` — compact `FAA | Room Reserve` wordmark and
  responsive header structure.
- `core/templates/core/grid.html` — availability page hierarchy, filters, timetable
  and floor-plan panel.
- `core/templates/core/partials/dashboard.html`
- `core/templates/core/partials/grid_table.html`
- `core/templates/core/partials/slot_cell.html`
- Other student/staff templates received the preceding visual refresh; inspect the
  full diff before committing.
- `tailwind/input.css` — source design tokens and component styles.
- `core/static/css/tailwind.css` — generated CSS output; rebuild with
  `npm run build:css` after source changes.
- `core/static/img/floor-plan-dashboard.jpg` — canonical supplied floor-plan asset.
- `roomreserve/settings/base.py` — `ROOM_COUNT=10`, plus a label-only config entry
  that normalises the historical room-10 row to `ห้องซ้อม 10`; it remains a normal
  general room.

No booking service, model, calendar, email, permission, or URL behavior was
intentionally changed by the latest branding adjustment.

## 4. Verification evidence

Completed for the latest UI pass:

- `npm run build:css` — passed.
- `~/.virtualenvs/roomreserve/bin/python manage.py check` — passed.
- `~/.virtualenvs/roomreserve/bin/pytest -q tests/test_weekly_blocks.py tests/test_room_audience.py` — 36 passed.
- `npm run build:css` — passed; generated CSS is current.
- `git diff --check` — no whitespace errors; Git also prints the pre-existing
  non-monotonic AppleDouble pack-index warning.
- Fresh Django preview on port 8004 renders the compact wordmark, timetable, room
  map panel, and supplied floor-plan image.
- The local seed reports twelve active rooms, and the preview accessibility tree
  confirms room links 1–10, 303, and 304 plus the floor-plan image.

The latest non-browser test run reported:

`440 passed, 2 failed, 28 deselected`

The two failures are unrelated to room 10 or the visual update:
`tests/test_priority_three.py::test_lobby_status_does_not_expose_student_or_booking_details`
expects the English literal `"In use"` while the response is rendered in Thai, and
`tests/test_scheduler.py::test_a_voided_strike_no_longer_counts_towards_a_suspension`
is a time-sensitive sanctions assertion. The focused room tests passed; resolve or
document these existing failures before claiming a clean full-suite gate.

## 5. Local environment notes

- PostgreSQL is running in Docker as `roomreserve-db-1`.
- Port `8004` is the fresh preview for this checkpoint.
- Port `8003` may still be an older preview and should not be used to judge the
  current room-10 label or floor-plan asset.
- Port `8000` is an older/stale preview and should not be used to judge the current
  UI.
- `HEAD` before these uncommitted UI changes is `bc98e7a Add Priority 2 and 3
  booking workflows`.
- The repository repeatedly reports:
  `non-monotonic index .git/objects/pack/._pack-...idx`.
  Do not repair the Git object store destructively as part of this UI handoff.

## 6. Next steps

1. Review the current diff and separate the intended UI changes from unrelated
   existing worktree changes.
2. Inspect the dashboard at desktop width and a narrow mobile width; confirm the
   header does not push the title or timetable below the fold.
3. Rerun `~/.virtualenvs/roomreserve/bin/pytest -q -m 'not browser'` with Docker PostgreSQL available and resolve
   the lobby translation assertion before calling the revision clean.
4. Run the browser checks, especially bilingual navigation, grid actions, mobile
   layout, and room-map rendering.
5. Remove or deliberately retain the two unreferenced floor-plan images, then run
   `git diff --check` and commit only the reviewed revision.
6. Deploy only from that clean committed revision; no Hostinger deployment has been
   performed for this UI checkpoint.
