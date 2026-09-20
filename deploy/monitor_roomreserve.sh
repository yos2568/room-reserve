#!/usr/bin/env bash
# Check public health, scheduler/outbox freshness, backup age, and disk space.
# A configured webhook or email recipient receives only a short failure summary.
set -Eeuo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-/root/roomreserve/compose.hostinger.yaml}"
ENV_FILE="${ENV_FILE:-/root/roomreserve/.env}"
ROOMRESERVE_BASE_URL="${ROOMRESERVE_BASE_URL:?ROOMRESERVE_BASE_URL must be set}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/roomreserve}"
BACKUP_MAX_AGE_SECONDS="${BACKUP_MAX_AGE_SECONDS:-86400}"
DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-85}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
ALERT_EMAIL_TO="${ALERT_EMAIL_TO:-}"
ALERT_EMAIL_FROM="${ALERT_EMAIL_FROM:-}"

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

  if [[ -n "$ALERT_EMAIL_TO" ]]; then
    send_email_alert() {
      local message="$1"
      [[ -r "$ENV_FILE" ]] || {
        echo "Cannot read ENV_FILE for email alert: $ENV_FILE" >&2
        return 1
      }

      # Reuse the application's Mailcow SMTP settings by default. Optional
      # ALERT_SMTP_* variables can override them without changing .env.
      set -a
      # shellcheck disable=SC1090
      . "$ENV_FILE"
      set +a

      export ALERT_SMTP_HOST="${ALERT_SMTP_HOST:-${EMAIL_HOST:-}}"
      export ALERT_SMTP_PORT="${ALERT_SMTP_PORT:-${EMAIL_PORT:-587}}"
      export ALERT_SMTP_USER="${ALERT_SMTP_USER:-${EMAIL_HOST_USER:-}}"
      export ALERT_SMTP_PASSWORD="${ALERT_SMTP_PASSWORD:-${EMAIL_HOST_PASSWORD:-}}"
      export ALERT_SMTP_STARTTLS="${ALERT_SMTP_STARTTLS:-${EMAIL_USE_TLS:-true}}"
      export ALERT_EMAIL_FROM="${ALERT_EMAIL_FROM:-${DEFAULT_FROM_EMAIL:-$ALERT_SMTP_USER}}"
      export ALERT_EMAIL_TO
      export MONITOR_SUMMARY="$message"

      command -v python3 >/dev/null || {
        echo "python3 is required for email alerts" >&2
        return 1
      }

      python3 - <<'PY'
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import getaddresses


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required for email alerts")
    return value


recipients = [address for _, address in getaddresses([required("ALERT_EMAIL_TO")])]
if not recipients:
    raise RuntimeError("ALERT_EMAIL_TO does not contain an email address")

message = EmailMessage()
message["Subject"] = "RoomReserve monitor failure"
message["From"] = required("ALERT_EMAIL_FROM")
message["To"] = ", ".join(recipients)
message.set_content(required("MONITOR_SUMMARY"))

host = required("ALERT_SMTP_HOST")
port = int(os.environ.get("ALERT_SMTP_PORT", "587"))
with smtplib.SMTP(host, port, timeout=15) as smtp:
    if os.environ.get("ALERT_SMTP_STARTTLS", "true").lower() in {"1", "true", "yes", "on"}:
        smtp.starttls(context=ssl.create_default_context())
    smtp.login(required("ALERT_SMTP_USER"), required("ALERT_SMTP_PASSWORD"))
    smtp.send_message(message)
PY
    }

    if ! send_email_alert "$summary"; then
      echo "RoomReserve email alert could not be sent" >&2
    fi
  fi

  if [[ -n "$ALERT_WEBHOOK_URL" ]]; then
    curl --fail --silent --show-error --max-time 15 \
      -H 'Content-Type: application/json' \
      --data-binary "{\"text\":\"$summary\"}" \
      "$ALERT_WEBHOOK_URL" >/dev/null
  fi
  exit 1
fi

echo "RoomReserve monitor passed"
