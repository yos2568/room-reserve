# QA report — Room Reserve V3

**Verdict: `LOCAL_PASS_CANDIDATE`** — every automated local check passes on this
revision. Deployment and pilot are **BLOCKED** on missing external inputs, listed
in §7. Neither is claimed.

- **Date of final evidence**: 2026-09-15 (UTC), final run `20260915T161035Z`
  (earlier full passes `20260915T154813Z`, `20260915T145718Z`, `20260915T135931Z`
  and `20260915T133827Z` are kept as well)
- **Code revision**: `05f73ca` plus an uncommitted second batch (the roster email
  correction path, 12 files). See §8 for the hashes that identify what was verified.
- **Runner**: `scripts/verify`
- **Evidence**: `artifacts/qa/verify-20260915T161035Z/` (12 per-check logs plus
  `summary.tsv`) and the four earlier run directories

## 1. Environment

| Component | Version |
|---|---|
| OS | macOS 26.6.2, arm64 |
| CPU | Apple M4 Pro, 14 cores |
| RAM | 64 GB |
| Python | 3.12.11 (venv at `~/.virtualenvs/roomreserve`, APFS) |
| Django | 5.2.17 |
| PostgreSQL | 16.4 (Docker, named volume `roomreserve_pgdata`) |
| Docker Engine | 28.1.1 (Docker Desktop) |
| Playwright / Chromium | 1.62.0 / `chromium-1234` |
| Project filesystem | exFAT (`/Volumes/Crucial2TB/…`) — see §6 |

## 2. How to reproduce

```bash
open -a Docker
cd "/Volumes/Crucial2TB/All Codes/FAA/Room problem"
bash scripts/verify
```

Exit code 0 means every check passed. Non-zero means at least one check failed
**or was blocked**; the summary names which. Per-check logs and a machine-readable
`summary.tsv` land in `artifacts/qa/verify-<UTC timestamp>/`.

## 3. Results — `bash scripts/verify`, final run `20260915T161035Z`

Exit code **0**. `PASS=12 FAIL=0 BLOCKED=0`. Identical results in the four
preceding runs.

| # | Check | Result | Command | Evidence |
|---|---|---|---|---|
| 1 | `interpreter_and_pins` | PASS | compare `requirements/{base,dev}.txt` pins against installed distributions | `pins.log` |
| 2 | `lint` | PASS | `python -m ruff check .` | `lint.log` |
| 3 | `format_check` | PASS | `python -m ruff format --check .` | `format_check.log` |
| 4 | `django_check` | PASS | `manage.py check` (dev settings) | `django_check.log` |
| 5 | `migration_drift` | PASS | `manage.py makemigrations --check --dry-run` → "No changes detected" | `migration_drift.log` |
| 6 | `pytest_suite` | PASS | `python -m pytest -q` → `359 passed in 31.62s` | `pytest_suite.log` |
| 7 | `collectstatic` | PASS | `manage.py collectstatic --noinput` | `collectstatic.log` |
| 8 | `clean_bootstrap` | PASS | new database → `migrate` → `seed_rooms` → `seed_demo` → 7 routes over HTTP; asserts `ROOM_COUNT` rooms and exactly one restricted to piano and percussion | `clean_bootstrap.log` |
| 9 | `docker_image_build` | PASS | `docker compose build web` (legacy-builder fallback) | `docker_image_build.log` |
| 10 | `restart_persistence` | PASS | count rows, `docker compose restart db`, recount | `restart_persistence.log` |
| 11 | `backup_and_restore` | PASS | `pg_dump -Fc` → `createdb` → `pg_restore --exit-on-error` → compare counts | `backup_and_restore.log` |
| 12 | `local_mail_flow` | PASS | drain the outbox over real SMTP to a catcher, read the message back | `local_mail_flow.log` |

### Selected evidence, quoted

**Suite** — `359 passed in 31.62s` (28 of them in Chromium).

**Clean bootstrap** — ten rooms on a database created for the run:

```
bootstrap smoke: 10 rooms, 110 accounts, 1 bookings, 1 queued messages,
7 routes 200, check-in window 8:00-20:00 with 15 minute grace
```

**Backup and restore** — every table matched, and the restore was required to be
non-empty for users, bookings and pending work:

```
source vs restored
core_user: 110 -> 110
core_room: 9 -> 9
core_booking: 1 -> 1
core_notification: 1 -> 1
core_auditevent: 0 -> 0
dump bytes:   120573
```

**Local mail flow** — a real SMTP conversation, not a captured in-process mail:

```
"Subject":"จองห้องซ้อมเรียบร้อย"
"To":[{"Name":"","Address":"student0001@example.invalid"}]
```

**Image build** — `Successfully tagged roomreserve-app:local`.

### Browser suite

24 of the 265 tests are driven by Chromium against a live Django server
(`tests/browserlib.py`, fixture `watched`). They assert visible page state *and*
the persisted row, and fail on any uncaught JavaScript error, any 5xx, and any
unexpected console error.

| File | Tests | Acceptance |
|---|---|---|
| `test_browser_student.py` | 3 | A23 |
| `test_browser_walkin.py` | 8 | A24 (student) |
| `test_browser_staff.py` | 3 | A24 (staff), A16, A17, A18 |
| `test_browser_bilingual.py` | 6 | A25 |
| `test_browser_privacy_posters.py` | 4 | A26, A29 |

## 4. Defects found and fixed during this pass

Ten defects, all found by exercising the running application rather than by
reading it. Five were user-facing breakage that no existing test could see.

| # | Severity | Defect | Fix | Regression test |
|---|---|---|---|---|
| 1 | **Critical** | **Registration could never be completed.** `POST /verify/<token>/` called `verify_email()` (which stamps `used_at`) and then `set_initial_password()` (which re-asserts the same token as unused), so every attempt ended on "that link has already been used" with HTTP 410 and no usable password. | `identity.complete_registration()` consumes the single-use link exactly once (D-10). The password is validated *before* the link is consumed. | `tests/test_identity.py` ×6, including two HTTP-level tests; `test_browser_student.py::test_a23_...` |
| 2 | **High** | **Staff could not void a strike.** `staff_void_violation` had a URL, a view and a service, but no template linked to it, and the staff screen did not show the strikes behind a sanction. The student is told to "contact the department office … so it can be reviewed", so the advertised appeal path was impossible to complete. | Each user's open strikes are listed on the staff screen with a void action (D-15). | `test_browser_staff.py::test_voiding_a_strike_opens_a_review_instead_of_lifting_the_sanction` |
| 3 | **High** | **The printable poster sheet raised `TemplateSyntaxError`.** It used `{% static %}` without `{% load static %}`, and read `policy.grace_minutes`, which the model does not define. | Loads `static`; uses `checkin_grace_minutes` (D-13). | `test_browser_privacy_posters.py::test_the_printable_sheet_renders_one_legible_poster_per_room` |
| 4 | **High** | **The Rules and Help pages stated the check-in window as a blank.** `policy.grace_minutes` again — on the two pages a student reads to learn the rule: "closes exactly  minutes later". | Both use `checkin_grace_minutes`. | `tests/test_content_pages.py` ×8, which fails on any unfilled placeholder in either language |
| 5 | **Medium** | **The grid stated the opening hours as ":00–:00".** `PolicyVersion` exposed only the editable fields, so `policy.opening_hour` / `policy.closing_hour` resolved to nothing. | Read-only geometry properties (D-13). | `test_content_pages.py`, and the bootstrap smoke now prints the window |
| 6 | **Medium** | **`<html lang>` was always `th`, on every page, in every language.** `django.template.context_processors.i18n` is not enabled, so `{{ LANGUAGE_CODE|default:'th' }}` always fell back — and the language switcher always marked Thai as selected. | `{% get_current_language %}` in both base templates. | `test_browser_bilingual.py::test_both_language_routes_...` asserts the attribute |
| 7 | **Medium** | **The availability grid nested itself.** The htmx refresh target is the region itself, so `hx-swap="innerHTML"` added another `#availability-region` inside the first on every refresh, and `htmx:afterOnLoad` in the trigger list re-fired on its own response — a request loop that never terminates. | `hx-swap="outerHTML"`, and the self-trigger removed (D-14). | `test_browser_bilingual.py::test_the_grid_refresh_replaces_the_region_instead_of_nesting_it`, which also bounds the request count |
| 8 | **Medium** | **The use-now page offered a button that could only fail** — for a taken room or a suspended account. | The form appears only when the room is genuinely startable; the replacement copy names the situation (D-16). | `test_browser_walkin.py::test_a_room_that_is_already_taken_offers_no_way_to_start_it`, `::test_a_suspended_student_...` |
| 9 | **Medium** | **A weak password dead-ended the reset and invitation flows**, rendering "this link cannot be used" for a link that was still perfectly good. | A validation failure re-renders the form with the validator's message (D-11). | `tests/test_identity.py::test_the_verification_route_reports_a_weak_password_and_keeps_the_link` |
| 10 | **Medium** | **The roster import's dry run reported "0 would be created" for every valid file.** It returned before counting, so the preflight — the one control standing between staff and a bulk write of real personal data — read as "nothing to do", and it could not detect a conflicting row that would abort the real import. | The dry run counts created/unchanged and surfaces conflicts, read-only (D-19). | `tests/test_roster.py` ×14, a file that did not exist before — the roster import had **no tests at all** |

Also fixed: **the Dockerfile could not build.** Its `collectstatic` step set
`SECRET_KEY`, but `prod.py` requires `DJANGO_SECRET_KEY`, so the image build
stopped with `ImproperlyConfigured`. And there was **no `.dockerignore`**, so the
BuildKit context sender aborted on this volume's AppleDouble files before the
first instruction.

### A defect found by the new tests before it shipped

The first implementation of the room audience put the check inside
`eligibility.assert_may_use_room`, which the **walk-in** path also calls. That
silently refused walk-ins for every non-piano, non-percussion student — the exact
opposite of the agreed rule, and it would have made the new room dead capacity
whenever those students were not using it. `tests/test_room_audience.py` failed on
its first run (`room_not_for_instrument` where `reservation_held` or success was
expected). The fix was to give reserving its own function,
`eligibility.assert_may_reserve_room`, so the distinction is visible at every call
site rather than hidden in a flag (D-25).

Worth recording because it is the same class of failure as the ten defects above:
the behaviour looked right in the code and was wrong in the product.

**Test-infrastructure defects found and fixed** (they hid other problems rather
than being product bugs): the thread-local `frozen_clock` never reached the
request-serving thread, which made `freeze_from_environment()` — documented for
exactly this purpose — inert for `runserver` and for `live_server` (D-09); and
`DJANGO_SETTINGS_MODULE` exported in the environment silently replaced the test
settings, turning a green suite into dozens of unrelated failures. The latter is
now impossible to do quietly: `tests/test_environment_guard.py` fails by name.

## 5. What was actually run, and what that proves

**Proven locally**: the booking, quota, adjacency, lifecycle, check-in, walk-in,
no-show, strike, suspension, appeal, closure, incident, identity, permission,
idempotency, outbox and scheduler rules, on PostgreSQL, including concurrency with
independent connections. Every student and staff route responds, in Thai and
English, at 360 px and desktop, with and without JavaScript. Registration through
to a door check-in works in a real browser. One poster per room is generated and
each carries its own room's target. A backup restores into a clean database with
users, bookings and pending mail intact. The image builds.

**The instrument-specific room** (the tenth, added after the specification was
written) is covered from both ends: only piano and percussion students may reserve
it and nobody else may — asserted at the service layer and in Chromium — while any
eligible student may walk in once the hour is free and running, which is what keeps
it from being dead capacity. A booking made before the restriction survives it.
`tests/test_instruments.py` pins the canonical mapping against **every one of the
22 real spellings** in the department's roster, so no student is silently excluded
by a spelling nobody anticipated.

**Not proven, and not claimed**: nothing at all about deployment, a real mail
provider, a real domain, backup storage, alerting, deployment performance, or
physical posters on doors. Nothing about a real student having used it.

## 6. Environment notes that affect reproduction

- The project is on **exFAT**. There is no executable bit, so the runner is
  `bash scripts/verify`. `compilemessages` cannot walk the locale directory (it
  chokes on `._django.po`); use `msgfmt` directly, which is what was done.
- The Docker image build needs `DOCKER_BUILDKIT=0` here: BuildKit's context sender
  cannot read AppleDouble extended attributes and aborts. `scripts/verify` tries
  BuildKit and falls back automatically.
- SMTP port 1025 was already held on this machine, so `docker compose up mailpit`
  cannot start and the runner uses its own catcher on 11025/18025. The full
  development stack (`docker compose up`) was **not** exercised for that reason.
- Docker must be running before any test run; the suite is PostgreSQL-only by
  design, so SQLite is never a fallback.

## 7. Blocked — the minimum missing input

| Gate | Status | Missing input |
|---|---|---|
| `DEPLOYMENT_VERIFIED` | **BLOCKED** | A host with a domain and TLS; real SMTP credentials and a sender domain; a NAS or object-store backup destination; external uptime/disk/scheduler alerting with named owners. |
| `PILOT_ACCEPTED` | **BLOCKED** | Faculty endorsement of the data-handling basis and retention period; about ten approved students for two weeks; actual staff task completion; incident review; physical poster check; faculty sign-off. |
| A27 rollback rehearsal | **NOT RUN** | A pre-production environment with a previous image to roll back to. |
| A27 CI image push | **BLOCKED** | Registry credentials and a CI runner. |
| A30 performance (p95) | **NOT RUN** | The intended deployment hardware. A local M4 Pro number would not be evidence about the VPS either way. |
| A29 physical poster check | **NOT RUN** | Printing, and the correct doors. No QR decoder is installed; adding one only for a test would change the deployment surface. |

## 8. File hashes for the verified revision

The working tree is dirty, so these identify the verified code. Every file the
acceptance work touched, plus the runner:

| File | md5 |
|---|---|
| `scripts/verify` | `e3afc97eca7f03e2f98ab92dc5a92a1e` |
| `core/services/clock.py` | `a12c9e9f21f99a939626c1fccfb189c7` |
| `core/services/identity.py` | `fbe4efdb423fe6748337e32726dc84cf` |
| `core/services/protocol.py` | `729ef62a69f75cbf94d1c4a2a05ee03f` |
| `core/views/identity.py` | `507e94c7c8851e102fce5195ff71e10b` |
| `core/views/staff.py` | `19a91dee1c38fb2c40943424307ceaaf` |
| `core/views/booking.py` | `c663e4da9124a6c47289af715c0a5674` |
| `core/models/policy.py` | `6fcd75d1527343857b55ea734c0746fc` |
| `core/middleware.py` | `9d68567bda1d92c0078704f290dc1710` |
| `core/templates/core/base.html` | `3accf3a96f156d82201b7bcd6bcca0c4` |
| `core/templates/core/partials/grid_table.html` | `9c517be258d0503d532c7031ea8d4ff8` |
| `core/templates/core/use_now_confirm.html` | `f6c8399287e73032eb28c6cc3f4b04e4` |
| `core/templates/core/staff/users.html` | `585f22054ca662ffd31c76a53b8230db` |
| `locale/th/LC_MESSAGES/django.po` | `67170f72d09837e66bb4a84743231121` |
| `locale/th/LC_MESSAGES/django.mo` | `01b82350415cdda080c0ec461c4013c4` |
| `Dockerfile` | `cb3b33d5bd28ddd499bb14509d1bb074` |
| `.dockerignore` | `4d52aead3950b724a11f0b2022fca094` |
| `tests/browserlib.py` | `90980a641af7c5b3ad7c6bf52b99a2a6` |
| `core/services/roster.py` | `e6fc67867a4247b2f02433f36331a9cb` |
| `core/views/staff.py` | `0ed847826347c46b31c0132e24b93316` |
| `core/management/commands/import_roster.py` | `f415d2f75c632168b97bc8a526ec0637` |
| `core/templates/core/staff/roster.html` | `ad2ad5a5e913ac6d8111fad3c992c240` |
| `tests/test_roster.py` | `c51610e860de6380d291a26948f33ee1` |
| `tests/test_browser_staff.py` | `694b6ae4e12412698265d06f5ecf9d7e` |
| `core/services/instruments.py` | `b764570498c6bbcf236eb4aaa56fa150` |
| `core/services/rooms.py` | `b5b78df9d1e1ec5fd5cbc49ae106bfaf` |
| `core/services/eligibility.py` | `42fe45f7d5bdfa51966c81c73d8f0625` |
| `core/models/room.py` | `c6cce52b53e0699657d859b30a2cfcc6` |
| `core/models/identity.py` | `986b1674d879450dd9bbbe554ddba465` |
| `core/migrations/0004_room_reservation_scope_and_instrument_category.py` | `83b80b7bd3a98fdaa6f285175eea3498` |
| `core/migrations/0005_backfill_instrument_category.py` | `9db4bf7f8285a1e0426fec67ce5918ab` |
| `core/templates/core/partials/slot_cell.html` | `b6cb1e76513feaa665dcdb6f31ec7628` |
| `core/templates/core/staff/admin.html` | `7787f8a36085ded2d1ff79a2f09006ed` |
| `tests/test_instruments.py` | `be34c0987b380e3b404830fb54f92698` |
| `tests/test_room_audience.py` | `12c7249002e1a9f23814fce2ec9f666f` |
| `core/static/css/tailwind.css` | `69a6ebd698252cded5d2ed2cc661c0b6` |

`git diff` against `21d821d` is the authoritative change set; these hashes are for
spot-checking that a re-run used the same files.

## 9. Known limitations to carry forward

1. **No committed revision.** 65 modified and 21 untracked files sit uncommitted on
   top of `21d821d`. This is the single biggest process risk in the project: a
   verified tree that no hash identifies.
2. **Thai copy is unreviewed.** 554 catalogue entries were translated in bulk. Eight
   were added or corrected in this pass (four of them were `fuzzy`, meaning gettext
   ignored them and Thai users silently saw English). No Thai-speaking member of
   the department has read any of it. The catalogue currently has **zero** fuzzy
   and **zero** untranslated entries.
3. **The privacy notice is a draft** and needs faculty approval (§7).
4. **The instrument-specific room depends on the roster link.** Only 9 of the 74
   real students can reserve it, because 57 roster rows hold personal email
   addresses and a row is linked to an account only when the ID *and* email match.
   The rule is correct and tested; the data is not ready for it. Fixing the roster
   email column is now a prerequisite for this feature, not tidiness.
5. **An uncategorised instrument fails closed.** A piano or percussion student whose
   spelling is not in the alias table cannot reserve the room until somebody
   categorises it. The import reports those spellings and the staff roster screen
   flags them in amber, so the remedy is visible — but it is a manual step.
6. **Eleven rooms is a spec deviation.** V3 says nine, in five places, and A29 says
   nine posters. Recorded in `docs/acceptance-matrix.md` under "Deviations from V3";
   the specification text still needs the owner's amendment.
7. **Resolved (19 Sep 2026): the room's name.** The placeholder `ห้องซ้อมใหญ่` is
   retired (D-28): the restricted room is the real **room 303**, and **room 304**
   (`ห้องบรรยาย 1`) joined as a general room blocked during its class hours. Room
   304's blocked hours were derived from the teaching sheet's column geometry and
   still need the department's confirmation.
8. **Retention is unendorsed and unimplemented.** Nothing deletes automatically. The
   60-day history is a display limit, not a deletion policy.
9. **No per-student data export** exists, though the specification describes one.
10. **A QR code is not proof of presence.** The UI and the runbook both say so.
11. **Mail is at least once.** A provider that accepts a message and loses the worker
    before recording it will see it again. Booking outcomes never depend on mail.
12. **The adjacency rule is per-student, not per-room-pair.** Two rooms in the same
    hour are refused; a future policy might want geometry instead.
13. **A30's performance target is unmeasured**, and the specification says a local
    measurement would only be evidence for local hardware anyway.
14. **`scripts/verify` is not wired into CI.** It exists and runs locally; nothing
    runs it automatically, so a regression is only caught when someone runs it.

## 10. Honest summary

The application works end to end locally, and the evidence for that is a real
browser, a real database and a real SMTP conversation, not a checklist. Ten defects
were fixed in the process, one of which (registration) made the product unusable
for every student, and one of which (the appeal control) made a promise the product
could not keep. An eleventh was caught by the new tests before it left the working
tree.

`LOCAL_PASS` — the local gate in V3 §13 — is met as far as automated checks go.
Everything beyond it needs inputs that do not exist yet, and those are listed
rather than assumed. The instrument-specific room is built, tested and verified; it
is not yet *usable* by most of the students it is for, and that is a data problem in
the roster, not a code problem.
