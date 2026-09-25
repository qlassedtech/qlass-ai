"""
Retention for raw inbound WhatsApp webhook payloads. Every WATI delivery is
persisted verbatim in processed_webhook_messages (see app.routers.whatsapp
— the row is what lets a crashed/timed-out job be retried and the same
message_id be deduped on Wati's own redelivery). That payload carries the
sender's phone (`waId`) and message text, and until this script existed it
was kept forever — long after the message had been handled.

Two tiers, both only for rows Wati would never need to replay:

  - status='completed' and older than PURGE_PAYLOAD_AFTER_DAYS: the
    payload is replaced with a minimal {"purged": true} stub. The row
    itself STAYS so the message_id primary key still dedupes a very late
    redelivery (whatsapp.py treats an existing completed row as "already
    handled" and returns without reading the payload).
  - any row older than HARD_DELETE_AFTER_DAYS: deleted outright — Wati
    doesn't redeliver anything that old, so there's nothing left to dedupe.

Pending/failed/processing rows inside the 30-day window are never touched,
since the retry loop still needs their payload. Meant to run daily via cron
(see scripts/crontab), same pattern as scripts/send_reengagement_nudges.py:

    45 3 * * * cd /path/to/qlass-ai && venv/bin/python3 scripts/purge_webhook_payloads.py >> logs/cron-purge-webhook-payloads.log 2>&1

Usage:
    python scripts/purge_webhook_payloads.py [--dry-run]
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.core import ProcessedWebhookMessage  # noqa: E402

PURGE_PAYLOAD_AFTER_DAYS = 30
HARD_DELETE_AFTER_DAYS = 180
PURGED_PAYLOAD = {"purged": True}


def purge_webhook_payloads(db: Session, now: datetime | None = None, dry_run: bool = False) -> dict:
    """
    Returns {"purged": n, "deleted": n}. Hard-deletes first so a row past
    both cutoffs is counted once, as a delete. `now` is injectable for
    tests. Not committed on dry_run.
    """
    now = now or datetime.now(timezone.utc)
    hard_delete_cutoff = now - timedelta(days=HARD_DELETE_AFTER_DAYS)
    purge_cutoff = now - timedelta(days=PURGE_PAYLOAD_AFTER_DAYS)

    delete_query = db.query(ProcessedWebhookMessage).filter(ProcessedWebhookMessage.processed_at < hard_delete_cutoff)
    purge_query = db.query(ProcessedWebhookMessage).filter(
        ProcessedWebhookMessage.status == "completed",
        ProcessedWebhookMessage.processed_at < purge_cutoff,
        ProcessedWebhookMessage.processed_at >= hard_delete_cutoff,
    )
    # Rows whose payload is already the stub (or NULL, from before the
    # payload column existed) are skipped so the count reflects real work.
    to_purge = [row for row in purge_query.all() if row.payload is not None and row.payload != PURGED_PAYLOAD]

    if dry_run:
        return {"purged": len(to_purge), "deleted": delete_query.count()}

    deleted = delete_query.delete(synchronize_session=False)
    for row in to_purge:
        row.payload = dict(PURGED_PAYLOAD)
    db.commit()
    return {"purged": len(to_purge), "deleted": deleted}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would be purged/deleted without changing anything")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        result = purge_webhook_payloads(db, dry_run=args.dry_run)
        prefix = "[DRY RUN] Would purge" if args.dry_run else "Purged"
        print(
            f"{prefix} payload on {result['purged']} completed webhook row(s) older than {PURGE_PAYLOAD_AFTER_DAYS}d; "
            f"{'would delete' if args.dry_run else 'deleted'} {result['deleted']} row(s) older than {HARD_DELETE_AFTER_DAYS}d."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
