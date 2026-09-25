#!/usr/bin/env bash
# Nightly backup of the app database AND backend/static/uploads, with
# optional encryption + off-box copy, plus a restore drill.
#
#   bash scripts/backup_db.sh                 # nightly (cron, 02:30 IST)
#   bash scripts/backup_db.sh --restore-check # monthly drill (cron, 1st 04:00 IST)
#
# Per run it writes to $BACKUP_DIR (default /var/backups/skoolgpt):
#   skoolgpt-YYYY-MM-DD.dump.gz            pg_dump -Fc, gzipped
#   skoolgpt-uploads-YYYY-MM-DD.tar.gz     backend/static/uploads (student photos, generated diagrams)
#   <each>.sha256                          checksum, verify with `sha256sum -c`
# and, depending on two optional settings read from the repo-root .env
# (same file DATABASE_URL already comes from) or the environment:
#   BACKUP_PASSPHRASE     if set, both artefacts are encrypted with
#                         `gpg --symmetric --cipher-algo AES256` (-> .gpg) and
#                         the plaintext is deleted. Restore/decrypt with
#                         `gpg --batch --passphrase-fd 3 -d FILE.gpg 3<<<"$BACKUP_PASSPHRASE"`.
#   BACKUP_RCLONE_REMOTE  if set (e.g. `b2:skoolgpt-backups`), the day's
#                         artefacts are copied there with `rclone copy` and the
#                         copy is verified with `rclone lsl` — a missing remote
#                         copy fails the run loudly.
# A one-line warning is printed when either is unset, so a plaintext/
# same-disk-only backup is never a silent default. Local retention is 14
# days; retention on the remote is the remote's own lifecycle policy.
#
# Any failure exits non-zero and appends a `BACKUP FAILED` line to
# logs/cron-backup.log (cron already redirects stdout/stderr there, the
# extra line is a greppable marker). No alerting is attempted from here —
# scripts/ops_heartbeat.py, when present, is what alerts on a stale backup.
set -euo pipefail

MODE="backup"
case "${1:-}" in
  --restore-check) MODE="restore-check";;
  "") ;;
  *) echo "usage: $0 [--restore-check]" >&2; exit 2;;
esac

APP_DIR="${APP_DIR:-/usr/share/nginx/aitutor.qlass.in/public_python_aios}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/skoolgpt}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
LOG_FILE="$APP_DIR/logs/cron-backup.log"
RESTORE_DB="skoolgpt_restore_check"
TODAY="$(date +%F)"

# --- .env loading (mirrors the DATABASE_URL lookup this script always did) ---
dotenv_value() {  # dotenv_value KEY -> value of KEY= in $APP_DIR/.env, quotes stripped, empty if absent
  [ -f "$APP_DIR/.env" ] || return 0
  grep -E "^$1=" "$APP_DIR/.env" | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}
: "${DATABASE_URL:=$(dotenv_value DATABASE_URL)}"
: "${BACKUP_PASSPHRASE:=$(dotenv_value BACKUP_PASSPHRASE)}"
: "${BACKUP_RCLONE_REMOTE:=$(dotenv_value BACKUP_RCLONE_REMOTE)}"

# --- failure marker: any non-zero exit lands one greppable line in the cron log ---
on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
    printf '%s BACKUP FAILED (%s) exit=%s\n' "$(date '+%F %T')" "$MODE" "$rc" | tee -a "$LOG_FILE" >&2 || true
  fi
  cleanup_restore_check
  [ -n "${TMP_DIR:-}" ] && rm -rf "$TMP_DIR"
  exit "$rc"
}

sha256_of() {  # sha256_of FILE -> writes FILE.sha256 (filename-relative, so `sha256sum -c` works in-place)
  local dir base
  dir="$(dirname "$1")"; base="$(basename "$1")"
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$dir" && sha256sum "$base" > "$base.sha256")
  else
    (cd "$dir" && shasum -a 256 "$base" > "$base.sha256")
  fi
}

encrypt_in_place() {  # encrypt_in_place FILE -> FILE.gpg, plaintext removed; echoes the new path
  local f="$1"
  gpg --batch --yes --quiet --symmetric --cipher-algo AES256 --passphrase-fd 3 \
      --output "$f.gpg" "$f" 3<<<"$BACKUP_PASSPHRASE"
  rm -f "$f"
  echo "$f.gpg"
}

# --- restore-check helpers ---
# DATABASE_URL -> the same URL pointed at another database, keeping any ?query.
db_url_for() {
  local url="$DATABASE_URL" base query=""
  case "$url" in *\?*) query="?${url#*\?}"; url="${url%%\?*}";; esac
  base="${url%/*}"
  echo "$base/$1$query"
}
RESTORE_CHECK_ACTIVE=0
cleanup_restore_check() {
  if [ "$RESTORE_CHECK_ACTIVE" = 1 ]; then
    psql "$(db_url_for postgres)" -qAtc "DROP DATABASE IF EXISTS $RESTORE_DB" >/dev/null 2>&1 || true
    RESTORE_CHECK_ACTIVE=0
  fi
}

restore_check() {
  local newest plain
  # Newest dump by mtime — encrypted or not.
  newest="$(ls -t "$BACKUP_DIR"/skoolgpt-[0-9]*.dump.gz "$BACKUP_DIR"/skoolgpt-[0-9]*.dump.gz.gpg 2>/dev/null | head -n1 || true)"
  [ -n "$newest" ] || { echo "restore-check: no skoolgpt-*.dump.gz in $BACKUP_DIR" >&2; exit 1; }
  echo "restore-check: using $newest"
  TMP_DIR="$(mktemp -d)"
  plain="$TMP_DIR/restore.dump"
  if [ -f "$newest.sha256" ]; then
    (cd "$BACKUP_DIR" && { sha256sum -c --quiet "$(basename "$newest").sha256" 2>/dev/null || shasum -a 256 -c --quiet "$(basename "$newest").sha256"; }) \
      || { echo "restore-check: checksum mismatch for $newest" >&2; exit 1; }
  fi
  case "$newest" in
    *.gpg)
      [ -n "$BACKUP_PASSPHRASE" ] || { echo "restore-check: newest dump is encrypted but BACKUP_PASSPHRASE is unset" >&2; exit 1; }
      gpg --batch --yes --quiet --passphrase-fd 3 --decrypt "$newest" 3<<<"$BACKUP_PASSPHRASE" | gunzip -c > "$plain"
      ;;
    *) gunzip -c "$newest" > "$plain";;
  esac

  local admin_url scratch_url count
  admin_url="$(db_url_for postgres)"
  scratch_url="$(db_url_for "$RESTORE_DB")"
  psql "$admin_url" -qAtc "DROP DATABASE IF EXISTS $RESTORE_DB" >/dev/null
  psql "$admin_url" -qAtc "CREATE DATABASE $RESTORE_DB" >/dev/null
  RESTORE_CHECK_ACTIVE=1
  # pg_restore's exit status is non-zero on ANY warning (e.g. a
  # pre-existing extension/role it can't recreate as a non-superuser), so
  # the pass/fail verdict is the row-count query below, not pg_restore's rc.
  pg_restore --no-owner --no-privileges --dbname "$scratch_url" "$plain" 2>"$TMP_DIR/pg_restore.err" \
    || echo "restore-check: pg_restore reported $(grep -c 'error' "$TMP_DIR/pg_restore.err" || true) error line(s) (see below), continuing to verify"
  count="$(psql "$scratch_url" -qAtc "SELECT count(*) FROM students")"
  [[ "$count" =~ ^[0-9]+$ ]] || { echo "restore-check: could not count students in restored DB" >&2; cat "$TMP_DIR/pg_restore.err" >&2; exit 1; }
  if [ "$count" -eq 0 ]; then
    echo "restore-check: restored database has 0 students — treat the backup as broken" >&2
    cat "$TMP_DIR/pg_restore.err" >&2
    exit 1
  fi
  cleanup_restore_check
  echo "$(date '+%F %T') RESTORE CHECK OK: $count students restored from $(basename "$newest")"
}

# --- nightly backup ---
backup() {
  local artefacts=() dump uploads_tar f
  mkdir -p "$BACKUP_DIR"
  [ -n "$BACKUP_PASSPHRASE" ]     || echo "WARNING: BACKUP_PASSPHRASE unset — backups are stored unencrypted (set it in $APP_DIR/.env)"
  [ -n "$BACKUP_RCLONE_REMOTE" ]  || echo "WARNING: BACKUP_RCLONE_REMOTE unset — no off-box copy, a disk failure loses the backups too"

  # 1. Database (custom format so pg_restore can do selective/parallel restores).
  dump="$BACKUP_DIR/skoolgpt-$TODAY.dump"
  pg_dump -Fc "$DATABASE_URL" > "$dump"
  gzip -f "$dump"
  artefacts+=("$dump.gz")

  # 2. Uploaded files — not in Postgres, but every student photo / generated
  #    diagram / school logo referenced from it lives here.
  if [ -d "$APP_DIR/backend/static/uploads" ]; then
    uploads_tar="$BACKUP_DIR/skoolgpt-uploads-$TODAY.tar.gz"
    tar -czf "$uploads_tar" -C "$APP_DIR/backend/static" uploads
    artefacts+=("$uploads_tar")
  else
    echo "WARNING: $APP_DIR/backend/static/uploads not found — skipping uploads archive"
  fi

  # 3. Encrypt (optional) and checksum.
  if [ -n "$BACKUP_PASSPHRASE" ]; then
    command -v gpg >/dev/null 2>&1 || { echo "BACKUP_PASSPHRASE is set but gpg is not installed" >&2; exit 1; }
    for i in "${!artefacts[@]}"; do artefacts[$i]="$(encrypt_in_place "${artefacts[$i]}")"; done
  fi
  for f in "${artefacts[@]}"; do sha256_of "$f"; done

  # 4. Off-box copy + verification (optional).
  if [ -n "$BACKUP_RCLONE_REMOTE" ]; then
    command -v rclone >/dev/null 2>&1 || { echo "BACKUP_RCLONE_REMOTE is set but rclone is not installed" >&2; exit 1; }
    for f in "${artefacts[@]}"; do
      rclone copy --quiet "$f" "$BACKUP_RCLONE_REMOTE/"
      rclone copy --quiet "$f.sha256" "$BACKUP_RCLONE_REMOTE/"
    done
    local listing
    listing="$(rclone lsl "$BACKUP_RCLONE_REMOTE")"
    for f in "${artefacts[@]}"; do
      local base size remote_size
      base="$(basename "$f")"
      size="$(wc -c < "$f" | tr -d ' ')"
      remote_size="$(printf '%s\n' "$listing" | awk -v n="$base" '$NF == n {print $1}' | head -n1)"
      if [ "$remote_size" != "$size" ]; then
        echo "off-box copy of $base missing or wrong size on $BACKUP_RCLONE_REMOTE (local=$size remote=${remote_size:-absent})" >&2
        exit 1
      fi
    done
    echo "off-box copy verified on $BACKUP_RCLONE_REMOTE (${#artefacts[@]} artefact(s))"
  fi

  # 5. Local retention.
  find "$BACKUP_DIR" -maxdepth 1 -name 'skoolgpt-*' -mtime "+$RETENTION_DAYS" -delete

  for f in "${artefacts[@]}"; do echo "backup written: $f ($(du -h "$f" | cut -f1))"; done
  echo "$(date '+%F %T') BACKUP OK"
}

trap on_exit EXIT
[ -n "$DATABASE_URL" ] || { echo "DATABASE_URL not set (env or $APP_DIR/.env)" >&2; exit 1; }

case "$MODE" in
  backup) backup;;
  restore-check) restore_check;;
esac
