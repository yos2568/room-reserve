# Room Reserve App — Complete Project Plan
## ระบบจองห้องซ้อมดนตรี (Music Practice Room Booking System)

สาขาวิชาดุริยางคศิลป์ตะวันตก คณะศิลปกรรมศาสตร์ จุฬาลงกรณ์มหาวิทยาลัย
Western Music Department, Faculty of Fine and Applied Arts, Chulalongkorn University

Version 1.2 — Final decision-locked plan — September 2026
Status: **Planning complete, ready to build**

---

## Table of Contents

1. Project Overview
2. Regulation → Feature Mapping
3. Architecture & Technology Decisions
4. Data Model
5. Booking Rules Engine
6. Booking Lifecycle & State Machine
7. QR Check-in System
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
19. Decision Log (why each choice was made)

---

## 1. Project Overview

| Item | Value |
|---|---|
| Purpose | Replace manual booking of practice rooms with a fair, self-service, rule-enforcing web app |
| Users | ~100 music students |
| Staff | **10 accounts** — พี่ดิว (Sitanun S., Sitanun.S@chula.ac.th, 02-218-4604) + 9 staff, identical permissions |
| Rooms | 9 identical practice rooms, each with upright piano |
| Hours | Monday–Friday only, 08:00–20:00. Closed Saturday & Sunday |
| Language | Bilingual: Thai default, English toggle |
| Dates | Western calendar (ค.ศ.) with Thai month names |
| Deployment | Real production use by the faculty — Hostinger VPS (Docker) |
| Auth | Student-ID/employee-ID + password (OAuth upgrade path preserved) |
| Dashboard | Public read-only availability grid; booking/check-in require login |

## 2. Regulation → Feature Mapping

Every clause of the official regulation document maps to a system feature:

| Regulation clause | Rule | System implementation |
|---|---|---|
| ข้อ 1.1 | Max 1 hour per booking | Hourly slot model; grid only offers single-hour slots |
| ข้อ 1.2 | Max 2 bookings per person per day | Transactional quota check at booking creation |
| ข้อ 1.3 | Must finish on time | Hourly slots auto-complete; dashboard shows next booking |
| ข้อ 1.4 | No booking for others / no hoarding | Login required; booking bound to authenticated user account |
| ข้อ 2.1 | Arrive within 15 minutes | QR check-in enforced during start → start+15min window |
| ข้อ 2.2 | No-show = forfeit, room becomes free | Cron job (every 5 min) converts unconfirmed bookings to NO_SHOW; **same-day exception: released slots are instantly rebookable despite the 24h rule** |
| ข้อ 2.3 | Cancel early if unable to attend | Self-service cancel button up to start time; slot instantly returns to pool |
| ข้อ 3 | Music practice only | Stated in UI rules page; enforced by staff |
| ข้อ 4–5 | Care of rooms, no food | Stated in rules page students accept at booking |
| ข้อ 6 | Respect others' rights | Audit log; staff warnings |
| ข้อ 7 | Warnings & suspension for repeat violations | Auto-flag on every no-show; 3 flags → auto-suspend; staff sets end date |
| Contact | พี่ดิว, 02-218-4604, Sitanun.S@chula.ac.th | Shown in footer of every email + help page |

**Additional rules decided by the project owner (beyond the document):**

| Rule | Value |
|---|---|
| No consecutive hours | A user may not hold bookings in adjacent hour slots **across any rooms** |
| Booking window | At least 24 hours ahead, at most 7 days ahead |
| Check-in method | Printed QR code on each room door |
| Room types | All 9 rooms identical — no room-preference logic needed |
| Scheduled classes | Rooms are never blocked for classes — every hour bookable |

## 3. Architecture & Technology Decisions

### Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Owner already fluent in Python via AI/LLM work |
| Framework | Django 5.x + django-htmx + Tailwind CSS | Built-in admin panel delivers ~80% of staff features free; built-in auth; first-class Thai locale; single codebase — no separate SPA to maintain |
| Database | PostgreSQL 16 | Partial unique index gives database-level double-booking protection |
| Server | Gunicorn + Caddy | Caddy provides automatic free HTTPS certificates |
| Jobs | cron container → Django management commands | Lightweight; no Redis/Celery needed at this scale |
| Email | Hostinger SMTP (free with VPS plan) | Simple, zero cost |
| Deploy | Docker Compose on Hostinger VPS | One-command deploy, portable |

### Alternatives considered and rejected

| Option | Verdict |
|---|---|
| Next.js + Postgres | Two apps to build/maintain; admin panel built from scratch — no benefit for this project |
| Fork LibreBooking / MRBS | They have quotas and calendars, but patching QR check-in + 15-min auto-release + auto-suspension into legacy PHP is slower than clean Django |
| FastAPI + React | Modern but doubles frontend work; better for larger teams, not one maintainer |
| LINE Notify | Discontinued; LINE Messaging API costs ~800 THB/month — deferred |
| Celery + Redis | Overkill for one cron job; revisit only if real-time features are added later |

## 4. Data Model

```text
User (extends Django auth_user)
───────────────────────────────
username        = student_id or employee_id (staff)
email
th_name         CharField
en_name         CharField (blank OK)
is_staff        Boolean  — 10 accounts (พี่ดิว + 9 staff)
suspended_until DateTimeField (nullable) — lift date set by staff
force_password_reset  — True for all seeded accounts

Room
────
number          PositiveSmallIntegerField unique (1–9)
is_closed       Boolean — staff can close a room for maintenance

Booking
───────
user            FK User
room            FK Room
start_time      DateTime (top of hour; end = start + 1h)
status          SCHEDULED | IN_USE | COMPLETED | CANCELLED | NO_SHOW
created_at / canceled_at / checked_in_at
UNIQUE PARTIAL INDEX (room, start_time) WHERE status IN (SCHEDULED, IN_USE)
INDEX (user, start_time) — fast daily-quota lookup

CheckIn (audit-friendly; could also live as columns on Booking)
───────
booking FK, checked_in_at, scanned_room FK, matches_booking Boolean

Violation
─────────
user FK · booking FK (null if staff-entered manually)
type = NO_SHOW | MANUAL · created_at · note

RoomClosure
───────────
date (nullable) · weekday (nullable, 0–6) · reason · created_by
→ Sat/Sun seeded as two weekday closures; holidays added by staff

EmailLog — booking FK, kind, sent_at, status (for debugging delivery)

Setting (singleton) — opening_hours, max_per_day, grace_minutes, etc. (staff-editable)
```

**Design principle:** privacy-first — the public dashboard shows only
"จองแล้ว / ว่าง" (reserved/free) per slot, never which student booked it.

## 5. Booking Rules Engine

Single function `validate_booking(user, room, start_time)`, executed inside one
transaction with row locking; each failure returns a bilingual error message.

| # | Rule | Thai error message |
|---|---|---|
| 1 | Authenticated, not suspended | บัญชีของคุณถูกระงับสิทธิ์การจอง กรุณาติดต่อเจ้าหน้าที่ |
| 2 | Weekday only; date not in RoomClosure | ห้องซ้อมเปิดเฉพาะวันจันทร์–ศุกร์ |
| 3 | Within 08:00–20:00 operating hours | เวลาจองอยู่นอกเวลาทำการ (08:00–20:00 น.) |
| 4 | ≥ 24h ahead and ≤ 7 days ahead (waived for same-day released slots) | ต้องจองล่วงหน้าอย่างน้อย 24 ชั่วโมง และไม่เกิน 7 วัน |
| 5 | < 2 active bookings that day (ข้อ 1.2) | คุณจองครบ 2 ครั้ง/วันแล้ว |
| 6 | Exactly 60 minutes (ข้อ 1.1) | จองได้ครั้งละ 1 ชั่วโมงเท่านั้น |
| 7 | No active booking in adjacent hour anywhere (ข้อ owner rule) | ไม่สามารถจองชั่วโมงติดกันได้ |
| 8 | Slot free — DB index + UI refresh | เวลานี้ถูกจองแล้ว กรุณาเลือกเวลาอื่น |

**Concurrency guarantee:** the partial unique index makes double-booking impossible
at the database level; if 20 students click the same slot simultaneously, exactly one
transaction commits. Losing users see error #8 and the refreshed grid.

## 6. Booking Lifecycle & State Machine

```text
                 ┌──────────────────────────────────────────────────┐
create ─► SCHEDULED ──QR check-in in [start, start+15min]──► IN_USE ──+1h──► COMPLETED
    │        │                                                           (cron auto)
    │        ├── user cancels before start ────────► CANCELLED ──slot frees instantly
    │        └── cron at start+15min, no check-in ─► NO_SHOW ──► +1 Violation
    │                                                            │
    │                                            violations ≥ 3 ─┤
    │                                            ► auto-suspend ─► staff sets end date
    ▼                                                                 (dashboard banner:
grid update                                                       ติดต่อพี่ดิว)

Cron jobs (every 5 min):
1. release_no_shows   — SCHEDULED past grace → NO_SHOW + violation + email
2. complete_sessions  — IN_USE past end → COMPLETED
3. send_reminders     — T-30min booking reminders (deduped via EmailLog)
```

## 7. QR Check-in System

- **9 printed QR posters**, one per room door, encoding static URLs:
  `https://<domain>/r/<n>/check-in/` for n = 1..9
- Posters are **not secret** — security comes from login + ownership, so a student
  cannot check in on someone else's booking
- Flow: scan → if not logged in, login page with `?next=` redirect → server looks up
  user's SCHEDULED booking for that room within the check-in window → big green
  button "ยืนยันเข้าใช้ห้อง" → status becomes IN_USE
- Edge cases handled:
  - Too early → "ยืนยันได้ตั้งแต่ 08:00 น." with countdown
  - Too late (auto-released) → "การจองถูกยกเลิกแล้ว ห้องว่าง จองใหม่ได้เลย" + link
  - Wrong room → "การจองของคุณอยู่ที่ห้อง 5"
  - Booking exists but belongs to someone else → login gate already blocks
- QR images generated with the Python `qrcode` package; A4 poster templates included
- Every check-in records timestamp (and room) for staff dispute resolution

## 8. Violations & Suspension Policy

| Trigger | System action |
|---|---|
| No-show (auto) | Violation record created; polite bilingual email with remaining-strike count |
| 3rd auto violation | `suspended_until` required → booking buttons hidden; banner shows พี่ดิว's contact |
| Staff manual violation | Admin form (type, note, linked booking optional) |
| Lift suspension | Staff sets/clears `suspended_until`; confirmation email to student |
| Staff edits | Django admin history log ON for User/Booking/Violation/RoomClosure — with 10 staff, audit trail ("who did what when") is mandatory |

Suspension durations are set by staff judgment per ข้อ 7 — the system does not
impose fixed durations, only enforcement and counting.

## 9. Pages & User Flows

| Path | Audience | Description |
|---|---|---|
| `/` | **Public, read-only** | Availability grid: 9 rooms × 12 hourly slots, 7-day picker, weekend cells shaded "ปิดวันเสาร์–อาทิตย์", TH/EN toggle, htmx auto-refresh every 30s. "จอง" buttons redirect guests to login |
| `/login/`, `/password-reset/` | Public | ID + password; reset via email link |
| `/book/<room>/<slot>/` | Student | Confirm page, short rules summary, big green confirm button |
| `/my-bookings/` | Student | Upcoming + cancel; past history (60 days), violation count, suspension status |
| `/r/<n>/check-in/` | Student via QR | See §7 |
| `/staff/` | 10 staff | Django admin customized in Thai: users, suspensions (end-date picker), closures, violations, CSV usage export, per-room stats |
| `/rules/` | Public | Bilingual rendering of the regulation document |
| `/help/` | Public | How to book/check-in, พี่ดิว's contact |

Mobile-first design — the QR flow happens entirely on phones.

**Demo/roleplay testing (Phase 4 deploy):** the prototype can run with a demo
admin above the student role for recruitment-style demos, but production keeps
students and 10 staff as the only roles.

## 10. Notifications

Email via Hostinger SMTP, bilingual subject + body:

| Event | Recipient | Notes |
|---|---|---|
| Booking confirmed | Student | Includes room, time, cancel link, "scan QR at door" reminder |
| T-30min reminder | Student | Cron-driven; idempotent via EmailLog |
| Cancelled | Student | Confirmation |
| No-show | Student | Polite, shows strikes (e.g. "2/3"), พี่ดิว's contact |
| Suspension applied / lifted | Student | States end date |
| Weekly digest | พี่ดิว + staff | Usage stats, violation queue (OK to defer to v1.1) |

Volume estimate: ≤ 2 bookings/day × 100 users ≈ ≤ 400 emails/day — trivially within
SMTP limits. LINE Messaging API deferred until students demonstrably ignore email.

## 11. Deployment & Operations

```yaml
# compose.yaml (production)
services:
  web:     gunicorn config.wsgi  (Django 5, Python 3.12)
  db:      postgres:16 (named volume pgdata)
  cron:    alpine + crond → */5 * * * * python manage.py run_all_crons
  caddy:   automatic HTTPS for <domain>
```

- **CI:** GitHub Actions — lint (ruff), tests (pytest), docker build on push to main
- **Semantic versioning + tags from day one:** v0.1.0 (core), v0.2.0 (check-in),
  v0.3.0 (staff panel), v1.0.0 (pilot launch). Every main merge tagged; Caddy/VPS
  deploy = `git checkout vX.Y.Z && docker compose up -d --build`
- **Backups:** nightly `pg_dump` → rsync to owner's NAS, 30-day retention
- **Monitoring:** Uptime Kuma (self-hosted, free); daily backup check task
- **Costs:** domain ~THB 300–800/yr; VPS (KVM 1–2) ~THB 1,100–2,900-1,600/yr promo

## 12. Seed Data & Onboarding

Management command `seed_initial`:

- 9 rooms (ห้องซ้อม 1–9)
- 2 recurring weekday closures: Saturday, Sunday
- **10 staff accounts** (พี่ดิว + 9), `force_password_reset=True`
- ~100 students from CSV (student_id, th_name, email)
  - **Fallback:** self-registration restricted to `@student.chula.ac.th` domain
- Thai + English locale files
- `Setting` defaults: 2/day, 1h slots, +15min grace, 08:00–20:00, Mon–Fri

## 13. Build Phases & Acceptance Gates

Est. ~4–6 weeks part-time with AI-assisted coding.

| Phase | Duration | Scope | Acceptance gate |
|---|---|---|---|
| **0 — Foundation** | 3 days | Repo, dev Docker stack, models + migrations, seeds, TH locale, ruff CI, README | Fresh clone → `docker compose up` boots seeded system |
| **1 — Booking core** | 1 wk | Public grid, rules engine, book, cancel, bilingual copy | Rules tests (≥40); concurrency test: 20 parallel bookings → exactly 1 succeeds |
| **2 — Check-in lifecycle** | 1 wk | QR pages, no-show cron, violations, auto-suspend, emails | Simulated no-show frees slot + flags; 3rd strike suspends |
| **3 — Staff experience** | 1 wk | Thai admin polish, suspension end-dates, closures, CSV export, audit log | พี่ดิว completes every staff task unassisted |
| **4 — Deploy & pilot** | 1 wk | Prod compose + Caddy, backups, monitoring, QR posters, 2-week pilot with ~10 students | Live use, zero downtime, feedback folded into v1.0 |

Docker Compose used for both dev and prod so localhost and VPS match exactly —
environment parity was a stated requirement.

## 14. QA & Test Plan

- [ ] Race-condition script: 20 concurrent booking attempts on one slot → exactly 1 success, others get clean Thai error
- [ ] Rule matrix tests: 2/day limit, no-consecutive (cross-room), 24h minimum, 7-day max, weekend rejected, closed-room rejected
- [ ] Same-day released-slot exemption works only from +15min releases, not as a general hole
- [ ] Cron simulation: booking with past start → NO_SHOW, slot visibly free, violation count +1, email logged
- [ ] Suspension flow: 3 flags → booking blocked → staff sets end date → auto-lift at date
- [ ] Thai UI completeness: no untranslated strings; ค.ศ. dates render correctly
- [ ] Old-phone test: dashboard + QR flow on a slow Android browser
- [ ] Email deliverability: test to @chula.ac.th addresses (check spam folder)
- [ ] Backup restore drill: `pg_restore` from NAS dump into a fresh container
- [ ] Pilot checklist signed off by พี่ดิว

## 15. Cost Breakdown

| Item | Cost (THB/yr) |
|---|---|
| Hostinger VPS KVM 1–2 (promo) | 1,100–2,900 (renewal higher) |
| Domain (if new) | 300–800 |
| Django / Postgres / Caddy / HTMX / Tailwind / Uptime Kuma | 0 (open source) |
| SMTP (Hostinger mailbox) | 0 |
| QR posters × 9 | ~100 (one-off) |
| **Year-1 total** | **≈ 1,000–3,000** |
| Optional later: LINE Messaging API | ~800/month — deferred |
| Optional later: Chula Google OAuth integration | 0 (IT coordination time only) |

## 16. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Chula mail server blocks automated mail | Send from a verified Hostinger mailbox; test early (Phase 2); fallback Resend free tier |
| Students screenshot/forward QR to check in remotely | Not secret by design — but check-in only works for the booking owner; abuse visible in audit trail |
| พี่ดิว wants changes mid-course | 2-week pilot phase explicitly collects feedback before v1.0 |
| VPS disk fills | Log rotation in compose; `pg_dump` retention 30 days; disk alert in Uptime Kuma |
| Rules change (e.g. opens Saturday) | All rules live in `Setting`/closures, not code — staff-editable, no redeploy |
| Faculty IT approval required | Demo-ready prototype + this document used to pitch/approve |

## 17. Out of Scope (v1)

- Ensemble/group bookings (private practice only)
- Queue/time-contention racing visualization (document noted: two students grabbing the same slot — v1 resolves it via DB constraint + clean error only)
- Mobile app (mobile web only), payments, equipment checkout
- LINE notifications, Google OAuth, faculty SSO
- Roleplay demo admin role beyond pilot demos

## 18. Outstanding Operational Items (before "go")

1. **Student CSV** from พี่ดิว: student_id, Thai name, email (~100 rows) — or confirm self-register fallback
2. **Staff CSV**: 10 rows (พี่ดิว + 9) with emails for account seeding
3. **Domain name** decision (can wait until Phase 4; localdev first)
4. 권กฎ พ.ศ. vs display wording decisions already resolved (ค.ศ.)

## 19. Decision Log

| Decision | Options weighed | Final | Reason |
|---|---|---|---|
| Build vs fork | MRBS, LibreBooking, Seatsurfing vs custom | **Custom Django** | QR check-in, 15-min auto-release, auto-suspension need deep patches to legacy PHP; Django admin free |
| Framework | Django / Next.js / FastAPI | **Django 5** | Owner fluent in Python; built-in admin, auth, i18n |
| Frontend | React SPA vs HTMX | **HTMX + Tailwind** | One codebase, server-rendered grid, mobile-first |
| Auth | OAuth vs ID+password | **ID + password** | Simple; OAuth onboarding deferred, path preserved |
| Notify | LINE (dead), LINE OA (~800 THB/mo), email | **Email** | Free, sufficient volume |
| Deploy | Vercel vs VPS | **Hostinger VPS + Docker** | Owner wants own infrastructure, NAS backups |
| Long-slots rule | same-room vs cross-room | **Cross-room (strict)** | Owner decision; prevent chain-booking |
| Dates format | พ.ศ. vs ค.ศ. | **ค.ศ. + Thai months** | Owner decision |
| Dashboard | login vs public | **Public read-only** | Owner decision; faster availability checking |
| Weekend | open vs closed | **Closed Sat–Sun** | Owner decision; recurring closures, staff-overridable |
| Staff count | 6 vs 10 | **10 (พี่ดิว + 9)** | Owner revision; audit log required |
