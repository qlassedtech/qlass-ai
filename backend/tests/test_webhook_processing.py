"""
Reliability of the persisted-webhook job runner and its recovery worker
(app.routers.whatsapp._process_queued_webhook / retry_pending_webhooks) —
the audit (Sept 2026) findings for duplicate execution + double billing,
the recovery worker dying on its first DB blip, owner-echo replies and the
silent third failure. SessionLocal is pointed at the SQLite test session.
"""
import asyncio

import pytest
from redis.exceptions import LockNotOwnedError

from app.models.core import Centre, ProcessedWebhookMessage, Student
from app.routers import whatsapp
from app.services import rate_limit


@pytest.fixture()
def wa_db(db_session, monkeypatch):
    """Route the job runner's own SessionLocal() to the test session (close() is harmless on it)."""
    monkeypatch.setattr(whatsapp, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    return db_session


def _queue_job(db, message_id: str, phone: str, attempts: int = 0) -> ProcessedWebhookMessage:
    job = ProcessedWebhookMessage(
        message_id=message_id, status="pending", attempts=attempts,
        payload={"eventType": "message", "owner": False, "type": "text", "waId": phone, "text": "hi"},
    )
    db.add(job)
    db.commit()
    return job


class _ExpiringLock:
    """A lock whose TTL elapsed while the turn ran: release raises LockNotOwnedError, as redis' does."""

    def __init__(self):
        self.released = False

    async def acquire(self, *args, **kwargs):
        return True

    async def release(self):
        self.released = True
        raise LockNotOwnedError("Cannot release a lock that's no longer owned")


def test_student_lock_outlives_processing_timeout():
    """The one relationship that makes duplicate execution impossible: lock TTL > turn timeout (+ margin)."""
    assert rate_limit.STUDENT_LOCK_TIMEOUT_SECONDS > whatsapp.WEBHOOK_PROCESSING_TIMEOUT_SECONDS
    assert rate_limit.STUDENT_LOCK_TIMEOUT_SECONDS == whatsapp.WEBHOOK_PROCESSING_TIMEOUT_SECONDS + 60
    assert whatsapp.WEBHOOK_PROCESSING_TIMEOUT_SECONDS < whatsapp.WEBHOOK_LEASE_SECONDS


async def test_lock_expiring_after_a_completed_turn_marks_the_job_completed_and_sends_nothing_more(wa_db, monkeypatch):
    """
    Regression for the double-billing bug: a LockNotOwnedError on lock
    release used to escape after _handle_message had already sent+billed
    the reply, get caught by the generic handler, re-queue the job as
    pending, and the retry worker ran the whole turn again.
    """
    handled, sent = [], []
    lock = _ExpiringLock()

    async def fake_handle_message(db, payload):
        handled.append(payload["waId"])

    async def fake_send(phone, text, *a, **kw):
        sent.append(text)
        return {"sent": True}

    monkeypatch.setattr(whatsapp, "_handle_message", fake_handle_message)
    monkeypatch.setattr(whatsapp, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(rate_limit, "student_lock", lambda phone: lock)
    _queue_job(wa_db, "wamid.lock1", "919000000101")

    await whatsapp._process_queued_webhook("wamid.lock1")

    job = wa_db.query(ProcessedWebhookMessage).filter_by(message_id="wamid.lock1").one()
    assert lock.released is True
    assert job.status == "completed"
    assert job.last_error is None
    assert handled == ["919000000101"]  # ran exactly once
    assert sent == []  # no notice/apology — the turn's own reply was the only send

    # And nothing is left for the retry worker to pick up.
    await whatsapp._retry_pending_webhooks_once()
    assert handled == ["919000000101"]


async def test_timeout_is_not_shadowed_by_lock_release_and_sends_the_slow_reply_notice(wa_db, monkeypatch):
    sent = []

    async def hanging_handle_message(db, payload):
        await asyncio.sleep(5)

    async def fake_send(phone, text, *a, **kw):
        sent.append((phone, text))
        return {"sent": True}

    monkeypatch.setattr(whatsapp, "_handle_message", hanging_handle_message)
    monkeypatch.setattr(whatsapp, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(whatsapp, "WEBHOOK_PROCESSING_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(rate_limit, "student_lock", lambda phone: _ExpiringLock())
    _queue_job(wa_db, "wamid.timeout1", "919000000102")

    await whatsapp._process_queued_webhook("wamid.timeout1")

    job = wa_db.query(ProcessedWebhookMessage).filter_by(message_id="wamid.timeout1").one()
    assert job.status == "failed"
    assert "timed out" in job.last_error
    assert sent == [("919000000102", whatsapp.SLOW_REPLY_NOTICE)]


async def test_third_failure_sends_one_apology_and_gives_up(wa_db, monkeypatch):
    sent = []

    async def broken_handle_message(db, payload):
        raise RuntimeError("provider exploded")

    async def fake_send(phone, text, *a, **kw):
        sent.append((phone, text))
        return {"sent": True}

    monkeypatch.setattr(whatsapp, "_handle_message", broken_handle_message)
    monkeypatch.setattr(whatsapp, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(rate_limit, "student_lock", lambda phone: _ExpiringLock())
    # Two earlier attempts already burned; this claim makes it the third.
    _queue_job(wa_db, "wamid.fail3", "919000000103", attempts=whatsapp.WEBHOOK_MAX_ATTEMPTS - 1)

    await whatsapp._process_queued_webhook("wamid.fail3")

    job = wa_db.query(ProcessedWebhookMessage).filter_by(message_id="wamid.fail3").one()
    assert job.status == "failed"
    assert job.attempts == whatsapp.WEBHOOK_MAX_ATTEMPTS
    assert "provider exploded" in job.last_error
    assert sent == [("919000000103", whatsapp.FAILED_JOB_NOTICE)]

    # A failed job is never re-run, so the apology can only ever go out once.
    await whatsapp._retry_pending_webhooks_once()
    assert sent == [("919000000103", whatsapp.FAILED_JOB_NOTICE)]


async def test_earlier_failures_requeue_silently(wa_db, monkeypatch):
    sent = []

    async def broken_handle_message(db, payload):
        raise RuntimeError("blip")

    async def fake_send(phone, text, *a, **kw):
        sent.append(text)
        return {"sent": True}

    monkeypatch.setattr(whatsapp, "_handle_message", broken_handle_message)
    monkeypatch.setattr(whatsapp, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(rate_limit, "student_lock", lambda phone: _ExpiringLock())
    _queue_job(wa_db, "wamid.fail1", "919000000104")

    await whatsapp._process_queued_webhook("wamid.fail1")

    job = wa_db.query(ProcessedWebhookMessage).filter_by(message_id="wamid.fail1").one()
    assert job.status == "pending"
    assert sent == []


async def test_retry_worker_survives_an_iteration_that_raises(db_session, monkeypatch):
    """The recovery loop must outlive a DB blip and only ever stop on cancellation."""
    calls = []
    iterations_done = asyncio.Event()

    class _BrokenSession:
        def query(self, *a, **kw):
            raise RuntimeError("database connection dropped")

        def close(self):
            pass

    def flaky_session_local():
        calls.append(1)
        if len(calls) == 1:
            return _BrokenSession()
        if len(calls) >= 3:
            iterations_done.set()
        monkeypatch.setattr(db_session, "close", lambda: None)
        return db_session

    monkeypatch.setattr(whatsapp, "SessionLocal", flaky_session_local)
    monkeypatch.setattr(whatsapp, "WEBHOOK_RETRY_ERROR_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(whatsapp, "WEBHOOK_RETRY_INTERVAL_SECONDS", 0)

    task = asyncio.create_task(whatsapp.retry_pending_webhooks())
    await asyncio.wait_for(iterations_done.wait(), timeout=5)
    assert not task.done()  # still running after the first iteration blew up
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 3


# --- Owner echo / status events (P1) ---


def _enrolled(db, phone: str) -> Student:
    centre = Centre(name="Echo School")
    db.add(centre)
    db.commit()
    student = Student(name="Real Student", phone=phone, centre_id=centre.id)
    db.add(student)
    db.commit()
    return student


@pytest.mark.parametrize(
    "payload",
    [
        {"eventType": "message", "owner": True, "type": "text", "waId": "919000000105", "text": "our own reply"},
        {"eventType": "sentMessageDELIVERED", "owner": False, "waId": "919000000105"},
        {"eventType": "message", "owner": False, "type": "template", "waId": "919000000105"},
        {"eventType": "message", "owner": False, "waId": "919000000105"},
    ],
    ids=["owner-echo", "status-event", "unknown-type", "no-content"],
)
async def test_non_customer_payloads_are_acknowledged_silently(db_session, monkeypatch, payload):
    _enrolled(db_session, "919000000105")
    sent = []

    async def fake_send(phone, text, *a, **kw):
        sent.append(text)
        return {"sent": True}

    async def fail_if_processed(db, student, text):
        raise AssertionError("process_message must not run for a non-customer payload")

    monkeypatch.setattr(whatsapp, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(whatsapp, "send_whatsapp_buttons", fake_send)
    monkeypatch.setattr(whatsapp, "process_message", fail_if_processed)

    await whatsapp._handle_message(db_session, payload)

    assert sent == []


async def test_a_real_unsupported_message_type_still_gets_the_cant_handle_reply(db_session, monkeypatch):
    from app.services import cost_tracker

    student = _enrolled(db_session, "919000000106")
    cost_tracker.add_trial_credits(db_session, student.id)
    sent = []

    async def fake_send(phone, text, *a, **kw):
        sent.append(text)
        return {"sent": True}

    monkeypatch.setattr(whatsapp, "send_whatsapp_message", fake_send)

    await whatsapp._handle_message(
        db_session, {"eventType": "message", "owner": False, "type": "sticker", "waId": "919000000106"},
    )

    assert len(sent) == 1
    assert "I can only handle text" in sent[0]
