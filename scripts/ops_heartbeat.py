"""
Hourly "is anything quietly broken?" check for the Skoolgpt host, alerting a
human over WhatsApp (see app.services.alerts.alert_ops) when it is. Every
check is independent and each has its own alert cooldown, so one failing
check doesn't hide the others and a persistent problem re-alerts at most
once per cooldown rather than every hour.

Checks:
  (a) inbound silence — zero rows in processed_webhook_messages for the
      last 6 h during waking hours (a dead webhook/ngrok/Wati config looks
      exactly like a quiet afternoon otherwise);
  (b) provider errors — an Anthropic/Sarvam/Wati error counter (see
      alerts.note_provider_error) at or above PROVIDER_ERROR_THRESHOLD in
      the current or previous hour;
  (c) disk usage of the repo's filesystem at or above DISK_ALERT_PERCENT;
  (d) the nightly DB backup (scripts/backup_db.sh -> BACKUP_DIR) missing
      or older than BACKUP_MAX_AGE_HOURS;
  (e) the app's /ready endpoint returning non-200 (Postgres or Redis down
      behind a still-running uvicorn).

Meant to run hourly via cron (see scripts/crontab; server timezone assumed
to be IST):

    15 * * * *   cd $APP && $PY scripts/ops_heartbeat.py >> logs/cron-ops-heartbeat.log 2>&1

Usage:
    python scripts/ops_heartbeat.py [--dry-run]
"""
import argparse
import asyncio
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.config import REPO_ROOT  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.core import ProcessedWebhookMessage  # noqa: E402
from app.services import alerts  # noqa: E402

# (a) Inbound silence. The lookback is 6 h, and the check only runs when
# that whole window falls inside the 07:00-22:00 local (IST) waking day —
# i.e. from 13:00 onward — so the 07:15 run doesn't alert on the
# (perfectly normal) 01:00-07:00 overnight silence every single morning.
INBOUND_SILENCE_HOURS = 6
WAKING_HOURS_START = 7
WAKING_HOURS_END = 22

# (b) Provider error counters, per hour (see alerts.note_provider_error).
PROVIDER_ERROR_THRESHOLD = 5
PROVIDERS = ("anthropic", "sarvam", "wati")

# (c) Disk.
DISK_ALERT_PERCENT = 85

# (d) Backups — scripts/backup_db.sh runs nightly at 02:30 IST, so anything
# older than ~a day + slack means last night's run didn't happen.
BACKUP_DIR = Path("/var/backups/skoolgpt")
BACKUP_MAX_AGE_HOURS = 26

# (e) Readiness — the uvicorn port from scripts/server_hardening.sh's
# systemd unit (and scripts/deploy.sh's READY_URL).
READY_URL = "http://127.0.0.1:8096/ready"

# One alert per problem per this long, not one per hourly run.
ALERT_COOLDOWN_SECONDS = 6 * 3600


def check_inbound_silence(db, now: datetime | None = None) -> str | None:
    now = now or datetime.now()  # server local time — IST on the production host
    window_start_hour = WAKING_HOURS_START + INBOUND_SILENCE_HOURS
    if not (window_start_hour <= now.hour < WAKING_HOURS_END):
        return None
    since = datetime.now(timezone.utc) - timedelta(hours=INBOUND_SILENCE_HOURS)
    count = db.query(ProcessedWebhookMessage).filter(ProcessedWebhookMessage.processed_at >= since).count()
    if count > 0:
        return None
    return (
        f"No inbound WhatsApp messages have reached the webhook in the last {INBOUND_SILENCE_HOURS} h "
        f"(it's {now:%H:%M} local — students should be messaging). Check the Wati webhook URL/secret "
        f"and that the backend is up: {READY_URL}"
    )


def check_provider_errors() -> str | None:
    now = datetime.now(timezone.utc)
    problems = []
    for provider in PROVIDERS:
        this_hour = alerts.get_provider_error_count(provider, now)
        last_hour = alerts.get_provider_error_count(provider, now - timedelta(hours=1))
        worst = max(this_hour, last_hour)
        if worst >= PROVIDER_ERROR_THRESHOLD:
            problems.append(f"{provider}: {worst} errors in an hour (this hour {this_hour}, last hour {last_hour})")
    if not problems:
        return None
    return (
        "Provider API errors above threshold — " + "; ".join(problems) + ". Check the provider dashboards "
        "(console.anthropic.com / dashboard.sarvam.ai / app.wati.io) and the backend logs."
    )


def check_disk_usage() -> str | None:
    usage = shutil.disk_usage(REPO_ROOT)
    percent = usage.used / usage.total * 100
    if percent < DISK_ALERT_PERCENT:
        return None
    free_gb = usage.free / 1024**3
    return (
        f"Disk on the app host is {percent:.0f}% full ({free_gb:.1f} GB free) — Postgres/logs/backups will "
        f"start failing when it fills. Prune logs/ and old files in {BACKUP_DIR}."
    )


def check_backup_freshness(now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    if not BACKUP_DIR.is_dir():
        return f"Backup directory {BACKUP_DIR} is missing — no DB backups are being taken. Check scripts/backup_db.sh and its cron entry."
    files = [p for p in BACKUP_DIR.iterdir() if p.is_file()]
    if not files:
        return f"Backup directory {BACKUP_DIR} is empty — no DB backups are being taken. Check scripts/backup_db.sh and its cron entry."
    newest = max(files, key=lambda p: p.stat().st_mtime)
    age = now - datetime.fromtimestamp(newest.stat().st_mtime, tz=timezone.utc)
    if age <= timedelta(hours=BACKUP_MAX_AGE_HOURS):
        return None
    return (
        f"Newest DB backup ({newest.name}) is {age.total_seconds() / 3600:.0f} h old — last night's "
        f"scripts/backup_db.sh run didn't produce one. Check logs/cron-backup.log."
    )


def check_readiness() -> str | None:
    try:
        resp = httpx.get(READY_URL, timeout=5)
    except httpx.HTTPError as exc:
        return f"{READY_URL} is unreachable ({exc}) — the backend is down. Check `systemctl status skoolgpt` and the logs."
    if resp.status_code == 200:
        return None
    return (
        f"{READY_URL} returned {resp.status_code}: {resp.text[:200]} — the backend is up but Postgres or "
        f"Redis isn't. Check `systemctl status postgresql redis`."
    )


async def run_heartbeat(dry_run: bool) -> int:
    db = SessionLocal()
    problems = 0
    try:
        checks = [
            ("heartbeat_inbound_silence", lambda: check_inbound_silence(db)),
            ("heartbeat_provider_errors", check_provider_errors),
            ("heartbeat_disk_usage", check_disk_usage),
            ("heartbeat_backup_stale", check_backup_freshness),
            ("heartbeat_not_ready", check_readiness),
        ]
        for kind, check in checks:
            try:
                message = check()
            except Exception as exc:  # one broken check must not stop the rest
                message = f"Heartbeat check itself failed: {exc}"
            if message is None:
                print(f"OK    {kind}")
                continue
            problems += 1
            if dry_run:
                print(f"[DRY RUN] ALERT {kind}: {message}")
            else:
                sent = await alerts.alert_ops(kind, message, cooldown_seconds=ALERT_COOLDOWN_SECONDS)
                print(f"ALERT {kind} ({'sent' if sent else 'suppressed by cooldown / not delivered'}): {message}")
        print(f"\n{datetime.now():%Y-%m-%d %H:%M} heartbeat: {problems} problem(s) found.")
        return problems
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be alerted without sending anything")
    args = parser.parse_args()
    asyncio.run(run_heartbeat(args.dry_run))


if __name__ == "__main__":
    main()
