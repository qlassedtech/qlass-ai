"""
scripts/purge_webhook_payloads.py — retention for raw WATI webhook payloads
(phone + message text) in processed_webhook_messages. Completed rows older
than 30 days keep their message_id (so dedup still works) but lose the
payload; anything older than 180 days is deleted outright.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from purge_webhook_payloads import (  # noqa: E402
    HARD_DELETE_AFTER_DAYS, PURGE_PAYLOAD_AFTER_DAYS, PURGED_PAYLOAD, purge_webhook_payloads,
)

from app.models.core import ProcessedWebhookMessage  # noqa: E402

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _row(db_session, message_id, age_days, status="completed", payload=None):
    row = ProcessedWebhookMessage(
        message_id=message_id, status=status,
        payload=payload if payload is not None else {"waId": "919000000001", "text": f"hello {message_id}"},
        processed_at=NOW - timedelta(days=age_days),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _get(db_session, message_id):
    db_session.expire_all()
    return db_session.query(ProcessedWebhookMessage).filter(ProcessedWebhookMessage.message_id == message_id).first()


def test_completed_rows_older_than_30_days_lose_payload_but_keep_row(db_session):
    _row(db_session, "old_completed", PURGE_PAYLOAD_AFTER_DAYS + 1)
    _row(db_session, "recent_completed", PURGE_PAYLOAD_AFTER_DAYS - 1)

    result = purge_webhook_payloads(db_session, now=NOW)

    assert result == {"purged": 1, "deleted": 0}
    old = _get(db_session, "old_completed")
    assert old is not None  # message_id kept for dedup
    assert old.payload == PURGED_PAYLOAD
    assert old.status == "completed"
    assert _get(db_session, "recent_completed").payload["waId"] == "919000000001"


def test_unfinished_rows_inside_180_days_keep_payload_for_retry(db_session):
    _row(db_session, "old_failed", PURGE_PAYLOAD_AFTER_DAYS + 5, status="failed")
    _row(db_session, "old_pending", PURGE_PAYLOAD_AFTER_DAYS + 5, status="pending")

    result = purge_webhook_payloads(db_session, now=NOW)

    assert result == {"purged": 0, "deleted": 0}
    assert _get(db_session, "old_failed").payload["text"] == "hello old_failed"
    assert _get(db_session, "old_pending").payload["text"] == "hello old_pending"


def test_rows_older_than_180_days_are_hard_deleted_regardless_of_status(db_session):
    _row(db_session, "ancient_completed", HARD_DELETE_AFTER_DAYS + 1)
    _row(db_session, "ancient_failed", HARD_DELETE_AFTER_DAYS + 1, status="failed")
    _row(db_session, "old_completed", HARD_DELETE_AFTER_DAYS - 1)

    result = purge_webhook_payloads(db_session, now=NOW)

    assert result == {"purged": 1, "deleted": 2}
    assert _get(db_session, "ancient_completed") is None
    assert _get(db_session, "ancient_failed") is None
    assert _get(db_session, "old_completed").payload == PURGED_PAYLOAD


def test_already_purged_rows_are_not_counted_again_and_dry_run_changes_nothing(db_session):
    _row(db_session, "already_purged", PURGE_PAYLOAD_AFTER_DAYS + 10, payload=dict(PURGED_PAYLOAD))
    _row(db_session, "old_completed", PURGE_PAYLOAD_AFTER_DAYS + 10)
    _row(db_session, "ancient", HARD_DELETE_AFTER_DAYS + 10)

    dry = purge_webhook_payloads(db_session, now=NOW, dry_run=True)
    assert dry == {"purged": 1, "deleted": 1}
    assert _get(db_session, "old_completed").payload["text"] == "hello old_completed"
    assert _get(db_session, "ancient") is not None

    assert purge_webhook_payloads(db_session, now=NOW) == {"purged": 1, "deleted": 1}
    assert purge_webhook_payloads(db_session, now=NOW) == {"purged": 0, "deleted": 0}
