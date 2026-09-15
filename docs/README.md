# Room Reserve — documentation

Practice-room reservation for the Western Music Department, Faculty of Fine and
Applied Arts, Chulalongkorn University.

| Document | Read it for |
|---|---|
| [`acceptance-matrix.md`](acceptance-matrix.md) | Every A01–A30 ID mapped to the artefact that evidences it, and what is still blocked. |
| [`architecture.md`](architecture.md) | How the code is organised and why. |
| [`policy.md`](policy.md) | The booking rules, with the values currently in force. |
| [`privacy.md`](privacy.md) | What personal data is held, why, and for how long (draft, needs faculty approval). |
| [`runbook.md`](runbook.md) | Restart, restore, roll back, rotate secrets, add staff, and the traps already hit. |
| [`decisions.md`](decisions.md) | Every non-obvious choice and the rejected alternatives. |
| [`../QA_REPORT.md`](../QA_REPORT.md) | What was actually run, when, with what result, and what is not done. |

## Running it locally

```bash
open -a Docker                                   # the daemon must be up first
cd "/Volumes/Crucial2TB/All Codes/FAA/Room problem"
docker compose up -d db
~/.virtualenvs/roomreserve/bin/python manage.py migrate
~/.virtualenvs/roomreserve/bin/python manage.py seed_rooms
~/.virtualenvs/roomreserve/bin/python manage.py seed_demo
~/.virtualenvs/roomreserve/bin/python manage.py runserver 127.0.0.1:8000
```

Then open <http://127.0.0.1:8000/th/>.

`seed_demo` prints the synthetic student and staff credentials it creates. They
exist only in the local database and grant no access to any deployed environment.

## Checking it

```bash
bash scripts/verify            # every automated local check, with per-check status
bash scripts/verify            # (run as `bash`: exFAT carries no executable bit)
```

It exits non-zero if anything failed *or* was blocked, and writes per-check logs to
`artifacts/qa/verify-<timestamp>/`. Individual commands:

```bash
~/.virtualenvs/roomreserve/bin/python -m pytest -q                 # 257 tests, includes the browser suite
~/.virtualenvs/roomreserve/bin/python -m pytest -q -m "not browser"  # fast pass, no Chromium
~/.virtualenvs/roomreserve/bin/python -m ruff check .
~/.virtualenvs/roomreserve/bin/python -m ruff format --check .
```

## The parts worth knowing before you change anything

- **Rules live in `core/services/`, never in views.** Views parse, call one
  service through `run_view_operation`, and render the outcome.
- **Every mutation goes through `core/services/protocol.py`**, in a fixed order:
  lock → reconcile → idempotency → validate → savepoint → audit + outbox.
  Reconciling before validating is what makes a refused request still commit the
  reconciliation that made it correct.
- **`core/services/clock.py` is the only source of "now".**
- **`tests/browserlib.py` + the `browser` marker** are the real-browser suite.
  Fixed-time browser fixtures need `browser_clock` (process-wide), not the
  thread-local `frozen`.
- **Never export `DJANGO_SETTINGS_MODULE` around pytest.** It silently overrides
  the test settings. `tests/test_environment_guard.py` fails loudly if you do.
