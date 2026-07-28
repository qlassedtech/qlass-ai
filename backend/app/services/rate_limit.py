import asyncio
import time
import uuid
from collections import defaultdict

import redis.asyncio as redis

from app.config import settings

RATE_LIMIT_MAX_MESSAGES = 15
RATE_LIMIT_WINDOW_SECONDS = 60
STUDENT_LOCK_TIMEOUT_SECONDS = 60  # auto-expires if a worker crashes mid-processing
STUDENT_LOCK_BLOCKING_TIMEOUT_SECONDS = 30

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None

# Fallback for when Redis isn't reachable at all: per-process only, same
# limitation this module exists to fix, but better than crashing outright.
_fallback_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_fallback_timestamps: dict[str, list] = defaultdict(list)


async def is_rate_limited(phone: str) -> bool:
    """
    Sliding-window rate limit backed by Redis, so it's shared across every
    worker process — a plain in-memory dict only rate-limits within a
    single process; with N uvicorn/gunicorn workers each would independently
    allow 15/min, silently multiplying the real limit by N.
    """
    if _redis is None:
        now = time.monotonic()
        window = _fallback_timestamps[phone]
        window[:] = [t for t in window if now - t <= RATE_LIMIT_WINDOW_SECONDS]
        window.append(now)
        return len(window) > RATE_LIMIT_MAX_MESSAGES

    key = f"ratelimit:{phone}"
    now = time.time()
    member = f"{now}:{uuid.uuid4()}"  # unique member so same-millisecond calls don't collide/overwrite
    pipe = _redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - RATE_LIMIT_WINDOW_SECONDS)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    _, _, count, _ = await pipe.execute()
    return count > RATE_LIMIT_MAX_MESSAGES


def student_lock(phone: str):
    """
    Distributed lock so only one worker process at a time processes
    messages for a given student — an in-memory asyncio.Lock only
    serializes within a single process; across multiple workers, two
    processes could still race on the same student's chat history (the
    original duplicate-reply bug this was built to fix).
    """
    if _redis is None:
        return _fallback_locks[phone]
    return _redis.lock(
        f"studentlock:{phone}",
        timeout=STUDENT_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=STUDENT_LOCK_BLOCKING_TIMEOUT_SECONDS,
    )
