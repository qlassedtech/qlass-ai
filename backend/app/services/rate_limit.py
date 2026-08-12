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

# Deliberately stricter and a separate key namespace from the WhatsApp
# chat limit above — a 6-digit OTP only has 1M combinations, so without
# this, an unauthenticated attacker could brute-force it across its
# 10-minute lifetime given enough parallel requests. Also protects
# request-otp itself from being used to spam a victim's WhatsApp.
OTP_RATE_LIMIT_MAX_ATTEMPTS = 5
OTP_RATE_LIMIT_WINDOW_SECONDS = 600

# Confirmed live (code review, Aug 2026): neither new-account path — the
# public web form (app.routers.public) nor a cold WhatsApp first-message
# (app.routers.whatsapp._create_new_student) — had ANY rate limiting before
# this, letting a script mint unlimited accounts each carrying a real ₹50
# trial-credit grant. A much larger window/threshold than the per-student
# message or OTP limiters above, since this only needs to catch a scripted
# burst — a real school's normal enrollment, even a rapid one, comes
# nowhere near it.
SIGNUP_RATE_LIMIT_MAX_ATTEMPTS = 30
SIGNUP_RATE_LIMIT_WINDOW_SECONDS = 600

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


async def is_otp_rate_limited(purpose: str, phone: str) -> bool:
    """Same sliding-window approach as is_rate_limited, but its own key
    namespace/threshold — used for both requesting and verifying an OTP
    (see app.services.otp), so neither can be spammed or brute-forced."""
    key_prefix = f"otprate:{purpose}"
    if _redis is None:
        now = time.monotonic()
        window = _fallback_timestamps[f"{key_prefix}:{phone}"]
        window[:] = [t for t in window if now - t <= OTP_RATE_LIMIT_WINDOW_SECONDS]
        window.append(now)
        return len(window) > OTP_RATE_LIMIT_MAX_ATTEMPTS

    key = f"{key_prefix}:{phone}"
    now = time.time()
    member = f"{now}:{uuid.uuid4()}"
    pipe = _redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - OTP_RATE_LIMIT_WINDOW_SECONDS)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, OTP_RATE_LIMIT_WINDOW_SECONDS)
    _, _, count, _ = await pipe.execute()
    return count > OTP_RATE_LIMIT_MAX_ATTEMPTS


async def is_signup_rate_limited(key: str) -> bool:
    """
    Sliding-window limiter for new-account creation. `key` is whatever
    identifies the source of a signup burst — the requester's IP for the
    public web form (no other identity exists before an account is
    created), or a shared constant for WhatsApp cold-starts (no IP is ever
    available on a webhook delivery, so this is deliberately a single
    global bucket rather than per-phone — a per-phone limit can't stop an
    attacker who has many distinct throwaway numbers, which is exactly the
    abuse this guards against).
    """
    key_prefix = "signuprate"
    if _redis is None:
        now = time.monotonic()
        window = _fallback_timestamps[f"{key_prefix}:{key}"]
        window[:] = [t for t in window if now - t <= SIGNUP_RATE_LIMIT_WINDOW_SECONDS]
        window.append(now)
        return len(window) > SIGNUP_RATE_LIMIT_MAX_ATTEMPTS

    redis_key = f"{key_prefix}:{key}"
    now = time.time()
    member = f"{now}:{uuid.uuid4()}"
    pipe = _redis.pipeline()
    pipe.zremrangebyscore(redis_key, 0, now - SIGNUP_RATE_LIMIT_WINDOW_SECONDS)
    pipe.zadd(redis_key, {member: now})
    pipe.zcard(redis_key)
    pipe.expire(redis_key, SIGNUP_RATE_LIMIT_WINDOW_SECONDS)
    _, _, count, _ = await pipe.execute()
    return count > SIGNUP_RATE_LIMIT_MAX_ATTEMPTS


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
