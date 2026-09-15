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
   when their institutional ID **and** email match a row together. It also refuses
   to change the email on an existing institutional ID — a contradiction aborts the
   whole file rather than overwriting it.
3. **Check the email column's domain before importing.** The importer validates
   only that an address is well-formed. The department's spreadsheet for 2569 had
   17 institutional addresses and 57 personal ones (55 `gmail.com`, one `gmail.con`
   — a typo — and one `suthi.ac.th`). Registration **refuses** a non-institutional
   domain, so those 57 rows can never match a registration and those students have
   to be approved by hand. If the intent is automatic approval, the file needs the
   institutional addresses first.

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

## 8. The instrument-specific room

Room 10 (`ห้องซ้อมใหญ่`) can only be **reserved** by piano and percussion students;
anyone may walk in once the hour has started and the room is still free. Changing
that is a staff action: **Configuration → Rooms → Who may reserve**. It is audited
(`room.audience_changed`) and it never cancels an existing booking.

**If a piano or percussion student says they cannot reserve the room**, the cause is
almost always one of two data problems, in this order:

1. **Their roster row is not linked to their account.** Linking happens when the
   institutional ID *and* email match at verification. A row holding a personal
   address (the 2569 roster has 57 of them) never links, so no instrument can be
   found for that account. Fix the roster email first — that is the runbook section
   above, and it is the more common cause.
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

## 9. Emergency closure

Staff → Closures → preview the range → confirm. The preview counts the sessions
that will be cancelled and flags any that are in progress; the confirm step
rechecks the same range under the shared lock, so a booking made in between is
still caught.

Reopening a closure does **not** resurrect cancelled bookings and does not make a
completed slot reusable. If students were wrongly penalised, record a
`ServiceIncident` (Staff → Incidents); voiding the incident's violations removes
the penalties without touching anyone else's.

---

## 9. Traps already encountered

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

## 10. What is still manual

- **Physical poster check.** Whether the printed sheet is legible and hung on the
  correct door cannot be automated. The QR *targets* are verified; the doors are
  not.
- **Retention.** Nothing deletes automatically. The one-academic-year period is an
  unendorsed default until faculty approve it.
- **Thai copy review.** The catalogue was translated in bulk and has not been read
  by a Thai-speaking member of the department.
- **Privacy notice approval.** It is explicitly a draft needing faculty sign-off.
