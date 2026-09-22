# Handoff — Room Reserve V3 · 22 September 2026

**Project:** `/Volumes/Crucial2TB/All Codes/FAA/Room problem`
**Repository:** `https://github.com/yos2568/room-reserve` (private)
**Specification:** `roomreserveapp.v3.md`
**Previous handoff:** `handoff21sep.md` (superseded by this file)
**Branch:** `main` · this checkpoint includes local account onboarding; push all
unpublished commits before deployment.
**Local preview:** `http://localhost:8010/th/` (`.claude/launch.json`, name `roomreserve-dev`)

## Honest status

`LOCAL_PASS_CANDIDATE`. **495 tests pass** on PostgreSQL, including the Chromium
browser tests; ruff check and format are clean; `makemigrations --check` finds nothing.
Nothing is deployed to Hostinger and no real student has used the app.

The local development database has since received the staff and maintainer
accounts described in the local database checkpoint below. Database rows are not
captured by Git and must be onboarded separately in production.

Everything described in `handoff21sep.md` is now committed (it was all
uncommitted working tree before this session). Two ultrareviews ran; every finding
was verified and fixed except one efficiency item (§6).

## 1. What changed this session, in commit order

| Commit | What |
|---|---|
| `b167999` | Checkpoint: the whole handoff21sep working tree (complaints, token locking, room board, move booking, room policy). |
| `a48b9ec` | ruff format/fix on five files left unformatted in the checkpoint. |
| `918cabc` | Review fixes: walk-ins bypassed approval for 303/304; room 301 (view-only) and approval rooms were offered as walk-in suggestions; the repeat cap ran after building the date list; `seed_rooms` reset staff room-profile edits; the room-move email said "room unchanged"; `COMPLAINT_RECIPIENT_EMAIL` defaulted to a real person. |
| `16e77ab` | Three tests that depended on the real date or on language state leaked from earlier tests. |
| `432cac5` | Ultrareview of PR #1: maintainer-only room-admin grant skipped `STAFF_ALLOWED_IPS`; details edits on pending bookings were dropped as stale; approval emails after a move were swallowed by a dedupe key; a non-numeric `room_id` in a move was a 500; small clean-ups. |
| `a86b953` | Browser check: phone cells showed 12 identical "Reserve" buttons with no hour; the colour key contradicted the grid (free was crimson); ~190 Thai strings missing or fuzzy; week view squeezed room names on phones; phone first screen was all heading. |
| `622efd0` | **D-37 — check in only at the door's printed QR.** Email QR link and the My bookings button no longer check anyone in. |
| `fa24041` | **D-38 — book 2 days ahead, hold at most 4 upcoming hours; weekly repeat removed.** |

PR #1 (`main` → `review-base`) was review-only; it is closed and `review-base` deleted.

## 2. Booking rules now in force (policy table in `docs/policy.md`)

| Rule | Value | Where |
|---|---|---|
| Horizon | 2 days ahead, including today | `POLICY_HORIZON_DAYS`, versioned policy |
| Daily limit | 2 bookings per person per day | `POLICY_DAILY_QUOTA` |
| Upcoming hours | at most 4 not yet finished, walk-ins included; one frees up when its hour ends | `POLICY_MAX_UPCOMING_HOURS`, staff Policy screen |
| Weekly repeat | not offered; existing series can still be cancelled | — |
| Check-in | door QR only, from the hour's start until 15 min after | `POLICY_CHECKIN_GRACE_MINUTES` |
| Approval rooms (303, 304) | advance requests only; **no walk-in** | `requires_approval` |
| Room 301 | timetable only; never reservable, walkable or suggested | `availability_only` |

Policy values are **versioned**: settings only seed a *new* database. The local dev
database was moved to policy **v2** (2 days, 4 hours) this session. Production will
start with the new defaults on first deploy.

## 3. Check-in (D-37) — what it does and does not prove

- A student checks in only on the room page the printed door poster opens:
  `/r/<room>/` → button posting to `/r/<room>/bookings/<booking>/check-in/`.
  The service still enforces owner, room and window under the lock.
- Emails and My bookings now say "scan the QR code on the room door".
- **A photo of the poster still works from anywhere.** Check-in is a declaration;
  staff spot checks are the backstop; staff manual check-in is the fallback.
- **Walk-ins ("Use now") still work from anywhere** — same loophole, not changed.
- Stronger options were weighed with the owner and deferred until the pilot shows a
  need: a changing TOTP code on a display at each door (~600–1,200 THB per room,
  e-ink DIY or key-fob token), a scanner at the floor entrance reading a
  per-booking QR, or restricting check-in to the building's network.
- `Booking.checkin_token` is no longer written; the column can be dropped later.

## 4. Decisions still needed from the owner

1. **Three untracked floor-plan images** (`floor-plan-isometric.webp`,
   `floor-plan-polished.png`, `floor-plan.png`): keep and commit, delete, or add to
   `.gitignore`. Nothing references them. `git add core` keeps sweeping them into
   commits — twice this session they had to be removed with `--amend`.
2. **`uv.lock` and `.claude/`** are untracked on purpose. `.claude/launch.json` now
   points at `~/.virtualenvs/roomreserve/bin/python` (the old `./.venv` never existed).
3. **`SUPPORT_CONTACT_EMAIL` defaults to a real person's address**
   (`Sitanun.S@chula.ac.th`) and is shown on every page. Fine if she is the public
   contact, but it should come from the environment like `COMPLAINT_RECIPIENT_EMAIL`.
4. **Capacity labels** show "1" for every room, including lecture room 301. Correct
   the data or remove the labels (handoff21sep item 5).
5. **The student roster spreadsheet is still in git history** (commit `55d7650`) on
   the private GitHub repo. Only a history rewrite removes it.

## Local database onboarding checkpoint

Applied to the local PostgreSQL database only; no Hostinger or production database
was changed:

- Nine owner-supplied staff identities were stored as lowercase operational-staff
  accounts. Each has a pending single-use activation invitation and no preset
  password.
- The single technical maintainer account was created for the owner-supplied admin
  address. A password-reset invitation is queued; no bootstrap password or token
  is recorded in this handoff.
- The outbox worker must be running for the activation and password-reset messages
  to be delivered. Repeat this onboarding deliberately against production after
  deployment and verify the destination before sending real invitations.

## 5. Needs someone else

- **Thai review.** ~190 strings (commit `a86b953`) and every message added since were
  translated by the assistant. The existing translation of "Check in" is
  ลงชื่อเข้าใช้ (usually "sign in"); consider เช็กอิน.
- **Class timetable.** Room 304's blocked hours are approximate; correct them from
  the department's timetable images before the pilot.
- **Print the door posters** from the staff Posters page and check each QR opens the
  right room — this is now the only way students check in.

## 6. Open work (code)

1. **Signed-in browser check not done.** Signed-out pages were checked in Thai and
   English at desktop and phone widths. Booking, requests, My bookings, the
   complaint form and staff screens still need a pass: sign in at
   `/th/login/` as `660000001` / `demo-student-password-1` (staff:
   `demo-staff-password-1`). The assistant may not type passwords, so a person
   signs in and the assistant continues.
2. **Efficiency.** The suggestions panel rebuilds the whole grid on every 30-second
   refresh, and the week view recomputes room options for each of 7 days.
3. **handoff21sep leftovers:** confirm the owner accepts configuration-only editing
   for room 301's timetable; no staff timetable editor exists. Remove capacity
   labels if wanted (§4.4).
4. **Walk-ins from anywhere** (§3) — tie "Use now" to the door page if wanted.

## 7. Before deploying to Hostinger

1. Push all unpublished commits, including this handoff update; deploy only a
   committed revision.
2. Production environment must set `COMPLAINT_RECIPIENT_EMAIL` (the app refuses to
   start without it), plus `SITE_BASE_URL`, `DEFAULT_FROM_EMAIL` and the Mailcow
   SMTP settings. Consider `SUPPORT_CONTACT_EMAIL` (§4.3).
3. `migrate` (includes 0012 and 0013), `collectstatic`, rebuild CSS if needed.
4. Restart Django and the outbox worker; send one controlled complaint; check the
   staff outbox page.
5. Put up the printed door posters (§5).

## 8. Environment notes learned this session

- **Pushes** may require the approved network path in this environment; after
  pushing, verify the remote branch with `git ls-remote origin refs/heads/main`.
- **`/code-review ultra` with no argument reviews only what `main` has that GitHub
  does not** — after a push it finds nothing. To review pushed work, open a
  review-only PR from `main` into a base branch and run `/code-review ultra <PR#>`.
  Don't use `gh pr close --delete-branch` on such a PR: it tries to delete `main`.
- **AppleDouble `._` files inside `.git`** caused `non-monotonic index` errors on
  every git command. Fixed with `dot_clean -m .git`; they can return after git
  repacks on exFAT — run it again if the warning reappears.
- **The preview on port 8004** (started with `--noreload`) served stale code; use
  the `roomreserve-dev` preview on 8010. After `npm run build:css`, hard-reload the
  browser — it caches `/static/css/tailwind.css`.
- **Thai catalogue:** never run `makemessages` in place (it rewrites ~900 lines and
  marks entries fuzzy, which gettext ignores). Add entries by hand, then
  `msgfmt --check -o locale/th/LC_MESSAGES/django.mo locale/th/LC_MESSAGES/django.po`.
  `tests/test_ui_regressions.py` fails if any entry is empty or fuzzy.
- **Tests that book later dates** (weekends, next Monday) need the `wide_horizon`
  fixture now that the window is 2 days.
- Earlier gotchas in `handoff15sep26.md` §9 still apply (Docker first, never export
  `DJANGO_SETTINGS_MODULE` around pytest, legacy Docker builder on exFAT).

## 9. Commands

```bash
cd "/Volumes/Crucial2TB/All Codes/FAA/Room problem"
open -a Docker && docker compose up -d db
~/.virtualenvs/roomreserve/bin/python -m pytest -q          # 495 tests
~/.virtualenvs/roomreserve/bin/python -m ruff check . && ~/.virtualenvs/roomreserve/bin/python -m ruff format --check .
bash scripts/verify                                           # the 12-check gate
npm run build:css                                             # after template class changes
git push origin main                                          # run yourself
```
