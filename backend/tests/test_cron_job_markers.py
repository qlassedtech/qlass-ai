"""
Idempotency of the cron send_* jobs (scripts/job_markers.py +
scripts/send_habit_nudges.py / send_revision_reminders.py /
send_parent_digests.py): a recipient marked as sent today is skipped on a
re-run, and the marker is written BEFORE the send so a crash mid-send can't
produce a duplicate. Uses the in-memory sqlite db_session fixture and an
in-process (no Redis) marker, same import pattern as
tests/test_nudge_funfact_batch.py.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from app.models.core import Centre, Parent, Student  # noqa: E402

import job_markers  # noqa: E402
import send_habit_nudges  # noqa: E402
import send_parent_digests  # noqa: E402
import send_revision_reminders  # noqa: E402
from job_markers import MARKER_TTL_SECONDS, JobMarker  # noqa: E402


def _make_student(db_session, **overrides):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    defaults = dict(
        name="Test Student", phone="919000000040", centre_id=centre.id, class_="9", board="CBSE",
        is_deleted=False, is_staff_profile=False,
    )
    defaults.update(overrides)
    student = Student(**defaults)
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


# ---------------------------------------------------------------------------
# JobMarker itself
# ---------------------------------------------------------------------------

async def test_marker_without_redis_is_scoped_by_job_day_and_recipient():
    marker = JobMarker("habit_nudges", redis_url="", run_date=date(2026, 9, 25))
    assert not marker.using_redis
    assert marker.key(42) == "cron_job_sent:habit_nudges:2026-09-25:42"
    assert marker.key(42, "Fractions") == "cron_job_sent:habit_nudges:2026-09-25:42:Fractions"

    assert await marker.already_sent(42) is False
    await marker.mark_sent(42)
    assert await marker.already_sent(42) is True
    assert await marker.already_sent(43) is False
    assert await marker.already_sent(42, "Fractions") is False

    other_day = JobMarker("habit_nudges", redis_url="", run_date=date(2026, 9, 26))
    assert await other_day.already_sent(42) is False


class _FakeRedis:
    def __init__(self, fail=False):
        self.store = {}
        self.set_calls = []
        self.fail = fail
        self.closed = False

    async def get(self, key):
        if self.fail:
            raise RedisConnectionError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.fail:
            raise RedisConnectionError("redis down")
        self.store[key] = value
        self.set_calls.append((key, value, ex))

    async def aclose(self):
        self.closed = True


async def test_marker_uses_redis_ttl_keys_when_available(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(job_markers.redis.Redis, "from_url", staticmethod(lambda url, **kw: fake))
    marker = JobMarker("parent_digests", redis_url="redis://fake:6379/0", run_date=date(2026, 9, 25))
    assert marker.using_redis

    assert await marker.already_sent(7) is False
    await marker.mark_sent(7)
    assert fake.set_calls == [("cron_job_sent:parent_digests:2026-09-25:7", "1", MARKER_TTL_SECONDS)]
    assert await marker.already_sent(7) is True

    # A second process/run on the same day sees the Redis key — that's the
    # whole point (the in-process set alone wouldn't survive a crash).
    rerun = JobMarker("parent_digests", redis_url="redis://fake:6379/0", run_date=date(2026, 9, 25))
    assert await rerun.already_sent(7) is True

    await marker.close()
    assert fake.closed


async def test_marker_degrades_to_in_process_when_redis_fails(monkeypatch, capsys):
    fake = _FakeRedis(fail=True)
    monkeypatch.setattr(job_markers.redis.Redis, "from_url", staticmethod(lambda url, **kw: fake))
    marker = JobMarker("revision_reminders", redis_url="redis://fake:6379/0")

    assert await marker.already_sent(1) is False  # doesn't raise
    assert not marker.using_redis
    await marker.mark_sent(1)
    assert await marker.already_sent(1) is True  # still dedups within this run
    assert "WARNING: Redis unavailable" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# send_habit_nudges.py
# ---------------------------------------------------------------------------

def _wire_habit(monkeypatch, db_session, marker, sends):
    async def fake_send_notification(phone, template, params, fallback):
        sends.append(phone)
        return {"sent": True}

    monkeypatch.setattr(send_habit_nudges, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(send_habit_nudges, "JobMarker", lambda job: marker)
    monkeypatch.setattr(send_habit_nudges, "send_notification", fake_send_notification)
    monkeypatch.setattr(send_habit_nudges.settings, "habit_bonus_template", "habit_bonus")
    monkeypatch.setattr(db_session, "close", lambda: None)


async def test_habit_nudge_is_not_resent_on_rerun_same_day(db_session, monkeypatch):
    # Day-1 milestone window: created ~1.5 days ago, never chatted.
    student = _make_student(db_session, created_at=datetime.now(timezone.utc) - timedelta(days=1, hours=12))
    marker = JobMarker("habit_nudges", redis_url="")
    sends = []
    _wire_habit(monkeypatch, db_session, marker, sends)

    await send_habit_nudges.send_nudges(dry_run=False)
    assert sends == [student.phone]

    await send_habit_nudges.send_nudges(dry_run=False)  # "crashed, re-run by hand"
    assert sends == [student.phone]  # no second message
    assert await marker.already_sent(student.id)


async def test_habit_nudge_marks_before_sending_so_a_crash_cannot_duplicate(db_session, monkeypatch):
    student = _make_student(db_session, created_at=datetime.now(timezone.utc) - timedelta(days=1, hours=12))
    marker = JobMarker("habit_nudges", redis_url="")
    _wire_habit(monkeypatch, db_session, marker, [])

    async def exploding_send(*args, **kwargs):
        raise RuntimeError("WhatsApp API down")

    monkeypatch.setattr(send_habit_nudges, "send_notification", exploding_send)
    with pytest.raises(RuntimeError):
        await send_habit_nudges.send_nudges(dry_run=False)
    assert await marker.already_sent(student.id)


async def test_habit_nudge_dry_run_does_not_set_the_marker(db_session, monkeypatch):
    student = _make_student(db_session, created_at=datetime.now(timezone.utc) - timedelta(days=1, hours=12))
    marker = JobMarker("habit_nudges", redis_url="")
    sends = []
    _wire_habit(monkeypatch, db_session, marker, sends)

    await send_habit_nudges.send_nudges(dry_run=True)
    assert sends == []
    assert not await marker.already_sent(student.id)


# ---------------------------------------------------------------------------
# send_revision_reminders.py
# ---------------------------------------------------------------------------

async def test_revision_reminder_is_keyed_per_student_and_topic(db_session, monkeypatch):
    student = _make_student(db_session)
    due = [SimpleNamespace(student_id=student.id, topic="Fractions")]
    marker = JobMarker("revision_reminders", redis_url="")
    sends = []

    async def fake_send(phone, message):
        sends.append((phone, message))
        return {"sent": True}

    monkeypatch.setattr(send_revision_reminders, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(send_revision_reminders, "JobMarker", lambda job: marker)
    monkeypatch.setattr(send_revision_reminders.revision_scheduler, "get_due_reviews", lambda db: due)
    monkeypatch.setattr(send_revision_reminders, "send_whatsapp_message", fake_send)
    monkeypatch.setattr(db_session, "close", lambda: None)

    await send_revision_reminders.send_reminders(dry_run=False)
    await send_revision_reminders.send_reminders(dry_run=False)
    assert len(sends) == 1
    assert "Fractions" in sends[0][1]

    # A different topic coming due the same day is a different reminder.
    due.append(SimpleNamespace(student_id=student.id, topic="Decimals"))
    await send_revision_reminders.send_reminders(dry_run=False)
    assert len(sends) == 2
    assert "Decimals" in sends[1][1]


# ---------------------------------------------------------------------------
# send_parent_digests.py
# ---------------------------------------------------------------------------

async def test_parent_digest_is_not_resent_on_rerun_same_day(db_session, monkeypatch):
    student = _make_student(db_session)
    db_session.add(Parent(student_id=student.id, name="Parent One", phone="919000000041"))
    db_session.commit()
    marker = JobMarker("parent_digests", redis_url="")
    sends = []

    async def fake_send_notification(phone, template, params, fallback):
        sends.append(phone)
        return {"sent": True}

    monkeypatch.setattr(send_parent_digests, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(send_parent_digests, "JobMarker", lambda job: marker)
    monkeypatch.setattr(send_parent_digests, "get_student_stats", lambda db, sid, days=None: {})
    monkeypatch.setattr(send_parent_digests, "get_activity_stats", lambda db, sid: {})
    monkeypatch.setattr(send_parent_digests, "format_parent_digest", lambda name, stats, activity: f"digest for {name}")
    monkeypatch.setattr(send_parent_digests, "format_parent_digest_summary", lambda stats, activity: "summary")
    monkeypatch.setattr(send_parent_digests, "send_notification", fake_send_notification)
    monkeypatch.setattr(send_parent_digests.settings, "parent_digest_template", "parent_digest")
    monkeypatch.setattr(db_session, "close", lambda: None)

    await send_parent_digests.send_digests(dry_run=False)
    await send_parent_digests.send_digests(dry_run=False)
    assert sends == ["919000000041"]
