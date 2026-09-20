#!/usr/bin/env bash
# Keep Mailcow's SMTP/IMAP certificate in sync with the certificate that
# Traefik renews for the Mailcow hostname.
set -Eeuo pipefail

umask 077

MAILCOW_ROOT="${MAILCOW_ROOT:-/opt/mailcow-dockerized}"
TRAEFIK_ACME_JSON="${TRAEFIK_ACME_JSON:-/var/lib/docker/volumes/traefik-h9li_traefik-letsencrypt/_data/acme.json}"
MAILCOW_HOSTNAME="${MAILCOW_HOSTNAME:-mail.yos.in.th}"
CERT_FILE="$MAILCOW_ROOT/data/assets/ssl/cert.pem"
KEY_FILE="$MAILCOW_ROOT/data/assets/ssl/key.pem"

command -v base64 >/dev/null || { echo "base64 is required" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 2; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 2; }
[[ -r "$TRAEFIK_ACME_JSON" ]] || {
  echo "Cannot read Traefik ACME storage: $TRAEFIK_ACME_JSON" >&2
  exit 2
}

tmp_dir="$(mktemp -d /tmp/roomreserve-mailcow-tls.XXXXXX)"
trap 'rm -rf -- "$tmp_dir"' EXIT

jq -r --arg hostname "$MAILCOW_HOSTNAME" \
  '.letsencrypt.Certificates[] | select(.domain.main == $hostname) | .certificate' \
  "$TRAEFIK_ACME_JSON" | base64 -d >"$tmp_dir/cert.pem"
jq -r --arg hostname "$MAILCOW_HOSTNAME" \
  '.letsencrypt.Certificates[] | select(.domain.main == $hostname) | .key' \
  "$TRAEFIK_ACME_JSON" | base64 -d >"$tmp_dir/key.pem"

openssl x509 -in "$tmp_dir/cert.pem" -noout -checkend 604800 >/dev/null || {
  echo "Traefik certificate is missing or expires within seven days" >&2
  exit 1
}

cert_modulus="$(openssl x509 -noout -modulus -in "$tmp_dir/cert.pem" | openssl md5)"
key_modulus="$(openssl rsa -noout -modulus -in "$tmp_dir/key.pem" 2>/dev/null | openssl md5)"
[[ "$cert_modulus" == "$key_modulus" ]] || {
  echo "Traefik certificate and key do not match" >&2
  exit 1
}

new_fingerprint="$(openssl x509 -in "$tmp_dir/cert.pem" -noout -fingerprint -sha256)"
old_fingerprint=""
if [[ -r "$CERT_FILE" ]]; then
  old_fingerprint="$(openssl x509 -in "$CERT_FILE" -noout -fingerprint -sha256 2>/dev/null || true)"
fi

if [[ "$new_fingerprint" == "$old_fingerprint" ]]; then
  echo "Mailcow TLS certificate is already current"
  exit 0
fi

install -m 0644 "$tmp_dir/cert.pem" "$CERT_FILE.new"
install -m 0600 "$tmp_dir/key.pem" "$KEY_FILE.new"
mv -f -- "$CERT_FILE.new" "$CERT_FILE"
mv -f -- "$KEY_FILE.new" "$KEY_FILE"

docker restart \
  mailcowdockerized-postfix-mailcow-1 \
  mailcowdockerized-dovecot-mailcow-1 \
  mailcowdockerized-nginx-mailcow-1 >/dev/null

echo "Mailcow TLS certificate synchronized for $MAILCOW_HOSTNAME"
