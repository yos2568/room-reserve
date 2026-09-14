# Room Reserve App — Complete Project Plan
## ระบบจองห้องซ้อมดนตรี (Music Practice Room Booking System)

สาขาวิชาดุริยางคศิลป์ตะวันตก คณะศิลปกรรมศาสตร์ จุฬาลงกรณ์มหาวิทยาลัย
Western Music Department, Faculty of Fine and Applied Arts, Chulalongkorn University

Version 2.0 — Commercial-grade revision — September 2026
Status: **Planning complete, ready to build**
Supersedes v1.2 (kept as `roomreserveapp.v1.2.md`)

---

## 0. What changed from v1.2

| Area | v1.2 | v2.0 | Why |
|---|---|---|---|
| Lead time | ≥ 24 h ahead + same-day exception for released slots | **No minimum** — book any free slot from now up to 7 days ahead | Practice rooms are used ad hoc; the 24 h rule blocked same-day use and needed a fragile special case |
| Walk-in | Not supported | **Scan door QR on a free room → instant booking for the current hour** | Regulation ข้อ 2.2 says other users may "จองหรือเข้าใช้" a forfeited room |
| Strikes | Cumulative forever | **Rolling 30-day window**, staff-configurable; auto-suspension has a default duration | Students should not carry a strike from September into March |
| Onboarding | Seeded accounts with default passwords + `force_password_reset` | **Self-registration with `@student.chula.ac.th` verification (on by default)** + CSV import that sends set-password invite links | No shared passwords; roster xlsx lacks IDs/emails for years 2–4 today; staff can turn self-registration off later |
| Concurrency | Slot uniqueness only | Slot uniqueness **+ per-user row lock** for quota/adjacency + idempotent booking form | Quota and no-consecutive checks raced when one user double-submitted |
| Status timing | Cron every 5 min flips SCHEDULED→NO_SHOW | **Derived status** computed from timestamps; cron (every 1 min, advisory-locked) only persists and notifies | Grid and check-in page are correct the second the grace period ends |
| Email | Sent inline + `EmailLog` | **Transactional outbox** with retry and dedupe keys | No lost emails on SMTP hiccups; reminders idempotent by design |
| Data model | `CheckIn`, `RoomClosure`, `Room.is_closed` | Check-in fields on `Booking`; `RecurringClosure` + `Closure(room, date range)`; DB check constraints | Fewer tables, clearer semantics, dated audit trail for closures |
| Security | Django defaults | django-axes, rate limits, HSTS, secret hygiene, staff re-auth | Real faculty deployment |
| PDPA | Absent | Privacy notice, consent at first login, retention + anonymisation policy | Required under Thai PDPA for a system holding student data |
| Staff UX | Django admin only | Admin **+ `/staff/today/` operations dashboard** | Admin CRUD is not a daily-ops tool |
| Audit | Admin history | `django-simple-history` on core models | Captures cron and self-service changes, not only admin edits |
| Ops | Uptime Kuma, NAS backup | + `/healthz/`, JSON logs, Sentry, off-site backup, runbook, quarterly restore drill | Commercial baseline |
| Facts | ~100 students | **~85 students** across 4 years; roster includes instrument | From the actual roster file |

---

## Table of Contents

1. Project Overview
2. Regulation → Feature Mapping
3. Architecture & Technology Decisions
4. Data Model
5. Booking Rules Engine
6. Booking Lifecycle & State Machine
7. QR Check-in & Walk-in
8. Violations & Suspension Policy
9. Pages & User Flows
10. Notifications
11. Deployment & Operations
12. Seed Data & Onboarding
13. Build Phases & Acceptance Gates
14. QA & Test Plan
15. Cost Breakdown
16. Risks & Mitigations
17. Out of Scope (v1)
18. Outstanding Operational Items
19. Decision Log

---

## 1. Project Overview

| Item | Value |
|---|---|
| Purpose | Replace manual booking of practice rooms with a fair, self-service, rule-enforcing web app |
| Users | ~85 undergraduate music students (years 1–4) |
| Staff | **10 accounts** — พี่ดิว (Sitanun S., Sitanun.S@chula.ac.th, 02-218-4604) + 9 staff, identical permissions |
| Rooms | 9 identical practice rooms, each with upright piano |
| Hours | Monday–Friday, 08:00–20:00 (12 hourly slots). Closed Saturday & Sunday and on staff-entered holidays |
| Language | Bilingual: Thai default, English toggle |
| Dates | Western calendar (ค.ศ.) with Thai month names |
| Deployment | Real production use by the faculty — Hostinger VPS (Docker) |
| Auth | Student-ID/employee-ID + password; self-registration via Chula email; OAuth upgrade path preserved |
| Dashboard | Public read-only availability grid; booking, check-in and walk-in require login |
| Compliance | Thai PDPA: privacy notice, consent, retention policy |

## 2. Regulation → Feature Mapping

Every clause of the official regulation document maps to a system feature:

| Regulation clause | Rule | System implementation |
|---|---|---|
| ข้อ 1.1 | Max 1 hour per booking | Hourly slot model; grid only offers single-hour slots |
| ข้อ 1.2 | Max 2 bookings per person per day | Quota check inside a per-user locked transaction; walk-ins count |
| ข้อ 1.3 | Use at the booked time, finish on time | Derived status auto-completes at end; check-in page shows "next booking starts at …" |
| ข้อ 1.4 | No booking for others / no hoarding | Login required; booking bound to authenticated user; no-consecutive rule; no-show strikes |
| ข้อ 2.1 | Arrive within 15 minutes | QR check-in enforced during [start, start+15 min] |
| ข้อ 2.2 | No-show = forfeit, room becomes free, **others may book or use it** | Slot is free the instant grace ends (derived status); others can book it from the grid **or walk in by scanning the door QR** |
| ข้อ 2.3 | Cancel early if unable to attend | Self-service cancel up to start time; slot returns to pool instantly; late-cancel (< 1 h) tracked as a metric, not a violation |
| ข้อ 3 | Music practice only (incl. ensemble) | Stated in rules page; enforced by staff. Group booking is a v1.1 candidate (see §17) |
| ข้อ 4–5 | Care of rooms, no food | Rules page students accept at first login |
| ข้อ 6 | Respect others' rights | Audit trail; staff manual violations |
| ข้อ 7 | Warnings & suspension for repeat violations | Auto-strike per no-show; 3 strikes in a rolling 30 days → auto-suspend for a default period; staff may edit or pardon |
| Contact | พี่ดิว, 02-218-4604, Sitanun.S@chula.ac.th | Footer of every email + help page |

**Additional rules decided by the project owner (beyond the document):**

| Rule | Value |
|---|---|
| No consecutive hours | A user may not hold bookings in adjacent hour slots **across any rooms** |
| Booking window | From now (current hour bookable until its grace deadline) up to 7 days ahead |
| Walk-in | Scan QR on a free room during the current hour → booking created already checked-in |
| Strike window | Rolling 30 days (staff-configurable) |
| Check-in method | Printed static QR code on each room door |
| Room types | All 9 rooms identical — no room-preference logic |
| Scheduled classes | Rooms never blocked for classes — every hour bookable unless a closure exists |

## 3. Architecture & Technology Decisions

### Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Owner already fluent in Python |
| Framework | Django 5.x + django-htmx + Tailwind CSS | Built-in admin, auth, i18n, Thai locale; one server-rendered codebase |
| Database | PostgreSQL 16 | Partial unique index + row locks + advisory locks give database-level correctness |
| Server | Gunicorn + Caddy | Caddy: automatic HTTPS, HSTS, gzip |
| Jobs | cron container → one idempotent management command every minute | No Redis/Celery at this scale; advisory lock prevents overlap |
| Email | Hostinger SMTP via transactional outbox | Zero cost; retries handled by the app |
| Settings | `django-solo` singleton | Staff-editable rules without redeploy |
| Audit | `django-simple-history` | Row-level history for User/Booking/Violation/Closure |
| Security | `django-axes`, `django-ratelimit`, Django password validators | Login lockout, endpoint rate limits |
| Errors/monitoring | Sentry (free tier) + Uptime Kuma + `/healthz/` | Error tracking, uptime, disk, outbox lag |
| Deploy | Docker Compose on Hostinger VPS; images built in GitHub Actions | One-command deploy by tag; dev/prod parity |

### Alternatives considered and rejected

| Option | Verdict |
|---|---|
| Next.js + Postgres | Two apps to maintain; admin from scratch — no benefit here |
| Fork LibreBooking / MRBS | Patching QR check-in, walk-in, grace release, and strikes into legacy PHP is slower than clean Django |
| FastAPI + React | Doubles frontend work for one maintainer |
| Celery + Redis | Overkill; one cron command with an advisory lock covers all jobs |
| LINE Messaging API | ~800 THB/month — deferred until email is demonstrably ignored |
| Django admin as the only staff UI | Rejected for daily operations; one custom dashboard page added |

## 4. Data Model

```text
User (AbstractUser)
───────────────────
username          = student_id (10 digits) or employee_id (staff)
email             unique
th_name, en_name  CharField (en blank OK)
instrument        CharField blank   ← from roster
year_of_study     SmallInt nullable ← from roster sheet
is_staff          Boolean — 10 accounts
suspended_until   DateTime nullable   (active if > now; no cron needed to lift)
pdpa_accepted_at  DateTime nullable   (gate: must accept before first booking)
email_verified_at DateTime nullable
history           (django-simple-history)

Room
────
number            PositiveSmallInt unique (1–9)
(no is_closed flag — use Closure with open end_date)

Booking
───────
user              FK User
room              FK Room
start_time        DateTime (top of hour; end = start + slot_minutes)
status            SCHEDULED | IN_USE | COMPLETED | CANCELLED | NO_SHOW
source            GRID | WALK_IN | STAFF
created_at, cancelled_at, checked_in_at
checked_in_room   FK Room nullable (room whose QR was scanned)
idempotency_key   CharField unique nullable (form nonce)
history           (django-simple-history)
UNIQUE PARTIAL INDEX (room, start_time) WHERE status IN ('SCHEDULED','IN_USE')
CHECK  extract(minute from start_time) = 0 AND extract(second from start_time) = 0
INDEX  (user, start_time)      — daily quota + adjacency lookups
INDEX  (status, start_time)    — cron scans

Violation
─────────
user FK · booking FK nullable · type NO_SHOW | MANUAL · note
created_at · expires_at (= created_at + strike_window_days at creation)
cleared_at, cleared_by FK User nullable   ← staff pardon
history
INDEX (user, expires_at) WHERE cleared_at IS NULL

RecurringClosure
────────────────
weekday 0–6 unique · reason          → Sat/Sun seeded

Closure
───────
room FK nullable (null = all rooms) · start_date · end_date nullable (open = until further notice)
reason · created_by FK · history     → holidays, piano tuning, maintenance

Notification (transactional outbox)
──────────────────────────────────
user FK · kind (CONFIRMED | REMINDER | CANCELLED | NO_SHOW | SUSPENDED | LIFTED | INVITE | VERIFY | DIGEST)
payload JSON · dedupe_key unique · scheduled_for · sent_at nullable
attempts · last_error · created_at
INDEX (sent_at, scheduled_for)

Setting (django-solo singleton, staff-editable)
──────────────────────────────────────────────
opening_hour=8 · closing_hour=20 · slot_minutes=60 · grace_minutes=15
max_per_day=2 · max_days_ahead=7 · no_consecutive=True
strike_window_days=30 · strikes_to_suspend=3 · default_suspension_days=7
self_registration_enabled=True · allowed_email_domain='student.chula.ac.th'
```

**Design principles**
- Privacy-first: the public grid shows only จองแล้ว / ว่าง per slot, never who booked it. Names are visible only on staff pages.
- Derived status: `effective_status(booking, now)` is a pure function used by every view. Persisted status is catch-up bookkeeping done by cron.
- Everything a rule depends on lives in `Setting`, `RecurringClosure`, or `Closure` — never in code.

## 5. Booking Rules Engine

One function `create_booking(user, room, start_time, source, idempotency_key)`:

```text
BEGIN
  SELECT * FROM user WHERE id = :user FOR UPDATE      -- serialises all per-user checks
  if idempotency_key already exists → return existing booking (no error)
  run rules 1–7 (all in Asia/Bangkok local time)
  INSERT booking                                      -- partial unique index enforces rule 8
  enqueue Notification(CONFIRMED)
COMMIT
```

| # | Rule | Thai error message |
|---|---|---|
| 1 | Authenticated, PDPA accepted, not suspended (`suspended_until` null or ≤ now) | บัญชีของคุณถูกระงับสิทธิ์การจอง กรุณาติดต่อเจ้าหน้าที่ |
| 2 | Weekday not in RecurringClosure; date/room not in an active Closure | ห้องซ้อมปิดในวันที่เลือก |
| 3 | Within opening–closing hours | เวลาจองอยู่นอกเวลาทำการ (08:00–20:00 น.) |
| 4 | `start + grace ≥ now` and `start ≤ now + max_days_ahead` | เลยเวลาจองแล้ว / จองล่วงหน้าได้ไม่เกิน 7 วัน |
| 5 | Active bookings (SCHEDULED, IN_USE, COMPLETED) that day < max_per_day — cancelled and no-show ones do not count; walk-ins do | คุณจองครบ 2 ครั้ง/วันแล้ว |
| 6 | Slot is exactly one `slot_minutes` unit on the hour | จองได้ครั้งละ 1 ชั่วโมงเท่านั้น |
| 7 | No active booking in the adjacent hour before or after, any room | ไม่สามารถจองชั่วโมงติดกันได้ |
| 8 | Slot free — enforced by the partial unique index; `IntegrityError` mapped to this message | เวลานี้ถูกจองแล้ว กรุณาเลือกเวลาอื่น |

**Concurrency guarantees**
- Different users, same slot: the partial unique index lets exactly one commit; losers see error 8 and a refreshed grid.
- Same user, two slots at once: the `FOR UPDATE` on the user row serialises the quota and adjacency checks, so a double-click cannot exceed 2/day or create adjacent bookings.
- Same user, same slot twice (double POST): the idempotency key returns the first booking instead of an error.
- A slot freed by a no-show is bookable by anyone from `start + grace` onward because rule 4 and the derived status both use the same clock.

**Cancellation:** allowed while `effective_status == SCHEDULED` and `now < start`. Cancelling < 60 min before start sets a `late_cancel` flag on the booking for staff reporting only.

## 6. Booking Lifecycle & State Machine

```text
grid booking ─► SCHEDULED ──QR check-in in [start, start+15m]──► IN_USE ──end──► COMPLETED
                   │
                   ├── user cancels before start ─────────────► CANCELLED (slot free instantly)
                   └── no check-in by start+15m ──────────────► NO_SHOW  (+1 strike, email)
                                                                   │
walk-in (QR on free room, current hour) ───────────────────────► IN_USE ──end──► COMPLETED

strikes in last 30 days ≥ 3 ─► suspended_until = now + default_suspension_days
                               (staff may extend, shorten, or pardon a strike)
```

**Derived status** (used by grid, my-bookings, check-in page):

| Persisted | Condition | Effective |
|---|---|---|
| SCHEDULED | now > start + grace | NO_SHOW (slot shown free) |
| IN_USE | now ≥ end | COMPLETED |
| anything else | — | as persisted |

**Cron** — one command, every minute, wrapped in `pg_advisory_xact_lock(<const>)` so overlapping runs exit immediately:

1. `persist_no_shows` — SCHEDULED past grace → NO_SHOW; create Violation; enqueue NO_SHOW email; if strike count ≥ threshold, set `suspended_until` and enqueue SUSPENDED email.
2. `persist_completions` — IN_USE past end → COMPLETED.
3. `enqueue_reminders` — bookings starting in 30 ± 1 min → Notification(REMINDER, dedupe_key=`reminder:<booking_id>`).
4. `drain_outbox` — send up to 100 due notifications; exponential backoff on failure (1, 5, 30 min; give up after 6 attempts and surface in staff dashboard).
5. `weekly_digest` — Mondays 08:00 only: usage stats + strike queue to all staff.

Because status is derived, a cron outage degrades only emails and bookkeeping, never room availability.

## 7. QR Check-in & Walk-in

- **9 printed A4 posters**, one per door, encoding static URLs `https://<domain>/r/<n>/` for n = 1..9. Generated by the `qrcode` package via `manage.py make_posters`.
- Posters are not secret. Security comes from login + booking ownership + the audit trail.

**Flow after scan (login gate with `?next=` if needed):**

| Situation (room n, current hour h) | Page shows |
|---|---|
| User has SCHEDULED booking for room n at h, within grace | Big green **ยืนยันเข้าใช้ห้อง** → IN_USE, `checked_in_at`, `checked_in_room` |
| User has booking for room n at h but before start | "ยืนยันได้ตั้งแต่ hh:00 น." with countdown; auto-enables at start |
| User's booking at h is in another room | "การจองของคุณอยู่ที่ห้อง 5" with link |
| Room n at h is free (no active booking) and user passes rules 1–7 | **ใช้ห้องนี้เลย** → creates Booking(source=WALK_IN, status=IN_USE) — this is ข้อ 2.2 |
| Room n at h is free but user fails a rule (quota, adjacency, suspended) | The rule's bilingual message |
| Room n at h is held by someone else, still inside grace | "ห้องนี้มีผู้จองไว้ ว่างได้ตั้งแต่ hh:15 น. หากผู้จองไม่มา" with countdown and auto-refresh |
| User's own booking passed grace | "การจองถูกยกเลิกแล้ว (ไม่มาตามเวลา)"; if the room is still free, offers walk-in |
| Outside opening hours / closure | Closed message |

**Known limitation (documented honestly):** a static QR can be scanned from a photo, so remote "check-in" is possible. Mitigations in v1: audit trail (`checked_in_room`, timestamps, IP/user-agent in history) and staff spot checks. Optional v1.1 hardening: a rotating 4-digit code displayed on a small card refreshed daily by staff, required alongside the scan.

## 8. Violations & Suspension Policy

| Trigger | System action |
|---|---|
| No-show (auto) | Violation with `expires_at = now + 30 d`; polite bilingual email with current count "2/3" |
| Count of unexpired, uncleared violations ≥ 3 | `suspended_until = now + default_suspension_days` (7); booking and walk-in blocked; banner with พี่ดิว's contact; SUSPENDED email |
| Staff manual violation | Form on `/staff/today/` or admin: type, note, optional booking link |
| Staff pardon | Sets `cleared_at/cleared_by`; recount happens on next check |
| Staff edits suspension | Any date or clear; LIFTED email if cleared early |
| Auto-lift | Purely time-based (`suspended_until ≤ now`), no job needed |
| Audit | `django-simple-history` on User, Booking, Violation, Closure — who/what/when for all 10 staff and for cron |

Suspension length remains staff judgment per ข้อ 7; the default only guarantees the suspension takes effect immediately without waiting for staff.

## 9. Pages & User Flows

| Path | Audience | Description |
|---|---|---|
| `/` | Public | Availability grid: 9 rooms × 12 slots, 7-day picker, weekend/holiday cells shaded, TH/EN toggle, htmx refresh every 30 s served from a 10 s fragment cache. Past slots greyed; current-hour slots show "ว่าง — เข้าใช้ได้" when free |
| `/register/` | Public (if enabled) | Chula email → verification link → set password. Username = local part of the email (student ID) |
| `/invite/<token>/` | Invited student | Set password from CSV-import invite (7-day signed token) |
| `/login/`, `/password-reset/` | Public | ID + password; reset via email link; lockout after 5 failures (axes) |
| `/privacy/` | Public | PDPA notice (TH/EN); consent recorded at first login |
| `/book/<room>/<slot>/` | Student | Confirm page with rules summary, hidden idempotency key, big green confirm |
| `/my-bookings/` | Student | Upcoming with cancel; 60-day history; strike count and expiry dates; suspension status |
| `/r/<n>/` | Student via QR | Check-in / walk-in (§7) |
| `/staff/today/` | Staff | Operations dashboard: today's grid **with names**, live no-show list, strike queue (users at 2/3), failed-email list, quick actions: cancel, manual violation, close room, pardon |
| `/staff/admin/` | Staff | Django admin in Thai: users, suspensions, closures, holidays, violations, settings, CSV export (bookings/violations by date range), CSV import (students) |
| `/rules/`, `/help/` | Public | Regulation text; how-to; พี่ดิว's contact |
| `/healthz/` | Monitoring | 200 if DB reachable and outbox lag < 10 min |

Mobile-first: the QR and walk-in flows happen entirely on phones. Add-to-home-screen manifest included; no full PWA.

## 10. Notifications

All email goes through the outbox (§4). Nothing is sent inside a web request.

| Event | Recipient | dedupe_key | Notes |
|---|---|---|---|
| Verification / invite | Student | `verify:<user>` / `invite:<user>` | Signed links, 7-day expiry |
| Booking confirmed | Student | `confirmed:<booking>` | Room, time, cancel link, "scan QR at door" |
| T-30 min reminder | Student | `reminder:<booking>` | Skipped for walk-ins |
| Cancelled | Student | `cancelled:<booking>` | |
| No-show | Student | `noshow:<booking>` | Count "n/3", expiry date of oldest strike, contact |
| Suspended / lifted | Student | `susp:<user>:<ts>` | States end date |
| Weekly digest | All staff | `digest:<iso-week>` | Usage, no-shows, strike queue, failed emails |

Volume: ≤ 2 bookings/day × 85 users ≈ ≤ 350 emails/day, within SMTP limits. Failed sends are visible on `/staff/today/`. LINE deferred.

## 11. Deployment & Operations

```yaml
# compose.yaml (production)
services:
  web:    ghcr.io/<owner>/roomreserve:<tag>  gunicorn config.wsgi  (2 workers)
  cron:   same image, command: supercronic /app/crontab   # * * * * * manage.py tick
  db:     postgres:16  (volume pgdata)
  caddy:  caddy:2  automatic HTTPS + HSTS for <domain>
  backup: postgres:16  nightly pg_dump → /backups (rsync to NAS + rclone to Backblaze B2)
```

- **Config:** all secrets from `.env` (never committed); `.env.example` in repo; Django `SECURE_*` flags on; `DEBUG=False` enforced by a startup check.
- **CI:** GitHub Actions — ruff, mypy (light), pytest with real Postgres service, Playwright smoke, docker build + push on tag.
- **Releases:** semantic tags v0.1.0 (core), v0.2.0 (check-in/walk-in), v0.3.0 (staff + PDPA), v1.0.0 (pilot). Deploy = `TAG=v1.0.0 docker compose pull && docker compose up -d`.
- **Backups:** nightly `pg_dump`, 30-day retention on NAS, 90-day on B2 (free 10 GB). **Quarterly restore drill** into a scratch container, logged in the runbook.
- **Monitoring:** Uptime Kuma checks `/healthz/` and disk; Sentry for exceptions; JSON logs with request id, rotated by Docker.
- **Runbook (1 page in repo):** restart, roll back to previous tag, restore from dump, rotate `SECRET_KEY`/SMTP password, add a staff account, regenerate posters.
- **Time:** `TIME_ZONE=Asia/Bangkok`, `USE_TZ=True`; all rule math via `timezone.localtime()`.

## 12. Seed Data & Onboarding

Management commands:

- `seed_initial` — 9 rooms; Sat/Sun `RecurringClosure`; `Setting` defaults; 10 staff accounts each receiving an **invite email** (no default passwords); Thai holiday list for the current academic year if provided.
- `import_students <csv>` — columns `student_id, th_name, en_name, email, instrument, year`. Creates or updates users, sends invite links. Idempotent; a student who already self-registered is matched by `student_id` and left untouched.
- `make_posters` — 9 A4 PDFs with QR + room number + short instructions.

**Self-registration (on by default):** student enters an email ending in `allowed_email_domain`; the ID is the local part; a verification link sets the password. Staff can switch it off in `Setting` once the roster is complete.

**Roster today:** the provided xlsx has IDs only for year 1 (20 rows) and names + instrument only for years 2–4 (~65 rows). Full IDs and emails will be supplied by the owner later; until then self-registration covers everyone.

## 13. Build Phases & Acceptance Gates

Est. ~5–7 weeks part-time with AI-assisted coding.

| Phase | Duration | Scope | Acceptance gate |
|---|---|---|---|
| **0 — Foundation & security baseline** | 4 days | Repo, compose dev stack, models + constraints + migrations, `Setting`, simple-history, axes/ratelimit, `.env` handling, `/healthz/`, outbox model, CI (ruff + pytest + Postgres) | Fresh clone → `docker compose up` boots seeded system; CI green |
| **1 — Booking core** | 1.5 wk | Public grid with derived status + fragment cache, `create_booking` with user lock + idempotency, cancel, my-bookings, bilingual copy, confirmed/cancelled emails via outbox | ≥ 40 rule tests incl. midnight and adjacency; concurrency test: 20 parallel → exactly 1 succeeds; same-user double-submit → 1 booking |
| **2 — Check-in, walk-in, strikes** | 1.5 wk | `/r/<n>/` all states, walk-in, cron `tick` with advisory lock, violations with expiry, auto-suspend, reminders, outbox drain with retry | Simulated no-show frees slot instantly; walk-in creates IN_USE; 3rd strike in window suspends; expired strike does not count; SMTP outage → retries then staff alert |
| **3 — Staff experience & PDPA** | 1 wk | `/staff/today/`, admin polish in Thai, closures/holidays, CSV import/export, pardon, privacy page + consent gate, registration + invites | พี่ดิว completes every staff task unassisted; new student self-registers end to end |
| **4 — Deploy & pilot** | 1 wk | Prod compose + Caddy, backups to NAS + B2, Sentry, Uptime Kuma, runbook, posters, restore drill, 2-week pilot with ~10 students | Live, zero downtime, restore drill passed, feedback folded into v1.0 |

## 14. QA & Test Plan

- [ ] Concurrency: 20 threads book one slot against real Postgres → 1 success, 19 clean Thai errors
- [ ] Same user double-submits two different slots simultaneously → quota/adjacency still enforced
- [ ] Same user double-POSTs same form → one booking, no error
- [ ] Rule matrix (parametrized): 2/day, no-consecutive cross-room, 7-day max, past slot rejected, current hour bookable until grace, weekend, holiday, per-room closure
- [ ] Midnight: booking at 19:00 and 08:00 next day not adjacent; quota resets at 00:00 Bangkok time
- [ ] Derived status: SCHEDULED past grace renders free on grid before cron runs
- [ ] Walk-in: free room → IN_USE with source WALK_IN; held-within-grace room → wait message; quota-full user → rejected
- [ ] Strikes: 3 within 30 days suspends; 2 + 1 expired does not; pardon reduces count
- [ ] Cron: two overlapping `tick` runs → second exits immediately (advisory lock)
- [ ] Outbox: SMTP failure → retry schedule respected; dedupe key prevents duplicate reminder
- [ ] Auth: 5 bad logins → lockout; rate limit on `/book/` and `/r/<n>/`
- [ ] PDPA: unaccepted user cannot book; privacy page renders TH/EN
- [ ] Registration: non-Chula domain rejected; ID derived correctly; existing ID not duplicated by CSV import
- [ ] Thai UI completeness: no untranslated strings; ค.ศ. dates with Thai months
- [ ] Playwright mobile viewport: grid → book → scan URL → check-in
- [ ] Old Android phone manual test of QR flow
- [ ] Email deliverability to @chula.ac.th and @student.chula.ac.th (spam check)
- [ ] Backup restore drill from NAS and from B2
- [ ] Pilot checklist signed off by พี่ดิว

## 15. Cost Breakdown

| Item | Cost (THB/yr) |
|---|---|
| Hostinger VPS KVM 1–2 | ~1,100–2,900 (promo year 1; renewal higher) |
| Domain (if new) | ~300–800 |
| Django / Postgres / Caddy / HTMX / Tailwind / Uptime Kuma / simple-history / axes | 0 |
| SMTP (Hostinger mailbox) | 0 |
| Sentry free tier, Backblaze B2 (< 10 GB), GitHub Actions (public or free minutes) | 0 |
| QR posters × 9 | ~100 (one-off) |
| **Year-1 total** | **≈ 1,500–3,800** |
| Optional later: LINE Messaging API | ~800/month — deferred |
| Optional later: Chula SSO / Google OAuth | 0 (IT coordination time) |

## 16. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Chula mail blocks automated mail | Verified Hostinger mailbox with SPF/DKIM; test in Phase 1; outbox retries; fallback Resend free tier |
| Remote QR scan from a photo | Audit trail + staff spot checks; optional rotating code in v1.1 |
| Walk-in abuse (grab a room, leave) | Walk-ins count toward 2/day and adjacency; visible in audit |
| พี่ดิว wants changes mid-course | Rules in `Setting`; 2-week pilot before v1.0 |
| VPS disk fills | Log rotation, backup retention, Uptime Kuma disk alert |
| Cron container dies | Availability unaffected (derived status); `/healthz/` reports outbox lag → alert |
| Data protection complaint | PDPA notice, consent, retention/anonymisation policy, export/delete path via staff |
| Roster incomplete | Self-registration on by default; CSV import idempotent later |
| Faculty IT approval | Demo prototype + this document |

## 17. Out of Scope (v1)

- **Group/ensemble bookings** — permitted by ข้อ 3.1, so this is the first v1.1 candidate (booking with multiple attendees, one quota charge each)
- Rotating check-in codes (v1.1 hardening)
- Native mobile app, payments, equipment checkout
- LINE notifications, Chula SSO / Google OAuth
- Waitlists / notify-when-free

## 18. Outstanding Operational Items (before "go")

1. **Full student list** from the owner: student_id, Thai name, English name, email, instrument, year (~85 rows). Not blocking — self-registration covers the gap.
2. **Staff list**: 10 rows (พี่ดิว + 9) with emails for invite seeding.
3. **Domain name** decision (Phase 4).
4. **Thai holiday list** for academic year 2569 (staff can enter later via admin).
5. **PDPA notice wording** reviewed by faculty (template supplied in Phase 3).
6. Date display resolved: ค.ศ. with Thai month names.

## 19. Decision Log

| Decision | Options weighed | Final | Reason |
|---|---|---|---|
| Build vs fork | MRBS, LibreBooking, Seatsurfing vs custom | **Custom Django** | Walk-in, grace release, strikes need deep patches elsewhere |
| Framework | Django / Next.js / FastAPI | **Django 5** | Owner fluent in Python; admin, auth, i18n built in |
| Frontend | React SPA vs HTMX | **HTMX + Tailwind** | One codebase, mobile-first |
| Auth | OAuth vs ID + password | **ID + password**, self-registration by Chula email | Simple; OAuth path preserved |
| Onboarding | Default passwords vs invite links | **Invite/verification links** | No shared secrets; PDPA-friendly |
| Lead time | 24 h min vs none | **None (0)** | Practice rooms are ad hoc; removes special-case logic (owner decision, Sep 2026) |
| Walk-in | Bookings only vs QR walk-in | **QR walk-in** | Implements ข้อ 2.2 literally (owner decision, Sep 2026) |
| Strike window | Never / semester / rolling | **Rolling 30 days** | Fair, configurable (owner decision, Sep 2026) |
| Status model | Cron-flipped vs derived | **Derived + cron bookkeeping** | Correct instantly; cron outage harmless |
| Email | Inline send + log vs outbox | **Transactional outbox** | Retries, idempotency, visibility |
| Notify | LINE OA vs email | **Email** | Free, sufficient |
| Deploy | Vercel vs VPS | **Hostinger VPS + Docker** | Own infrastructure, NAS backups |
| Consecutive rule | same-room vs cross-room | **Cross-room (strict)** | Prevent chain-booking |
| Dates | พ.ศ. vs ค.ศ. | **ค.ศ. + Thai months** | Owner decision |
| Dashboard | login vs public | **Public read-only** | Faster availability checking |
| Weekend | open vs closed | **Closed Sat–Sun** | Recurring closure, staff-overridable |
| Staff count | 6 vs 10 | **10** | Owner revision; audit trail required |
| Staff UI | Admin only vs + dashboard | **Admin + `/staff/today/`** | Daily ops need one page, not CRUD screens |
