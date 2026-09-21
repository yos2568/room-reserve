# Handoff — Room Reserve V3 · 21 September 2026

**Project:** `/Volumes/Crucial2TB/All Codes/FAA/Room problem`  
**Specification:** `roomreserveapp.v3.md`  
**Previous handoff:** `handoff20sep.md`  
**Local preview:** `http://127.0.0.1:8004/th/`  
**Current branch:** `main`  
**Current HEAD:** `bc98e7a Add Priority 2 and 3 booking workflows`

## Current status

The application is running locally with the recent dashboard redesign, the new
floor-plan presentation, the room 10 addition, room 301 view-only behavior,
approval workflows, account-path registration, single-use token locking, and a
complaint email form.

The worktree is still dirty and contains a mixture of earlier Priority 2/3 work,
visual changes, room policy changes, and the latest complaint and token-security
changes. It has **not** been committed, pushed, or deployed to Hostinger. Review
and separate the intended revision before deployment.

## Latest completed work

### Account security

- Activation, email-verification, and password-reset links are now redeemed in a
  transaction that locks the invitation row with `select_for_update()`.
- Password validation, password persistence, and `used_at` consumption happen as
  one atomic operation.
- A PostgreSQL concurrency test starts two redemption attempts together and
  verifies that exactly one succeeds and the other receives `TOKEN_USED`.
- Students still choose their own password; there is no default password.

Relevant files:

- `core/services/identity.py`
- `tests/test_concurrency.py`

### Complaint form

Signed-in users can open `/th/complaints/` from Help or the account menu. The form
accepts:

- complaint subject;
- optional active room;
- message with a minimum length and maximum size.

The sender name and reply address come from the signed-in account. The destination
is fixed by `COMPLAINT_RECIPIENT_EMAIL`, defaulting to
`Sitanun.S@chula.ac.th`; the form cannot redirect messages to another address.
The email is queued through the existing transactional outbox, receives a unique
reference, uses Reply-To for the sender, and is retried if Mailcow/SMTP is
temporarily unavailable.

The form is protected by login, CSRF, account-bound signed form tokens, duplicate
submission protection, and a limit of three new submissions per account per hour.
Users who cannot sign in see a direct support email link on the login page.

Relevant files:

- `core/views/complaints.py`
- `core/templates/core/complaint.html`
- `core/templates/core/email/complaint.txt`
- `core/services/outbox.py`
- `core/services/notifications.py`
- `core/migrations/0013_notification_external_recipient.py`
- `tests/test_complaints.py`
- `docs/complaints.md`

The migration was applied to the local development database. Production still
needs migration `0013_notification_external_recipient` during deployment.

### Room and booking policy currently represented in code

- Active rooms: room 301, rooms 1–10, room 303, and room 304.
- Room 301 is `availability_only`: students can see its timetable but cannot
  reserve it or use it as a walk-in.
- Rooms 303 and 304 are marked `requires_approval=True` for advance reservations.
- Room 303 remains restricted to the configured instrument categories.
- The room board is `/th/choose-room/`.
- Registration separates Student, Faculty, and Staff/administrator paths.
- Faculty/staff/admin accounts are invitation-only and do not use student ID or
  instrument fields.
- The dashboard uses the supplied 3D floor-plan asset at
  `core/static/img/floor-plan-dashboard.jpg`.
- The large faculty logo was replaced with a compact `FAA | Room Reserve` header.

## Verification

Passed:

- `python manage.py check`
- `python manage.py makemigrations --check --dry-run`
- `npm run build:css`
- Complaint and outbox tests: **30 passed**
- Token-lock focused tests: **3 passed**
- Full suite: **479 passed, 2 failed**

The two full-suite failures are pre-existing and unrelated to the complaint or
token-lock work:

1. `tests/test_priority_three.py::test_lobby_status_does_not_expose_student_or_booking_details`
   expects the English text `In use`, while the response is rendered in Thai.
2. `tests/test_scheduler.py::test_a_voided_strike_no_longer_counts_towards_a_suspension`
   calls a real-clock query after creating a suspension at the frozen September
   14 test time; on September 21 the suspension is no longer returned.

The repository also prints the repeated Git warning about the AppleDouble pack
index:

`non-monotonic index .git/objects/pack/._pack-...idx`

Do not repair the Git object store destructively as part of this handoff.

## Known work still required before production

1. Enforce approval for students using rooms 303 and 304 on every entry path.
   The advance-reservation path creates `PENDING_APPROVAL`, but the “Use now”
   walk-in service currently does not check `requires_approval`. This must be
   resolved before students can use those rooms.
2. Ensure room 301 is clearly shown as availability-only in the lobby, room board,
   suggestions, and every timetable view. Suggestions must not offer it as an
   actionable alternative.
3. Verify that only admins can create or edit room 301 timetable slots, while the
   public view continues to show current availability and class blocks.
4. Correct the first-semester teaching blocks against the supplied room timetable
   images. The current configuration is approximate and has known time differences,
   especially for room 304. Confirm the annotated date-specific Monday class
   before making it recurring.
5. Remove the remaining capacity/seat labels consistently from all user-facing
   timetable, week, profile, and move-booking views if that requirement still
   applies.
6. Fix the two existing failing tests, then rerun the complete suite and browser
   checks in Thai and English at desktop and mobile widths.
7. Inspect the complete dirty diff and decide whether unreferenced floor-plan
   assets, `.claude/`, `uv.lock`, and prior unrelated changes belong in the release.

## Jev AI decision

Jev from TypeSafe AI is intended for structured decisions with confidence values.
It could later classify complaint messages or suggest urgency for staff review.
It should not approve room bookings or replace admin/faculty decisions. Keep it
out of the pilot until the core approval, timetable, and complaint workflows have
operated reliably and there is a clear data-handling decision for sending complaint
content to an external AI service.

Reference: `https://docs.typesafe.ai/introduction`

## Deployment notes

Deploy only from a reviewed, committed revision. Before Hostinger deployment:

1. Separate intended changes from unrelated worktree changes.
2. Run migrations, including `0013_notification_external_recipient`.
3. Set production environment values, especially `COMPLAINT_RECIPIENT_EMAIL`,
   `SITE_BASE_URL`, `DEFAULT_FROM_EMAIL`, and Mailcow SMTP settings.
4. Run `collectstatic` and build/rebuild CSS as required by the deployment setup.
5. Restart Django and the outbox worker.
6. Send one controlled complaint test and confirm delivery to
   `Sitanun.S@chula.ac.th`.
7. Check the staff outbox page for pending or exhausted mail.

No Hostinger deployment has been performed for this checkpoint.
