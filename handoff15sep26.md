# Handoff — Room Reserve V3 · 15 September 2026 (checkpoint 2)

**Project:** `/Volumes/Crucial2TB/All Codes/FAA/Room problem`
**Spec:** `roomreserveapp.v3.md` (sole product specification)
**Execution contract:** `deepseek-v3-build-loop.md`
**Previous checkpoint:** `handoff15sep.md` (superseded by this file)
**Evidence:** `QA_REPORT.md`, `docs/acceptance-matrix.md`, `artifacts/qa/`

## Honest status

**`LOCAL_PASS_CANDIDATE`.** `bash scripts/verify` passes 12/12 checks with nothing
blocked; 359 tests pass on PostgreSQL, 28 of them driving Chromium. The application
works end to end locally — registration through to a door check-in, in a real
browser, against a real database, with real mail.

**Not** `DEPLOYMENT_VERIFIED` or `PILOT_ACCEPTED`: nothing has ever run on a host,
and no real student has used it. Both need inputs that do not exist yet (§7).

**Two things block a decision, not the code** — see §6. Read that section first.

---

## 1. Where everything lives

| Item | Path |
|---|---|
| Project root | `/Volumes/Crucial2TB/All Codes/FAA/Room problem` (exFAT volume) |
| Python venv | `~/.virtualenvs/roomreserve` (APFS — exFAT has no permission bits) |
| Database | PostgreSQL 16.4 in Docker Compose, named volume `roomreserve_pgdata` |
| Local env file | `.env` (gitignored, development values only — not loaded by `manage.py`) |
| Acceptance list | `roomreserveapp.v3.md` §12 (`A01`–`A30`) |
| Verification runner | `scripts/verify` — twelve checks, per-check PASS/FAIL/BLOCKED |
| Evidence | `artifacts/qa/verify-<UTC stamp>/` (gitignored) |
| Documentation | `docs/` — acceptance matrix, decisions, runbook, policy, architecture, privacy, README |

Doctrine, if you read nothing else: `docs/architecture.md` (how it is put together),
`docs/policy.md` (the rules), `docs/decisions.md` (D-01 … D-27, why each choice),
`docs/runbook.md` (operations *and* the traps that already caught us).

## 2. What changed since `handoff15sep.md`

That checkpoint said domain logic was done and browser acceptance was not. Since then:

- **Browser acceptance A23–A26, A29** — 28 real-browser tests; the P3 gate is met.
- **`scripts/verify`** — the twelve-check local gate, with the rule that a *blocked*
  check also fails the run.
- **The department's real roster** imported: 74 students, replacing 100 synthetic rows.
- **A tenth room** reserved for piano and percussion students, with a canonical
  instrument category underneath it.
- **The roster email correction path**, without which the corrected sheet would have
  been rejected.
- **`docs/` and `QA_REPORT.md`** written; `BUILD_STATUS.md` rewritten.
- **Eleven defects fixed** (§4), one of which made registration impossible.

## 3. State of the code

```
a4e562b  Make a wrong roster address correctable                     (14 files)
05f73ca  Browser acceptance, verification runner, roster import,
         instrument-specific room                                    (92 files)
21d821d  Room Reserve V3: domain models, services, views, ...        (previous session)
```

**Working tree: one modified file, deliberately left alone** — the department's
`รายชื่อนิสิตป.ตรี ทั้ง 4 ปี.xlsx`. See §6.1; it is a privacy matter, not a code one.

Everything else is committed. `QA_REPORT.md` §8 has md5s for the files that matter.

### Test suite — 359 tests, PostgreSQL only, 28 in Chromium

| File | Tests | File | Tests |
|---|---|---|---|
| `test_permissions.py` | 59 | `test_clock.py` | 10 |
| `test_instruments.py` | 37 | `test_sanctions.py` | 10 |
| `test_roster.py` | 33 | `test_content_pages.py` | 8 |
| `test_identity.py` | 33 | `test_concurrency.py` | 7 |
| `test_booking.py` | 27 | `test_browser_bilingual.py` | 6 |
| `test_calendar.py` | 24 | `test_browser_staff.py` | 4 |
| `test_outbox.py` | 23 | `test_browser_privacy_posters.py` | 4 |
| `test_room_audience.py` | 20 | `test_environment_guard.py` | 4 |
| `test_scheduler.py` | 18 | `test_browser_student.py` | 3 |
| `test_lifecycle.py` | 18 | | |
| `test_browser_walkin.py` | 11 | | |

`tests/browserlib.py` holds the browser machinery (`watched` page, grid locators,
`live_server` helpers); fixtures `browser_clock` and `watched` are in `conftest.py`.

### Last verified run

`bash scripts/verify` → exit 0, `PASS=12 FAIL=0 BLOCKED=0`, evidence in
`artifacts/qa/verify-20260915T161035Z/`. The bootstrap reported
`10 rooms, 110 accounts, 1 bookings, 1 queued messages, 7 routes 200`.

## 4. Defects found and fixed — eleven

Found by driving the running application, not by reading it. The pattern is the
lesson: **the code looked right and the product was wrong.**

| What | Why it mattered |
|---|---|
| **Registration could never be completed** | `POST /verify/<token>/` consumed the single-use link and then rejected it. Every signup ended on HTTP 410 with no usable password. Two services, one token, each consuming it. |
| **Staff could not void a strike** | The view and service existed; no template reached them. The student page tells them to contact the office "so it can be reviewed" — the office had no way to act. |
| **The poster sheet could not render** | `{% static %}` without `{% load static %}`: `TemplateSyntaxError`. |
| **Rules and Help showed a blank check-in window** | `policy.grace_minutes` does not exist — on the two pages that teach the rule: "closes exactly  minutes later". |
| **The grid stated the opening hours as ":00–:00"** | `PolicyVersion` exposed only the editable fields. |
| **`<html lang>` was always `th`** | No i18n context processor, so every `/en/` page claimed Thai and the language switcher always highlighted Thai. |
| **The grid refresh nested itself** | `hx-swap="innerHTML"` on the region itself added a second `#availability-region` per refresh, and `htmx:afterOnLoad` re-fired on its own response — an unbounded request loop. |
| **Use-now offered a button that could only fail** | For a taken room or a suspended account. |
| **A weak password dead-ended reset and invitation** | Rendered "this link cannot be used" for a link that was fine. |
| **The roster dry run said "0 would be created"** | It returned before counting, and skipped conflict detection — useless as the preflight for a bulk write of real data. |
| **The Dockerfile could not build** | `SECRET_KEY` vs `DJANGO_SECRET_KEY`; plus no `.dockerignore`, so BuildKit aborted on this volume's AppleDouble files. |

Plus one caught by new tests **before** it left the working tree: the room audience
was first enforced inside `assert_may_use_room`, which the **walk-in** path also
calls, so it refused walk-ins for everyone outside piano and percussion — the exact
opposite of the agreed rule (D-25). And two test-infrastructure faults that hid
other problems: the thread-local test clock never reached the request thread, and an
exported `DJANGO_SETTINGS_MODULE` silently replaced the test settings.

## 5. Acceptance status at a glance

| IDs | Status |
|---|---|
| A01 | **Passed** — clean bootstrap on a database the runner creates, plus a real SMTP mail flow |
| A02–A18 | **Tested** — domain, policy, lifecycle, concurrency (independent connections + barriers) |
| A19–A22, A28 | **Tested** — calendar, identity, permissions, outbox, scheduler |
| A23–A26, A29 | **Browser-verified** — 28 Chromium tests; A29's physical door check is still manual |
| A27 | **Partly** — image build, migration drift, collectstatic, restart persistence and backup→restore pass; `check --deploy`, rollback rehearsal and CI push need inputs |
| A30 | **Tested, performance unmeasured** — 20 rounds with fresh fixtures; p95 needs the deployment hardware |
| Deployment, pilot | **Blocked** — real host, domain, SMTP, backup destination, alerting; faculty sign-off and ~10 students for two weeks |

Full mapping, including what is deliberately manual: `docs/acceptance-matrix.md`.

## 6. Two things need a human decision

### 6.1 The department's spreadsheet is tracked in git — privacy

`รายชื่อนิสิตป.ตรี ทั้ง 4 ปี.xlsx` (74 real students' IDs, names and emails) was
committed in `55d7650` and **is in the repository history**. It currently also shows
as *modified* in the working tree; that change was deliberately not committed,
because an unexplained modification to a file of personal data is not something to
commit blind.

- If this repository is ever pushed to a shared remote, that is 74 real people's
  data leaving the building.
- **Recommended:** stop tracking it (`git rm --cached`, add to `.gitignore`, keep
  the file on disk) and treat the history as never-to-be-pushed. Removing it from
  history requires a rewrite, which is a decision for the owner, not a tidy-up.
- The *content* of the modification is unknown. If it is a newer roster, it exists
  nowhere else.

### 6.2 The tenth room's name

`ROOM_OVERRIDES` in `roomreserve/settings/base.py` calls it `ห้องซ้อมใหญ่` — my
placeholder. The name prints on the door poster and shows on the grid, so it should
be the department's actual name for the room. One-line change.

Also pending: V3 says "nine rooms" in five places and A29 says "nine posters". The
deviation is recorded in `docs/acceptance-matrix.md` rather than edited into the
specification, which is the owner's call.

## 7. What remains, in order

1. **Get the 57 corrected addresses from the department.** This is now a **data**
   task, not a code one — §8 explains the tooling. It is the prerequisite for both
   automatic registration and the instrument-specific room for 65 of the 74 students.
2. **Clear the demo logins.** 100 synthetic students and 10 synthetic staff are
   still active, verified and bookable, alongside the real roster.
3. **Wire `scripts/verify` into CI.** It exists and runs locally; nothing runs it
   automatically, so a regression is only caught when someone remembers.
4. **A27 leftovers** — `manage.py check --deploy` with production-shaped values,
   a migration/image rollback rehearsal, and a registry push.
5. **A30 performance** — p95 for 20 concurrent mutation attempts, on the intended
   hardware, with the hardware recorded.
6. **Print the posters** — now ten, including the restricted room — and check each
   QR against the physical door.
7. **Thai copy review** — 599 catalogue entries, all translated here, none read by a
   Thai speaker.
8. **Deployment, then the pilot.** Needs a host, a domain, TLS, real SMTP
   credentials and a sender domain, a NAS/object-store backup destination, external
   alerting with named owners, faculty endorsement of the data-handling basis and
   retention, and about ten students for two weeks.

## 8. How the two new mechanisms work (do not re-derive these)

### The instrument-specific room (room 10)

- **Only piano and percussion students may reserve it.** Everyone else sees the hour
  marked "Piano & percussion only" — the grid says *why*, because an unexplained gap
  reads as a fault.
- **Anyone may walk in** once the hour has started and the room is still free. That
  is the release valve that keeps it from being dead capacity.
- **Check-in is untouched**, so a booking made before a restriction survives it, and
  changing a room's audience never cancels a booking.
- Enforced in exactly one place: `eligibility.assert_may_reserve_room`, called only
  by `booking.validate_advance_target`. `walkin.use_now` uses `assert_may_use_room`
  (no audience check). Staff have no bypass; a rehearsal uses the walk-in rule.
- Configured by `ROOM_COUNT` / `ROOM_OVERRIDES`, or by staff at
  Configuration → Rooms → *Who may reserve* (audited as `room.audience_changed`).
  A room set to "listed" with nothing ticked lets **nobody** reserve it — fails closed.

### Instrument categories

- The roster's `instrument` column is free text: **22 spellings across 74 students**,
  including the same instrument twice (`กีตาร์`/`กีต้าร์คลาสสิก`, `เชลโล`/`เชลโล่`).
  Nothing matches on it.
- `EligibleStudent.instrument_category` holds the canonical family, derived by
  `core/services/instruments.py`; the raw text is kept as provenance.
- Anything unrecognised → `UNKNOWN`, which **fails closed** and is **reported**: the
  import lists it and the staff roster screen flags those rows in amber. Extend
  `CATEGORY_ALIASES` from what the tooling reports, not from guesses.
- `tests/test_instruments.py` pins all 22 real spellings and asserts the migration's
  frozen copy of the table has not drifted from the service.

### Roster addresses — the thing the import refuses to do

A student is approved automatically only when their institutional ID **and** email
match a roster row, and the row is linked to their account only at that moment. The
instrument lives on the row, so **a wrong roster address also means no instrument**.

The import will **not** change an address on an entry that already exists (D-27).
Two deliberate paths exist instead:

```bash
# One student: Staff -> Roster -> "Correct email".  Audited: roster.email_corrected.

# Many: dry-run first — it reports how many addresses it would rewrite.
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev \
  ~/.virtualenvs/roomreserve/bin/python manage.py import_roster corrected.csv \
  --dry-run --allow-email-change --batch "roster-emails-2569"
# then drop --dry-run. On the staff screen it is the "Also rewrite existing
# addresses in this file" checkbox, off by default.
```

Neither path takes an address owned by a different ID, edits a student's account,
or withdraws an approval. `docs/runbook.md` §7–§8 has the full procedure.

## 9. Environment gotchas (learned the hard way)

- **`DJANGO_SETTINGS_MODULE` in the environment overrides the test settings.**
  pytest-django resolves `--ds` → environment → `pyproject.toml`, so exporting it
  around a pytest run silently swaps in development settings: no isolation, no
  in-memory mail or cache, dozens of unrelated failures. `tests/test_environment_guard.py`
  now fails by name if this happens. **Never export it in a script that runs pytest.**
- **Docker must be running** before any test run: `open -a Docker`, then
  `docker compose up -d db`. The suite is PostgreSQL-only; SQLite is never a fallback.
- **The Docker image needs the legacy builder here**: BuildKit's context sender
  cannot read AppleDouble extended attributes on exFAT and aborts before the first
  instruction. `DOCKER_BUILDKIT=0 docker compose build web`. `.dockerignore` alone
  does not help. `scripts/verify` tries BuildKit and falls back automatically.
- **SMTP port 1025 is held on this machine** (a macOS Finder extension), so
  `docker compose up mailpit` fails. `scripts/verify` starts its own catcher on
  11025/18025 for that reason; the full dev stack is therefore *not* exercised.
- **`makemessages` produces `fuzzy` entries that gettext ignores.** A fuzzy
  translation silently falls back to English — several had *unrelated* Thai
  (`Percussion` → "เวอร์ชัน", `Save address` → "save who may reserve"). Always check
  after regenerating, and check by parsing the file: `msgattrib --no-fuzzy` *strips*
  flags rather than filtering, which makes it look like entries lost their text.
- **`compilemessages` cannot walk the locale directory** on exFAT (it chokes on
  `._django.po`). Use `msgfmt -o locale/th/LC_MESSAGES/django.mo ...`.
- **The Playwright sync API leaves an asyncio loop marked as running**, so Django's
  async-context guard rejects every later ORM call, including pytest's teardown
  flush. `conftest.py` sets `DJANGO_ALLOW_ASYNC_UNSAFE=1` **only** when the session
  collected a browser test.
- **The frozen test clock has two forms.** `frozen_clock` is thread-local (unit
  tests); `freeze_process_clock` is process-wide and is what browser tests need,
  because Django serves requests on other threads.
- **The frozen clock and the outbox disagree by design** — `enqueue()` stamps with
  `timezone.now()` while `clock.now()` follows the freeze. Tests that drain must pass
  `timezone.now()` explicitly. `sanctions_under_review()` reads the real clock.
- **`collectstatic` is required** for the production manifest storage; `staticfiles/`
  is `STATIC_ROOT`, gitignored, and regenerated at image build time.
- **`npm install --include=dev`** — an ambient `NODE_ENV=production` makes npm omit
  Tailwind and the CSS build silently produces nothing. After editing templates with
  new Tailwind classes, run `npm run build:css`.
- **`pip` is not installed in the venv.** Use `python -m …` and
  `importlib.metadata` for versions.
- **`raison d'être` of `scripts/verify`:** never add `|| true`, never deselect tests,
  never treat BLOCKED as PASS. A blocked check means `NOT_LOCAL_PASS`.

## 10. Commands to resume

```bash
cd "/Volumes/Crucial2TB/All Codes/FAA/Room problem"

# Infrastructure
open -a Docker && docker compose up -d db

# The gate (all twelve checks; exits non-zero on FAIL or BLOCKED)
bash scripts/verify                       # run as `bash`: exFAT has no exec bit

# Individual checks
~/.virtualenvs/roomreserve/bin/python -m pytest -q                  # 359, ~32 s
~/.virtualenvs/roomreserve/bin/python -m pytest -q -m "not browser" # fast, no Chromium
~/.virtualenvs/roomreserve/bin/python -m ruff check .
~/.virtualenvs/roomreserve/bin/python -m ruff format --check .

# The app
~/.virtualenvs/roomreserve/bin/python manage.py migrate
~/.virtualenvs/roomreserve/bin/python manage.py seed_rooms
~/.virtualenvs/roomreserve/bin/python manage.py seed_demo
~/.virtualenvs/roomreserve/bin/python manage.py runserver 127.0.0.1:8000
# then http://127.0.0.1:8000/th/   (seed_demo prints the synthetic credentials)

# Thai catalogue
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev \
  ~/.virtualenvs/roomreserve/bin/python manage.py makemessages -l th
# translate the new msgids, then:
msgfmt -o locale/th/LC_MESSAGES/django.mo locale/th/LC_MESSAGES/django.po

# CSS after template changes
npm run build:css
```

## 11. Known limitations to carry into the final report

1. **Nothing has run outside this machine.** No host, no domain, no real SMTP, no
   backup destination, no alerting, no deployment performance measurement.
2. **The roster data is not fit for automatic approval.** 17 of 74 rows carry an
   institutional address; 57 hold personal ones, one of them a typo (`gmail.con`).
   The tooling to fix it now exists; nobody has done it.
3. **Only 9 of 74 students can reserve the instrument-specific room** (6 piano,
   3 percussion) — the same root cause as (2). The rule is correct and tested; the
   data is not ready for it.
4. **100 synthetic students and 10 synthetic staff are still live logins.** Harmless
   locally, wrong for anything resembling a pilot.
5. **Thai copy is unreviewed** — 599 entries, all machine-translated here.
6. **The privacy notice is a draft** needing faculty approval; retention is
   unendorsed and unimplemented; nothing deletes automatically; there is no
   per-student data export, though the specification describes one.
7. **The department's spreadsheet is in git history** (§6.1) — decide before any push.
8. **A QR code is not proof of presence**, and the physical poster check has not been
   done. Mail is at least once; booking outcomes never depend on it.
9. **A30's performance target is unmeasured**, and the specification says a local
   measurement would only be evidence for local hardware.
10. **`scripts/verify` is not wired into CI.** A regression is caught only when
    somebody runs it.
11. **The verification is mine alone.** Eleven defects surfaced this session, two of
    them fatal, all invisible to a 213-test suite and to reading the code. Before
    real students' data depends on this, have somebody else try to break it — that is
    the cheapest risk reduction still available.
