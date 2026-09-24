# Handoff — Room Reserve · 24 September 2026

**Project:** `/Volumes/Crucial2TB/All Codes/FAA/Room problem`
**Repository:** `https://github.com/yos2568/room-reserve`
**Previous handoff:** `handoff22sep.md` (superseded by this file)
**Branch:** `main` at `516e322` (PR #5 merged). This handoff and the user manual are on
`docs/handoff-24sep`, open as PR #6 (not merged yet).
**Production:** `https://roomreserve.yos.in.th` (Hostinger VPS, Docker Manager project `roomreserve`)

## Honest status

`PRODUCTION_WORKING`. Sign-in, registration, password reset, email delivery,
QR door check-in messages, the staff set-password link, and room 301 approval
are live and were checked on the production site. Two owner tasks remain open
(DMARC tightening and deleting the old mailbox), plus optional follow-ups below.

## What was fixed or added on 23–24 September

| PR | What | Status |
|---|---|---|
| #2 | Migration `0015_create_cache_table` (login/register/reset returned **500** because the rate limiter's DatabaseCache table never existed) and `SITE_BASE_URL` passed in `compose.hostinger.yaml` (email links pointed at `http://localhost:8000`) | Live, migration applied |
| #3 | Door page (`/r/<room>/`) explains why there is no check-in button: too early (with minutes), awaiting approval, closed (with time), wrong room | Live |
| #4 | Staff → Users → Manage → **New set-password link** (superuser only, staff/teacher accounts only, shown once, single use, 24 h, not emailed, audited without token). Invitations page shows a full URL | Live |
| #5 | **D-39: room 301 reservable by approved request only** (teacher or admin named as its room administrator approves; no walk-in; class hours stay blocked). Door page status row translated (was raw `held`) | Live, `seed_rooms` run |

Full suite: 519 tests passed locally on 23 September (520 collected on 24 September; they
could not be re-run then, see "Local environment" below); CI green on every PR.

## Production configuration now in force

- `APP_IMAGE=ghcr.io/yos2568/room-reserve@sha256:ad843588453691a84e7c8ad02198722adce2c1ba08e9976ceacd8c6e2f388fd5` (main `516e322`)
- `SITE_BASE_URL=https://roomreserve.yos.in.th` (env var **and** a line in the Docker Manager compose YAML)
- Mail: `EMAIL_HOST=mail.yos.in.th`, `EMAIL_HOST_USER` and `DEFAULT_FROM_EMAIL` = `superuser@yos.in.th`
  (the old `roomreserve@yos.in.th` login no longer authenticates)
- `SUPPORT_CONTACT_EMAIL=superuser@yos.in.th`
- Admin account: username `maintainer`, email `superuser@yos.in.th`, password reset by the owner
- Only one account exists in production (the maintainer). No staff/teacher invited yet.

No password, token or secret is recorded here.

## How to deploy (the working procedure)

1. Merge the PR on GitHub; wait for the main CI run to publish the image.
2. Get the digest from the CI log line `pushing manifest for ghcr.io/yos2568/room-reserve:sha-<commit>@sha256:<digest>`.
3. hPanel → Docker Manager → **roomreserve** → Manage → set `APP_IMAGE` to `ghcr.io/yos2568/room-reserve@sha256:<digest>` → **Save and deploy**.
4. If the release has migrations: `ssh -i ~/.ssh/hostinger_ed25519 root@mail.yos.in.th 'docker exec roomreserve-web-1 python manage.py migrate --noinput'`
5. If room settings changed: same, with `python manage.py seed_rooms`.

The compose file and environment live in Docker Manager, not in a file you edit
on disk. Keep the Docker Manager YAML in step with `compose.hostinger.yaml`.

## Email / DNS state (verified)

- DNS for `yos.in.th` is hosted at **z.com** (`ns-a1/a3/a4.cloud.z.com`).
- MX `mail.yos.in.th` → `72.61.117.10`; PTR `72.61.117.10` → `mail.muaytune.com` (forward-confirmed).
- SPF `v=spf1 ip4:72.61.117.10 ip6:2a02:4780:5e:3a89::1 -all`
- DKIM selector `dkim` published; Mailcow signs outgoing mail `d=yos.in.th`.
- DMARC currently `v=DMARC1; p=none;`
- Test mail from `superuser@yos.in.th` to Gmail: **SPF, DKIM, DMARC all PASS** (24 Sep).
- Chula Outlook: mail arrives; Microsoft Safe Links sometimes shows "We can't check the safety of this website" (`url=null`). Workaround: copy the link into the browser.

## Open tasks, in order

1. **Tighten DMARC at z.com** (owner, in progress — was on "log in to z.com DNS").
   Change TXT `_dmarc.yos.in.th` to:
   `v=DMARC1; p=quarantine; rua=mailto:superuser@yos.in.th; adkim=r; aspf=r; pct=100`
   Verify afterwards: `dig @8.8.8.8 +short TXT _dmarc.yos.in.th`
   With `p=quarantine`, daily aggregate reports arrive in the `superuser@` inbox; glance
   at the first few to confirm no legitimate mail is failing.
2. **Delete the old `roomreserve@yos.in.th` mailbox** in Mailcow admin (owner must click Delete; Claude does not permanently delete data).
   Safe now: the new sender `superuser@` passed SPF/DKIM/DMARC on 24 Sep. Do it after task 1 is verified.
3. **Invite staff and teachers** (Staff → Invitations), then **name the teacher who approves room 301** (Staff → Configuration → room 301 → add room administrator; maintainer only). A test staff account can use `superuser+stafftest@yos.in.th` (Mailcow delivers plus-addresses to the superuser inbox).
4. **Reprint door posters** from Staff → Posters (Staff web sheet uses the correct URL; the CLI `generate_posters` uses `SITE_BASE_URL`).
5. **Review and merge PR #6** (this handoff + the user manual, already committed on
   `docs/handoff-24sep`). Docs only, so no deploy is needed. Its first CI run failed
   lint/format on `docs/manual/build/*.py`; those scripts were cleaned up in a follow-up
   commit (layout only, plus one unused import removed). Merge once CI is green.

## User manual (new, not yet reviewed by the owner in print)

`docs/manual/`:
- `roomreserve-manual.md` — Thai manual, 3 levels (นิสิต / อาจารย์ / แอดมิน), warnings and tips
- `roomreserve-manual.pdf` — designed A4, 14 pages (cover, contents, colour per level)
- `roomreserve-manual.html` — the print source
- `img/` — 22 screenshots taken from a **synthetic** local database (no real student data)
- `build/` — `scenario.py` (demo data), `shoot.py` (screenshots), `build_html.py` + `print_pdf.py` (MD → HTML → PDF)

Rebuild after editing the MD. Both steps use the project venv (`markdown` is now in
`requirements/dev.txt`, Playwright already was):

```
~/.virtualenvs/roomreserve/bin/python docs/manual/build/build_html.py docs/manual/roomreserve-manual.md docs/manual/roomreserve-manual.html
~/.virtualenvs/roomreserve/bin/python docs/manual/build/print_pdf.py docs/manual/roomreserve-manual.html docs/manual/roomreserve-manual.pdf
```

Screenshots need the local demo DB `roomreserve_manual` (created with
`migrate`, `seed_rooms`, `seed_demo --students 12 --staff 2`, then
`manage.py shell < docs/manual/build/scenario.py`) and
`shoot.py <out-dir>` with `ALLOW_TEST_CLOCK=true`.

## Working notes for the next session

- Claude's auto mode blocks SSH reads/writes on production and PR merges; the
  owner runs those in the terminal pane. Give manual steps **one at a time**.
- `diag_mail.sh` (read-only mail diagnosis) was a scratch script and **is not in the repo
  or the project folder**; rewrite it if needed. Its checks were:
  app mail env (no password), accounts with the address, recent
  `password_reset` notifications with `last_error`, scheduler log, Postfix log.
- Untracked on purpose: `.claude/`, `uv.lock`, floor-plan images, `Room 304.png`
  (owner decision still pending from `handoff22sep.md`).
- Local Docker Postgres has two extra databases: the dev DB (contains real
  roster data — never use for screenshots) and `roomreserve_manual` (synthetic).

## Local environment (checked 24 September)

- **Project venv:** `~/.virtualenvs/roomreserve` was missing and was rebuilt on 24 Sep
  (Python 3.12, `requirements/dev.txt`, Playwright Chromium). To rebuild again:
  `uv venv --python 3.12 ~/.virtualenvs/roomreserve && uv pip install --python ~/.virtualenvs/roomreserve/bin/python -r requirements/dev.txt && ~/.virtualenvs/roomreserve/bin/python -m playwright install chromium`
- **Docker is not installed** on this Mac right now (`docker` not found), so the local
  Postgres (test DB, dev DB, `roomreserve_manual`) is unavailable. Tests error on DB
  connection and the manual's screenshots cannot be retaken until the owner reinstalls
  Docker Desktop and starts `compose.yaml`. Whether the old volumes (with the dev and
  manual databases) survived depends on how Docker was removed; if they are gone,
  recreate `roomreserve_manual` as described above. CI is unaffected.

## Possible follow-ups (not started)

- Send HTML email with a proper link alongside the plain text, so Outlook Safe Links handles links reliably.
- Staff password link for students (currently staff/teacher only, by design).
- Commit decision on the leftover `Booking.checkin_token` column (unused since D-37).
