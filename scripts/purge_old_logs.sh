#!/usr/bin/env bash
# One-off PII purge for the backend's own log files written BEFORE log
# redaction landed (commit 2052fce, 2026-09-11 — app/logging_config.py's
# RedactingFilter masks phone numbers and key/token values from that point
# on; anything logged earlier can carry raw student phone numbers).
#
#   bash scripts/purge_old_logs.sh            # truncate, print what was done
#   bash scripts/purge_old_logs.sh --dry-run  # only print what WOULD be done
#
# Targets $APP_DIR/logs/app.log* only (app.log plus its RotatingFileHandler
# backups app.log.1..5). A file is purged when its last-modified time is
# before the cutoff, i.e. nothing has been appended to it since redaction
# went live. Purging is a copytruncate-style in-place truncate (`: > file`),
# NOT an rm: the running uvicorn workers hold app.log open, and a rotated
# backup keeps its slot so RotatingFileHandler's numbering stays intact.
# A file modified after the cutoff whose FIRST line predates it (the live
# app.log straddling the deploy) is reported as MIXED and left alone — its
# pre-cutoff lines age out with the next rotations; purge it by hand once
# it has rotated if you'd rather not wait.
#
# Not run by any cron/deploy step, deliberately: run it once, by hand.
set -euo pipefail

APP_DIR="${APP_DIR:-/usr/share/nginx/aitutor.qlass.in/public_python_aios}"
CUTOFF="${CUTOFF:-2026-09-11}"   # YYYY-MM-DD, the redaction commit date (IST)
DRY_RUN=0
case "${1:-}" in
  --dry-run) DRY_RUN=1;;
  "") ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 2;;
esac

cutoff_epoch="$(date -d "$CUTOFF" +%s)"
purged=0; skipped=0; mixed=0
shopt -s nullglob
for f in "$APP_DIR"/logs/app.log*; do
  [ -f "$f" ] || continue
  mtime="$(stat -c %Y "$f")"
  size="$(stat -c %s "$f")"
  if [ "$mtime" -lt "$cutoff_epoch" ]; then
    if [ "$DRY_RUN" = 1 ]; then
      echo "WOULD TRUNCATE $f (${size} bytes, last modified $(date -d "@$mtime" +%F))"
    else
      : > "$f"
      echo "TRUNCATED $f (${size} bytes, last modified $(date -d "@$mtime" +%F))"
    fi
    purged=$((purged + 1))
    continue
  fi
  # Modified after the cutoff: check whether its first line predates it
  # (log lines start with "YYYY-MM-DD HH:MM:SS,mmm LEVEL ...").
  first_date="$(head -c 10 "$f" 2>/dev/null || true)"
  if [[ "$first_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [ "$(date -d "$first_date" +%s)" -lt "$cutoff_epoch" ]; then
    echo "MIXED     $f starts $first_date but was written to after $CUTOFF — left alone (see header comment)"
    mixed=$((mixed + 1))
  else
    echo "KEPT      $f (last modified $(date -d "@$mtime" +%F), all after cutoff)"
    skipped=$((skipped + 1))
  fi
done

label="truncated"; [ "$DRY_RUN" = 1 ] && label="would truncate"
echo "purge_old_logs: $label $purged file(s), kept $skipped, mixed $mixed (cutoff $CUTOFF, dir $APP_DIR/logs)"
