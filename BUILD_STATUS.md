# BUILD_STATUS — Room Reserve V3

**Last updated:** 2026-09-15 (instrument-specific tenth room)
**Status:** `LOCAL_PASS_CANDIDATE` — every automated local check passes.
Deployment and pilot are **BLOCKED** on missing external inputs.

Superseded detail lives in `handoff15sep.md` (the previous checkpoint),
`docs/acceptance-matrix.md` (A01–A30 → evidence, plus the **deviations from V3**)
and `QA_REPORT.md` (what was run, with what result).

## Phase

| Phase | State |
|---|---|
| P0 Environment, schema, services, synthetic fixtures | **Complete** |
| P1 Identity, availability, booking, quota, cancellation | **Complete** |
| P2 Check-in, walk-in, lifecycle, strikes, outbox | **Complete** |
| P3 Staff operations, bilingual UI, posters, incidents | **Complete**, with browser evidence |
| P4 Integration, Docker, recovery, verification runner | **Complete locally** |
| P5 Real hosting, mail, physical posters, faculty pilot | **Blocked** |

## Revision

- **Git:** `21d821d7641fdeabb05eb367df54eac6854eae08`, with a **dirty working tree**
  (86 modified/added/untracked files). **The work is not committed**, so the revision
  hash does not identify the verified code. Hashes are in `QA_REPORT.md` §8.
  **Committing is the outstanding action.**
- **Runner:** `scripts/verify`

## Latest real commands and results

```bash
bash scripts/verify              # exit 0 — PASS=12 FAIL=0 BLOCKED=0
python -m ruff check .           # All checks passed!
python -m ruff format --check .  # 100 files already formatted
python -m pytest -q              # 343 passed in 30.94s (27 of them in Chromium)
```

Full evidence: `artifacts/qa/verify-20260915T154813Z/`. The bootstrap reports
`10 rooms` and asserts exactly one room is restricted to piano and percussion.

## Added since the last checkpoint: the instrument-specific tenth room

The department's larger room, reservable only by piano and percussion students.
Full reasoning in `docs/decisions.md` D-21 … D-26; V3 deviations in
`docs/acceptance-matrix.md`.

- **Canonical instrument categories.** The roster's `instrument` column is free text
  with 22 spellings across 74 students, including the same instrument twice. A new
  `EligibleStudent.instrument_category` is derived by a lookup table and backfilled
  by migration `0005`; anything unrecognised becomes `UNKNOWN`, which fails closed
  and is **reported by the import** and flagged on the staff roster screen.
- **A per-room reservation audience** (`EVERYONE` / `LISTED` + categories), failing
  closed on an empty list, enforced in one place
  (`eligibility.assert_may_reserve_room`) and only on **reserving**.
- **Walk-in stays open to everyone** once an hour has started and the room is free;
  check-in is untouched, so existing bookings survive a restriction.
- **A staff control** to change a room's audience (Configuration → Rooms), audited as
  `room.audience_changed`, which never cancels an existing booking.
- **Grid** shows `SlotState.RESTRICTED` with a label that names the audience, rather
  than an unexplained gap.
- **Room count is now configuration** (`ROOM_COUNT` / `ROOM_OVERRIDES`), replacing
  four duplicated literals and a dead settings constant.
- 66 new tests: `test_instruments.py` (37), `test_room_audience.py` (20), roster
  category coverage, and 3 browser flows. `scripts/verify` and the poster tests
  assert per active room instead of the literal nine.
- Thai catalogue recompiled: **592 entries, zero fuzzy, zero untranslated**. Seven
  fuzzy entries had wrong or unrelated Thai (`Percussion` → "เวอร์ชัน") and were
  corrected.

**The dev database has the room**: `seed_rooms` created it, and 9 real roster
students (6 piano, 3 percussion) may reserve it. The other 65 cannot yet — see the
roster email item below, which is now a hard prerequisite for this feature.

## Test suite

343 tests, PostgreSQL only. 27 driven by Chromium through `live_server`.

| File | Tests | File | Tests |
|---|---|---|---|
| `test_permissions.py` | 59 | `test_instruments.py` | 37 |
| `test_identity.py` | 33 | `test_room_audience.py` | 20 |
| `test_booking.py` | 27 | `test_roster.py` | 18 |
| `test_calendar.py` | 24 | `test_browser_walkin.py` | 11 |
| `test_outbox.py` | 23 | `test_clock.py` | 10 |
| `test_scheduler.py` | 18 | `test_sanctions.py` | 10 |
| `test_lifecycle.py` | 18 | `test_browser_bilingual.py` | 6 |
| `test_content_pages.py` | 8 | `test_concurrency.py` | 7 |
| `test_environment_guard.py` | 4 | `test_browser_privacy_posters.py` | 4 |
| `test_browser_student.py` | 3 | `test_browser_staff.py` | 3 |

## Remaining, in order

| # | Item | Why it is not done |
|---|---|---|
| 1 | **Commit the working tree** | 86 files uncommitted on top of `21d821d`; the largest process risk. |
| 2 | **Correct the roster email column** | 57 of 74 students have a personal address on file, so they cannot self-register, cannot be matched, and now also **cannot reserve the instrument-specific room** — the roster link is what carries their instrument. Needs the departmental institutional addresses; runbook §7. |
| 3 | **Clear the demo logins** | 100 synthetic students + 10 staff remain active and bookable. |
| 4 | A30 p95 latency | Needs the intended deployment hardware. |
| 5 | A27 `check --deploy`, rollback rehearsal, CI image push | Needs real hostnames/TLS, a previous image, registry credentials. |
| 6 | A29 physical poster check | Printing and doors; and the poster for the tenth room must be printed too. |
| 7 | Thai copy review | 592 entries; the new ones were translated here and still need a Thai speaker. |
| 8 | Deployment, then pilot | Real SMTP, domain, TLS, backup destination, alerting, then ~10 students for two weeks. |

## Blockers and the smallest required action

- **Deployment** — a host with a domain, TLS, real SMTP credentials, a backup
  destination and alerting contacts. Nothing else is missing.
- **Pilot** — faculty endorsement of the data-handling basis and retention period,
  plus about ten approved students for two weeks.
- **No tool or software blocker remains.**

## Real roster imported (2026-09-15)

The department's four-year undergraduate list (74 students) is in the **local
development database**, replacing the 100 synthetic demo rows:

```
74 rows read; 74 valid; 74 were created; 0 unchanged; 0 errors.
100 stale entries were deactivated.
```

- Active roster: **74** (year 1: 17, year 2: 20, year 3: 19, year 4: 18);
  100 synthetic rows deactivated; `User` accounts untouched (110).
- Batch label `roster-4years-2569`; both actions are in the audit trail
  (`roster.imported`, `roster.deactivated`).
- **Only 17 of the 74 can auto-approve a registration**, and **only 9 of the 74 can
  reserve the instrument-specific room** (6 piano, 3 percussion). Both limits come
  from the same root cause: the sheet's email column holds 57 personal addresses
  (55 `gmail.com`, one `gmail.con`, one `suthi.ac.th`), and the roster row is linked
  to an account only when the ID *and* email match. See D-20/D-22 and runbook §7–§8.
- The import had **no tests**; `tests/test_roster.py` (18) now covers validation,
  atomicity, the preflight, deactivation, export → import round trip, and the
  instrument category.

⚠️ The development database holds real personal data. It must not be copied into
artefacts or reports. `scripts/verify` uses its own throwaway databases, so no real
data enters the evidence.

## Environment

| Tool | Version |
|---|---|
| Python (venv) | 3.12.11 at `~/.virtualenvs/roomreserve` (APFS; the project is on exFAT) |
| PostgreSQL | 16.4, Docker named volume `roomreserve_pgdata` |
| Docker / Compose | 28.1.1 |
| Django / psycopg | 5.2.17 / 3.3.5 |
| Playwright / Chromium | 1.62.0 / `chromium-1234` |
| Host | macOS 26.6.2 arm64, Apple M4 Pro (14 cores), 64 GB |

## Decisions

D-01 … D-26 are recorded in `docs/decisions.md`, including D-09 (process-wide test
clock), D-10 (registration is one operation), D-17 (the async guard is stood down
only for browser runs), D-21…D-26 (the room audience, canonical instrument
categories, the ten-room deviation, and why the audience applies to reserving only).

## Next action

Commit the working tree, then open the deployment conversation with the smallest
missing input: a host and a domain.
