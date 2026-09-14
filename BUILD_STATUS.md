# BUILD_STATUS — Room Reserve V3

Last updated: 2026-09-14 (Phase P0, pre-implementation checkpoint)

## Phase

**P0 — repository inspection and environment.** No application code written yet.

## Current state

Reconnaissance complete. Repository is **not yet a git repository** (`git init` still to run).
No application exists; the directory currently holds only specification and presentation material.

Existing, unrelated work that must be preserved:

- `roomreserveapp.md`, `roomreserveapp.v1.2.md`, `roomreserveapp.v3.md` — specs (V3 authoritative)
- `deepseek-v3-build-loop.md` — execution prompt
- `outputs/room-plan/`, `outputs/plan-review/`, `assets/room-plan/` — presentation artefacts (pptx/pdf/png)
- `graft/` — graft repo-map cache (regenerable, gitignored)
- `.presentation-build/`, `.chart-data-TJiVp4/`, the PDF and XLSX source documents

## Environment recorded

| Tool | Version / status |
|---|---|
| macOS | darwin (Apple Silicon, `/opt/homebrew`) |
| Python (venv target) | 3.12.11 at `/opt/homebrew/bin/python3.12` |
| uv | available at `~/.local/bin/uv` |
| Docker | 28.1.1, daemon **started** this session and reachable |
| Docker Compose | v2.35.1-desktop.1 |
| Node / npm | v22.22.3 / 10.9.8 |
| git | `/usr/bin/git`, no repo in this directory |
| psql (host) | **not installed** — PostgreSQL will be provided by Compose, not the host |
| Network | verified (`pip download django==5.2` succeeded) |

## Decisions recorded so far

- Project root is this directory; Django project `roomreserve/` and app `core/` live beside the specs.
- `.venv` inside the project root; dependencies to be pinned to exact tested versions.
- PostgreSQL 16 and the dev mail catcher come from Compose (host has no psql).

## Blockers

- **BLOCKED (tooling):** `uv venv` / `uv pip install` was denied by the permission prompt, so the
  virtualenv and dependency install have not run. Resume by either relaunching with `--yolo`
  (bypass) or pre-approving `Shell(uv:*)`, `Shell(docker compose:*)` and the `.venv/bin/*` commands.

## Next action

1. Create `.venv` with Python 3.12 (or 3.11 if the mount rejects venv symlinks) and install pinned deps.
2. `git init` and commit the specs so revisions are traceable for QA evidence.
3. Write `compose.yaml`, `Dockerfile`, `.env.example`, `requirements/*.txt`, settings split.
4. Build the domain models and the `core` migration set (`BookingControl` singleton pre-seeded).

## Acceptance IDs

None attempted yet. A01–A30 remain unimplemented; see `docs/acceptance-matrix.md` (not yet created).
