#!/usr/bin/env bash
# Nightly pg_dump of the app database; keeps 30 days locally.
set -euo pipefail

APP_DIR="${APP_DIR:-/usr/share/nginx/aitutor.qlass.in/public_python_aios}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/skoolgpt}"

if [ -z "${DATABASE_URL:-}" ]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' "$APP_DIR/.env" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
fi
[ -n "$DATABASE_URL" ] || { echo "DATABASE_URL not set" >&2; exit 1; }

mkdir -p "$BACKUP_DIR"
out="$BACKUP_DIR/skoolgpt-$(date +%F).dump"
pg_dump -Fc "$DATABASE_URL" > "$out"
gzip -f "$out"
find "$BACKUP_DIR" -name 'skoolgpt-*.dump.gz' -mtime +30 -delete
echo "backup written: $out.gz"
