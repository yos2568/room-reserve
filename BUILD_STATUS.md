# BUILD_STATUS — Room Reserve V3

Last updated: 2026-09-15 (Phase P1–P3 in progress)

## Phase

**P0–P2 complete for the domain core; P3 templates written.** The Django application exists,
runs locally against PostgreSQL 16, and its core booking protocol is verified by an
automated test suite.

## Environment recorded

| Tool | Version / status |
|---|---|
| Python (venv) | 3.12.11 at `~/.virtualenvs/roomreserve` (venv lives on APFS, not the exFAT volume) |
| PostgreSQL | 16.4 in Docker Compose, named volume `roomreserve_pgdata` (never the exFAT bind mount) |
| Docker / Compose | 28.1.1 / v2.35.1 |
| Node / npm | v22.22.3 / 10.9.8 |
| Django | 5.2.17; psycopg 3.3.5; django-htmx 1.29.0; gunicorn 23.0.0; whitenoise 6.12.0; segno 1.6.6 |

## Decisions recorded so far

- D-01 The project directory lives on an **exFAT** volume: no symlinks, no POSIX permission
  bits. Therefore `.venv` lives on APFS at `~/.virtualenvs/roomreserve`, and PostgreSQL uses a
  Docker named volume rather than a bind mount.
- D-02 `username` holds the institutional login ID and is the single source of truth
  (a second column could diverge; V3 section 4 needs uniqueness, not a second copy).
- D-03 `slot_date` is a denormalised Bangkok date stored on each booking so the daily quota
  lookup is a direct indexed query.
- D-04 `BLOCKING_STATUSES`/`QUOTA_STATUSES` are module-level constants in
  `core/models/booking.py`, referenced by services and constraints alike.
- D-05 Ambient `NODE_ENV=production` in this shell makes npm omit devDependencies. Use
  `npm install --include=dev` / `npm ci --include=dev`; the Dockerfile does the same.

## Verified by tests (52 passing)

- A02 horizon/calendar; A03 exact-hour stale handling; A04 check-in window boundaries;
  A05/A06 walk-in on never-booked, cancelled and released no-show slots, original end kept;
  A07 quota incl. COMPLETED/NO_SHOW, cancellation restores, Bangkok date split;
  A08 same/adjacent-hour rejection, two-hour separation, short walk-in blocking next hour;
  A09 20 users / one slot race (exactly one persisted success);
  A10 one user / several slots with one allowance left;
  A11 idempotency replay, payload mismatch, no resurrection;
  A12 check-in vs no-show reconciliation race (never both);
  A13 scheduler-stopped reconciliation and third-strike suspension in one request;
  A14 rejection still commits reconciliation and sends no confirmation mail;
  A18 closure vs creation race has no surviving scheduled booking.

## Real defect found and fixed

The check-in service originally trusted the caller's in-memory booking object. In a race with
reconciliation it could overwrite a committed NO_SHOW and leave a strike on an in-use booking.
Fixed by re-reading every mutated instance under a row lock (`core/services/refs.py`),
and the race test asserts the invariant.

## Blockers

- None outstanding for the local build.

## Next actions

1. Sanction expiry/consumption, outbox retry/lease, incident and identity tests (A15-A22, A28).
2. Browser flows with Playwright on the isolated test app (A23-A26, A29).
3. `scripts/verify`, CI, Docker build, backup/restore rehearsal (A01, A27).
4. Docs: acceptance matrix, runbook, policy, architecture, privacy, README; then QA_REPORT.
