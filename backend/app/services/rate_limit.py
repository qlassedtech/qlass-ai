import asyncio
import ipaddress
import time
import uuid
from collections import defaultdict

import redis.asyncio as redis
from fastapi import Request

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
# burst.
#
# Raised from 30 to 300 (security-audit review, Aug 2026, ahead of a real
# 200-student same-network onboarding): the original threshold assumed "a
# real school's normal enrollment... comes nowhere near it," which turned
# out false the moment a genuinely large classroom rollout was tested —
# 200 students self-registering from a shared school WiFi/NAT all share
# ONE X-Real-IP, and 30/10min would have 429'd roughly 85% of them. 300
# still comfortably catches a scripted-abuse burst (this endpoint no
# longer grants credit on its own since the OTP-gated /public/register/
# verify split — see that endpoint's docstring — so this limiter's real
# job now is bounding OTP-send volume/cost, not blocking free-credit
# farming outright).
SIGNUP_RATE_LIMIT_MAX_ATTEMPTS = 300
SIGNUP_RATE_LIMIT_WINDOW_SECONDS = 600

# Password login: failed attempts only. Tight per (phone, IP) so a
# legitimate user isn't locked out by a stranger, with a looser pure
# per-phone ceiling so an attacker rotating IPs is still bounded.
LOGIN_FAIL_MAX_PER_IP = 5
LOGIN_FAIL_MAX_PER_PHONE = 30
LOGIN_FAIL_WINDOW_SECONDS = 600

# Razorpay order/subscription creation — each call hits Razorpay's API and
# leaves an unpaid order behind, so bound it per phone and per IP.
PAYMENT_RATE_LIMIT_MAX_PER_PHONE = 10
PAYMENT_RATE_LIMIT_MAX_PER_IP = 30
PAYMENT_RATE_LIMIT_WINDOW_SECONDS = 600

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


def client_ip(request: Request) -> str:
    """
    request.client.host is authoritative once uvicorn runs with
    --proxy-headers; only trust X-Real-IP when the direct peer is loopback
    (i.e. the local reverse proxy), so a remote client can't spoof it.
    """
    host = request.client.host if request.client else None
    if host:
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return host
        except ValueError:
            return host
    return request.headers.get("x-real-ip") or host or "unknown"


async def _sliding_window_count(key: str, window_seconds: int, record: bool = True) -> int:
    """Shared sliding-window counter (Redis, with the same in-process fallback as above)."""
    if _redis is None:
        now = time.monotonic()
        window = _fallback_timestamps[key]
        window[:] = [t for t in window if now - t <= window_seconds]
        if record:
            window.append(now)
        return len(window)

    now = time.time()
    pipe = _redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - window_seconds)
    if record:
        pipe.zadd(key, {f"{now}:{uuid.uuid4()}": now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    results = await pipe.execute()
    return results[2] if record else results[1]


async def is_login_blocked(phone: str, ip: str) -> bool:
    """Read-only check — call before verifying the password; see record_login_failure."""
    per_ip = await _sliding_window_count(f"loginfail:ip:{phone}:{ip}", LOGIN_FAIL_WINDOW_SECONDS, record=False)
    per_phone = await _sliding_window_count(f"loginfail:phone:{phone}", LOGIN_FAIL_WINDOW_SECONDS, record=False)
    return per_ip >= LOGIN_FAIL_MAX_PER_IP or per_phone >= LOGIN_FAIL_MAX_PER_PHONE


async def record_login_failure(phone: str, ip: str) -> None:
    await _sliding_window_count(f"loginfail:ip:{phone}:{ip}", LOGIN_FAIL_WINDOW_SECONDS)
    await _sliding_window_count(f"loginfail:phone:{phone}", LOGIN_FAIL_WINDOW_SECONDS)


async def is_payment_rate_limited(phone: str, ip: str) -> bool:
    per_phone = await _sliding_window_count(f"payrate:phone:{phone}", PAYMENT_RATE_LIMIT_WINDOW_SECONDS)
    per_ip = await _sliding_window_count(f"payrate:ip:{ip}", PAYMENT_RATE_LIMIT_WINDOW_SECONDS)
    return per_phone > PAYMENT_RATE_LIMIT_MAX_PER_PHONE or per_ip > PAYMENT_RATE_LIMIT_MAX_PER_IP


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
