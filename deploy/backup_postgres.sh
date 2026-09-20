#!/usr/bin/env bash
# Create an encrypted custom-format PostgreSQL backup and copy it to independent
# storage. This script is intended to run on the VPS host, outside Docker.
set -Eeuo pipefail

umask 077

COMPOSE_FILE="${COMPOSE_FILE:-/root/roomreserve/compose.hostinger.yaml}"
ENV_FILE="${ENV_FILE:-/root/roomreserve/.env}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/roomreserve}"
BACKUP_REMOTE="${BACKUP_REMOTE:?BACKUP_REMOTE must point to a dedicated rclone destination}"
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT must be set}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

if [[ ! "$RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]]; then
  echo "RETENTION_DAYS must be a positive integer" >&2
  exit 2
fi
[[ -r "$ENV_FILE" ]] || { echo "Cannot read ENV_FILE: $ENV_FILE" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
command -v age >/dev/null || { echo "age is required" >&2; exit 2; }
command -v rclone >/dev/null || { echo "rclone is required" >&2; exit 2; }

# Compose's env file is also the source for the database name and user. It is
# never printed, and the resulting dump is encrypted before it leaves the host.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

POSTGRES_DB="${POSTGRES_DB:-roomreserve}"
POSTGRES_USER="${POSTGRES_USER:-roomreserve}"
mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_tmp="$BACKUP_DIR/.roomreserve-$stamp.dump"
encrypted="$BACKUP_DIR/roomreserve-$stamp.dump.age"
checksum="$encrypted.sha256"

cleanup() {
  rm -f -- "$dump_tmp"
}
trap cleanup EXIT

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc >"$dump_tmp"

age -r "$BACKUP_AGE_RECIPIENT" -o "$encrypted" "$dump_tmp"
sha256sum "$encrypted" >"$checksum"

rclone copyto "$encrypted" "${BACKUP_REMOTE%/}/$(basename "$encrypted")"
rclone copyto "$checksum" "${BACKUP_REMOTE%/}/$(basename "$checksum")"

# Keep a short local recovery window as well as the independent remote copy.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'roomreserve-*.dump.age*' \
  -mtime "+$RETENTION_DAYS" -delete
rclone delete "$BACKUP_REMOTE" --include 'roomreserve-*.dump.age*' --min-age "${RETENTION_DAYS}d"

echo "RoomReserve backup completed: $(basename "$encrypted")"
