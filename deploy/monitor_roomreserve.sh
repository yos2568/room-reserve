#!/usr/bin/env bash
# Check public health, scheduler/outbox freshness, backup age, and disk space.
# A configured webhook receives only a short failure summary.
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-/root/roomreserve/compose.hostinger.yaml}"
ENV_FILE="${ENV_FILE:-/root/roomreserve/.env}"
ROOMRESERVE_BASE_URL="${ROOMRESERVE_BASE_URL:?ROOMRESERVE_BASE_URL must be set}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/roomreserve}"
BACKUP_MAX_AGE_SECONDS="${BACKUP_MAX_AGE_SECONDS:-86400}"
DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-85}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"

failures=()
fail() { failures+=("$1"); }

for path in healthz/ readyz/; do
  if ! curl --fail --silent --show-error --max-time 15 "$ROOMRESERVE_BASE_URL/$path" >/dev/null; then
    fail "${path%/} is unhealthy"
  fi
done

if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T web python manage.py shell -c '
from datetime import timedelta
from django.utils import timezone
from core.models import JobHeartbeat, Notification

now = timezone.now()
heartbeat = JobHeartbeat.objects.filter(name="tick").first()
if heartbeat is None or now - heartbeat.last_run_at > timedelta(seconds=180):
    raise SystemExit("scheduler heartbeat is stale")
oldest = Notification.objects.filter(status="PENDING").order_by("next_attempt_at").first()
if oldest is not None and now - oldest.next_attempt_at > timedelta(seconds=600):
    raise SystemExit("outbox has a message older than ten minutes")
' >/dev/null; then
  fail "scheduler or outbox freshness check failed"
fi

latest_backup="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'roomreserve-*.dump.age' -print0 \
  | xargs -0 -r ls -1t 2>/dev/null | head -n 1)"
if [[ -z "$latest_backup" ]]; then
  fail "no encrypted backup exists"
else
  backup_mtime="$(stat -c %Y "$latest_backup" 2>/dev/null || stat -f %m "$latest_backup")"
  if (( $(date -u +%s) - backup_mtime > BACKUP_MAX_AGE_SECONDS )); then
    fail "latest backup is older than the configured limit"
  fi
fi

disk_used="$(df -P "$BACKUP_DIR" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
if [[ -n "$disk_used" && "$disk_used" -ge "$DISK_WARN_PERCENT" ]]; then
  fail "backup filesystem is ${disk_used}% full"
fi

if ((${#failures[@]})); then
  summary="RoomReserve monitor failed: $(IFS='; '; echo "${failures[*]}")"
  printf '%s\n' "$summary" >&2
  if [[ -n "$ALERT_WEBHOOK_URL" ]]; then
    curl --fail --silent --show-error --max-time 15 \
      -H 'Content-Type: application/json' \
      --data-binary "{\"text\":\"$summary\"}" \
      "$ALERT_WEBHOOK_URL" >/dev/null
  fi
  exit 1
fi

echo "RoomReserve monitor passed"
