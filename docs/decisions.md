# Decisions

Every non-obvious choice, with the reason. Recorded so the next person does not
have to reverse-engineer it, and so a reviewer can disagree with a specific
decision rather than the whole build.

`D-01`–`D-08` were made before this handoff and are carried forward from
`BUILD_STATUS.md`. `D-09` onward were made while closing the browser-acceptance
and verification gaps.

---

## Environment and platform

**D-01 — exFAT root, so the venv and the database live elsewhere.**
The project directory is on an exFAT volume: no symlinks, no POSIX permission
bits. PostgreSQL refuses to initialise a data directory it cannot `chmod 0700`,
so the database is a named Docker volume and the virtualenv is on APFS at
`~/.virtualenvs/roomreserve`. A bind-mounted data directory is not an option on
this filesystem, and was not attempted.

**D-02 — `username` holds the institutional ID.**
`User.institutional_id` is a property over `username`, not a second column. Two
columns would allow them to disagree, and the institutional ID is already the
login identifier.

**D-03 — `slot_date` is denormalised.**
A Bangkok-local date derived in `Booking.save()`. It makes the "one room, one
hour" partial unique index and the per-day quota queries expressible in SQL
rather than in Python.

**D-04 — `BLOCKING_STATUSES` / `QUOTA_STATUSES` are module constants.**
Services and database constraints share them, so a status cannot be "blocking" in
one place and not the other.

**D-05 — `NODE_ENV=production` in the ambient shell.**
An inherited `NODE_ENV=production` makes npm skip devDependencies, so the Tailwind
build silently does nothing. Always `npm install --include=dev`. The Dockerfile
does the same.

**D-06 — Plain static storage in tests.**
Production uses `CompressedManifestStaticFilesStorage`, which only answers after
`collectstatic`. Test renders therefore failed with "Missing staticfiles manifest
entry". Test settings use `StaticFilesStorage`; the manifest storage is exercised
by the bootstrap check in `scripts/verify`, which runs `collectstatic` for real.

**D-07 — `outbox.enqueue()` returns `(notification, created)`.**
The reminder pass needs to know whether it actually queued new work.

**D-08 — The scheduler drains against the current wall clock.**
Not the moment captured before reconciliation, so a tick delivers the mail it just
queued instead of waiting for the next minute.

---

## Added while closing the acceptance gaps

**D-09 — The test clock has a process-wide form.**
`clock.frozen_clock` is `threading.local`, which is right for a unit test and
useless for anything served: Django answers requests on worker threads, so a
freeze set by the caller never reaches a view. This made
`freeze_from_environment()` — documented for exactly this purpose — inert for both
`runserver` and the `live_server` fixture. `clock.freeze_process_clock()` sets a
module-level freeze that every thread observes, gated on `ALLOW_TEST_CLOCK` like
every other test-clock entrance point. `frozen_clock` still wins inside its own
thread. `tests/test_clock.py` pins both behaviours, including that a thread-local
freeze does *not* leak to a server thread.

**D-10 — Redeeming a verification link and setting the password is one operation.**
`identity.complete_registration()` consumes the single-use link exactly once. The
previous shape called `verify_email()` (which stamps `used_at`) and then
`set_initial_password()` (which re-asserts the same token as unused), so every
real registration ended on "that link has already been used" with an HTTP 410 and
no usable account. The password is validated *before* the link is consumed, so a
weak password leaves the link usable.

**D-11 — A password-validation failure re-renders the form, not a link error.**
All three "choose a password" routes (verification, invitation, reset) previously
rendered the "this link cannot be used" page for *any* `OperationRejected`,
including a password that failed the validators. A weak password is a form error;
saying otherwise sends the student to ask for a replacement link they do not need.

**D-12 — `PersonalResponseCacheMiddleware` sets the cache directives.**
Django sets no `Cache-Control` of its own, so nothing prevented an intermediary
from storing a signed-in student's page. Authenticated responses, and every
non-GET, are now `private, no-store`; every response varies on `Cookie`.
Anonymous pages are deliberately *not* marked `public`: they carry the CSRF
cookie, and a shared cache allowed to store one would replay it to other people.

**D-13 — `PolicyVersion` exposes `opening_hour` and `closing_hour`.**
The grid and the poster sheet read them from the policy object, but only the
editable fields had properties, so both rendered ":00–:00". The geometry stays
non-editable (it is overwritten from settings in `_validate`) and is now readable.
`grace_minutes` was not added as an alias: the poster sheet uses the real name,
`checkin_grace_minutes`.

**D-14 — The availability region swaps with `outerHTML`.**
The htmx refresh target is the region itself, so `hx-swap="innerHTML"` nested a
second `#availability-region` inside the first on every refresh, and each later
lookup then addressed the stale outer copy. `htmx:afterOnLoad` was also removed
from the trigger list: a region that reloads on its own response would keep
firing as long as the page stayed open. `tests/test_browser_bilingual.py` asserts
both the single region and a bounded request count.

**D-15 — The staff user screen lists a student's strikes and can void one.**
`staff_void_violation` existed as a URL, a view and a service, but no template
linked to it, and the staff screen did not show the strikes behind a sanction. The
student page tells them to "contact the department office with the date and time
so it can be reviewed", so without this control the appeal path the product
advertises could not be completed by anyone. Voiding still only *opens* a review;
lifting the sanction remains a separate, explicit staff decision.

**D-16 — The use-now page does not offer an action that can only fail.**
`use_now_confirm.html` rendered its submit button unconditionally, so a stale grid
link or a suspended account reached a page whose only control was guaranteed to be
refused. The button now appears only when the room is genuinely startable, and the
replaced copy says which of the two situations applies.

**D-17 — Django's async-context guard is stood down for browser runs.**
Playwright's sync API runs its own event loop in the test thread and calls
`asyncio._set_running_loop()` on the way out, which it never clears. From the
first Playwright call onward, `asyncio.get_running_loop()` succeeds while no async
code is running, so Django's `async_unsafe` refuses every later ORM call —
including pytest-django's end-of-test flush. `tests/conftest.py` sets
`DJANGO_ALLOW_ASYNC_UNSAFE=1` only when the session actually collected a
`browser`-marked test, so the guard stays live for the rest of the suite. Every
view in this project is synchronous, so the guard protects nothing here.

**D-18 — `scripts/verify` treats a blocked check as a failure of the gate.**
There is no `|| true` and no empty test selection. The script creates its own
databases (`roomreserve_bootstrap_<stamp>`, `roomreserve_restore_<stamp>`), never
touches the application database or the `roomreserve_pgdata` volume, and drops
only what it created. Any `FAIL` or `BLOCKED` yields `NOT_LOCAL_PASS` and a
non-zero exit.

**D-19 — The roster dry run counts what it would do, and can fail.**
`import_roster_rows` returned before counting in dry-run mode, so every valid file
reported "0 would be created" — the opposite of useful for the one control that
stands between staff and a bulk write of real personal data. It also skipped
conflict detection, so the preflight could not warn about the contradiction that
would abort the real import. Both are now computed read-only. The import had no
tests at all before this; `tests/test_roster.py` covers validation, atomicity, the
preflight, deactivation, and an export → import round trip.

**D-20 — The department's roster spreadsheet is not imported verbatim without a
warning.** The 2569 file's email column holds 17 institutional addresses and 57
personal ones (55 `gmail.com`, one `gmail.con`, one `suthi.ac.th`). The importer
validates only the *shape* of an address, and registration refuses a
non-institutional domain, so those 57 rows can never match a registration and those
students need staff approval. The operator was told before writing, chose to import
the file as it reads, and the consequence is recorded in the runbook rather than
silently normalised — deriving `<id>@student.chula.ac.th` would have meant
inventing addresses the university may not have assigned.

**D-21 — A room's reservation audience is a room field that fails closed.**
`Room.reservation_scope` is `EVERYONE` (default) or `LISTED`, with the permitted
instrument categories in `RoomAllowedCategory` rows. A room marked `LISTED` with no
categories permits **nobody**: an empty list is far more likely to be a mistake
than an intention to open the room, so the failure closes rather than opens.

**D-22 — Instrument categories are canonical, derived, and never guessed.**
The roster's `instrument` column is free text — 22 spellings across 74 students,
including the same instrument twice (`กีตาร์`/`กีต้าร์คลาสสิก`, `เชลโล`/`เชลโล่`,
`แซ็ก`/`แซ็กโซโฟน`). A room's audience is never matched against that text.
`EligibleStudent.instrument_category` holds the canonical family, derived by a
lookup table in `core/services/instruments.py`; the raw text is kept as provenance.
Anything the table does not cover becomes `UNKNOWN`, which cannot open a restricted
room, and the roster import **reports** it so a human extends the table. The
alternative — mapping by guesswork — would either refuse legitimate students or
admit the wrong ones, and nobody would know which.

**D-23 — The tenth room is a deliberate deviation from V3.**
V3 says "nine rooms" (five places) and A29 says "nine posters". The department added
a larger room that only piano and percussion students may reserve. The
specification is not silently rewritten: the deviation is recorded here, in
`docs/acceptance-matrix.md` under "Deviations from V3", and the affected V3 lines
are listed for the owner to amend.
*Superseded by D-28: the "tenth room" turned out to be room 303, joined by room
304, and the placeholder name `ห้องซ้อมใหญ่` is retired.*

**D-24 — No staff bypass.**
Staff keep exactly the abilities they had. They cannot reserve the restricted room
for a non-eligible student, because the application has no staff "create booking"
action at all and the staff screen already states that staff cannot bypass booking
rules. A rehearsal for another instrument is served by the walk-in rule instead.

**D-25 — The audience applies to *reserving*, so it is its own function.**
`eligibility.assert_may_reserve_room` holds the audience check, and only
`booking.validate_advance_target` calls it. `walkin.use_now` calls
`assert_may_use_room` (account state and room active) without the audience check,
and `checkin.check_in` calls neither.

This started as the audience check inside `assert_may_use_room`, which is shared
with the walk-in path — so the first version refused walk-ins for everyone outside
piano and percussion, directly contradicting "others may walk in once the hour
starts". `tests/test_room_audience.py` caught it on the first run. A flag parameter
would have been the other option; a separate, correctly named function makes the
distinction impossible to miss at the call site.

**D-26 — Changing who may reserve a room never cancels a booking.**
Restricting a room is not retroactive: an existing reservation survives and still
checks in, and the staff action says so on the page. The alternative punishes a
student for an administrative decision they had no part in.

**D-27 — Correcting a roster address is an explicit, audited action — never an
import side effect.**
The import refuses to change the address on an entry that already exists, because
it must never silently rebind an identity. That is right, but it left **no way at
all** to fix a roster that is simply wrong — which is the common case here: the
department's list holds personal addresses for students who must register with the
institutional one, and until this existed the corrected file would have been
rejected. Two ways in, both deliberate:

* a staff action on the roster screen, one entry at a time, with a mandatory reason
  (`roster.email_corrected`, recording before and after);
* `--allow-email-change` on the import (or the checkbox on the staff form), which
  reports how many addresses it rewrote rather than doing it quietly. An address
  that already belongs to a **different institutional ID** is refused either way:
  that is rebinding a person, not correcting a record.

The correction touches the roster row only. It does not edit a linked account's own
address — a login identity is the student's, not something a spreadsheet correction
rewrites — and it does not re-run eligibility, because withdrawing an approval over
somebody else's clerical error penalises the wrong person. When a linked account no
longer matches the corrected address, the action says so rather than acting on it.

**D-28 — The real floor: stalls 1–9, room 303 restricted, room 304 general with
class hours blocked.**
The department's floor plan and teaching sheet (`ตารางห้อง อาคารศิลปกรรมชั้น3.pdf`,
semester 1/2569) replace the placeholder "tenth room" (`ห้องซ้อมใหญ่`, D-23). The
nine numbered stalls are the general rooms; room 303 — one of the two large rooms
at the back of the floor — carries the piano-and-percussion reservation rule; room
304 (`ห้องบรรยาย 1`) is a general practice room outside its class hours. The old
placeholder room 10 is deactivated by `seed_rooms`, never deleted, so its history
survives.

Room 304's class hours were read from the sheet's own column geometry: pdftotext
word coordinates against the hour ruler, with each label matched to the column or
span it is centred on. The supplied timetable shows Counterpoint on Monday
10:00–12:00, Skill-Piano on Tuesday 12:00–14:00, Harmony on Thursday
10:00–12:00, and Wind Pedagogy on Friday 13:00–15:00. The end hour is exclusive;
blank hours remain reservable. The sheet has no page for A303, so 303 is
unblocked.

**Update 2026-09-20:** The department confirmed that room 304 follows the same
08:00–20:00 Monday–Friday opening schedule as the other rooms. That opening
schedule applies outside the four class spans above; one-off events should use
an explicit closure or date override instead of changing the recurring timetable.

The mechanism is a recurring `WeeklyBlock` per room, configured in
`ROOM_WEEKLY_BLOCKS` and applied by `seed_rooms` like the room audiences, so a
schedule change is a configuration change rather than a migration. A blocked hour
refuses reservations **and** walk-ins through the same `assert_slot_open` the
closures use, the grid labels it "Class" with the course name, and — like a
room-audience change (D-26) — it never cancels a booking that already exists.

**D-29 — Room suggestions are a deterministic ranking, never a model.**
Students under contention need "which room should I take?" answered fast, and the
temptation is to reach for an AI recommendation. That would be the wrong tool:
the grid already computes every room's every slot, so the question has an exact
answer, and a probabilistic answer would sometimes name a room the server then
refuses — raising exactly the failure rate it was meant to lower. The suggester
(`core/services/suggest.py`) ranks the same per-cell states the table renders:
legal for this viewer (calendar, class hours, audience; the current hour under
the walk-in rule), clear of the viewer's own adjacent bookings, inside the
remaining quota, soonest hour then room position. It appears as a dashboard
panel above the grid, and inside refusals as "other rooms free at this hour",
computed fresh per request. A probabilistic service also cannot pass
`scripts/verify` deterministically, and student data must not reach an external
API while the privacy notice is still awaiting faculty approval.

**D-30 — A student may declare their instrument at registration; the roster stays
the authority.**
Supersedes the second half of D-22 ("derived, never self-declared"). With the 2569
roster holding personal addresses for most students, no piano or percussion row
could link to an account, so the restricted room 303 was reserved-by-nobody — the
rule worked and the feature was dead. Registration now asks for an instrument
family (`User.declared_category`). The declaration opens restricted rooms only
once the account is approved — the approval step that already gates every account
— and a roster link, when it appears, silently overrides the declaration because
`category_for_user` prefers the roster row. Staff can set or clear the value on
the user screen (`user.declared_category_set`, audited), and `UNKNOWN` is not
declarable: a student cannot claim our inability to classify them. Everyone,
declared or not, sees restricted rooms' availability; only reserving is filtered.

**D-31 — The emailed QR check-in link: convenience, not proof.** *(Superseded by D-37.)*
Every advance reservation carries a secret token (`Booking.checkin_token`). The
confirmation and reminder emails embed a QR code for `/check-in/<token>/`; a scan
brings the owner to the same deliberate confirm button as everywhere else — same
window rules, same idempotency, same audit. Three gates make the link safe: the
token is unguessable and binds it to that one reservation; sign-in binds the
action to the owner (a token is that student's key, and another account gets a
404); and nothing checks in without the explicit POST. The token never expires on
its own — the check-in window is the gate that matters — and reconciliation still
converts a missed deadline into a no-show before any late confirm. This does not
weaken the standing honesty rule: a QR code, emailed or posted, is never proof of
presence. It lowers the friction of *declaring* presence honestly; staff spot
checks remain the actual check.

**D-32 — The visual refresh uses modern platform CSS, progressively.**
The interface was modernised with patterns from GoogleChrome/modern-web-guidance:
a sticky glass header (backdrop-filter, with a solid fallback), scroll-state
queries that deepen its shadow only while stuck, scroll-driven entry reveals on
the suggestion cards, `:user-invalid` form state, and cross-document view
transitions. Everything is plain CSS — no new JavaScript — and anything that
moves is gated on prefers-reduced-motion; browsers without a feature render the
plain surface untouched.

View transitions and `scroll-behavior: smooth` were **rejected after trial**:
each hangs a path the browser tests guarantee — transitions broke the
JavaScript-disabled navigation flow, smooth scrolling made Playwright's
scroll-into-view never settle on the horizontally scrolling grid. The rejected
rules are kept in `tailwind/input.css` as warning comments. The lesson matches
the house doctrine — the verification harness is the trust anchor, and a visual
nicety that makes the harness hang is a defect, not a delight.

**D-33 — The interface wears the faculty's own colours.**
The generic blue was the loudest AI-slop signal, so the palette is now taken from
the faculty itself: the crimson of the logo and of the Arts Building's red lattice
(`#a02b33`, with tint and shade steps), plaster-white paper (`#f6f5f1`), and the
real faculty logo in the header, vendored under `static/img/` like htmx — no CDN.
Per the frontend-design guidance, colour comes from the subject's world, not from
a framework default.

**D-34 — Faculty accounts are invited, sign in with email or ID, and are
read-only today.**
Roles are never self-selected at sign-in: a self-chosen "admin" is privilege
escalation by definition. Teachers get accounts by staff invitation (the existing
`invite_account`, now with a teacher flag, audited); they set their own password
through the emailed activation link, and sign in with **email or institutional
ID** — one login field, one error message. A teacher account is approved,
verified — and read-only: `TEACHER_READ_ONLY` is stated in
`eligibility.account_block_code`, so every booking mutation refuses and every
button hides without per-view special cases. Widening a teacher's permissions
later is one gate to move, deliberately.

**D-35 — Room 10 is an active general practice room.**
The updated department floor-plan artwork supplied on 2026-09-21 labels a tenth
numbered stall. The operational room set is therefore ten numbered stalls (1–10)
plus rooms 303 and 304: twelve active rooms in total. Room 10 uses the same
08:00–20:00 Monday–Friday opening schedule and general reservation rules as rooms
1–9. It is included by `ROOM_COUNT=10`; its label is explicit only to normalise
the historical row previously named "ห้องซ้อมใหญ่". This supersedes the D-28
sentence that described placeholder room 10 as deactivated; the historical row is
reactivated rather than deleted when `seed_rooms` is run.

**D-36 — Room 301 is a timetable-only teaching room; rooms 303 and 304 require approval.**
The supplied first-semester report identifies A301 as the main teaching room with
recurring classes across Monday–Friday. Its approximate empty hours are shown in
the timetable and room board for planning, but room 301 does not accept advance
reservations or walk-ins. Rooms 303 and 304 remain visible to students; a request
for either room is stored as pending until an assigned room administrator or
operational staff approves it. The server enforces both rules, including direct
requests that bypass the interface.

---

## Rejected alternatives

**Not using SQLite for tests.** The concurrency guarantees rest on row locks,
partial unique indexes and serialisation errors that SQLite does not reproduce,
so a SQLite suite would have passed while the real behaviour was broken.

**Not raising the daily quota for the demo.** The quota is a policy value read
from the environment and editable as a policy version; a demo-only code path
would have been an untested branch in the booking rule.

**Not adding a QR decoder dependency.** `A29` is verified by regenerating each
poster's code from the target it should carry and comparing bytes, plus fetching
each target and confirming the room. Reading the printed sheet back needs a
decoder that is not installed; adding one purely for a test would change the
deployment surface, so that stays a deployment check.

**Not deleting records on the retention default.** The one-academic-year figure is
an unendorsed default. Nothing deletes automatically until faculty approve it.

**D-37 — Students check in only at the door's printed QR.**
The emailed QR link (D-31) and the My bookings button both checked a student in
from anywhere, which made the check-in rule easy to satisfy from bed. Self-service
check-in now lives only at `/r/<room>/bookings/<booking>/check-in/`, the button on
the room page the printed door poster opens; the service still enforces owner,
room and window under the lock. Emails and My bookings tell the student to scan
the door. Staff assisted check-in is unchanged. This raises the effort to cheat but
does not prove presence: a photographed poster works remotely. Stronger options
were weighed and deferred until the pilot shows a need — a changing TOTP code on a
display at each door (roughly 600–1,200 THB per room), a scanner at the floor
entrance reading a per-booking QR, or restricting check-in to the building's
network. `Booking.checkin_token` is no longer written; the column can be dropped in
a later migration.

**D-38 — Book two days ahead, hold at most four upcoming hours; no weekly repeat.**
The department's rule: a student may reserve only two days ahead and hold at most
four hours at once; when one of those hours finishes, they may book another. The
horizon default drops from 7 to 2 days, and a new versioned policy value,
`max_upcoming_hours` (default 4, staff-editable on the Policy screen), caps the
bookings a student holds whose hour has not ended — `PENDING_APPROVAL`,
`SCHEDULED` and `IN_USE`, walk-ins included. Cancelled and no-show hours never
count; a finished hour stops counting the moment it ends. The existing limit of
two bookings per day stays alongside it, so no one takes four hours of one day.
Moving a booking to another room does not run the check, because it does not add
an hour. Weekly repeat booking (up to four weeks ahead) contradicted a two-day
window and was removed; series created earlier can still be cancelled. Existing
databases keep their current policy version until staff create a new one; a
fresh deployment starts with these defaults.

**D-39 — Room 301 is reservable by approved request only (24 September 2026).**
The owner asked that room 301, the main classroom, be usable outside its class
hours once a teacher or an admin approves the request. It is no longer
availability-only: it takes advance requests, which wait in `PENDING_APPROVAL`,
and it refuses walk-ins, like 303 and 304. Who approves is not a new role: the
maintainer names a teacher (or staff member) as 301's room administrator on the
Configuration screen, and superusers and operational staff can approve every
room as before. A teacher account stays read-only for its own bookings; the
room-administrator grant only lets it decide requests for its assigned room.
Class hours on 301 stay blocked.
