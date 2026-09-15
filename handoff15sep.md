# Handoff — Room Reserve V3 · 15 September 2026

**Project:** `/Volumes/Crucial2TB/All Codes/FAA/Room problem`
**Spec:** `roomreserveapp.v3.md` (sole product specification; `roomreserveapp.md` and
`roomreserveapp.v1.2.md` are historical references and must not override V3)
**Execution contract:** `deepseek-v3-build-loop.md`
**Working state:** 213 automated tests pass, linter and formatter clean, Thai catalogue compiled.
**Honest status:** `LOCAL_PASS` is **not** yet claimable. Domain, policy, lifecycle, concurrency,
identity, outbox and scheduler behaviour are implemented and evidenced by PostgreSQL tests.
Browser-level acceptance (A23–A26, A29), the verification runner and clean-bootstrap evidence
(A01), packaging/recovery (A27), the `docs/` set and `QA_REPORT.md` are **not done**.

---

## 1. Where everything lives

| Item | Path |
|---|---|
| Project root | `/Volumes/Crucial2TB/All Codes/FAA/Room problem` (exFAT volume) |
| Python venv | `~/.virtualenvs/roomreserve` (APFS — exFAT has no permission bits) |
| Database | PostgreSQL 16.4 in Docker Compose, named volume `roomreserve_pgdata` |
| Local env file | `.env` (gitignored, development values only) |
| Acceptance list | `roomreserveapp.v3.md` §12 (`A01`–`A30`) |
| Build status | `BUILD_STATUS.md` (may now be stale relative to this file) |

Docker Compose publishes PostgreSQL on `127.0.0.1:5432` and the dev server on
`127.0.0.1:8000`; `compose.override.yaml` merges automatically.

---

## 2. Application structure

The Django project is split into a thin project package and one fat app.

```
roomreserve/                  project package
├── settings/
│   ├── base.py               shared settings; env-driven, no secrets by default
│   ├── dev.py                local development (test clock allowed)
│   ├── test.py               PostgreSQL test DB, locmem mail/cache, plain static storage
│   └── prod.py               fails at startup on missing mandatory secrets
├── env.py                    env_str / env_int / env_bool / env_list readers
└── urls.py                   /healthz/, /readyz/, i18n switcher, /maintainer/ admin,
                              everything else under /th/ and /en/

core/
├── models/
│   ├── user.py               User (institutional ID lives in `username`), roles, eligibility
│   ├── identity.py           EligibleStudent roster, Invitation (digest only)
│   ├── room.py               Room, Closure, CalendarOverride
│   ├── booking.py            BookingControl singleton lock, Booking + all constraints
│   ├── sanctions.py          Violation (strike), Suspension + query helpers
│   ├── policy.py             PolicyVersion (immutable), ServiceIncident
│   └── operations.py         AuditEvent (append-only), Notification (outbox),
│                             OperationRequest (idempotency), JobHeartbeat
├── services/                 all business rules; no rule lives in a view
│   ├── protocol.py           the shared transaction protocol (lock → reconcile → idempotency
│   │                         → validate → savepoint → audit/outbox)
│   ├── booking.py            advance reservations, horizon, stale-hour handling
│   ├── walkin.py             use-now, original slot end, short-session warning
│   ├── checkin.py            check-in window, wrong-room/other-owner rules
│   ├── cancel.py             cancellation, quota restoration, late-cancel reporting
│   ├── reconcile.py          no-show, completion, reminders, suspension triggers
│   ├── sanctions.py          strike recording/voiding, strike summary, appeals
│   ├── eligibility.py        account-state and suspension gates
│   ├── quota.py              daily quota, adjacency, no-show reclaim
│   ├── availability.py       read-only slot state derivation and the public grid
│   ├── calendar.py           closure > override > weekly timetable precedence
│   ├── slots.py              fixed hourly geometry, slot encoding for URLs
│   ├── clock.py              authoritative time, injectable test clock
│   ├── outbox.py             enqueue, lease, backoff, exhaustion, cancel
│   ├── notifications.py      render + revalidate before send, drain loop
│   ├── identity.py           registration, verification, invitations, reset, approval
│   ├── incidents.py          service incidents: cancel without penalty, void strikes
│   ├── maintenance.py        closures, overrides, deactivation, policy versions
│   ├── roster.py             CSV roster import (all-or-nothing)
│   ├── csvio.py              spreadsheet-safe CSV read/write
│   ├── ratelimit.py          per-identifier + per-address limits
│   └── refs.py               re-read rows under a lock before mutating
├── views/                    thin HTTP layer
│   ├── public.py             public grid + HTMX fragment + QR landing
│   ├── booking.py            reserve, use now, check in, cancel, my bookings
│   ├── identity.py           register, verify, invite, login, password reset
│   ├── staff.py              the whole staff surface (~1030 lines)
│   ├── content.py            rules, help, privacy
│   ├── health.py             liveness (no DB) and readiness (DB only)
│   └── _helpers.py           staff_required, safe_next_url, run_view_operation
├── templates/core/           base + student pages + staff/ + email/*.txt
├── static/css, static/js     built Tailwind CSS, vendored htmx (no CDN)
└── management/commands/      seed_rooms, seed_demo, import_roster, invite_staff,
                              create_maintainer, generate_posters, tick

tests/                        213 tests, PostgreSQL only
locale/th/LC_MESSAGES/        Thai catalogue (554 strings) + compiled django.mo
deploy/Caddyfile              TLS termination and reverse proxy
requirements/{base,dev,prod}.txt, Dockerfile, compose.yaml, gunicorn.conf.py
```

### Design decisions already recorded (also in `BUILD_STATUS.md`)

- **D-01** exFAT root: venv on APFS, PostgreSQL in a named Docker volume, never a bind mount.
- **D-02** `username` holds the institutional ID; `User.institutional_id` is a property, not a
  second column.
- **D-03** `slot_date` is a denormalised Bangkok date, derived in `Booking.save()`.
- **D-04** `BLOCKING_STATUSES` / `QUOTA_STATUSES` are module constants shared by services and
  constraints.
- **D-05** an ambient `NODE_ENV=production` in the shell makes npm skip devDependencies; use
  `npm install --include=dev`. The Dockerfile does the same.
- **D-06** (new) Test settings use plain static storage. The production manifest storage answers
  only after `collectstatic`, so without this every view test failed with
  `Missing staticfiles manifest entry for 'css/tailwind.css'`.
- **D-07** (new) `outbox.enqueue()` returns `(notification, created)`. The reminder pass needs to
  know whether it actually queued new work.
- **D-08** (new) The scheduler drains the outbox against the current wall clock, not the moment
  captured before reconciliation, so a tick delivers the mail it just queued instead of waiting
  for the next minute.

---

## 3. What is done

### 3.1 Implemented scope

Every in-scope student and staff route from V3 §9 exists and is wired through a service under the
shared lock. Not placeholders: reservation, use-now, check-in, cancellation, history, quota,
adjacency, closures, date overrides, incidents, strikes/suspensions, appeals, approvals,
invitations, roster import/export, posters and QR targets, policy versions, audit trail, outbox
screen, statistics and CSV exports.

### 3.2 Test evidence — `213 passed` in ~9 s

| File | Tests | Acceptance IDs covered |
|---|---|---|
| `tests/test_booking.py` | 25 | A02, A03, A07, A08, A11, A14 |
| `tests/test_lifecycle.py` | 18 | A03–A06 |
| `tests/test_concurrency.py` | 7 | A09, A10, A12, A13, A18, A30 |
| `tests/test_sanctions.py` | 10 | A15–A17 |
| `tests/test_calendar.py` | 18 | A19 |
| `tests/test_identity.py` | 27 | A20 |
| `tests/test_permissions.py` | 25 | A21 (+ CSV formula safety, rate limiting) |
| `tests/test_outbox.py` | 23 | A22 |
| `tests/test_scheduler.py` | 18 | A28 |

Concurrency tests use independent PostgreSQL connections with a `threading.Barrier`, not
sequential loops, and assert invariants rather than one interleaving. The A30 stress test runs
20 rounds with fresh fixtures.

### 3.3 Bilingual delivery

`locale/th/LC_MESSAGES/django.po` now carries **554 translated strings** (was an empty
catalogue, so Thai users previously saw English). Compiled to `django.mo`. Both `/th/` and `/en/`
route prefixes work; years are Gregorian (ค.ศ.).

### 3.4 Defects found and fixed in this session

1. **Password reset was completely broken.** `core/services/identity.py` used
   `KIND_PASSWORD_RESET` without importing it → `NameError` (HTTP 500) on every reset request.
   Fixed the import; regression test in `tests/test_identity.py`.
2. **The scheduler crashed whenever it queued a reminder.** `reconcile.enqueue_due_reminders()`
   unpacked `enqueue()` as `(notification, created)` but `enqueue()` returned a single object →
   `TypeError` inside the tick's transaction, which would also roll back that tick's
   reconciliation. Fixed by returning the tuple (D-07).
3. **A tick could never deliver its own mail.** It drained using a timestamp captured before
   reconciliation, so newly queued rows were always "not due yet". Fixed (D-08).
4. **No view-level test could render a page.** Test settings used the production manifest static
   storage without a `collectstatic` run. Fixed (D-06).
5. **`compilemessages` picked up macOS AppleDouble files** (`._django.po`) on this exFAT volume and
   aborted. `django.mo` is still produced; see §5 for the workaround.
6. **Config defect:** `pyproject.toml` per-file-ignores targeted `core/settings/*` while the
   settings package is `roomreserve/settings/*`, so the intended F403/F405 ignores never applied.
   Fixed, plus a whole-repo `ruff format` pass (whitespace only) so the formatter gate is honest.
7. **Earlier (pre-session):** check-in trusted the caller's in-memory booking, which could
   overwrite a committed NO_SHOW under a race. Fixed by re-reading under a lock (`refs.py`).

---

## 4. What remains

Ordered by what unblocks the most. Every item is required by `deepseek-v3-build-loop.md`.

### 4.1 Browser acceptance — A23, A24, A25, A26, A29 (highest priority, phase P3 gate)

Playwright **is installed** in the venv and **Chromium is downloaded**. No browser tests are
written yet. `tests/` has no `browser`-marked file, and `pyproject.toml` already registers the
`browser` marker. `pytest-django` 4.14.0 provides a `live_server` fixture.

Flows to drive against a real browser, per V3:

- **A23** register → verify → approve → login → reserve → cancel → reserve → door check-in →
  history, with fixture dates that respect the quota.
- **A24** current-slot walk-in after :15; wrong-room / too-late / full / suspended / closed
  feedback; staff closure and appeal workflows.
- **A25** Thai **and** English routes and email copy, Gregorian years, 360 px mobile and desktop
  layouts, keyboard access, JS-disabled forms, stale refresh.
- **A26** public HTML/JSON/log inspection finds no student identities or secrets; personal
  responses not publicly cached; CSV formula payload safety (the last part is already unit-tested).
- **A29** nine posters render legibly and QR URLs resolve to the correct rooms. A screenshot does
  not establish a flow works — inspect visible state, console errors and persisted rows.

Useful fixtures already available: `factories.py`, `helpers.py`, `tests/conftest.py`
(`frozen`, `rooms`, `student`, `staff_user`, `run_concurrently`).

### 4.2 `scripts/verify` — A01 and the P4 gate

Does not exist yet (`scripts/` is empty). It must run all automated local checks, fail non-zero on
failure, and record per-check status — no `|| true`, no empty test selection. Minimum content:

1. dependency install / pinned-version check; `ruff check` + `ruff format --check`
2. `manage.py check`; `makemigrations --check --dry-run` (migration drift)
3. full pytest run against PostgreSQL
4. the browser suite
5. Docker image build and restart-persistence check
6. backup → restore into an isolated fresh database, then functional checks

A skipped or missing required check must report **BLOCKED/NOT RUN** and prevent `LOCAL_PASS`.

### 4.3 Clean bootstrap and packaging — A01, A27

- Documented fresh bootstrap on an isolated new PostgreSQL database/Compose project. **Never drop
  the existing `roomreserve_pgdata` volume to prove a clean boot.**
- `collectstatic` (STATIC_ROOT `staticfiles/` does not exist yet — tests warn about this).
- Immutable image build; `manage.py check --deploy` on `prod.py`; dependency/security review.
- Nightly `pg_dump` to NAS + off-site object store; **restore drill into a fresh database**.
- Migration/image rollback rehearsal with documented schema compatibility.
- CI (GitHub Actions) building and pushing the image; VPS pulls by tag.

### 4.4 `docs/` — empty, all of it required

`docs/acceptance-matrix.md` (map every A01–A30 to a real test location or an explicit manual/
deployment check — a checkbox is not a test), plus `decisions.md`, `runbook.md` (restart, restore,
rotate secrets, add staff), `policy.md`, `architecture.md`, `privacy.md`, `README.md`.

### 4.5 `QA_REPORT.md`

Not written. Needs requirement IDs, exact commands, timestamps, exit codes, pass/fail/blocked,
environment, evidence paths, known limitations and the code revision. Must mark local vs
deployment vs pilot **separately**, and label deployment/pilot `BLOCKED` for missing real
SMTP/domain/NAS/pilot inputs rather than inventing a pass.

### 4.6 A30 reporting gap

The 20-round stress test exists and passes, but A30 also requires reporting **p95 and hardware**.
The performance target (p95 mutation response under 2 s with 20 concurrent attempts) is not
measured yet.

### 4.7 Adversarial review pass

Not yet performed end to end against A01–A30. The build loop lists specific things to try to
break: exact :00/:15/:60 boundaries, late/short walk-ins, completed/no-show quota, cross-room
same-hour and adjacency, stale unique rows, rejected-request reconciliation rollback, scheduler
outage and third strike, repeated/expired/voided sanctions, simultaneous POSTs, closure/
deactivation conflicts, invitation replay, staff privilege escalation, privacy leaks, outbox
retries and restore of stale messages.

---

## 5. Environment gotchas (learned the hard way)

- **exFAT root.** No symlinks, no POSIX permission bits. Hence the venv on APFS and the
  PostgreSQL named volume. `find`/`ls` output is littered with AppleDouble `._*` files.
- **`compilemessages` breaks on this volume** because it walks the locale directory and chokes on
  `._django.po`. `django.mo` is still produced. To compile cleanly, run `msgfmt` directly:
  `msgfmt -o locale/th/LC_MESSAGES/django.mo locale/th/LC_MESSAGES/django.po`, or delete stale
  `._*.po` files first.
- **`pip` is not installed in the venv.** Use `python -m …` tools directly; `importlib.metadata`
  for versions.
- **Docker must be running** before any test run: `open -a Docker`, then
  `docker compose up -d db`.
- **`makemigrations --check` warns** that the test database does not exist when run outside
  pytest. It still reports drift correctly.
- **The frozen test clock and the outbox disagree by design.** `enqueue` stamps rows with
  `timezone.now()`; `clock.now()` follows the freeze. Tests that drain must pass
  `timezone.now()` explicitly — this cost three confusing failures.
- **`sanctions_under_review()` reads the real clock.** Do not mix it with a frozen fixture.
- **Static storage in tests** is plain (D-06). Do not "fix" the `No directory at: .../staticfiles/`
  warning by running `collectstatic` inside tests.
- **`npm` and the ambient `NODE_ENV=production`** (D-05): always `--include=dev`.

---

## 6. Acceptance status at a glance

| IDs | Status | Evidence or gap |
|---|---|---|
| A02–A18 | **Tested** | `test_booking.py`, `test_lifecycle.py`, `test_concurrency.py`, `test_sanctions.py`, `test_calendar.py` |
| A19–A22, A28 | **Tested** | `test_calendar.py`, `test_identity.py`, `test_permissions.py`, `test_outbox.py`, `test_scheduler.py` |
| A23–A26, A29 | **NOT RUN** | Playwright installed, no browser tests written |
| A01, A27 | **NOT RUN** | no `scripts/verify`, no clean-bootstrap evidence, no backup/restore drill |
| A30 | **Partial** | 20-round stress passes; p95 and hardware not measured |
| Docs, QA_REPORT | **NOT RUN** | `docs/` empty; no `QA_REPORT.md` |
| Deployment, pilot | **BLOCKED** | real SMTP, domain, NAS and pilot inputs absent |

---

## 7. Commands to resume

```bash
cd "/Volumes/Crucial2TB/All Codes/FAA/Room problem"

# 1. Infrastructure
open -a Docker
docker compose up -d db

# 2. Tests (213 passing)
~/.virtualenvs/roomreserve/bin/python -m pytest -q
~/.virtualenvs/roomreserve/bin/python -m pytest -q -m "not slow"   # skip the 20-round stress

# 3. Lint and format
~/.virtualenvs/roomreserve/bin/python -m ruff check .
~/.virtualenvs/roomreserve/bin/python -m ruff format --check .

# 4. Django checks
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev ALLOW_TEST_CLOCK=true \
  ~/.virtualenvs/roomreserve/bin/python manage.py check
DJANGO_SETTINGS_MODULE=roomreserve.settings.test \
  ~/.virtualenvs/roomreserve/bin/python manage.py makemigrations --check --dry-run

# 5. Run the app locally (dev server on the host, DB in Docker)
~/.virtualenvs/roomreserve/bin/python manage.py migrate
~/.virtualenvs/roomreserve/bin/python manage.py seed_rooms
~/.virtualenvs/roomreserve/bin/python manage.py seed_demo
~/.virtualenvs/roomreserve/bin/python manage.py runserver 127.0.0.1:8000
# then open http://127.0.0.1:8000/th/

# 6. Rebuild the CSS after editing templates
npm run build:css

# 7. Rebuild the Thai catalogue after adding strings
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev \
  ~/.virtualenvs/roomreserve/bin/python manage.py makemessages -l th
# translate the new msgids, then compile with msgfmt directly (see §5)
```

---

## 8. Known limitations to carry into the final report

- No browser evidence yet; every UI claim in this document rests on template inspection and
  HTTP-level tests, not on a driven browser.
- Translation quality: 554 strings were translated in bulk during this session and have not been
  reviewed by a Thai-speaking member of the department. The privacy notice is explicitly a draft
  needing faculty approval before real use.
- The static QR code records an account action only — it cannot prove physical presence. This is
  stated in the UI and must stay in the runbook.
- Mail delivery is **at least once**; a duplicate is possible if the provider accepts a message and
  the worker dies before recording it. Booking outcomes never depend on mail.
- PDPA: consent capture, retention automation and the data-export/delete path are described in the
  privacy page but the retention period is an unendorsed default and nothing is deleted
  automatically.
