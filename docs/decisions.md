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
