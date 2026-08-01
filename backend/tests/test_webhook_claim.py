from datetime import datetime, timedelta, timezone

from app.models.core import ProcessedWebhookMessage
from app.routers.whatsapp import _claim_webhook_job


def test_only_one_claim_succeeds_for_a_pending_job(db_session):
    """
    Regression test: claiming used to be read-job-then-write-status, safe
    only because a single process never interleaves between those two
    steps. With more than one backend process there is no such guarantee,
    so the claim must be one atomic conditional UPDATE — simulated here by
    calling it twice in a row for the same still-pending job, as two
    concurrent processes racing on the same message_id would.
    """
    job = ProcessedWebhookMessage(message_id="wamid.test1", payload={"waId": "919000000001"}, status="pending")
    db_session.add(job)
    db_session.commit()

    now = datetime.now(timezone.utc)
    first = _claim_webhook_job(db_session, "wamid.test1", now)
    second = _claim_webhook_job(db_session, "wamid.test1", now)

    assert first is True
    assert second is False


def test_completed_job_can_never_be_reclaimed(db_session):
    job = ProcessedWebhookMessage(message_id="wamid.test2", payload={"waId": "919000000002"}, status="completed")
    db_session.add(job)
    db_session.commit()

    assert _claim_webhook_job(db_session, "wamid.test2", datetime.now(timezone.utc)) is False


def test_a_job_with_an_expired_lease_can_be_reclaimed(db_session):
    now = datetime.now(timezone.utc)
    job = ProcessedWebhookMessage(
        message_id="wamid.test3", payload={"waId": "919000000003"}, status="processing",
        lease_expires_at=now - timedelta(seconds=1),
    )
    db_session.add(job)
    db_session.commit()

    assert _claim_webhook_job(db_session, "wamid.test3", now) is True


def test_a_job_with_a_still_active_lease_cannot_be_reclaimed(db_session):
    now = datetime.now(timezone.utc)
    job = ProcessedWebhookMessage(
        message_id="wamid.test4", payload={"waId": "919000000004"}, status="processing",
        lease_expires_at=now + timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()

    assert _claim_webhook_job(db_session, "wamid.test4", now) is False
