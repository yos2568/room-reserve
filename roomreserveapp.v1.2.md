# Room Reserve App — Revised Implementation Plan
## ระบบจองห้องซ้อมดนตรี (Music Practice Room Booking System)

สาขาวิชาดุริยางคศิลป์ตะวันตก คณะศิลปกรรมศาสตร์ จุฬาลงกรณ์มหาวิทยาลัย
Western Music Department, Faculty of Fine and Applied Arts, Chulalongkorn University

Revision 1.2-R1 — 14 September 2026  
Status: **Technical plan revised; policy proposals require reconciliation before production.**

This revision preserves the v1.2 baseline: nine rooms, ten operational staff accounts, Thai-first bilingual UI, ID/password login, public availability, Monday–Friday 08:00–20:00, one-hour slots, two bookings/day, no adjacent hours across rooms, and bookings 24 hours to seven days ahead. It clarifies implementation gaps without declaring new policy approved.

**Document relationship:** `roomreserveapp.md` already identifies itself as v2.0 and records different decisions: no minimum lead time, general QR walk-ins, rolling strikes, and self-registration. Do not combine these documents implicitly. This file is a reviewed v1.2 alternative, not a replacement for v2.0. Phase 0 must select one authoritative policy. The untouched input is archived at `outputs/plan-review/roomreserveapp.v1.2.original.md`.

## 0. Review findings and revision summary

| Priority | Finding in v1.2 | Revision |
|---|---|---|
| Critical | Active-only quota lets completed sessions disappear from the daily count; simultaneous bookings in different rooms are allowed | Explicit quota statuses and same-hour exclusion |
| Critical | Slot uniqueness does not protect a user's quota or adjacency across concurrent requests | Shared locking protocol for all writes, plus database constraints |
| Critical | Rebooking an expired slot creates an immediately overdue booking | Separate released-slot claim: immediate check-in for remaining time only |
| Critical | `suspended_until = null` cannot represent pending, indefinite suspension | Explicit suspension record with nullable end and separate lifting event |
| High | Five-minute cron conflicts with release at exactly 15 minutes | Deadline-based display plus transactional reconciliation before writes |
| High | Closures lack room scope and a policy for existing bookings | Dated closures with preview, atomic application and notifications |
| High | Admin history misses student and job actions | Application audit events for every mutation |
| High | Static QR described as sufficient protection against remote attendance | Explicit attendance limitation and staff dispute process |
| High | Default credentials and email-domain-only registration weaken roster control | Individual activation invitations; roster approval for eligibility |
| Medium | SMTP entitlement, volume and costs treated as verified | Provider checks and itemized quote-based budget |
| Medium | “Ready to build” and a one-week phase containing a two-week pilot | Policy gate, measured acceptance criteria and separate pilot |

## 1. Scope and authority

| Item | Baseline |
|---|---|
| Users | Approximately 100 for capacity planning; reconcile actual eligible roster before launch |
| Rooms | Nine practice rooms with upright pianos; confirm physical room labels for posters |
| Operational staff | Ten named accounts, identical room-operation permissions |
| Technical administration | Restricted deployment/superuser access for the maintainer; not granted to all staff |
| Contact | พี่ดิว — Sitanun.S@chula.ac.th, 02-218-4604; verify before publishing |
| Availability | Public, showing room/time/status only; names, IDs and booking identifiers remain private |
| UI | Thai default, English toggle, Gregorian year (ค.ศ.), Thai month names in Thai UI |
| Hosting | Django application and PostgreSQL on Hostinger VPS using Docker Compose |

### Regulation traceability

The clause summaries below are inherited from v1.2, **not independently verified against the local regulation PDF in this review**. Staff must verify the wording and publish the approved bilingual rules before launch. Software cannot itself ensure that a student leaves on time or uses a room appropriately.

| Clause cited in v1.2 | Intended implementation |
|---|---|
| 1.1: maximum one hour | Fixed hour boundaries; released-slot use ends at the original hour end |
| 1.2: maximum two bookings/day | Transactional daily count with explicit status semantics (§3) |
| 1.3: finish on time | Show fixed end time and next slot; staff handles overstays |
| 1.4: no proxy booking/hoarding | Authenticated ownership, quotas, overlap/adjacency rules; staff handles credential sharing |
| 2.1–2.2: 15-minute arrival and forfeiture | Deadline check, no-show event and controlled claim of released time |
| 2.3: cancel in advance | Cancellation before scheduled start |
| 3–6: permitted use, care, conduct | Rules acknowledgement plus staff enforcement |
| 7: warnings/suspension | Recorded violations and reviewable suspension workflow |

## 2. Technology and application boundaries

- Python 3.12; **Django 5.2 LTS**, using the latest compatible security patch at implementation; PostgreSQL 16 with current minor patches. Pin dependencies and container versions, and schedule updates. Django 5.2 supports Python 3.12 and receives extended support through April 2028. [Django release notes](https://docs.djangoproject.com/en/5.2/releases/5.2/) · [Support schedule](https://www.djangoproject.com/download/)
- Django templates, django-htmx and locally built Tailwind CSS. No separate SPA is required.
- Gunicorn behind Caddy for HTTPS. PostgreSQL is accessible only on the private container network.
- A scheduler container uses **the same application image and configuration** as web, with Python and management commands present. Run jobs every minute; Redis/Celery are unnecessary for the initial workload.
- All booking, check-in, cancellation, closure, violation and suspension changes use application services. Views, staff actions and jobs call those services; raw admin edits to lifecycle fields are disabled.
- Email uses a transactional outbox and a verified SMTP provider. Do not assume email is bundled with the VPS: Hostinger's inclusion guidance describes Web/Cloud hosting; confirm the actual account entitlement and sending limits. [Hostinger email inclusion guidance](https://support.hostinger.com/en/articles/5832752-how-to-check-the-email-service-included-in-your-hosting-plan)
- Django admin handles roster/configuration and history. A small `/staff/today/` page handles current sessions, no-shows, closures and pending suspension reviews.

This stack fits one maintainer and a small faculty deployment. Earlier claims that alternatives necessarily require two apps or would take longer to customize were not established by a comparison and are removed.

## 3. Precise booking policy

All policy changes below marked **proposed** are implementation defaults for review, not newly approved owner decisions.

### Time and eligibility

- Store timezone-aware timestamps in UTC; evaluate days, opening hours and labels in `Asia/Bangkok`. Use one server/database time captured **after acquiring locks** for each operation; never trust a browser clock.
- Twelve slots per open date: 08:00–09:00 through 19:00–20:00. `slot_start` is an exact hour; `slot_end = slot_start + 60 minutes` and is stored or derived immutably.
- Ordinary booking is valid when `now + 24h <= slot_start <= now + 7 days`, inclusively. The date picker exposes every date containing an eligible slot, even when that requires today plus seven date labels.
- Reject inactive/unactivated accounts, active suspensions, closed rooms, closed dates and invalid slot boundaries on the server.
- The weekly timetable sets Monday–Friday open and weekends closed. Date-specific opening overrides take precedence over that timetable; explicit room/all-room closures take precedence over opening overrides.

### Quota, overlapping and adjacent bookings — proposed clarification

| Status | Counts toward two/day | Blocks same/adjacent hour for that user |
|---|---|---|
| SCHEDULED | Yes | Yes |
| IN_USE | Yes | Yes |
| COMPLETED | Yes | Yes |
| NO_SHOW | Yes, to prevent forfeiture/rebooking from bypassing the daily limit | No |
| CANCELLED | No | No |

Count by the booking's Bangkok slot date, across all rooms. A released-slot claim counts as one booking. Reject any existing blocking booking at the same hour or ±1 hour, across all rooms. Cancellation before start restores quota. Staff cancellations due to closure restore quota and never generate a strike. Staff cannot silently bypass these rules through admin.

### No-show release and remaining-time claim — proposed clarification

Normal check-in is allowed in the half-open interval `[slot_start, slot_start + 15 minutes)`. At exactly the deadline the booking forfeits. Display that boundary clearly; this resolves the original inclusive/exclusive ambiguity.

A same-day exemption exists **only** for a slot with a persisted no-show release event. Before processing a claim, the service reconciles overdue bookings transactionally so an expired reservation cannot remain protected by a stale unique index.

- The door page offers **ใช้เวลาที่เหลือ / Use remaining time**, showing the fixed end and minutes remaining.
- Claiming requires login, a distinct eligible user, quota/overlap/adjacency checks, no closure and `deadline <= now < slot_end`.
- One atomic POST creates the replacement booking already `IN_USE`, with `checked_in_at = now`, linked to the release. It receives no second grace period and no new full hour.
- The forfeiting user cannot reclaim that release. Only one successful claim may consume a release; failed attempts leave it available.
- Ordinary cancellations inside the 24-hour window do not acquire the exemption. General walk-ins into never-booked rooms remain out of scope for this v1.2 baseline.

Example: an 08:00 reservation forfeits at 08:15. Another eligible student claims at 08:22 and may use the room only until 09:00. At 09:00 the old slot is no longer claimable.

This restrictive baseline leaves some rooms unused on the same day. The newer v2.0 document changes that policy; choose explicitly in Phase 0 rather than loosening the implementation accidentally.

## 4. Data model and invariants

Use a custom `User(AbstractUser)` from the first migration. Preserve bookings and audit history when an account is deactivated; do not cascade-delete operational records.

| Entity | Required fields and constraints |
|---|---|
| User | Unique login ID, verified email, Thai/English names, active/activation state; staff membership via permission group. IDs stored as strings to preserve leading zeros |
| Room | Unique room number/label, active flag for retirement; temporary unavailability represented by Closure |
| Booking | User, room, immutable slot_start/slot_end, status, source `ADVANCE` or `RELEASE_CLAIM`, created/cancelled/checked-in timestamps, cancellation actor/reason, policy version, release FK when applicable |
| SlotRelease | Original booking unique FK, room, slot_start, released_at, expires_at = slot_end; replacement booking uses a unique FK to this record |
| Violation | User, optional booking, type `NO_SHOW`/`MANUAL`, occurred_at, created_at, actor, note, voided_at/by/reason; unique no-show violation per booking |
| Suspension | User, starts_at, nullable ends_at, lifted_at/by, reason, triggering violation FK, created_by; null end means suspended pending staff review |
| StrikeReset | User, effective_at, recorded_at, actor, reason, resolved suspension FK; counting boundary applied only once effective |
| Closure | Optional room FK (null = all), starts_at, ends_at, reason, actor, revoked_at; half-open interval, end later than start |
| CalendarOverride | Unique date, open/closed instruction and reason, actor; opening override does not defeat a specific Closure |
| PolicyVersion | Validated immutable snapshot, effective_at, actor; baseline slot length remains fixed at 60 minutes |
| AuditEvent | Actor or job identity, action, entity, timestamp, request ID, safe before/after values and reason; no password/token fields |
| Notification | Recipient, kind, payload, unique dedupe key, due_at, attempt count, next_attempt_at, lease/status, sent_at, sanitized last error |
| BookingRequest | User + idempotency key unique, operation, request fingerprint and result; prevent reuse with a different payload |

Database constraints:

- Partial unique `(room, slot_start)` for `SCHEDULED, IN_USE, COMPLETED`; completed records prevent accidental reuse of elapsed slots.
- Partial unique `(user, slot_start)` for `SCHEDULED, IN_USE, COMPLETED`.
- Valid status/source values and timestamp combinations; slot duration exactly one hour; timestamp alignment explicitly checked in Bangkok time where SQL timezone conversion is needed.
- Unique no-show event/violation and unique release consumption; foreign keys protect provenance.
- Index `(user, slot_start)`, `(status, slot_start)`, active suspensions by user, closure intervals, and pending notifications by due time.

The database protects uniqueness; service transactions protect quota, adjacency and cross-record policy. Neither alone is the complete concurrency solution.

## 5. Transaction and concurrency design

For this small installation, use a **single pre-seeded booking-control row** locked with `SELECT ... FOR UPDATE` as the first lock in every operational mutation. This intentionally serializes short writes across nine rooms; ordinary availability reads do not take this lock. It is simpler to audit than several partially overlapping lock protocols. Measure contention during the pilot before considering finer-grained locks.

```text
BEGIN
  lock booking-control row (common to web, staff and job operations)
  capture authoritative now
  reconcile due no-shows/completions and resulting sanctions
  find idempotency result scoped to authenticated user and operation
  if matching successful request exists: return its result
  validate current policy, eligibility, closures, quota, overlap and adjacency
  perform requested transition / create booking
  write audit event, release/violation/suspension if applicable, and outbox rows
  store successful idempotency result
COMMIT
```

Reconciliation scans the small set of overdue active records, with an indexed query. It must reconcile a user's other overdue reservations before accepting a new booking so delayed cron cannot bypass suspension. Do not perform email/network calls while holding the control lock. On lock timeout return a retryable busy message; do not bypass validation.

Database uniqueness errors are handled outside the failed transaction or inside a savepoint, then mapped to a translated conflict response. Every POST revalidates state regardless of what the grid showed. Bind idempotency keys to the authenticated user and payload; repeated check-in/cancellation is a harmless result lookup, not a second event.

Closure creation, policy activation, manual sanctions and account eligibility changes use the same protocol. Test every entry point; direct admin editing must not create a second path around it.

## 6. Lifecycle and exact timing

| Transition | Preconditions | Atomic effects |
|---|---|---|
| Create → SCHEDULED | Ordinary booking passes §3 | Booking, audit, confirmation outbox |
| SCHEDULED → IN_USE | Owner, matching room, active eligibility, within grace | Check-in timestamp and audit |
| SCHEDULED → CANCELLED | Owner and `now < slot_start` | Cancellation reason, audit, notification |
| SCHEDULED → NO_SHOW | `now >= check_in_deadline` | One release, violation, audit, notification; apply threshold if crossed |
| Released slot → IN_USE | Valid remaining-time claim | New booking, release consumed, audit, confirmation |
| IN_USE → COMPLETED | `now >= slot_end` | Completion event; no extra quota charge |
| SCHEDULED → CANCELLED by staff | Closure/suspension or approved correction | Recorded reason, notification, no no-show strike |

Terminal states are not rewritten casually. Disputes void a violation or record a correction with an audit event; history remains inspectable.

Read-only pages may derive `NO_SHOW`/`COMPLETED` from deadlines for display. **Derived state does not remove persisted database constraints.** Every subsequent write first reconciles persisted state under the common lock. Public GET requests never mutate bookings. A released slot remains a candidate until the claim POST commits.

Jobs run every minute: reconcile lifecycle, enqueue eligible reminders, recover/retry outbox work and record a heartbeat. Use a non-blocking scheduler lock (`pg_try_advisory_lock`, with guaranteed release) to skip overlapping runs; a blocking advisory lock would wait, not skip. Jobs process bounded batches and remain safe to rerun.

The grid refreshes every 30 seconds and after mutations; server deadlines are authoritative even while a browser is stale. Scheduler failure delays unsolicited notifications, but request-side reconciliation keeps mutations correct. Alert on stale job heartbeat and oldest pending email.

## 7. QR check-in and user experience

Nine printed URLs: `https://<domain>/r/<room>/check-in/`. Include human-readable room number, short URL, support contact, and a statement that login is required. Verify every poster against its physical door.

Flow: scan → authenticate using a validated local `next` redirect → show room, reservation, end time and status → explicit CSRF-protected POST to check in or claim remaining time. Never mutate state on GET or automatically from an email link.

- Too early: show the check-in opening time and countdown.
- Too late: explain forfeiture and show current availability; do not promise the room remains free.
- Wrong room: direct the user to their own booked room without disclosing another person's booking.
- Repeated submission: show the existing successful result.
- No camera/internet: staff may record an assisted check-in after verifying attendance, using the same deadline/rules and a required audit reason. Post-deadline disputes require a separate correction, never backdated silent check-in.

**Attendance limitation:** a static QR URL can be copied and opened remotely by the booking owner. Login proves account control, not physical presence, and the audit trail cannot reliably prove absence. Staff must accept this limitation for the pilot and use spot checks. Stronger physical-presence verification is a separate design decision, not a claimed feature.

The mobile grid has a date selector and a readable room/day list alternative to the 9×12 table. Provide keyboard navigation, visible focus, labelled controls, sufficient contrast, text alongside colors, accessible status announcements, and usable forms when HTMX is unavailable. Preserve focus during refresh and show network failures explicitly.

## 8. Violations, suspension and appeals

Baseline threshold remains three valid violations. **Proposed lifecycle:** count unvoided violations since the latest staff-recorded reset; do not silently add the rolling window or fixed suspension duration used by v2.0.

1. A no-show creates exactly one violation. Staff-entered violations require a reason and explicit indication that they count toward the threshold.
2. Crossing three creates one active Suspension with no end date, meaning **pending staff review**. Booking/claim/check-in are blocked immediately even before an email is sent.
3. Proposed handling of existing bookings: atomically cancel future SCHEDULED bookings without penalties; allow an already IN_USE session to finish. Staff review is required for changes to this default.
4. Staff set an end time or lift the suspension with a reason. At expiry, access is allowed by timestamp comparison without waiting for cron. A lift notification is queued once when reconciliation observes the expiry.
5. Staff must record the strike reset boundary when resolving a suspension (effective at the scheduled end or immediate lift time); older violations remain in history but do not immediately trigger another suspension. New violations count toward the next threshold.
6. Students can see their own violations and contact staff to dispute them. Voiding a triggering violation prompts staff to review the linked suspension; no silent destruction of records.

A boolean or nullable user date alone is insufficient to represent this lifecycle. The staff dashboard must prominently show suspensions awaiting an end/reset decision.

## 9. Staff controls, closures and configuration

Ten staff share operational permissions: roster invitations/deactivation, current bookings, assisted check-in, closures, violation review, suspension resolution and usage export. They cannot grant staff/superuser access or edit infrastructure secrets. Permission checks apply server-side to every endpoint.

Closure flow: choose room/all rooms and interval → preview affected reservations → confirm application. Under the shared lock, recheck conflicts, create the closure, cancel affected future bookings, enqueue notices and audit the action. An overlap with IN_USE requires explicit acknowledgement and an on-site handover; software cannot physically clear a room. Record the actual resolution separately. Reopening does not resurrect cancelled reservations.

Keep fixed one-hour slot geometry in v1. Policy changes create a validated new version and an affected-booking preview. Existing bookings retain stored deadlines; schedule changes that invalidate them require explicit cancellations/notifications. Avoid an unrestricted “everything editable” settings table.

## 10. Notifications, security and data handling

Email events: activation/reset, confirmation, cancellation, reminder, no-show and suspension applied/lifted. Weekly digest is deferred. Use recipient language preference and include room/date/time in Bangkok time. Emails are informational; booking success depends on the committed database transaction.

- Insert notification rows in the same transaction as their event. A worker claims rows with a lease, sends outside transactions, retries with bounded exponential backoff and exposes exhausted failures to staff.
- Unique event keys prevent duplicate enqueueing. SMTP delivery is **at least once**, not exactly once: a crash after provider acceptance can cause a duplicate. Use provider idempotency where available and stable message IDs for diagnosis.
- Enqueue reminders when `start - 30m <= now < start`, including missed runs; recheck that the booking remains SCHEDULED before sending. Do not send stale reminders after cancellation or start.
- Confirm SPF/DKIM and appropriate domain mail configuration, actual provider quotas and delivery to faculty/student mailboxes during the pilot.
- Initial planning envelope: 200 bookings/day × confirmation, reminder and lifecycle notice can reach 600 messages/day, plus invitations, resets and retries. Size against actual event mix and provider limits, not the previous unsupported 400/day ceiling.

Security baseline: CSRF protection, secure/HttpOnly session cookies, HTTPS, `DEBUG=False`, restrictive allowed hosts/origins, password validation, login/reset rate limits, generic reset responses, expiring single-use activation links, staff session controls and re-authentication for sensitive operations. Keep secrets outside Git; redact credentials, reset links and unnecessary personal data from logs. Escape spreadsheet formula prefixes in staff CSV exports.

Use verified roster imports and individual set-password invitations. No shared default passwords. Self-registration is disabled for the baseline; if enabled later, verified institutional email alone does not establish department eligibility—require roster matching or staff approval. Import dry-runs validate duplicate IDs/emails and report errors without partially inviting a bad batch. Seed commands are idempotent and never reset existing passwords or fabricate production staff.

Collect only necessary account/contact/booking information. Provide a privacy notice distinct from rules acknowledgement; faculty must determine the applicable data-handling basis and approve retention/access procedures. This plan does not assert that a consent checkbox establishes legal compliance. Proposed retention for review: identifiable booking/audit records for one academic year, followed by anonymized usage aggregates; active disputes may require a documented hold. Student UI history of 60 days is a display limit, not deletion policy. Define account offboarding, correction/export requests, backup expiry and restored-data deletion procedures before launch.

## 11. Pages and routes

| Route | Audience / behavior |
|---|---|
| `/` | Public availability, date picker, room list/grid, TH/EN; no personal fields in HTML, JSON or cache |
| `/login/`, `/activate/`, `/password-reset/` | Login and secure activation/reset; no account enumeration |
| `/book/<room>/<slot>/` | Authenticated confirmation GET; creation POST with idempotency token |
| `/my-bookings/` | Own upcoming bookings, cancel action, history, violations and suspension details |
| `/r/<room>/check-in/` | QR landing GET and explicit check-in/remaining-time-claim POST |
| `/staff/today/` | Operational dashboard with overdue jobs/emails and pending reviews |
| `/staff/` | Permission-limited admin for records, invitations and configuration |
| `/rules/`, `/help/`, `/privacy/` | Approved bilingual rules, assistance and privacy notice |
| `/healthz/`, `/readyz/` | Minimal liveness/readiness; detailed diagnostics restricted |

Demo and pilot environments use separate data and secrets. Demo accounts and role-switching facilities never ship enabled in production.

## 12. Deployment, recovery and support

Docker Compose services: `web`, `db`, `scheduler` (application image), `caddy`. Build immutable images in CI; run migrations once as a release step, not from every web/scheduler startup. Dev and production share the application image and service topology while keeping secrets, debug settings, data and exposure appropriate to each environment.

Release procedure: required CI checks → approved release tag → pre-migration backup → review migration compatibility → migrate → deploy image digest → readiness/smoke checks. Tag releases, not every main merge. Keep a tested prior image; database rollback requires migration-specific planning and may need restore. Do not promise zero downtime for a single VPS.

- Nightly encrypted PostgreSQL custom-format dump, transferred to the owner's NAS with a separately protected key and 30-day proposed retention. Monitor success, age and available storage. A backup on the VPS alone is insufficient.
- Initial recovery targets for faculty approval: RPO ≤24 hours; RTO ≤4 hours during staffed support. Demonstrate recovery into a clean environment using `pg_restore`, including user access, bookings and pending jobs.
- External uptime probe; VPS disk/resource monitoring; job heartbeat and outbox-age alerts to a named maintainer. Monitoring only on the same VPS cannot reliably report its own outage.
- Rotate application and proxy logs; patch dependencies/OS on a documented cadence. Restrict SSH and database exposure.
- Runbook covers deployment/rollback, password recovery, mail failure, no-show disputes, incorrect room closures, backup restore and service outage. During outages staff keeps a temporary timestamped room-use log and reconciles it before booking reopens; do not penalize students for a documented service failure.

## 13. Delivery phases and acceptance gates

Estimate **4–6 weeks of part-time implementation plus a two-week pilot**, subject to roster, hosting and faculty availability. Gates define completion; test count and “zero downtime” do not.

| Phase | Deliverable | Exit gate |
|---|---|---|
| 0 — Policy and foundation | Select authoritative plan; resolve §16 proposals; repository, schema, service contracts, seed dry-run and CI | Documented policy matrix; fresh development environment boots; no real accounts seeded into demos |
| 1 — Booking core | Public grid, authentication/invites, transactions, quotas and cancellation | Rule/boundary tests and real PostgreSQL concurrency tests pass |
| 2 — Lifecycle | Check-in, release claims, sanctions, reconciliation and outbox | Deadline races, scheduler outage and duplicate operations pass; test email received |
| 3 — Staff and access | Closures, appeals, audit, exports, bilingual/mobile usability | Staff completes scenario checklist without developer intervention; unauthorized actions rejected |
| 4 — Deployment rehearsal | HTTPS, images, migrations, monitoring, backup and runbook | Restore drill meets agreed recovery target; rollback rehearsal and alerts verified |
| 5 — Two-week pilot | About ten approved students, staff supervision, verified posters | No unresolved critical correctness/security issues; incidents reconciled; faculty signs launch checklist |

Pilot measures: successful booking/check-in attempts, rule rejection reasons, failed/late email, staff interventions, lock waits and user-reported confusion. Agree performance targets in Phase 0; proposed target is p95 mutation response below two seconds at 20 concurrent attempts on the intended VPS, excluding email delivery.

## 14. Required verification matrix

Use PostgreSQL and independent connections for transaction tests; SQLite and ordinary single-threaded unit tests do not verify production locking. Use a controlled server clock for boundary cases.

| Area | Required cases and result |
|---|---|
| Slot race | 20 eligible users submit the same slot: exactly one success and clean conflict responses |
| User race | Same user submits several nonadjacent slots with one quota place remaining: exactly one success |
| Overlap/adjacency | Same user attempts same hour in different rooms and ±1h: rejected; 2h separation accepted if quota permits |
| Daily quota | COMPLETED and NO_SHOW still count; cancellation restores allowance; Bangkok midnight separates dates |
| Window | Just before/at/after 24h and 7-day boundaries; 19:00 valid, 20:00 invalid; off-hour input rejected |
| Check-in | Before start rejected; at start accepted; just before deadline accepted; at deadline rejected |
| Release | Two claims race: one succeeds; immediate IN_USE; original end retained; original owner rejected; at end rejected |
| Exemption | Never-booked and ordinarily cancelled near-term slots cannot use the no-show exception |
| Reconciliation | Scheduler stopped: claim clears stale SCHEDULED row and records one violation; repeated job adds nothing |
| Deadline race | Check-in versus no-show job produces exactly one legal outcome, never IN_USE plus no-show violation |
| Suspension | Third valid strike blocks immediately; future cancellation policy applied; indefinite/end-date/lift/reset tested |
| Closure race | Booking and closure concurrent: no surviving booking contradicts the final closure; existing users notified |
| Idempotency | Repeated POST returns same result; changed payload or another user's key cannot retrieve/create unintended data |
| Permissions | Students cannot read another user's booking or mutate staff records; staff cannot grant superuser |
| Outbox | Rollback sends nothing; retries/restart recover leased work; cancelled reminders suppressed; SMTP ambiguity documented |
| Privacy/UI | Anonymous responses contain no personal details; TH/EN, keyboard, small-screen and slow-network flows work |
| Recovery | Clean restore, migration failure/rollback and missing SMTP do not corrupt booking state |

## 15. Budget and operating ownership

Obtain written/current quotes rather than retaining unverified promotional prices. Record billing period, currency, tax, renewal price and multi-year prepayment separately.

| Cost line | Basis to fill before purchase |
|---|---|
| VPS | Actual selected plan, recurring and renewal charges |
| Domain | Registration and renewal; faculty-provided domain may eliminate this line |
| Transactional email | Confirm entitlement or budget separately for measured volume |
| Backup/storage | NAS capacity, transfer and any independent storage subscription |
| Monitoring/CI | Confirm selected free allowances or paid limits |
| Posters | One-off printing quote for nine rooms |
| Maintenance | Named maintainer and staff time; not included in software license cost |
| Contingency | Owner-approved allowance |

`Year 1 = annual service charges + setup/printing + contingency`. Show renewal-year total separately. Django/PostgreSQL/Caddy and the selected frontend libraries do not require paid licenses, but that does not make hosting, mail or maintenance free. The original total was inconsistent with its own line items and is removed.

Faculty operations owns room rules, roster eligibility, closures and appeals. The technical maintainer owns releases, patching, backup/restore and alerts. Assign named primary and backup contacts before launch.

## 16. Decisions and launch prerequisites

These items are explicit gates, not reasons to halt technical prototyping.

| Item | Recommended baseline / required evidence | Owner |
|---|---|---|
| Authoritative plan | Select this v1.2 revision or reconcile v2.0; especially lead time, walk-ins and strikes | Project owner |
| Quota interpretation | Approve §3 count of completed/no-show bookings and same-hour exclusion | Faculty operations |
| Released time | Approve immediate remaining-time claim, distinct claimant and fixed end | Faculty operations |
| Deadline | Approve exclusive 15-minute cutoff and published wording | Faculty operations |
| Sanctions | Approve strike reset, pending-review suspension, existing booking treatment and appeal workflow | Faculty operations |
| Regulation | Verify local PDF clause mapping, including whether ensemble use changes scope | Faculty operations |
| Attendance | Accept static QR limitation or commission stronger verification | Faculty operations |
| Roster and staff | Validate eligible student list and ten individual staff identities/emails | Faculty operations |
| Privacy/retention | Approve notice, handling basis, retention, permissions and request procedure | Faculty responsible office |
| Infrastructure | Confirm domain, SMTP, quotes, support owner, recovery targets and restored backup | Owner + maintainer |
| Calendar | Enter upcoming holidays/closures and verify physical room labels | Staff |

## 17. Deferred features

General same-day walk-ins (unless the authoritative policy changes), group attendance tracking, waitlists, LINE integration, SSO/OAuth, rotating physical-presence codes, native mobile apps, payments and equipment checkout. Do not describe ensemble practice itself as forbidden without checking the regulation; group-booking software is what is deferred here.

## 18. Decision record

Preserved: custom Django, HTMX/Tailwind, PostgreSQL, own VPS/Compose, ID/password, public availability, Thai default with English, ค.ศ., nine rooms, ten operational staff, 24-hour minimum, seven-day maximum, two/day and no adjacent hours across rooms.

Technical corrections adopted in this revision: supported Django LTS line, common transaction lock, database invariants, deadline reconciliation before writes, immutable policy/deadline history, outbox delivery, explicit audit events, secure invitations, closure conflict handling, restore verification and quote-based costing.

Policy clarifications remain proposals until the §16 decisions are recorded. Implementation acceptance must use the final approved matrix, not infer approval from the word “revised.”
