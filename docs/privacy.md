# Privacy

> **Draft.** The faculty must approve the data-handling basis, the notice and the
> retention periods before this system is used with real students. This page is not
> a claim of legal compliance. The in-app notice at `/th/privacy/` carries the same
> warning, and so does the footer of every page.

## What is held

| Data | Where | Why |
|---|---|---|
| Institutional ID, institutional email, name (Thai and English) | `User` | Identifying the account, matching it to the roster, contacting the student |
| Password hash | `User` | Sign-in. Never stored or logged in plaintext |
| Bookings: room, hour, status, source, check-in time, cancellation | `Booking` | The service itself, and the quota and strike rules |
| Strikes and suspensions, with reason and recording staff member | `Violation`, `Suspension` | Enforcing the department's rules, and reviewing appeals |
| Roster rows: ID, email, name, instrument, active flag | `EligibleStudent` | Proving department membership. A roster row is not an account |
| Invitations: **digest only** | `Invitation` | Single-use links. The plaintext token exists only in the outbox payload and the email |
| Operational audit trail | `AuditEvent` | Accountability: who changed what, when, and from which request |
| Outbox messages | `Notification` | Delivering mail reliably. Rendered and revalidated at send time |
| Eligibility decisions, with reason and deciding staff member | `User` | Explaining why an account can or cannot book |

A booking stores the `PolicyVersion` that permitted it, so a later policy change
cannot retroactively re-judge a historical decision.

## What is not held

- **No location tracking, no device fingerprinting, no IP-address history.** Rate
  limiting uses counters keyed by identifier and address; they expire and are not a
  log of who visited.
- **No proof of physical presence.** A printed QR code records an account action.
  It cannot show that a person was in the room, the interface says so, and staff
  may spot check. This must stay in the runbook.
- **No student identity on any public page.** The availability grid exposes slot
  states, and an "is mine" flag only for the signed-in viewer. Nine anonymous
  pages are scanned for a student's name, ID and email in
  `tests/test_browser_privacy_posters.py`, and the check also looks for the secret
  key.
- **No personal data in a shared cache.** `PersonalResponseCacheMiddleware` marks
  every authenticated response and every non-GET `private, no-store`, and varies
  every response on `Cookie`. Anonymous pages are deliberately not marked `public`,
  because they carry the CSRF cookie.

## Retention

- **Identifiable records: one academic year plus a documented dispute hold.**
  Proposed, not endorsed.
- **Backups: 30 days.**
- **Nothing is deleted automatically.** A retention dry-run and report are
  described in the specification; neither is implemented. Until the faculty endorse
  the period, implementing automatic deletion would destroy records under a default
  nobody has agreed to.
- The 60-day history shown in the application is a **display limit, not a deletion
  policy**. The interface says so.

**If a restore happens**: restoring an older backup brings back records that were
deleted after that backup was taken. Cleanup must be reapplied before reopening,
and this is written into the runbook's restore procedure.

## The student's options

- **Correction**: ask the department office; staff can change eligibility, and
  changing an email invalidates verification and re-runs the roster check.
- **Deactivation**: staff can deactivate an account. Deactivation keeps booking and
  sanction history, so room records stay accurate; it does not erase the person
  from the past.
- **Copy of the records**: staff exports exist for the roster, statistics and the
  audit trail. A per-student export is described in the specification and is **not
  implemented**; producing one today means reading the records out of the staff
  screens or the database.
- **Deletion**: not currently offered. The retention period is unendorsed, and no
  automated deletion exists.

## Test data and the local database

Fixtures are synthetic (`tests/factories.py`). Names, IDs and addresses are
invented, and the email domain used in fixtures is reserved for documentation.
No real roster data is used in tests. `seed_demo` prints the synthetic credentials
it creates and states that they grant no access to any deployed environment.

The **local development database** is a different matter: on 2026-09-15 the real
four-year undergraduate roster (74 students) was imported into it, replacing the
synthetic demo rows. That database is a development instance on an external,
unencrypted volume — it must not be copied into evidence, artefacts, reports or
version control, and it is not a place to keep the authoritative copy. The
converted CSV was kept in a session temporary directory, not in the project.

## Compliance gaps to close before real use

1. **Faculty approval** of the data-handling basis, the notice and the retention
   period.
2. **A per-student export**, and a documented deletion/anonymisation procedure.
3. **Retention automation**, once a period is endorsed.
4. **A retention dry-run report** to show what would be removed, before anything is.
5. **Thai copy review** by a Thai-speaking member of the department — the notice
   itself is part of the bulk translation.
6. **A data-processing record** for the mail provider and the backup destination,
   including where backups physically live.
