# Load & concurrency testing

Two different questions, two different tools — don't conflate them:

| Question | Tool | Where |
|---|---|---|
| Does the *database/service layer* stay correct when many bookings race for the same slot? (no double-booking, no 500s, clean refusals) | Already covered — in-process threads on independent Postgres connections | [`tests/test_concurrency.py`](../tests/test_concurrency.py) |
| Does the *running app* (gunicorn, sessions, CSRF, connection pool, nginx/caddy) hold up under ~100 real concurrent HTTP users? | Locust, real HTTP | `loadtest/locustfile.py` (this directory) |

Run the first with `pytest tests/test_concurrency.py`. This README is about the second.

## 1. Install

```bash
pip install locust
```

(Or add `locust` to a dev-only requirements file / `uv pip install locust` — it's a test tool, not a runtime dependency, so keep it out of `requirements/`.)

## 2. Start the app locally — never point this at production

```bash
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createcachetable
docker compose exec web python manage.py seed_demo --students 100
```

`seed_demo` is idempotent and creates 100 synthetic students (`660000001`
… `660000100`, password `demo-student-password-1`) plus the real room set.
The id is `66` plus a 7-digit index, the same format as `seed_demo`.
Credentials only exist in this local database — see
[`core/management/commands/seed_demo.py`](../core/management/commands/seed_demo.py).

Confirm it's up: `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/healthz/` should print `200`.

## 3. Realistic mixed load — 100 concurrent users, ramped

Each simulated user logs in as their own seeded student, looks at the room
board, and books whatever's open — a normal traffic pattern, just a lot of it
at once.

```bash
locust -f loadtest/locustfile.py --host http://localhost:8000 \
  -u 100 -r 10 --run-time 5m --headless \
  --csv loadtest/results/mixed
```

- `-u 100` — 100 concurrent simulated users
- `-r 10` — ramp up 10 users/second (avoid a thundering-herd login spike that isn't realistic)
- `--run-time 5m` — sustain for 5 minutes
- `--csv` — writes `loadtest/results/mixed_stats.csv` etc. for later review

Watch for in the output / CSV:
- **Failures** — anything other than a booking success (302) or a clean refusal
  (409) counts as a failure (see `_book()` in the locustfile) — a 500 here is a
  real bug.
- **p95 / p99 response time** on `confirm/ [POST]` — is it still sub-second at 100 users?
- `docker compose logs web --tail 200` for gunicorn worker timeouts or DB pool exhaustion.

Drop `--headless` and open <http://localhost:8089> to drive it interactively instead.

## 4. The race scenario — everyone fights for one slot

This is the HTTP-level counterpart to `test_twenty_users_race_for_one_slot`
in the pytest suite: confirm the same guarantee holds when the requests
actually go over HTTP through gunicorn, not just through Django's ORM in a
test transaction.

```bash
HOT_SLOT=1 locust -f loadtest/locustfile.py --host http://localhost:8000 \
  -u 50 -r 50 --run-time 30s --headless \
  --csv loadtest/results/hotslot
```

All 50 users resolve the same open `(room, slot)` pair on their first pass at
the room board, then hammer `confirm_booking` for it repeatedly for 30
seconds.

**Verify afterward** that at most one live booking exists for that slot:

```bash
docker compose exec web python manage.py shell -c "
from core.models import Booking
qs = Booking.objects.exclude(status=Booking.Status.CANCELLED).order_by('room_id','slot_start')
from collections import Counter
counts = Counter((b.room_id, b.slot_start) for b in qs)
dupes = {k: v for k, v in counts.items() if v > 1}
print('duplicate (room, slot) pairs:', dupes or 'none')
"
```

`dupes` must be `none`. If it isn't, that's a real double-booking bug in the
live stack (as opposed to the ORM-level test, which already passes) — check
whether it reproduces with `gunicorn --workers 1` vs multiple workers to
narrow it to a cross-process issue.

Also check the Locust summary line printed at the end (`failures=0` expected
— see `_summary()` in the locustfile): 409 refusals are expected and healthy
here; anything else is not.

## 5. Cleanup

`seed_demo` never deletes data, so re-running load tests just adds more
bookings for the synthetic students. To reset, drop and recreate the local
`db` volume (`docker compose down -v && docker compose up -d`) — never do
this against staging/production data.
