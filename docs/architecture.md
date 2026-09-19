# Architecture

What the system is made of, and why it is shaped this way. The binding
specification is `roomreserveapp.v3.md`; this file explains how the code meets it.

## Shape

```
roomreserve/            project package: settings, URLs, WSGI
  settings/base.py      env-driven; no secrets by default
  settings/dev.py       local development; the test clock is allowed here
  settings/test.py      PostgreSQL test database, locmem mail/cache, plain static storage
  settings/prod.py      fails at startup on a missing mandatory secret
core/                   one fat app: everything the product does
  models/               persistence and its constraints
  services/             every business rule
  views/                a thin HTTP layer
  templates/            server-rendered HTML, Thai and English
  static/               built Tailwind CSS and a vendored htmx
tests/                  PostgreSQL-only suite, including real-browser tests
scripts/verify          the local verification runner
deploy/Caddyfile        TLS termination and reverse proxy
```

One project package plus one app. Splitting the domain across several Django apps
would add import ceremony without adding a boundary: the booking rules genuinely
do reach into identity, sanctions and the calendar.

## The three layers

**Views never decide anything.** A view parses input, calls one service inside
`run_view_operation`, and renders an outcome. This is what makes the rules
testable without HTTP and keeps the browser tests about presentation rather than
about policy. The one place a rule is expressed in a template is which control to
*offer* — and the server re-checks it anyway.

**Services own the rules.** Each operational mutation goes through
`core/services/protocol.py`, which fixes the order:

```
lock → reconcile → idempotency → validate → savepoint → audit + outbox
```

The order is the specification. Reconciling *before* validating is what makes a
rejected request still commit the reconciliation that made it correct (a no-show
strike is recorded even though the check-in it triggered was refused). The
idempotency step is what makes a retried POST return the recorded result instead
of creating a second booking.

**Models hold constraints, not rules.** Partial unique indexes ("one blocking
booking per room and hour"), check constraints (an automatic sanction always has
an end) and the `BLOCKING_STATUSES` / `QUOTA_STATUSES` constants live with the
schema, so two code paths cannot disagree about what "taken" means.

## Rooms and who may reserve them

A room carries a **reservation audience**: `EVERYONE` (the default) or a list of
instrument categories. The check lives in exactly one place,
`eligibility.assert_may_reserve_room`, and exactly one mutation calls it —
`booking.validate_advance_target`. Two paths deliberately do not:

- **Walk-in** takes any eligible student once the hour has started and nobody holds
  the room. That is the release valve that keeps a restricted room from being dead
  capacity, and it is why the audience is a *reserving* rule rather than a room
  permission.
- **Check-in** only checks the account, so a booking made before a room was
  restricted stays valid and a student is never punished for an administrative
  change.

The grid reflects the same rule without duplicating it: a future slot the viewer may
not reserve is `SlotState.RESTRICTED` rather than `BOOKABLE`, and the current free
hour stays `FREE_NOW` for everyone. The viewer's category is resolved once per grid
render, and `allowed_categories` is prefetched, so a restricted room costs one query
rather than one per cell.

The teaching timetable rides the same rails as closures: a recurring `WeeklyBlock`
on a room makes its hours `CLOSED` on the grid (with the course as the reason) and
is refused by the one calendar gate both booking paths already call
(`calendar.assert_slot_open`), so reservations and walk-ins cannot disagree about
a class hour.

A student's category is derived, never matched from free text: the roster's
`instrument` column is what the department writes, and
`core/services/instruments.py` maps it to a canonical family. Keeping the raw value
means the mapping can be corrected without losing what the department actually
said, and `UNKNOWN` is a real value that fails closed and is reported.

## Time

`core/services/clock.py` is the only source of "now". Services receive the moment
as a parameter; only the protocol calls `clock.now()`, once, after the lock.

A test clock exists for tests and development, gated on `ALLOW_TEST_CLOCK`, which
`prod.py` forces to `False`. It has two forms: `frozen_clock` (thread-local, for
unit tests) and `freeze_process_clock` (process-wide, needed for anything served,
because Django answers requests on threads the caller never touches). Nothing in
the URL map can reach either.

## Concurrency

The guarantees rest on PostgreSQL row locks and partial unique indexes. A
`BookingControl` singleton row serialises the operations that need a global view
(quota and adjacency, which span rows); per-slot work relies on the unique index.
Tests use independent connections and `threading.Barrier` rather than sequential
loops, because a sequential loop cannot produce the interleaving it claims to
test.

`refs.py` re-reads a row under the lock before mutating it. Without that, a
caller's stale in-memory copy can overwrite a committed change — the check-in
path had exactly that bug before it was fixed.

## Bilingual delivery

Every route is mounted under `/th/` and `/en/` by `i18n_patterns`. The active
language comes from `{% get_current_language %}` rather than a context processor,
so the `<html lang>` attribute and the language switcher's selection are correct
regardless of how templates are configured. Years are Gregorian in both languages
(ค.ศ.), which the footer states and the tests assert.

## Email and the outbox

Mail is never sent inline from a request. A service enqueues a `Notification` in
the same transaction as the state change, so a rolled-back booking cannot send a
confirmation. `notifications.revalidate()` re-reads the referenced rows at send
time and raises `MessageStale` when the message no longer describes reality — a
reminder for a cancelled booking is dropped, not delivered.

Delivery is **at least once**: a provider that accepts a message and then loses the
worker before the row is marked sent will see it again. Booking outcomes never
depend on mail arriving.

## Availability is derived, never stored

`availability.py` computes each cell from bookings, closures and the calendar,
without writing anything. A GET never mutates. Expired holds read as free, elapsed
use reads as completed, and the public grid exposes no student identity at all —
only a state, plus an "is mine" flag for the signed-in viewer.

## What is deliberately absent

- No REST API. The product is server-rendered HTML with a small amount of htmx for
  the 30-second grid refresh; every mutation is a plain form that works with
  JavaScript disabled.
- No task queue. The scheduler is `manage.py tick` once a minute plus opportunistic
  reconciliation on any mutation, so the system stays correct when the scheduler
  is down.
- No CDN. htmx and the compiled CSS are served locally, so the deployment has no
  third-party runtime dependency.
