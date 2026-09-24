# Runbook

Operational procedures, and the traps that have already caught this project once.
Written for whoever is on support, not for the person who wrote the code.

Values in `ALL CAPS` are environment variables read by
`roomreserve/settings/prod.py` with `required=True`. A missing one stops the
process at startup with a message naming it — that is deliberate, and it is why a
deployment cannot silently run on a demo default.

---

## 1. Daily and weekly checks

| Check | How | What "bad" looks like |
|---|---|---|
| App is up | `curl -fsS https://<host>/healthz/` | non-200, or a timeout |
| Database is reachable | `curl -fsS https://<host>/readyz/` | non-200 while `healthz` is fine |
| Scheduler is running | Staff → Today: the heartbeat banner | "The scheduler heartbeat is stale" |
| Mail is moving | Staff → Today: the oldest queued message age | "The oldest queued message is N seconds old" |
| Backup is fresh | backup age on the NAS, separately from HTTP uptime | older than 24 h (RPO) |
| Disk is not full | host disk, and the database volume | any trend toward full |

**A failed mail provider must not restart the web process.** SMTP errors are
surfaced on the staff screen and in the outbox, not through liveness. If
`healthz` is failing, that is the web process; if mail is failing, that is the
outbox. They are different alerts on purpose.

---

## 2. Start, stop, restart

Local development (database and mail on the host, app from source):

```bash
cd "/Volumes/Crucial2TB/All Codes/FAA/Room problem"
open -a Docker                 # the daemon must be running before anything else
docker compose up -d db
~/.virtualenvs/roomreserve/bin/python manage.py migrate
~/.virtualenvs/roomreserve/bin/python manage.py seed_rooms
~/.virtualenvs/roomreserve/bin/python manage.py runserver 127.0.0.1:8000
```

Production-shaped stack (gunicorn, scheduler, Caddy):

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=100 web scheduler
```

The Hostinger stack uses the image published by CI. Set `APP_IMAGE` in the
deployment environment to the exact release tag or digest before starting it;
do not leave it pointed at `latest` for a production release. The first release
after the room timetable change must also run:

```bash
docker compose --env-file .env -f compose.hostinger.yaml run --rm web python manage.py seed_rooms
```

That command applies the four recurring room 304 class blocks while preserving
bookings and audit history. Room 304 remains reservable during every other
Monday–Friday period from 08:00–20:00. Use an explicit closure or date override
for a special event instead of editing the recurring timetable.

The production environment must set `MAINTAINER_ALLOWED_IPS` to the
maintainer's fixed address or VPN CIDR. The technical admin at `/maintainer/`
is refused from every other network and is available only to an active
superuser. `STAFF_ALLOWED_IPS` is optional for deployments that keep staff
operations behind a campus network or VPN; leave it empty only when individual
staff accounts and the application login are the intended boundary. Never use
`0.0.0.0/0` as an allowlist. The production Caddy site must preserve the
`X-Real-IP` header override shown in `deploy/Caddyfile` so these checks cannot
be bypassed with a client-supplied forwarded header.

Migrations run **once as a release step**, never on worker startup:

```bash
docker compose run --rm web python manage.py migrate --noinput
```

Restarting the database alone is safe; the application reconnects. Restarting
`web` during a migration is not — run the migration with the app stopped.

---

## 3. Restore from backup

Targets: **RPO ≤ 24 h, RTO ≤ 4 h** during staffed support. Both are proposals
until the faculty owner accepts them.

```bash
# 1. Stop booking traffic so nothing new is written.
docker compose stop web scheduler

# 2. Copy the dump to the host and restore into a NEW database first.
docker compose exec -T db createdb -U roomreserve roomreserve_restore
docker compose exec -T db pg_restore -U roomreserve -d roomreserve_restore \
  --no-owner --exit-on-error < nightly.dump

# 3. Prove it before switching over: counts on both sides must match.
docker compose exec -T db psql -U roomreserve -d roomreserve -tAc "select count(*) from core_booking"
docker compose exec -T db psql -U roomreserve -d roomreserve_restore -tAc "select count(*) from core_booking"
```

Only then point the application at the restored database. `scripts/verify` runs
exactly this drill on a throwaway database as check 10; if it fails there, do not
trust a real restore.

**On restore, before reopening:**

1. **Pause outbound mail.** Old messages are still queued and some now describe
   bookings that no longer exist. `notifications.revalidate()` drops stale ones,
   but do not rely on that alone during an outage.
2. **Pause booking.** Take the app out of service until reconciliation has run.
3. **Reconcile offline and staff records.** Sessions that ran while the app was
   down, and any paper records staff kept.
4. **Expire stale work.** Old holds must not reappear as live bookings.
5. **Review incidents** and record a `ServiceIncident` for the outage window, so
   students are not penalised for it.
6. **Do not blindly resend old emails** and do not auto-penalise outage victims.

### 3.1 Enable the automated backup and monitor timers

The repository includes host-level scripts and systemd units. They deliberately
keep credentials outside the repository. The backup job requires an `age`
recipient and an independently accessible `rclone` destination; without both,
it fails closed instead of creating an unencrypted or same-disk-only backup.

On the VPS, after installing `age` and `rclone`:

```bash
cp deploy/backup.env.example /root/roomreserve-backup.env
cp deploy/monitor.env.example /root/roomreserve-monitor.env
chmod 600 /root/roomreserve-backup.env /root/roomreserve-monitor.env
# Edit both files: set the age recipient, rclone destination, and either
# ALERT_EMAIL_TO (Mailcow SMTP settings are reused from ENV_FILE) or a webhook.
install -m 0755 deploy/backup_postgres.sh /root/roomreserve/deploy/backup_postgres.sh
install -m 0755 deploy/monitor_roomreserve.sh /root/roomreserve/deploy/monitor_roomreserve.sh
install -m 0644 deploy/roomreserve-backup.service /etc/systemd/system/roomreserve-backup.service
install -m 0644 deploy/roomreserve-backup.timer /etc/systemd/system/roomreserve-backup.timer
install -m 0644 deploy/roomreserve-monitor.service /etc/systemd/system/roomreserve-monitor.service
install -m 0644 deploy/roomreserve-monitor.timer /etc/systemd/system/roomreserve-monitor.timer
systemctl daemon-reload
systemctl enable --now roomreserve-backup.timer roomreserve-monitor.timer
systemctl start roomreserve-backup.service
systemctl status roomreserve-backup.timer roomreserve-monitor.timer --no-pager
```

Confirm the first encrypted file exists locally and at the remote destination,
then perform one restore drill into a fresh database before opening the system
to more users. The monitor checks `healthz`, `readyz`, the scheduler heartbeat,
queued-mail age, backup freshness, and disk usage. Configure an external uptime
check for `https://<host>/healthz/` as a separate failure domain.

---

## 4. Roll back a release

Rolling back the image is allowed **only with a compatible schema**. The order:

```bash
docker compose pull                       # or: APP_TAG=<previous> docker compose up -d
docker compose up -d --no-deps web scheduler
curl -fsS https://<host>/healthz/
```

If the release included a migration, a code rollback alone is not enough: restore
the database as in section 3, or run the documented reverse migration. Record the
schema compatibility of each release before shipping it.

---

## 5. Rotate secrets

1. Generate new values (`python -c "import secrets; print(secrets.token_urlsafe(64))"`).
2. Update the deployment environment file. `DJANGO_SECRET_KEY` rotation invalidates
   existing sessions and password-reset links; users simply sign in again.
3. `docker compose up -d` to restart `web` and `scheduler`.
4. Verify `healthz`, then sign in with a test account.
5. Update the backup/restore documentation if the database password changed, since
   `pg_restore` scripts refer to it.

Never commit a real secret. `.env` is gitignored, and the image build uses
throwaway values that are not deployed.

---

## 6. Add a staff member

Staff accounts are **invited**, never promoted by hand, and never become
superuser through the app:

1. Sign in as existing staff → **Staff → Invitations**.
2. Institution ID, institutional email, name, and tick "staff".
3. The invitation link is single use and expires in 14 days. Deliver it out of band.
4. The new staff member sets their own password.

Inviting an existing ID with a different email is refused rather than rebinding
the identity. If someone has lost staff access, use the Django admin
(`/maintainer/`) — that is the maintainer's surface, and it is deliberately
separate from the day-to-day staff screens.

---

## 7. Refresh the roster

The roster decides who can register and which accounts the system approves
automatically, so treat a refresh as a data change with consequences, not as
housekeeping.

**The importer reads CSV; the department keeps an xlsx.** Convert first, keeping
the header names the importer expects (`institutional_id`, `email`, `name_th`,
`instrument`, `year`, `program` even if `program` is constant):

```bash
python3 - "roster.csv" <<'PY'
import openpyxl, csv, re, sys, unicodedata
wb = openpyxl.load_workbook("รายชื่อนิสิตป.ตรี ทั้ง 4 ปี.xlsx", data_only=True)

def clean(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip()

with open(sys.argv[1], "w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["institutional_id", "email", "name_th", "instrument", "year", "program"])
    for ws in wb.worksheets:
        year = int(re.search(r"ปี\s*(\d)", ws.title).group(1))
        rows = list(ws.iter_rows(values_only=True))
        start = next(i for i, r in enumerate(rows[:15])
                     if r and any(isinstance(c, str) and "รหัสนิสิต" in c for c in r)) + 1
        for r in rows[start:]:
            if r and r[1] not in (None, ""):
                writer.writerow([clean(r[1]), clean(r[4]), clean(r[2]),
                                 clean(r[3]), year, "ดุริยางคศิลป์ตะวันตก"])
PY
```

Then **always dry-run**, and read the numbers:

```bash
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev \
  ~/.virtualenvs/roomreserve/bin/python manage.py import_roster roster.csv \
  --dry-run --deactivate-missing --batch "roster-4years-2569"
```

The preflight reports how many rows would be created, how many are already
present, whether the import would be refused, and how many entries would be
deactivated. Only when those numbers are what you expect, drop `--dry-run`.

Three things to know before you trust a refresh:

1. **`--deactivate-missing` means "this file is the complete roster".** It
   deactivates every active entry absent from the file. A partial export used with
   this flag wipes the roster. That is why it is a separate, explicit flag.
2. **The import does not create accounts.** It writes `EligibleStudent` rows only.
   Students still register themselves, and a student is approved automatically only
   when their institutional ID **and** email match a row together.
3. **An import will not change an address that is already on the roster.** A
   difference aborts the whole file, because an import must never silently rebind an
   identity. That is the right default, and it means a corrected file needs one of
   the two deliberate paths in "Correcting a roster address" below — otherwise the
   department's fix will be rejected.
4. **Check the email column's domain before importing.** The importer validates
   only that an address is well-formed. The department's spreadsheet for 2569 had
   17 institutional addresses and 57 personal ones (55 `gmail.com`, one `gmail.con`
   — a typo — and one `suthi.ac.th`). Since D-40, registration accepts a personal
   address when it is exactly the one the active roster row lists for that student
   ID, so those students register with it and are approved automatically. Fix
   typos such as `gmail.con` before importing: a student cannot receive the
   verification email at a mistyped address.

```bash
# Which active rows could never auto-approve, before or after an import:
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev \
  ~/.virtualenvs/roomreserve/bin/python manage.py shell -c "
from core.models import EligibleStudent
from core.services.identity import email_domain_allowed
rows = list(EligibleStudent.objects.filter(is_active=True))
bad = [r for r in rows if not email_domain_allowed(r.email)]
print(f'{len(rows) - len(bad)}/{len(rows)} rows can auto-approve; {len(bad)} cannot')"
```

Every import and deactivation is written to the audit trail
(`roster.imported`, `roster.deactivated`).

### Correcting a roster address

This is the remedy for the 57 rows above, and it is also the prerequisite for the
instrument-specific room: a student's instrument is found through the roster row,
and the row is linked to an account only when the address matches.

**One student** — Staff → Roster → *Correct email* on their row. A reason is
required, the change is audited (`roster.email_corrected`), and it is the right tool
for a handful of students.

**Many at once** — the department returns a corrected CSV, and you tell the import
that rewriting addresses is intended:

```bash
# Always dry-run first: it reports how many addresses it would rewrite.
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev \
  ~/.virtualenvs/roomreserve/bin/python manage.py import_roster corrected.csv \
  --dry-run --allow-email-change --batch "roster-emails-2569"
```

Then drop `--dry-run`. On the staff screen the same thing is the checkbox *"Also
rewrite existing addresses in this file"*, and it is off by default for a reason.

What neither path will do:

- **Take an address that already belongs to a different institutional ID.** That is
  rebinding one student to another person's identity, and it is refused whether or
  not the opt-in is set.
- **Change the student's account.** The account's own email is their login
  identity. If the roster row is linked to an account whose address no longer
  matches, the action says so and leaves the account — and its approval — alone. If
  you decide the account should be re-checked, do that deliberately with
  *Approve/Reject* on the staff user screen; nothing does it automatically.
- **Write an invalid or empty address**, or accept a correction with no reason.

Every rewritten address is counted in the import report and recorded on the audit
row, so a bulk correction is never silent.

## 8. The instrument-specific room

Room 303 (`ห้อง 303`) can only be **reserved** by piano and percussion students;
anyone may walk in once the hour has started and the room is still free. Changing
that is a staff action: **Configuration → Rooms → Who may reserve**. It is audited
(`room.audience_changed`) and it never cancels an existing booking.

Room 10 is now an active general practice room and is provisioned by `seed_rooms`
alongside rooms 1–9. It follows the same weekday opening schedule and general
reservation rules as the other general rooms. A historical placeholder row, if
present in a database from an earlier revision, is reactivated rather than
deleted so its history remains attached to the room.

**If a piano or percussion student says they cannot reserve the room**, the cause is
almost always one of two data problems, in this order:

1. **Their roster row is not linked to their account.** Linking happens when the
   institutional ID *and* email match at verification. A row holding a personal
   address (the 2569 roster has 57 of them) never links, so no instrument can be
   found from the roster. Until the email is fixed, the student's **declared
   instrument** from registration covers it (D-30): set or correct it on
   Staff → Users → Manage → *Set declared instrument*, audited as
   `user.declared_category_set`. The declaration works as soon as the account is
   approved, and a later roster link overrides it automatically.
2. **Nobody has categorised their instrument.** Their roster row says something the
   category table does not know, so it is `UNKNOWN` and `UNKNOWN` cannot open a
   restricted room. The roster screen (Staff → Roster) marks those rows in amber,
   and the import reports them:

```bash
# Which spellings have no category, and who they belong to:
DJANGO_SETTINGS_MODULE=roomreserve.settings.dev \
  ~/.virtualenvs/roomreserve/bin/python manage.py shell -c "
from core.models import EligibleStudent
from core.services import instruments
rows = EligibleStudent.objects.filter(is_active=True, instrument_category='UNKNOWN')
for spelling in instruments.unrecognised(r.instrument for r in rows):
    print(spelling, '->', rows.filter(instrument=spelling).count(), 'student(s)')"
```

To teach it a new spelling, add it to `CATEGORY_ALIASES` in
`core/services/instruments.py` and re-run the import (or the same shell one-liner
with an explicit update). The table is meant to grow from what the tooling reports,
not from guesses — and `tests/test_instruments.py` will fail if the migration's
frozen copy of the table drifts from the live one.

**Adding another restricted room** is one entry in `ROOM_OVERRIDES`
(`roomreserve/settings/base.py`) plus `ROOM_COUNT`, then `manage.py seed_rooms`;
or, for a room that already exists, the staff screen alone.

## 9. The teaching timetable (rooms 301, 303 and 304)

Rooms 301, 303 and 304 host classes from the department's semester report
(`ตารางห้อง อาคารศิลปกรรมชั้น3.pdf`). Those hours are `WeeklyBlock` rows —
recurring, keyed by weekday and hour — configured in `ROOM_WEEKLY_BLOCKS` and
applied by `manage.py seed_rooms`. A blocked hour refuses reservations *and*
walk-ins, the grid shows it as "Class" with the course name, and a booking that
already existed still checks in.

**When the department's schedule changes** (a new semester, a moved course):

1. Edit `ROOM_WEEKLY_BLOCKS` in `roomreserve/settings/base.py`. Weekday is
   Monday=0; `end_hour` is exclusive. An optional `valid_from`/`valid_until`
   bounds a block to a semester without deleting it.
2. Run `manage.py seed_rooms` — it replaces the configured rooms' blocks, so a
   removed entry disappears. Rooms absent from the setting keep their database
   rows; clear those manually if they are genuinely gone.
3. Bookings inside newly blocked hours are **not** cancelled — the students keep
   them. If a class genuinely needs the hour back, contact the students or use a
   closure; both are visible on the audit trail.

The currently configured class spans for room 301 are Monday 13:00–15:00 Piano
III/IV, Tuesday 08:00–10:00 Theo Mus E Trg I and 12:00–15:00 Ensemble,
Wednesday 13:00–15:00 Piano I, Thursday 13:00–15:00 Chorus/Theory, and Friday
13:00–15:00 Orchestration I. Room 301 is availability-only: its free hours are
shown but cannot be reserved or used as a walk-in. Room 304 remains blocked on
Monday 10:00–12:00 Counterpoint, Tuesday 12:00–14:00 Skill-Piano, Thursday
10:00–12:00 Harmony, and Friday 13:00–15:00 Wind Pedagogy; blank hours remain
reservable by request. Rooms 303 and 304 reservations require approval. There is no staff
screen for the timetable yet; it is deliberately a configuration change with a
seed run, not a click, because a wrong schedule blocks a whole room for a
semester.

## 10. Emergency closure

Staff → Closures → preview the range → confirm. The preview counts the sessions
that will be cancelled and flags any that are in progress; the confirm step
rechecks the same range under the shared lock, so a booking made in between is
still caught.

Reopening a closure does **not** resurrect cancelled bookings and does not make a
completed slot reusable. If students were wrongly penalised, record a
`ServiceIncident` (Staff → Incidents); voiding the incident's violations removes
the penalties without touching anyone else's.

---

## 11. Traps already encountered

**`DJANGO_SETTINGS_MODULE` in the environment overrides the test settings.**
pytest-django resolves the settings module as `--ds` → environment →
`pyproject.toml`. Exporting `DJANGO_SETTINGS_MODULE=roomreserve.settings.dev`
around a pytest run therefore replaces the test settings with development ones:
no test isolation, no in-memory mail or cache, the wrong static storage, and
dozens of unrelated failures. `tests/test_environment_guard.py` now fails loudly
if this happens. Never export it in a script that also runs pytest.

**`compilemessages` reads AppleDouble files.** On this exFAT volume
`manage.py compilemessages` walks the locale directory, hits `._django.po`, and
gives up. `django.mo` is still produced. To compile cleanly:

```bash
msgfmt -o locale/th/LC_MESSAGES/django.mo locale/th/LC_MESSAGES/django.po
```

**The Docker image needs the legacy builder here.** BuildKit's context sender
cannot read the extended attributes of the `._*` sidecars that macOS writes
beside every file on exFAT, and aborts with
`failed to xattr …: operation not permitted`. A `.dockerignore` does not help,
because the sender fails while walking. Use:

```bash
DOCKER_BUILDKIT=0 docker compose build web
```

`scripts/verify` tries BuildKit first and falls back automatically.

**SMTP port 1025 is often already taken.** `docker compose up mailpit` publishes
`127.0.0.1:1025` and fails with "address already in use" if anything else holds it
— on this machine a macOS Finder extension does. `scripts/verify` starts its own
catcher on 11025/18025 for this reason. For manual work, either free the port or
set `EMAIL_PORT` to a free one and point the catcher at it.

**`pip` is not installed in the venv.** Use `python -m …` and
`importlib.metadata` for versions.

**Docker must be running before any test run.** `open -a Docker`, then
`docker compose up -d db`. The suite is PostgreSQL-only by design.

**The frozen test clock and the outbox disagree by design.** `enqueue()` stamps
rows with `timezone.now()` while `clock.now()` follows the freeze. Tests that
drain the outbox must pass `timezone.now()` explicitly. `sanctions_under_review()`
reads the real clock — do not mix it with a frozen fixture.

**Static files in tests are plain storage (D-06).** Do not "fix" the
`No directory at: …/staticfiles/` warning by running `collectstatic` inside tests.

**`npm` skips devDependencies when `NODE_ENV=production` is in the shell.**
Always `npm install --include=dev`, or the Tailwind build silently produces
nothing. The Dockerfile does the same.

---

## 12. What is still manual

- **Physical poster check.** Whether the printed sheet is legible and hung on the
  correct door cannot be automated. The QR *targets* are verified; the doors are
  not.
- **Retention.** Nothing deletes automatically. The one-academic-year period is an
  unendorsed default until faculty approve it.
- **Thai copy review.** The catalogue was translated in bulk and has not been read
  by a Thai-speaking member of the department.
- **Privacy notice approval.** It is explicitly a draft needing faculty sign-off.
