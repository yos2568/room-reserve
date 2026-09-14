# Room Reserve App — V3 Implementation and Acceptance Specification
## ระบบจองห้องซ้อมดนตรี

Western Music Department, Faculty of Fine and Applied Arts, Chulalongkorn University  
Version 3.0 — 15 September 2026  
Status: **Authoritative specification for implementation; application not yet built or verified.**

**Handoff:** Give DeepSeek this file and the companion prompt, then paste the complete prompt into its coding-agent session. Keep both files in the repository so interrupted runs can resume from the same requirements.

This document combines v2.0's same-day booking, walk-ins and rolling penalties with revised v1.2's transaction, security, testing and recovery requirements. It supersedes both earlier plans for the next build. Earlier files remain historical references, not competing instructions. “V3” is the specification version; the first application release may still be v1.0.0.

Use the companion [DeepSeek build-and-recheck prompt](deepseek-v3-build-loop.md) as the execution prompt. Build the complete local application against this document without waiting for production credentials. The concrete defaults below resolve the earlier contradictions for implementation; faculty must endorse the operational policy before public launch. No claim of flawless or production-ready software is justified by this plan alone.

## 0. Resolved decisions

| Topic | V3 decision |
|---|---|
| Advance booking | No minimum lead time; future slots up to seven days ahead |
| Current-hour use | Explicit “Use now” action creates an immediately checked-in booking for the remaining time |
| No-show room | Any eligible other student can use the remaining time; no second grace period |
| Quota | Two/day across rooms; scheduled, in-use, completed and no-show bookings count; cancelled bookings do not |
| Same/adjacent hour | No simultaneous rooms or adjacent slot hours for the same user |
| Check-in cutoff | Start inclusive, start +15 minutes exclusive; forfeiture begins exactly at the deadline |
| Strikes | Three eligible unconsumed strikes within a rolling 30-day window cause one seven-day suspension |
| Repeated suspension | A strike can contribute to only one automatic suspension; expiry does not retrigger it |
| Existing bookings on suspension | Cancel remaining scheduled bookings without extra penalty; allow an in-use session to finish |
| Registration | Self-registration enabled, but booking requires verified email and roster match or staff approval |
| Attendance | Static QR proves account action, not physical presence; disclose this limitation |
| Correctness | One common database control lock for short writes, plus database constraints and idempotency |
| Outages | Reconcile persisted status before mutations; scheduler downtime must not create a rule bypass |
| Completion | Local build pass, deployment verification and faculty pilot are separate evidenced gates |

## 1. Product scope and verified regulation

Nine upright-piano practice rooms; about 85–100 students for planning, with capacity tests using at least 100 synthetic users. Validate actual roster separately. Ten named operational staff accounts have identical operational permissions; only the technical maintainer receives superuser/deployment privileges.

Default opening hours: Monday–Friday 08:00–20:00, twelve fixed hourly slots. No scheduled classes are assumed, but staff may close rooms for holidays, maintenance or other authorized use. Thai default, English toggle; Gregorian years (ค.ศ.), with Thai month names in Thai UI. Public availability contains no student identities. All student actions require an account.

Contact: คุณสิตานันท์ (พี่ดิว), 02-218-4604, Sitanun.S@chula.ac.th.

The local one-page [room regulation](ระเบียบการจองและการใช้ห้องซ้อมดนตรี.pdf) was text-extracted and visually checked for this consolidation. The following is a paraphrased mapping, not a new regulation:

| Clause | Source requirement | App response |
|---|---|---|
| 1.1 | At most one hour per booking | Fixed hour slots; walk-in ends at the existing hour boundary |
| 1.2 | At most two bookings per person per day | Cross-room daily quota |
| 1.3 | Use at booked time and finish on time | Clear start/end; automatic completion; staff handles overstays |
| 1.4 | No booking for others or reserving without actual use | Ownership, same-hour exclusion, no-show process; staff handles credential sharing |
| 2.1–2.2 | Arrival within 15 minutes; forfeiture allows others to book/use | Exact deadline and remaining-time walk-in |
| 2.3 | Cancel promptly when unable to attend | Cancellation before start |
| 3.1–3.2 | Musical practice includes instruments, voice and ensemble; unrelated activities prohibited | Accurate rules copy; group-attendee tracking deferred, ensemble practice itself not prohibited |
| 4.1–4.4 | Clean up, return equipment, no unauthorized removal, report damage | Rules and clear staff reporting contact |
| 5.1–5.2 | No food; drinks allowed in securely covered containers with care | Do not incorrectly publish a blanket drinks ban |
| 6 | Respect others, avoid overstays/disturbance | Rules, staff intervention and audit |
| 7 | Warnings and possible suspension for repeat violations/damage | Reviewable sanctions; numeric thresholds are V3 operational defaults, not stated in the PDF |

Seven-day horizon, cross-room adjacency, quota status interpretation, exact cutoff convention and automatic sanction duration are application policy choices. Publish these separately from the regulation, in both languages, for faculty endorsement before launch.

## 2. Architecture and repository deliverables

Use Python 3.12, Django 5.2 LTS with a compatible current security patch, PostgreSQL 16 with a current minor patch, Django templates, django-htmx and locally compiled Tailwind CSS. Pin tested dependencies and image versions. Django 5.2 supports Python 3.12 and its extended support runs through April 2028. [Django release notes](https://docs.djangoproject.com/en/5.2/releases/5.2/) · [Support schedule](https://www.djangoproject.com/download/)

Gunicorn behind Caddy serves the app. Docker Compose services: `web`, `db`, `scheduler` using the same application image, and `caddy` for deployment. Local development adds a private mail catcher. PostgreSQL is not publicly exposed. No React SPA, Redis or Celery is needed initially.

All operational changes go through application services called by views, admin actions and jobs. Read-only query/presentation functions remain separate. Admin must not permit direct lifecycle, deadline, ownership or sanction-field editing that bypasses services.

Required repository outputs:

- Runnable application, migrations, translated templates, responsive UI, static assets and QR poster generator.
- `compose.yaml`, explicit development/production configuration, Dockerfile, pinned dependency files, `.env.example` without secrets, `.gitignore`.
- Idempotent demo seeding and validated roster/staff invitation commands; no fabricated production accounts.
- Unit/integration/PostgreSQL concurrency tests, real-browser end-to-end tests and CI.
- `README.md`, `docs/policy.md`, `docs/architecture.md`, `docs/runbook.md`, `docs/privacy-draft.md`, `docs/acceptance-matrix.md` and `docs/decisions.md`.
- `scripts/verify` (or a documented equivalent), `BUILD_STATUS.md`, `QA_REPORT.md`, ignored `artifacts/qa/` evidence and an explicit production launch checklist.

Use libraries only where they simplify maintained code. Optional audit/lockout packages do not substitute for the behavioral requirements below. Do not add unrequested services solely to make the stack look larger.

## 3. Time, calendar and booking semantics

### 3.1 Shared time rules

Store aware UTC timestamps; evaluate dates, slots and display in `Asia/Bangkok`. Obtain authoritative time after acquiring the operation lock; use that captured time consistently. A test clock may be injected only into test/development infrastructure, never a public production endpoint.

Each slot is `[slot_start, slot_end)` with an exact local-hour start, `slot_end = slot_start + 60 minutes`. Valid default starts are 08:00 through 19:00. At 20:00 the day is closed. Never trust client-provided user IDs, deadlines, status, duration, prices or eligibility. Slot identity is always room plus original hourly start, even for late walk-ins.

Effective calendar precedence: explicit room/all-room closure wins; then a date override; then the weekly timetable. Intersecting any part of an hourly slot with a closure blocks new reservations/walk-ins for that slot. This intentionally avoids partial availability within a closed slot. An opening override never defeats an explicit closure. Use finite closure intervals in V3; staff may extend them.

### 3.2 Future reservation versus use now

| Action | Time predicate | Initial state and deadline |
|---|---|---|
| Reserve future slot | `now < slot_start <= now + 7 days` | SCHEDULED; check-in deadline `slot_start + 15m` |
| Check in own reservation | `slot_start <= now < deadline` | Existing SCHEDULED becomes IN_USE |
| Use free current slot | `slot_start <= now < slot_end` | New WALK_IN booking already IN_USE; no check-in deadline |

There is no 24-hour minimum. Advance booking excludes the exact start instant; at that instant use the explicit current-slot action. If a future-booking confirmation becomes stale across the hour boundary, reject it with a refreshed “Use now” confirmation. Never silently turn an old future POST into an attendance declaration.

The public grid links a current free slot to its room page. Both grid and QR lead to the same explicit “I am at this room / ยืนยันว่าฉันอยู่ที่ห้องนี้” use-now POST. The app accepts that declaration but cannot prove it with a static URL.

Walk-in applies to never-booked rooms, ordinarily cancelled reservations and no-show releases alike. A reservation held by another user inside grace is unavailable. Once grace expires, reconcile it under lock and allow an eligible other user to claim the remaining time. The original no-show owner cannot reuse that room/hour; another room still requires quota and overlap checks. No-show records retain their charge and strike.

All walk-ins end at the original `slot_end`; no extra hour or renewed grace. Show actual remaining minutes, including a clear warning below five minutes, and require explicit confirmation. At end, reject the elapsed slot and show the next slot as a separate decision. There is no early self-checkout or re-selling of the same slot in V3.

### 3.3 Quota and adjacency

Count by Bangkok slot date across all rooms, after reconciling due state:

| Status | Daily quota charge | Blocks same/adjacent hourly slots across rooms |
|---|---|---|
| SCHEDULED | 1 | Yes |
| IN_USE | 1 | Yes |
| COMPLETED | 1 | Yes |
| NO_SHOW | 1 | No; however original room/hour cannot be reclaimed by its no-show owner |
| CANCELLED | 0 | No |

Creating any booking requires count `< 2`. Any blocking booking at `slot_start` or `slot_start ± 60m` rejects creation. A walk-in at 08:45 still belongs to the 08:00 slot and blocks 09:00, even though its actual use is shorter. Completed sessions must remain in the count for the date. Administrative termination of an in-use session retains its charge unless a separate audited faculty correction explicitly grants an exception; such quota exceptions are deferred from the initial app.

### 3.4 Cancellation

Students cancel only their own SCHEDULED booking while `now < slot_start`. Record cancellation actor/time/reason; cancelling less than 60 minutes ahead sets a reporting-only late-cancel flag. Cancellation restores quota and makes the slot available under the ordinary future/use-now rules. Rate-limit repeated create/cancel requests without inventing extra penalties. There is no student cancellation after start; staff resolves service incidents through the incident workflow.

### 3.5 Examples that must be executable tests

- At 10:40 reserve 11:00 today; check-in opens 11:00 and closes exactly 11:15.
- At 11:00 a free room can be used now until 12:00; a stale advance POST requires reconfirmation.
- At 11:15 an absent reservation forfeits. Another eligible user enters at 11:22 and finishes at 12:00.
- At 11:45 a never-booked or previously cancelled room also supports use-now until 12:00.
- One completed 08:00 session plus one no-show 10:00 session means no third booking that date.
- At 19:59 a walk-in ends at 20:00 with a short-time warning. At 20:00 it is rejected.

## 4. Data model and constraints

Use a custom `User(AbstractUser)` in the first migration. Store institutional IDs as strings. Do not infer an ID from an email local part or assume a length until roster validation establishes it.

| Entity | Required data / invariant |
|---|---|
| User | Unique institutional login ID, normalized verified email, Thai/optional English name, locale, active flag, verification state, eligibility PENDING/APPROVED/REJECTED, approval actor/time, rules version acknowledged |
| EligibleStudent | Unique institutional ID and approved email pair, active eligibility, minimal roster metadata; imports cannot overwrite a different account silently |
| Room | Unique number/label, active flag; temporary unavailability is a Closure |
| BookingControl | Singleton pre-seeded row, shared first lock for operational writes |
| Booking | User, room, immutable slot_start/end, nullable deadline, status, source ADVANCE/WALK_IN, created/checked-in/cancelled timestamps, actor/reasons, late_cancel, actual_end nullable, policy version, optional original no-show booking FK |
| Violation | User, optional booking, NO_SHOW/MANUAL, occurred_at, expires_at, recorded_at, counts_as_strike, actor/note, voided_at/by/reason, optional consumed_by suspension FK |
| Suspension | User, starts_at, required ends_at for automatic sanctions, optional null end for explicit staff indefinite suspension, lifted_at/by, reason, source AUTO/MANUAL, notification markers |
| Closure | Optional room (null = all), starts_at/ends_at, reason, creator, revoked_at; finite end > start |
| CalendarOverride | Unique local date, open/closed flag, hours if open, reason/actor |
| ServiceIncident | Affected interval/rooms, reason, creator, linked corrected bookings/voided violations and notifications |
| PolicyVersion | Immutable validated settings snapshot and activation time; original slot geometry remains fixed |
| AuditEvent | Actor or job, action, entity, time, request/correlation ID, safe changes and reason; append-only through app permissions |
| Notification | Kind, recipient, payload, unique event dedupe key, due/next-attempt time, lease, attempts, status, sent_at, sanitized error |
| OperationRequest | Authenticated user + operation + key unique, payload fingerprint and successful result reference |
| Invitation | User, token digest/generation, purpose, expiry/used/revoked times; no plaintext token logging |

Database constraints include:

- Unique `(room, slot_start)` and `(user, slot_start)` for SCHEDULED, IN_USE, COMPLETED; completed sessions reserve the historical slot.
- Slot length exactly one hour, exact Bangkok-hour alignment, valid statuses/sources, and consistent timestamps: ADVANCE has a deadline; WALK_IN is never SCHEDULED; check-in is within the slot; cancellation requires cancellation metadata.
- A no-show violation is unique per booking. `consumed_by` associates a violation with at most one sanction. Automatic suspension end is later than start.
- Index user/date lookup, status/deadline, unconsumed violations by user/expiry, closure bounds, active sanctions and due notifications. Prevent multiple concurrently active suspension records in the service under the common lock; staff edits the active sanction instead of creating an overlapping one.
- PROTECT/history-preserving relationships; account retirement never cascade-deletes bookings or sanctions. Privacy cleanup is an explicit audited operation.

The unique constraints alone cannot enforce quota/adjacency; all mutation paths must honor §5. Current-slot availability does not require a SlotRelease table because walk-ins are allowed on any free current slot. Optional original-no-show linkage preserves release provenance.

## 5. Transaction protocol and idempotency

Use one database BookingControl row as the first lock for every operational write, including staff, scheduler, closure, eligibility and policy operations. This serializes short writes at this scale and avoids mismatched lock orders. Reads remain unlocked. Measure contention before replacing it with finer-grained locks.

```text
BEGIN transaction
  lock BookingControl with bounded wait
  capture authoritative now
  reconcile all due booking states, violations, sanction expiry and threshold effects
  check authenticated user's idempotency key and payload
  if successful prior operation matches: return its result without repeating effects
  validate requested operation against reconciled state
  if validation rejects:
      record/return a clean rejection result; COMMIT reconciliation
  else:
      apply requested change inside a savepoint
      write audit and outbox events and successful operation result
      on expected uniqueness conflict: roll back that savepoint; return conflict
COMMIT transaction
```

**Critical:** a normal booking rejection must not roll back the due no-show or suspension work that made the rejection correct. Do not raise a validation exception out of the outer transaction after reconciliation. Unexpected failures roll back atomically and return an error without claiming success.

Reconcile overdue bookings in deterministic deadline/ID order, then evaluate sanctions using their recorded occurrence times. Process the small installation's due records before accepting a new booking; if a backlog is too large to reconcile within the configured request budget, fail closed with a temporary busy response and let the job catch up. Never skip sanctions or stale rows to improve speed.

Network/SMTP calls, poster generation and long exports occur outside the control lock. Return translated conflict/retry messages on bounded lock timeout. Use backend-aware retry rules for transient serialization/deadlock errors only, with a small limit; never blindly retry a non-idempotent external action.

Every state-changing form uses CSRF and POST; key ownership and payload fingerprints prevent a replay from exposing another student's booking or changing the requested room. Repeated successful check-in, booking and cancellation returns the recorded result, including if its current status has since changed. It never resurrects a booking or reruns quota consumption.

## 6. Lifecycle, reconciliation and scheduler

| Transition | Predicate | Atomic effects |
|---|---|---|
| New advance → SCHEDULED | §3 future rule and all eligibility checks | Booking, confirmation outbox, audit |
| SCHEDULED → IN_USE | Owner/staff-assisted, correct room, `start <= now < deadline`, eligible | Check-in and audit |
| New walk-in → IN_USE | Current slot free after reconciliation; all checks | Booking, check-in, provenance if applicable, confirmation, audit |
| SCHEDULED → CANCELLED | Student before start; or authorized staff action | Reason, audit, cancellation notification, no strike |
| SCHEDULED → NO_SHOW | `now >= deadline` and no incident exemption | One violation, audit, no-show notice |
| IN_USE → COMPLETED | `now >= slot_end` | Completion, actual_end = slot_end |
| IN_USE → COMPLETED early | Staff-confirmed evacuation/incident only | actual_end = now and required reason; historical slot stays occupied |

No ordinary transition out of a terminal status. Staff voids penalties or appends corrections; corrections never erase the original history or silently overwrite a replacement occupant.

Read-only availability can derive expired holds as free and elapsed use as completed. GET does not write. The next mutation reconciles the persisted rows, including the unique-index state, before inserting. Public availability is a snapshot, never a reservation guarantee. Refresh every 30 seconds and after mutation, preserve focus, and recheck on tab resume. Keep personal pages uncached; omit fragment caching initially to avoid unnecessary invalidation complexity.

Run a `tick` command every minute from the application-image scheduler: lifecycle reconciliation, due reminder enqueue, outbox drain, heartbeat. A non-blocking advisory lock (`pg_try_advisory_lock` with reliable release) skips overlapping ticks; a blocking advisory lock does not. Outbox work is bounded and outside the control transaction. Recovery of expired leases allows safe restart.

Scheduler outage delays background persistence and notifications; request reconciliation maintains correctness. It does not prove that mail or staff alerts arrived. Monitor heartbeat older than three minutes and oldest actionable outbox item older than ten minutes.

## 7. Rolling strikes, sanctions and service incidents

An eligible strike is non-voided, counts_as_strike, unconsumed, and `occurred_at <= now < expires_at`. For a no-show, occurrence is its check-in deadline, not the delayed job run. Freeze expiry at occurrence +30 days according to the policy version; later policy edits do not retrospectively lengthen it.

After reconciliation or a staff violation change, if an unsuspended user has at least three eligible strikes, create one seven-day suspension starting at reconciliation time. Link **all currently eligible strikes** to that suspension as consumed; no strike can trigger a second automatic suspension. Keep total historical violations distinct from “strikes toward the next suspension” in the UI.

While already suspended, do not automatically extend or stack sanctions. Staff can review new manual violations explicitly; newly eligible unconsumed strikes remain visible and are evaluated after expiry. Automatic sanction application cancels every remaining SCHEDULED booking, including future bookings beyond the seven-day suspension, with reason SUSPENSION and no additional penalties. Already IN_USE may finish. Students may still log in, view history and request help, but cannot reserve, check in or walk in while suspended or ineligible.

Suspension applies in `[starts_at, ends_at)` unless lifted earlier. Timestamp comparison restores eligibility at end without cron; reconciliation sends a single lift notice. Staff can shorten, extend or lift with reasons. Consumed strikes remain consumed even after an early lift. Voiding a consumed strike opens a review of the linked sanction; staff must explicitly decide whether to lift it. Explain this in the appeal UI.

All automatic numerical penalties are deployment defaults requiring faculty endorsement; do not claim the PDF mandates them.

For service failure, staff records a ServiceIncident interval/room scope. Before release/no-show processing, matching scheduled bookings can be staff-cancelled without penalty, with audit and notices. If no-shows were already recorded, staff voids affected strikes and reviews sanctions; do not silently backdate attendance. A documented outage runbook pauses new bookings if necessary, preserves current database records, and reconciles staff's temporary room-use log before reopening. Tests must distinguish expected scheduler delay from a confirmed service incident.

## 8. Identity, permissions and staff workflows

Self-registration is enabled for the pilot configuration: institutional ID + institutional email + name → expiring verification → password → eligibility. Domain defaults to `student.chula.ac.th`, configurable after validation. Email ownership alone does not establish department membership.

An exact ID/email match against an active approved roster entry may approve automatically. Otherwise create PENDING eligibility, visible to staff; pending students can log in and see approval status but cannot reserve/check in/use-now. Staff approval requires a recorded reason. Duplicate/contradictory ID/email submissions cannot take over an existing account. Changing email invalidates verification and requires a new eligibility check. Do not expose roster membership through public error messages.

CSV imports support dry-run, atomic validation, duplicate detection and idempotent application. Import minimal fields; instrument/year optional. Invitations use individual single-use links; resend increments a generation and invalidates older links. Re-running seeds never resets credentials or grants privilege. Staff identities are supplied by the owner; development uses clearly synthetic accounts with local-only credential instructions.

Staff operational group: today's bookings with names, roster invitations/approval/deactivation, assisted check-in, closures, warnings/violations, sanctions/appeals, audit review and CSV stats. Staff cannot grant superuser, modify secrets or bypass booking rules. Assisted check-in requires actual attendance verification, matching room/deadline and an audit reason. No shared staff login and no production role-switching demo feature.

Closure flow: preview affected sessions → confirm under shared lock and recheck preview changes → cancel scheduled bookings with notices; show IN_USE conflicts prominently. For immediate closure during use, require explicit acknowledgement and record a pending on-site intervention; confirmed evacuation completes the session early with reason and retains historical slot occupancy. Reopening does not restore bookings or make a completed slot reusable. Deactivation similarly cancels scheduled bookings without sanctions and flags any in-use session for staff review.

Policy edits create validated versions. Fixed hourly geometry is not staff-editable. Existing bookings retain stored slot/deadline values; changes to quota/horizon/grace apply to new reservations and do not retroactively invalidate an existing permitted check-in. Calendar invalidations require the explicit closure workflow. Changes to strike rules affect newly recorded violations; existing expiry/consumption remains intact.

## 9. Usable bilingual screens and QR flow

| Route | Required behavior |
|---|---|
| `/` | Public date/room grid and mobile list; future “Reserve”, current “Use now”, held/in-use/closed/past states; no personal fields in HTML/JSON |
| `/register/`, `/verify/`, `/invite/` | Verification and activation; pending approval messaging; safe resend |
| `/login/`, `/password-reset/` | ID/password, safe local return path, generic reset responses |
| `/book/<room>/<slot>/` | Future confirmation; POST revalidation; stale-hour reconfirmation |
| `/my-bookings/` | Upcoming actions, 60-day displayed history, remaining quota, strike expiry and sanction/appeal information |
| `/r/<room>/` | Stable printed QR URL; explicit check-in or use-now, correct room/end/remaining time |
| `/staff/today/` | Named schedule, pending approvals, active sanctions, incidents, closure actions, failed mail and job status |
| `/staff/admin/` | Permission-limited configuration/records; dangerous raw edits disabled |
| `/rules/`, `/help/`, `/privacy/` | Accurate bilingual content and contact; privacy notice marked draft until faculty review |
| `/healthz/`, `/readyz/` | Minimal process liveness and app/DB readiness; operational details restricted |

Create nine printable A4 QR posters with room label, short URL, instructions and support contact. All links remain GET until the user confirms a POST. Validate generated QR targets and map to physical room doors during deployment.

Show early/late/wrong-room/full-quota/suspended/closed/network-error states in both languages. A stale successful POST retry shows its existing result. Never tell a late user that the room is definitely still free. Static QR and IP/user-agent do not establish physical attendance; do not collect these solely as purported attendance proof. Staff spot checks address this pilot limitation.

At 360px width provide a readable list without page-wide horizontal overflow; desktop can show the full 9×12 grid. Target comfortable 44px touch controls, keyboard reachability, visible focus, labels and text beyond color. Use accessible announcements for result messages. HTMX refresh preserves active focus; basic forms work without JavaScript. Show loading/disabled-submit feedback but always rely on server idempotency. Do not display a static mockup as evidence of usability.

## 10. Notifications, privacy and security

Transactional outbox events: verification/invitation generations, reset, booking confirmation, reminder, cancellation, no-show, sanction applied/lifted and eligibility decision. Weekly digest is deferred. Send in recipient language with Bangkok date/time and contact. Booking success is the committed record, independent of SMTP.

Insert events in the state-change transaction. Worker leases due rows, sends outside locks, retries with backoff (for example 1/5/30/60 minutes, then an exhausted state after six attempts), and exposes failure to staff. Unique dedupe keys prevent repeated enqueue; SMTP acceptance plus worker crash can still produce duplicate delivery. Do not promise exactly-once email. Stable message IDs/provider idempotency may help where supported.

Reminder eligibility: ADVANCE still SCHEDULED and `start -30m <= now < start`, with no prior reminder event. Near-term reservations may get confirmation plus immediate reminder; walk-ins never get a reminder. Revalidate before sending; a concurrent cancellation can still race an external provider send, so links must resolve current state. Recover missed ticks; do not send old reminders after start. Expired verification generations must not be sent by retries.

Use a local mail catcher for development, real SMTP only when configured and authorized for the intended recipients. Verify actual mailbox entitlement and limits; VPS purchase does not establish free SMTP. [Hostinger inclusion guidance](https://support.hostinger.com/en/articles/5832752-how-to-check-the-email-service-included-in-your-hosting-plan)

Use 600 messages/day as an initial planning envelope, not a mathematical ceiling: repeated booking/cancellation, invites and retries can exceed it. Measure actual volume and throttle delivery to provider limits without dropping transactional events. Confirm deliverability and sender-domain configuration before launch.

Security requirements: CSRF; escaped templates; secure/HttpOnly cookies and HTTPS in production; `DEBUG=False`; validated hosts/origins and redirects; authentication/reset/registration rate limits that do not unfairly lock a whole campus behind one IP; password validation; no plaintext passwords/tokens in logs/Git; least privilege for staff; no personal data in public cache or error responses; formula-safe CSV exports; sanitized logs and dependency vulnerability review. Production must fail clearly on missing mandatory secrets, not use demo defaults.

Privacy notice and rules acknowledgement are separate. A checkbox is not a claim of legal compliance. Faculty must approve data-handling basis, notice and retention. Build minimal data collection, deactivation, authorized export/correction and a retention dry-run/report. Proposed identifiable retention is one academic year plus documented dispute holds; backup retention 30 days. Do not automatically delete records on this unendorsed retention default. A 60-day UI history is not deletion. Document deletion/anonymization and restoring old backups so cleanup can be reapplied before reopening. Keep test data synthetic.

## 11. Deployment, recovery, cost and operating ownership

Development and production share app image and database engine, with explicit differences for debug, secrets, HTTPS, mail and exposed ports. Pin build outputs. Run migrations once as a release step, not on each worker startup. Provide one documented local bootstrap command that starts DB/mail, applies migrations, seeds synthetic demo accounts and serves a usable app; fail visibly if a prerequisite is missing.

CI: lint/format, Django checks, migration drift check, PostgreSQL tests, browser tests and Docker build. Build immutable images on release tags. Deployment procedure: backup → migration compatibility check → one migration run → image rollout → health and real-flow checks. Rollback to a previous image is allowed only with compatible schema; otherwise execute a tested migration/restore plan. No zero-downtime guarantee on a single VPS.

Nightly encrypted custom-format `pg_dump` to independently accessible NAS/storage, proposed 30-day retention. Monitor backup age and disk separately from HTTP uptime. Demonstrate `pg_restore` in a clean database and prove bookings, users and pending work are restored. Initial recovery targets: RPO ≤24h and RTO ≤4h during staffed support, to be accepted by owner. On restore, pause outbound mail and booking, reconcile staff/offline records, expire stale work and review incidents before reopening; do not blindly resend old emails or auto-penalize outage victims.

External uptime checks, resource/disk/log rotation, scheduler heartbeat and outbox-age alerts need named maintainer and backup contacts. Detailed diagnostics are authenticated. A failed mail provider must not make process liveness fail and trigger restart loops. Document patching, restart, rollback, restore, lost staff access, email outage, disputed check-in and emergency closure procedures.

Budget is quote-based: VPS annual/renewal/prepayment, domain, SMTP allowance, backup storage, monitoring/CI allowances, nine posters, maintenance time and contingency. Record currency/tax/renewal separately. Do not retain unverified promo or free-tier claims from earlier plans. Faculty owns policy, roster, appeals and room operations; maintainer owns releases, patching, alerts and recovery.

## 12. Acceptance matrix — mandatory evidence

Each ID below must map to real test files or a named manual checklist with evidence in `docs/acceptance-matrix.md`. Timing tests use a controlled clock; concurrency tests use independent PostgreSQL connections/processes and synchronization barriers, not sequential loops. Fixed-time browser fixtures live only in an isolated test app.

| ID | Required case and expected result |
|---|---|
| A01 | Clean bootstrap from documented prerequisites yields accessible app, nine rooms, synthetic student/staff and working local mail flow |
| A02 | Same-day future booking succeeds; exactly seven days ahead succeeds; beyond fails; off-hour/closed-hour/weekend/holiday inputs rejected |
| A03 | Exact hour: use-now works; stale advance POST asks for confirmation and never silently checks in |
| A04 | Before start check-in fails; at start succeeds; deadline minus a small delta succeeds; at deadline fails |
| A05 | Never-booked, cancelled and no-show current slots all allow eligible use-now after reconciliation; fixed original end retained |
| A06 | Two users race for one remaining-time slot: one success; no second grace; original no-show user cannot retake same room/hour |
| A07 | Daily quota includes COMPLETED/NO_SHOW, cancellation restores it, Bangkok midnight separates dates |
| A08 | Same/adjacent hourly bookings across rooms rejected; two-hour separation allowed if quota remains; short walk-in still blocks next hour |
| A09 | Twenty eligible users simultaneously request one future slot: exactly one persisted success, nineteen clean conflicts |
| A10 | Same user concurrently requests several nonadjacent slots with one allowance left: exactly one success |
| A11 | Same key retry returns same successful result; changed payload rejected; another user cannot retrieve/replay result |
| A12 | Check-in races no-show reconciliation: one legal result; never check-in plus no-show strike |
| A13 | Scheduler stopped: next mutation clears expired database hold, creates one strike and enforces any resulting sanction |
| A14 | Rejected mutation still commits legitimate reconciliation; rollback of an unexpected transaction failure sends no email |
| A15 | Three unconsumed strikes in 30 days cause one seven-day sanction; exact expiry excludes a strike; repeat ticks do not extend/recreate sanctions |
| A16 | Consumed strikes cannot resuspend on expiry/lift; new unconsumed strikes and manual reviews follow §7; voiding opens review |
| A17 | Suspension cancels scheduled bookings without extra penalties, preserves in-use completion, permits history/help but blocks new use/check-in |
| A18 | Closure versus creation race has no unhandled surviving conflict; current use flagged, evacuation audited; reopening does not resurrect history |
| A19 | Date overrides obey closure precedence; changed future grace does not alter stored deadlines; no raw admin bypass |
| A20 | Registration requires verified ID/email roster match or staff approval; pending/inactive users blocked; account collision and resend-token replay rejected |
| A21 | Staff cannot become superuser; students cannot access others' bookings/exports or staff mutations; CSRF and redirect attacks rejected |
| A22 | Outbox rollback, retry, expired lease recovery and duplicate enqueue tested; stale reminders/generations suppressed; SMTP ambiguity documented |
| A23 | Real browser: register/verify/approve/login → reserve → cancel → reserve → door check-in → history, with fixture dates respecting quota |
| A24 | Real browser: current-slot walk-in after :15; wrong-room/too-late/full/suspended/closed feedback; staff closure and appeal workflows |
| A25 | Thai/English routes and email copy, Gregorian years, 360px mobile and desktop layouts, keyboard, JS-disabled forms, slow network and stale refresh |
| A26 | Public HTML/JSON/log inspection finds no student identities/secrets; personal responses not publicly cached; CSV formula payload safe |
| A27 | Production config checks, immutable image build, migration drift, restart persistence, clean backup/restore and rollback rehearsal |
| A28 | Tick overlap skips; job/mail outage visible without liveness restart loop; documented incident exempts/corrects affected penalties |
| A29 | Nine posters render legibly and QR URLs resolve correct rooms; deployment physical door mapping is a separate manual gate |
| A30 | Repeat concurrency stress at least 20 rounds with fresh fixtures; zero invariant breaches or intermittent errors; report p95 and hardware |

Performance target: p95 mutation response under two seconds with 20 concurrent attempts on intended deployment hardware, excluding email. Local measurements are evidence only for local hardware; deployment performance remains unverified until measured. No arbitrary test-count or coverage percentage substitutes for A01–A30.

## 13. Delivery phases and definition of done

| Phase | Deliverable | Gate |
|---|---|---|
| P0 | Repository inspection, environment, schema/services, policy matrix and synthetic fixtures | V3 decisions represented; bootstrap and CI foundation |
| P1 | Identity, public availability, future booking, quota and cancellation | Relevant A01–A03, A07–A11, A20–A21 pass |
| P2 | Check-in, walk-in, lifecycle, strikes, outbox | A04–A06, A12–A17, A22 pass |
| P3 | Staff operations, bilingual usable UI, posters, incidents | A18–A26, A28–A29 local evidence |
| P4 | Full integration, Docker, recovery, documented verification runner | All locally executable A01–A30 pass on final code |
| P5 | Real hosting/mail/physical posters and two-week faculty pilot | Deployment evidence, real staff usability and launch endorsement |

Implement phases continuously; do not stop merely to ask permission at every phase. Phase sequencing can adapt to dependencies, but completion gates remain. Estimate implementation separately from the two-week pilot; a coding agent cannot fabricate elapsed real-world pilot evidence.

**LOCAL_PASS:** complete scope works in local Docker with PostgreSQL and mail catcher; all local acceptance tests pass; no missing core routes, stubs, fake persistence, unhandled browser/server errors or unresolved critical/high findings; fresh bootstrap and restore verified; documented URL/demo access, evidence and startup steps exist. Local test credentials never grant access to production. Verified app can be demonstrated through student and staff workflows.

**DEPLOYMENT_VERIFIED:** LOCAL_PASS plus actual host HTTPS/domain, SMTP delivery to authorized test accounts, backup destination, restore/rollback, external alerts, deployment performance and physical poster checks. Missing access means BLOCKED/NOT RUN for those checks, not PASS.

**PILOT_ACCEPTED:** deployment verified plus two weeks with about ten approved students, actual staff task completion, incident review, accurate rules/privacy/calendar/roster and faculty signoff. Only then claim ready for faculty production use.

Do not weaken a requirement or mark a skipped test passing to reach a label. Low-impact residual issues must be explicit; core usability and data-integrity failures prevent LOCAL_PASS.

## 14. Build, inspect, repair and recheck loop

The exact paste-ready agent instructions are in `deepseek-v3-build-loop.md`. The required behavior is:

```text
inspect repository and tools → implement next complete slice
→ run relevant automated checks → exercise actual UI
→ inspect failing output and data invariants → reproduce and fix cause
→ add regression test → rerun affected checks
→ run complete verification at integration/release gates
→ adversarial review against A01–A30 → fix and repeat as needed
→ clean bootstrap + full final checks on unchanged code → report evidenced status
```

Stop the successful loop only when LOCAL_PASS is demonstrated; then complete authorized deployment checks if credentials/environment are available. If a fix changes code after a full pass, repeat affected tests and full final verification. Once all required checks pass and the fresh-start check passes on the same revision, stop rechecking unchanged code indefinitely.

No infinite blind retry: after three attempts at the same unresolved symptom, record attempted hypotheses and switch to diagnosis (logs, minimal reproduction, dependency/environment inspection). Continue independent work. If the remaining condition requires unavailable external input or permission, report the exact blocker and evidence honestly. API/context limits require a durable checkpoint, not a fabricated success. A prompt cannot bypass provider limits or keep a disconnected agent running; resume with the companion continuation prompt.

## 15. Faculty launch checklist and deferred scope

Before public use: approve the operational policy beyond the PDF; supply eligible roster and ten staff identities; confirm room labels/calendar, contact, domain/mail entitlement, privacy/retention wording, static QR limitation, hosting budget, recovery owners/targets and pilot results. These are launch dependencies, not blockers to a complete local build with synthetic data.

Deferred: multi-attendee/group-accounting UI, waitlists, LINE, SSO/OAuth, rotating on-site codes, native mobile apps, payments, equipment checkout and early self-checkout. Ensemble use remains allowed by the source rules; one named owner is responsible for the booking. Do not implement extras while core acceptance cases fail.

The intended result is a small, working faculty application with measured correctness and honest operational limits. All claims of completion must refer to evidence from the implemented app, never to the thoroughness of this specification.
