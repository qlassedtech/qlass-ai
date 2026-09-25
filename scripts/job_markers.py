"""
Per-run idempotency markers for the cron send_* jobs.

Problem: send_revision_reminders.py / send_habit_nudges.py /
send_parent_digests.py loop over every eligible recipient and send as they
go. If a run dies half-way (WhatsApp API outage, OOM, deploy restart) and
someone re-runs it — or cron's next day overlaps a hung one before
`flock -n` was added — every recipient who already got the message gets it
again. None of these jobs records "sent" in the database (the reminder is
deliberately not what advances a revision schedule, a habit nudge never
grants the credit itself, a digest is pure output), so there was nothing to
skip on.

Fix: the same Redis TTL-key pattern scripts/send_reengagement_nudges.py
already uses for its cooldown, keyed per job + calendar day + recipient:

    cron_job_sent:<job>:<YYYY-MM-DD>:<student_id>[:<extra>]

The marker is set BEFORE the send (a crash between "marked" and "sent"
costs one skipped message for that recipient today, the safe direction —
the alternative, mark-after-send, is exactly the duplicate-send race we're
fixing). Keys expire after MARKER_TTL_SECONDS so nothing accumulates; the
date in the key is what actually scopes the run, the TTL is just cleanup.

If Redis is unreachable or REDIS_URL is empty, the marker degrades to an
in-process set with one warning — a re-run after a crash then won't skip
(same fallback semantics as send_reengagement_nudges._fallback_sent), but
a single run can still never double-send within itself.

Usage inside a job:

    marker = JobMarker("habit_nudges")
    ...
    if await marker.already_sent(student.id):
        continue
    await marker.mark_sent(student.id)
    await send_...(...)
    ...
    await marker.close()
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import redis.asyncio as redis  # noqa: E402
from redis.exceptions import RedisError  # noqa: E402

from app.config import settings  # noqa: E402

KEY_PREFIX = "cron_job_sent"
# Two days: comfortably past the run's own day (keys are date-scoped so a
# longer TTL can't suppress tomorrow's send), short enough to never pile up.
MARKER_TTL_SECONDS = 48 * 3600


class JobMarker:
    def __init__(self, job: str, *, redis_url: str | None = None, run_date: date | None = None) -> None:
        self.job = job
        self.run_date = (run_date or date.today()).isoformat()
        url = settings.redis_url if redis_url is None else redis_url
        self._fallback: set[str] = set()
        self._warned = False
        self._redis = None
        if url:
            try:
                self._redis = redis.Redis.from_url(url, decode_responses=True)  # lazy — no connection yet
            except (RedisError, ValueError) as exc:
                self._degrade(exc)

    @property
    def using_redis(self) -> bool:
        return self._redis is not None

    def key(self, *parts: object) -> str:
        return ":".join([KEY_PREFIX, self.job, self.run_date, *(str(p) for p in parts)])

    def _degrade(self, exc: Exception) -> None:
        # One warning per process, then in-memory only for the rest of the run.
        if not self._warned:
            print(f"WARNING: Redis unavailable for {self.job} run markers ({exc!r}); "
                  f"falling back to in-process markers — a re-run of this job today may re-send.")
            self._warned = True
        self._redis = None

    async def already_sent(self, *parts: object) -> bool:
        key = self.key(*parts)
        if self._redis is not None:
            try:
                return await self._redis.get(key) is not None
            except (RedisError, OSError) as exc:
                self._degrade(exc)
        return key in self._fallback

    async def mark_sent(self, *parts: object) -> None:
        key = self.key(*parts)
        # Always record locally too, so a Redis failure mid-run can't make
        # the same run send twice to the same recipient.
        self._fallback.add(key)
        if self._redis is not None:
            try:
                await self._redis.set(key, "1", ex=MARKER_TTL_SECONDS)
            except (RedisError, OSError) as exc:
                self._degrade(exc)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except (RedisError, OSError):
                pass
