#!/usr/bin/env bash
# Merge scripts/crontab into a user's crontab WITHOUT clobbering other apps'
# entries, and WITHOUT leaving stale copies of our own.
#
# The previous merge (inlined in server_hardening.sh) only deduplicated
# byte-identical lines, so every schedule/flag change to a job appended a
# second line for the same script and the old one kept firing too. Here the
# identity of a job line is "which script (plus flags) it runs" — for each
# non-comment line of the fragment, any existing line invoking the same
# `scripts/<name> [--flags]` is REPLACED by the new one; environment lines
# (MAILTO=, APP=, PY=) replace the existing line with the same KEY=; every
# other app's line is passed through untouched, in its original order.
#
# Usage (idempotent, safe to re-run):
#   bash scripts/install_crontab.sh                     # current user's crontab
#   sudo bash scripts/install_crontab.sh --user qlass   # as root, for another user
#   bash scripts/install_crontab.sh --dry-run           # print the merged result only
#   bash scripts/install_crontab.sh --from FILE ...     # merge onto FILE instead of the
#                                                       # live crontab (tests / preview)
set -euo pipefail

FRAGMENT="${FRAGMENT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/crontab}"
TARGET_USER=""
DRY_RUN=0
FROM_FILE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --user) TARGET_USER="$2"; shift 2;;
    --dry-run) DRY_RUN=1; shift;;
    --from) FROM_FILE="$2"; shift 2;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) FRAGMENT="$1"; shift;;
  esac
done
[ -f "$FRAGMENT" ] || { echo "install_crontab: fragment not found: $FRAGMENT" >&2; exit 1; }

crontab_cmd() {
  if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "$(id -un)" ]; then
    sudo -u "$TARGET_USER" crontab "$@"
  else
    crontab "$@"
  fi
}

# Identity of a job line: the `scripts/<file> [--flag ...]` it invokes, with
# whitespace collapsed, ignoring the schedule, `cd $APP &&`, flock wrapper
# and the log redirection. Empty for a line that doesn't run one of our
# scripts (another app's job, an env assignment, a blank line).
job_key() {
  local line="$1" cmd
  cmd="${line%%>*}"           # drop the redirection onwards
  cmd="${cmd//	/ }"           # tabs -> spaces
  if [[ "$cmd" =~ (scripts/[A-Za-z0-9_./-]+([[:space:]]+-[^[:space:]]+)*) ]]; then
    printf '%s' "${BASH_REMATCH[1]}" | tr -s ' '
  fi
}

# KEY of a `KEY=value` environment line (MAILTO/APP/PY/...), else empty.
env_key() {
  local line="$1"
  if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)= ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
}

if [ -n "$FROM_FILE" ]; then
  existing="$(cat "$FROM_FILE")"
else
  existing="$(crontab_cmd -l 2>/dev/null || true)"
fi

# Legacy hand-added entries superseded by this fragment (kept from the old
# server_hardening.sh merge): the pre-fragment nudges line and the very
# first backup wrapper. Harmless when absent.
existing="$(printf '%s\n' "$existing" | grep -vE 'send_engagement_nudges\.py >> .*/logs/nudges\.log|skoolgpt-backup\.sh' || true)"

# Collect the fragment's real (non-comment, non-blank) lines and their keys.
new_lines=()
new_job_keys=()
new_env_keys=()
while IFS= read -r line || [ -n "$line" ]; do
  [[ -z "${line// /}" ]] && continue
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  new_lines+=("$line")
  new_job_keys+=("$(job_key "$line")")
  new_env_keys+=("$(env_key "$line")")
done < "$FRAGMENT"

MARKER="# --- Skoolgpt (managed by scripts/install_crontab.sh; edit scripts/crontab, not this) ---"

# Pass existing lines through unless the fragment carries a replacement for
# the same job / env key. Other apps' lines and comments are kept as-is; our
# own marker comment from a previous run is dropped and re-added at the end.
kept=""
replaced=0
while IFS= read -r line || [ -n "$line" ]; do
  [ "$line" = "$MARKER" ] && continue
  drop=0
  jk="$(job_key "$line")"; ek="$(env_key "$line")"
  if [ -n "$jk" ] || [ -n "$ek" ]; then
    for i in "${!new_lines[@]}"; do
      if { [ -n "$jk" ] && [ "$jk" = "${new_job_keys[$i]}" ]; } || { [ -n "$ek" ] && [ "$ek" = "${new_env_keys[$i]}" ]; }; then
        drop=1; break
      fi
    done
  fi
  if [ "$drop" = 1 ]; then
    replaced=$((replaced + 1))
  else
    kept="${kept}${line}"$'\n'
  fi
done <<<"$existing"

# Existing (filtered) lines first, then our block last. `cat -s` squeezes
# the blank-line runs that repeated merges would otherwise accumulate.
merged="$(printf '%s\n%s\n' "$kept" "$MARKER" | cat -s)"$'\n'
for line in "${new_lines[@]}"; do merged="${merged}${line}"$'\n'; done
merged="$(printf '%s' "$merged" | sed '/./,$!d')"$'\n'   # trim leading blank line(s)

if [ "$DRY_RUN" = 1 ]; then
  printf '%s' "$merged"
  echo "# (dry run: ${#new_lines[@]} fragment line(s), ${replaced} existing line(s) replaced)" >&2
  exit 0
fi

printf '%s' "$merged" | crontab_cmd -
echo "crontab updated for ${TARGET_USER:-$(id -un)}: ${#new_lines[@]} fragment line(s) installed, ${replaced} stale line(s) replaced"
