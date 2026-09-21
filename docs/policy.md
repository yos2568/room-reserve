# Policy

The rules the application enforces, and the values currently in force.

Everything below is enforced in `core/services/` and by database constraints, not
by the interface. The interface only decides which control to *offer*; the server
re-checks every request, so a stale page or a hand-crafted POST cannot produce a
booking the rules would refuse.

Values come from the environment and are frozen into an immutable `PolicyVersion`
row. A booking stores the version that permitted it, so changing the policy later
cannot retroactively invalidate a check-in that already happened.

## Values currently in force

| Setting | Value | Environment variable |
|---|---|---|
| Slot length | 60 minutes, on the hour | not editable (`SLOT_MINUTES`) |
| Opening hours | Monday–Friday 08:00–20:00 (last start 19:00) | not editable |
| Weekend | closed | not editable |
| Booking horizon | 2 days ahead, including today (D-38) | `POLICY_HORIZON_DAYS` |
| Daily quota | 2 bookings per person per day, across all rooms | `POLICY_DAILY_QUOTA` |
| Upcoming hours held | at most 4 hours not yet finished, walk-ins included; one frees up when its hour ends (D-38) | `POLICY_MAX_UPCOMING_HOURS` |
| Weekly repeat | not offered; students book each week themselves (D-38) | — |
| Check-in window | closes exactly 15 minutes after the hour starts | `POLICY_CHECKIN_GRACE_MINUTES` |
| Strike window | 30 days | `POLICY_STRIKE_WINDOW_DAYS` |
| Strike threshold | 3 unconsumed strikes | `POLICY_STRIKE_THRESHOLD` |
| Automatic suspension | 7 days | `POLICY_AUTO_SUSPENSION_DAYS` |
| Reminder lead | 30 minutes before the hour | `POLICY_REMINDER_LEAD_MINUTES` |
| Institutional email | `student.chula.ac.th` | `INSTITUTION_EMAIL_DOMAIN` |
| Rooms | 13 — room 301 view-only, stalls 1–10, room 303 instrument-specific, room 304 general | `ROOM_COUNT`, `ROOM_OVERRIDES` |
| Class hours on 301 | Mon 13–15, Tue 08–10 and 12–15, Wed–Fri 13–15 (approximate first-semester report) | `ROOM_WEEKLY_BLOCKS` |
| Class hours on 304 | Mon 10–12, Tue 12–14, Thu 10–12, Fri 13–15 | `ROOM_WEEKLY_BLOCKS` |

These numbers are the department's operational defaults. They are **not** figures
stated in the regulation, and the Rules page says so.

## The instrument-specific room

Room 303 (`ห้อง 303`) is one of the two larger rooms at the back of the floor, and
the department keeps it for piano and percussion practice. The rule is narrow on
purpose:

- **Only piano and percussion students may reserve it in advance.** Everyone else
  sees the hour marked "Piano & percussion only" rather than a Reserve control —
  the grid says why, because an unexplained gap in the grid reads as a fault.
- **Anyone may walk in once the hour has started and the room is still free.**
  This is the same release valve a no-show already provides, and it is what stops a
  restricted room from standing empty whenever those students are not using it.
  Starting a session there always ends at the hour boundary, like any walk-in.
- **A booking already made is never cancelled** by restricting the room, and it
  still checks in.
- **Staff cannot override it.** Staff have no "create booking" action, and the
  design keeps it that way: staff do not bypass booking rules. A rehearsal for
  another instrument uses the walk-in rule.

The audience is per room and can be changed by staff (Configuration → Rooms)
without a developer. A room set to "only these instrument categories" with nothing
selected lets **nobody** reserve it — an empty list is treated as a mistake, not as
an invitation.

**Who counts as a piano or percussion student** comes from two sources, in order
of authority (D-30):

1. **The roster.** A linked roster row's instrument is mapped to a canonical
   category (`core/services/instruments.py`) and always wins.
2. **The student's declaration.** Registration asks for an instrument; a declared
   category is honoured once staff have approved the account, and staff can change
   or clear it on the user screen (audited).

A student with neither sees the room's availability but cannot reserve it, and the
roster screen flags rows whose instrument nobody has categorised.

## Checking in at the door

A student checks in by **scanning the QR code printed on the room's door**, which
opens the room page with a check-in button for their booking. That is the only way
a student checks themselves in (D-37): the booking emails and My bookings say where
to check in but no longer check anyone in, because both worked from anywhere.

The window is from the start of the hour until the grace deadline (15 minutes by
default); after it, the booking becomes a no-show. The button works only for the
signed-in owner's booking and only on that room's door page.

A photo of the poster still works away from the room, so a scan is a declaration,
not proof of presence. Staff spot checks are the backstop, and staff can check a
student in by hand (for example if the student has no phone).

## The teaching timetable

Rooms 301, 303 and 304 are teaching rooms as well as practice rooms: classes meet in
them every week (the department's sheet `ตารางห้อง อาคารศิลปกรรมชั้น3.pdf`). A class
hour is blocked on the grid and labelled with the course, because an unexplained
gap reads as a fault. Room 304's blocked hours are Monday 10:00–12:00
(Counterpoint), Tuesday 12:00–14:00 (Skill-Piano), Thursday 10:00–12:00
(Harmony), and Friday 13:00–15:00 (Wind Pedagogy); the sheet has no page for
303, so it has none. Other 08:00–20:00 weekday hours remain reservable.

- **A blocked hour refuses reservations and walk-ins**, like a closure, but
  recurring: it comes back every week without anyone re-entering it.
- **A booking already made is never cancelled** when a schedule changes, and it
  still checks in — the same survivorship a room-audience change grants (D-26).
- An explicit closure still wins over a class hour, exactly as it wins over the
  weekly opening hours.

Room 301's first-semester time ranges are approximate because the source report
is reconstructed from hour-column geometry. The hours live in
`ROOM_WEEKLY_BLOCKS` and are applied by `manage.py seed_rooms`;
they are configuration, not code. There is no staff screen for them yet — a
schedule change is a settings change and a seed run (see the runbook).

## Suggestions

Above the grid, the page answers "where should I go?" directly: **Free right now**
lists the rooms a student could walk into for the rest of the current hour, and
**Bookable later today** lists the next hours with the rooms still open in each —
already filtered by the viewer's instrument eligibility and their own adjacent
bookings. When a reservation is refused because the hour was just taken, the page
names the rooms that would still accept it. These suggestions are a deterministic
ranking of the same data the grid shows — never a recommendation model — so the
panel and the table cannot disagree (D-29). At zero remaining quota nothing is
suggested and the panel says the day's limit.

## Reserving

- One booking is one hour, starting on the hour.
- A reservation may be made for any free hour from the current one through seven
  days ahead. Past hours, closed hours, weekends and closed dates are refused.
- **Adjacency.** You cannot hold two rooms in the same hour, or bookings in
  adjacent hours. Two hours apart is allowed if the quota still permits it.
- **Quota.** Two bookings per day, counted across all rooms. Cancelling gives the
  hour back and restores the quota immediately. `COMPLETED` and `NO_SHOW` both
  count; a booking you did not use still consumed your allowance.
- The day boundary is Bangkok midnight, not UTC.

## Using a room

Three different situations, deliberately kept apart:

| | How it starts | When | What it means |
|---|---|---|---|
| **Reservation** | Reserve a future hour | any free hour in the horizon | A claim on the hour, not a claim that you are present |
| **Check-in** | Check in for your own reservation | from the hour start until 15 minutes past | Confirms you are at the room |
| **Walk-in** | "Use now" on a free current hour | any time the room is free | Starts immediately, ends at the hour boundary |

- A reservation that is not checked in by the deadline is released, and **one
  strike** is recorded. The room then becomes available to anyone else — including
  someone who wants the remaining minutes of that hour, but **not** the student who
  missed it.
- A walk-in always ends at the hour boundary. Starting at :57 gives you three
  minutes, and the page says so before you confirm. There is no extra hour and no
  early self-checkout.
- A walk-in in the last minutes still blocks the next hour for that student,
  exactly as a full hour would.
- Check-in is for your own reservation only. Another room's door page offers you
  nothing, and a check-in for someone else's booking is refused rather than
  disclosing that it exists.

## Cancelling

- Cancel from My bookings before the hour starts. The hour is released and the
  quota restored.
- A cancellation within the hour before the start is flagged as a **late
  cancellation**. It is recorded for reporting; it is not a strike, and it does not
  change the quota rule.
- A booking cannot be cancelled once it has started. Staff can end a session early,
  which records a reason.

## Strikes and suspensions

- Three unconsumed strikes within 30 days cause **one** suspension of seven days.
- A strike is **consumed** when it causes a suspension. A consumed strike cannot
  cause a second one, and lifting a suspension does not re-arm its strikes.
- A strike expires at the end of its own 30-day window, counted from when it was
  recorded. Expiry is a timestamp comparison, so a suspended student is released
  on time even if the scheduler has been down.
- When a suspension starts, scheduled bookings are cancelled **without extra
  penalty**. A session already in use may finish.
- While suspended: you can read your history and the help pages, and you cannot
  reserve, check in or start a room.
- **Appeals.** If you believe a strike is wrong, contact the department office with
  the date and time. Staff can see the strikes behind a sanction and void one.
  Voiding **opens a review**; it does not lift the sanction by itself. Lifting is a
  separate, explicit staff decision, and the student's page says when a sanction is
  under review.

## Closures, overrides and incidents

- A closure cancels the bookings in its range without penalty, and the affected
  hours show as closed. Reopening a closure does **not** restore the cancelled
  bookings and does not make a completed slot reusable.
- A date override beats the default weekly timetable. A closure beats an override.
- A `ServiceIncident` (an outage, or authorised use) cancels sessions without
  penalty and can void the strikes that resulted. This is how outage victims are
  protected rather than penalised.
- Deactivating a room removes it from the grid without touching its history.

## Staff boundaries

Staff can approve or reject accounts, invite people, record and void violations,
suspend, adjust and lift suspensions, close rooms and ranges, import the roster,
review incidents, version the policy and read the audit trail.

Staff **cannot** grant superuser, change secrets, or bypass booking rules — no
interface performs those, and the tests assert it.
