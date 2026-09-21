# Acceptance matrix

Every ID from `roomreserveapp.v3.md` §12, mapped to the real artefact that
evidences it. **A checkbox is not a test.** Where an ID cannot be closed locally,
it says so and names the missing input rather than claiming a pass.

- Revision: see `QA_REPORT.md` (code revision and file hashes).
- Suite: **257 tests**, 24 of them driven by Chromium through `live_server`.
- Command for the whole local gate: `bash scripts/verify`.

Legend: **Tested** — automated, run in the suite. **Verified in a browser** —
driven through Chromium, not just HTTP. **Manual** — cannot be automated, with
the reason. **Blocked** — a named external input is missing.

| ID | Status | Evidence |
|---|---|---|
| A01 | **Passed** | `scripts/verify` check `clean_bootstrap`: a database created for the run, migrated from scratch, `seed_rooms` + `seed_demo`, then seven public routes fetched over HTTP and asserted 200 with the configured active room set (twelve after the room-10 addition), exactly one restricted to piano and percussion, and a staff account. `local_mail_flow` sends a real message over SMTP to a catcher and reads it back. |
| A02 | **Tested** | `tests/test_booking.py` (27) — same-day, exactly seven days, beyond the horizon, off-hours, weekends and holidays. |
| A03 | **Tested** + browser | `tests/test_booking.py`, `tests/test_lifecycle.py` (18); `tests/test_browser_student.py::test_the_public_grid_renders_every_room_and_every_hour` renders the exact-hour cell. |
| A04 | **Tested** + browser | `tests/test_lifecycle.py` — before start, at start, deadline − ε, at deadline; `tests/test_browser_walkin.py::test_a_stale_check_in_button_is_refused_and_reconciles_the_no_show` presses a stale Check in after the boundary. |
| A05 | **Tested** + browser | `tests/test_lifecycle.py`; `test_browser_walkin.py::test_the_student_cannot_reclaim_the_room_they_just_missed`; `test_a_walk_in_after_the_grace_period_keeps_the_original_hour` asserts `slot_end` is the hour boundary, not now + 60 min. |
| A06 | **Tested** | `tests/test_concurrency.py` (7) — two users race one remaining slot; no second grace. |
| A07 | **Tested** + browser | `tests/test_booking.py` (quota includes COMPLETED/NO_SHOW; cancellation restores); `test_browser_student.py::test_the_daily_quota_is_enforced_in_the_browser` proves the refusal persists nothing. |
| A08 | **Tested** | `tests/test_booking.py` — same and adjacent hours across rooms rejected, two-hour separation allowed, short walk-in still blocks the next hour. |
| A09 | **Tested** | `tests/test_concurrency.py` — twenty eligible users, one slot, exactly one persisted success. |
| A10 | **Tested** | `tests/test_concurrency.py` — one user, several non-adjacent slots, one allowance. |
| A11 | **Tested** | `tests/test_booking.py` — same key returns the recorded result; changed payload rejected; another user cannot read it. |
| A12 | **Tested** + browser | `tests/test_concurrency.py`; `test_browser_walkin.py` stale check-in asserts `NO_SHOW` and exactly one strike, never check-in plus no-show. |
| A13 | **Tested** | `tests/test_concurrency.py`, `tests/test_scheduler.py` (18) — scheduler stopped, next mutation clears the hold and records one strike. |
| A14 | **Tested** | `tests/test_booking.py`, `tests/test_outbox.py` (23) — a rejected mutation still commits reconciliation; an unexpected failure sends no mail. |
| A15 | **Tested** | `tests/test_sanctions.py` (10) — three unconsumed strikes in 30 days, exact expiry excluded, repeat ticks neither extend nor recreate. |
| A16 | **Tested** + browser | `tests/test_sanctions.py`; `test_browser_staff.py::test_voiding_a_strike_opens_a_review_instead_of_lifting_the_sanction` — voiding a consumed strike leaves the sanction in force and opens a review. |
| A17 | **Tested** + browser | `tests/test_sanctions.py`; `test_browser_staff.py::test_a_lifted_suspension_restores_booking` and `test_browser_walkin.py::test_a_suspended_student_is_told_why_and_cannot_start_a_room`. |
| A18 | **Tested** + browser | `tests/test_concurrency.py`; `test_browser_staff.py::test_staff_close_a_room_range_and_the_booking_inside_it_is_cancelled` — preview, confirm, cancellation, and the student's grid showing the hours closed. |
| A19 | **Tested** | `tests/test_calendar.py` (24) — override precedence, stored deadlines unchanged by a future grace change, no raw admin bypass. |
| A20 | **Tested** + browser | `tests/test_identity.py` (33) — roster match or staff approval, pending and inactive blocked, collision and resend-replay rejected. `test_browser_student.py::test_a23_...` drives register → verify → approve → login. |
| A21 | **Tested** + browser | `tests/test_permissions.py` (59) — no staff-to-superuser, cross-student isolation, CSRF and open-redirect rejection, CSV formula safety. `test_browser_staff.py` and `test_browser_student.py` drive the same boundaries through the UI. |
| A22 | **Tested** | `tests/test_outbox.py` (23) — rollback, retry, expired lease recovery, duplicate enqueue, stale suppression, SMTP ambiguity documented. |
| A23 | **Verified in a browser** | `tests/test_browser_student.py::test_a23_student_journey_from_registration_through_history` — register → emailed link → password → staff approval → login → reserve → cancel → re-reserve → QR door check-in → history, with both the visible state and the persisted row asserted at each step. |
| A24 | **Verified in a browser** | `tests/test_browser_walkin.py` (8) — walk-in after :15 with the original hour retained, the short-session warning, and full / wrong-room / too-late / suspended / closed feedback; `tests/test_browser_staff.py` (3) — closure preview and confirm, strike void opening a review, suspension lift. |
| A25 | **Verified in a browser** | `tests/test_browser_bilingual.py` (6) — both language prefixes serve their own copy with Gregorian years, email copy renders in both languages, a 360 px viewport gets the card layout with no horizontal overflow and can complete a booking, Tab reaches a Reserve control, the reserve → cancel flow runs with JavaScript disabled, and the grid refresh replaces rather than nests its region. |
| A26 | **Verified in a browser** | `tests/test_browser_privacy_posters.py::test_no_public_page_leaks_an_identity_or_a_secret` — nine anonymous pages scanned for a student's name, ID, email and the secret key; `::test_a_personal_response_is_not_publicly_cacheable` — `private, no-store` plus `Vary: Cookie` on personal responses. CSV formula payloads: `tests/test_permissions.py::test_csv_export_neutralises_formula_payloads` and `::test_roster_export_of_a_hostile_name_is_safe`. |
| A27 | **Partly passed, partly manual** | Immutable image build, migration drift, `collectstatic` and restart persistence: `scripts/verify` checks `docker_image_build`, `migration_drift`, `collectstatic`, `restart_persistence`. Clean backup/restore into a fresh database with counts compared: check `backup_and_restore`. `manage.py check --deploy` against `prod.py` is **manual** (it needs real hostnames and TLS values to be meaningful). Migration/image rollback rehearsal is **manual** and unperformed. CI pushing an image to a registry is **blocked**: no registry credentials. |
| A28 | **Tested** | `tests/test_scheduler.py` (18) — overlapping ticks skip, a mail outage is visible without a liveness restart loop, a documented incident exempts and corrects penalties. |
| A29 | **Partly verified, partly manual** | `tests/test_browser_privacy_posters.py::test_one_poster_per_room_is_generated_and_each_carries_its_own_room` — one PNG per active room, each byte-identical to a code generated from that room's own target, each target fetched and confirmed to render that room; `::test_the_printable_sheet_renders_one_legible_poster_per_room` — one poster per room, with the printed module size computed from the rendered box. The assertion is **per active room, not nine**; the current configured set is twelve rooms after adding numbered stall 10 (see the deviation below). **Manual**: the physical door mapping, and reading a printed code with a scanner — no QR decoder is installed, and adding one only for a test would change the deployment surface. |
| A30 | **Tested, performance unmeasured** | `tests/test_concurrency.py` runs 20 rounds with fresh fixtures and asserts zero invariant breaches (marked `slow`). **Not measured**: p95 mutation latency and the hardware it was measured on. The local machine is not the intended deployment hardware, so a number here would not be deployment evidence either way. |
| — | **Blocked** | Deployment: real SMTP, a domain and TLS, a NAS/object-store backup destination, and external alerting. Pilot: about ten approved students and two weeks of real use. Neither can be manufactured. |

---

## Deviations from V3

V3 is the sole product specification, so anything built that it does not describe is
recorded here rather than folded in silently. Both of these need the owner's
decision on amending the specification text.

**1. Twelve rooms, not nine.** The updated department floor plan gives ten numbered
stalls plus two larger rooms: room 303 (reserved to piano and percussion students,
D-28) and room 304 (general, but blocked during its class hours by the teaching
timetable, also D-28). V3 states nine rooms and A29 requires nine posters.
The code, the acceptance assertions and the runbook now follow the data
(`ROOM_COUNT`, `ROOM_OVERRIDES`, one poster per active room) instead of the
literal nine. Room 10 is a normal general room and follows the same weekday
08:00–20:00 opening schedule as stalls 1–9.

V3 lines that need amending: `roomreserveapp.v3.md` §2 line 35 ("Nine upright-piano
practice rooms"), §9 line 255 ("nine printable A4 QR posters"), §11 line 287
("nine posters"), §12 line 295 (A01 "nine rooms") and §12 line 323 (A29 "Nine
posters").

Evidenced by: `tests/test_room_audience.py`, `tests/test_instruments.py`,
`scripts/verify`'s `clean_bootstrap` (asserts `settings.ROOM_COUNT` rooms and exactly
one room restricted to piano and percussion).

**2. A per-room reservation audience, which V3 does not describe at all.** V3 has no
concept of a room being restricted to any group; `roomreserveapp.md` explicitly
records the original assumption "All 9 rooms identical — no room-preference logic".
The audience is therefore a net-new rule — see D-21 … D-26 in
`docs/decisions.md` — and it is enforced only on **reserving**, with walk-in open to
everyone and check-in untouched.

## Notes

**A29's QR evidence is deliberately not a decode.** Each poster's PNG is compared
byte-for-byte against a code regenerated from the URL that poster should carry, and
each URL is then fetched to confirm it resolves to that room. That proves the
mapping from poster to room. It does not prove that a phone camera reads the
printed sheet, which stays a deployment check.

**A30 is honest about its scope.** The invariant half is tested. The performance
half is not, and the specification itself says a local measurement is only
evidence for local hardware.

**A20's roster match is only as good as the roster.** The department's four-year
list was imported on 2026-09-15, but only 17 of its 74 rows carry an institutional
email address — the rest hold personal ones, and one is a typo. Registration
refuses a non-institutional domain and auto-approval needs the ID and email to
match together, so 57 students must be approved by staff until the addresses are
corrected. The mechanism is correct and tested (`tests/test_roster.py`,
`tests/test_identity.py`); the data is not yet fit for automatic approval.

**The remedy for those 57 rows exists and is tested.** Because the import refuses to
change an address on an existing entry, correcting a roster is an explicit action:
one entry at a time from the staff roster screen (audited as
`roster.email_corrected`), or a bulk refresh with `--allow-email-change`, which
counts and records every address it rewrites. Neither will take an address that
already belongs to a different institutional ID, neither edits the student's own
account, and neither withdraws an existing approval. Covered by
`tests/test_roster.py` (15 tests, including the permission boundary and the
importer's opt-in) and
`test_browser_staff.py::test_staff_correct_a_roster_address_from_the_roster_screen`.

**Nothing here was closed by loosening a threshold.** Two thresholds that look
arbitrary are derived: the poster legibility check computes printed millimetres per
QR module rather than asserting a pixel size, and the graph-refresh check asserts a
bounded request count rather than trusting that the loop terminates.

**3. A suggestions dashboard, which V3 does not describe.** A "where to go next"
panel above the grid ("Free right now", "Bookable later today") and
"other rooms free at this hour" inside refusals — a deterministic ranking over
the same grid states, never a recommendation model (D-29). Evidence:
`tests/test_suggest.py`.

**4. A self-declared instrument at registration, which V3 does not describe.** V3
derives eligibility from the roster alone; because most roster rows cannot link
(personal emails), registration now collects an instrument declaration that opens
restricted rooms once the account is approved, with the roster overriding it and
staff able to correct it (D-30, superseding part of D-22). Evidence:
`tests/test_declared_category.py`.
