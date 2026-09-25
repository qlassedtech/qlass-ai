import asyncio
import ipaddress
import json
import logging
import time
import uuid
import weakref
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime

import redis.asyncio as redis
from fastapi import Request
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import LockError, LockNotOwnedError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import settings

logger = logging.getLogger(__name__)

RATE_LIMIT_MAX_MESSAGES = 15
RATE_LIMIT_WINDOW_SECONDS = 60

# How long one student turn (STT -> tutor -> TTS -> send) may run before the
# caller gives up on it — app.routers.whatsapp wraps _handle_message in
# asyncio.wait_for with this value. The per-student Redis lock below MUST
# outlive it: confirmed live (audit, Sept 2026) that with a 60s lock TTL
# and a 240s processing timeout, any turn slower than 60s had its lock
# silently expire mid-flight, so `async with lock` raised
# LockNotOwnedError on exit AFTER the reply was already sent and billed —
# the generic error handler then re-queued the job and the whole turn ran
# again: second reply, second bill. Defined here, in one place, as
# processing timeout + margin so the two can never drift apart again (see
# tests/test_rate_limit.py::test_student_lock_outlives_processing_timeout).
STUDENT_TURN_TIMEOUT_SECONDS = 240
STUDENT_LOCK_TIMEOUT_SECONDS = STUDENT_TURN_TIMEOUT_SECONDS + 60  # auto-expires if a worker crashes mid-processing
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

# Short socket timeouts: every helper here runs inline on the hottest
# request path in the app, so a Redis that's hung (not just down) must
# cost seconds at most before the fail-open path below takes over.
_redis = (
    redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=5)
    if settings.redis_url
    else None
)

# redis.asyncio connections are bound to the event loop they were opened
# on — reusing the pool from another loop fails with "attached to a
# different loop" / "Event loop is closed". Production has exactly one
# loop per worker so it never notices, but FastAPI's TestClient runs each
# request/WebSocket context on its own loop, which is why Redis-touching
# endpoint tests failed depending on run order. One client per live loop
# (weak-keyed, so a finished loop's client is dropped with it); `_redis`
# stays the canonical/first client and the monkeypatch target for tests —
# anything assigned there that isn't a real redis.Redis is used as-is.
_loop_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, redis.Redis]" = weakref.WeakKeyDictionary()


def _client():
    if _redis is None or not isinstance(_redis, redis.Redis):
        return _redis
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _redis
    client = _loop_clients.get(loop)
    if client is None:
        # Always a fresh client per loop — never `_redis` itself, whose
        # pool may already hold connections opened on a loop that has
        # since been closed.
        client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=5,
        )
        _loop_clients[loop] = client
    return client


# Errors that mean "Redis itself is unreachable" (down, hung, mid-restart)
# — as opposed to a bug in how it's being called. Every helper in this
# module FAILS OPEN on these: confirmed live (audit, Sept 2026) that with
# Redis down, is_rate_limited/student_lock raised on every single call, so
# every WhatsApp message failed all 3 attempts with nothing sent to the
# student — an outage of the rate limiter became an outage of the whole
# product. Not limiting a few students for the duration of a Redis blip is
# the far smaller harm.
_REDIS_UNAVAILABLE_ERRORS = (RedisConnectionError, RedisTimeoutError)

# Fallback for when Redis isn't reachable at all: per-process only, same
# limitation this module exists to fix, but better than crashing outright.
_fallback_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_fallback_timestamps: dict[str, list] = defaultdict(list)

# Redis-outage logging is throttled to one line per minute per process —
# during an outage every message would otherwise log the same stack trace
# several times over (rate limit + lock + worksheet + ticket checks).
REDIS_OUTAGE_LOG_INTERVAL_SECONDS = 60
_last_outage_log_at: float = 0.0


def _note_redis_unavailable(exc: BaseException, where: str) -> None:
    """Log (throttled) and count a Redis outage; never raises."""
    global _last_outage_log_at
    now = time.monotonic()
    if now - _last_outage_log_at >= REDIS_OUTAGE_LOG_INTERVAL_SECONDS:
        _last_outage_log_at = now
        logger.warning(
            "Redis unavailable in rate_limit.%s (%s: %s) — failing open (not rate limiting / in-process fallbacks) "
            "until it's back; this line is logged at most once a minute",
            where, type(exc).__name__, exc,
        )
    try:
        # Lazy + optional: alerts may not exist in every deployment, and it
        # must never be a reason the fail-open path itself fails.
        from app.services import alerts

        alerts.note_provider_error("redis", None)
    except Exception:
        pass


def _fallback_window_count(key: str, window_seconds: int, record: bool = True) -> int:
    """In-process sliding window (used when Redis is not configured or unreachable)."""
    now = time.monotonic()
    window = _fallback_timestamps[key]
    window[:] = [t for t in window if now - t <= window_seconds]
    if record:
        window.append(now)
    return len(window)


async def _sliding_window_count(key: str, window_seconds: int, record: bool = True) -> int:
    """
    Shared sliding-window counter: Redis (shared across every worker
    process) with the in-process fallback when Redis isn't configured or
    is unreachable (see _REDIS_UNAVAILABLE_ERRORS).
    """
    client = _client()
    if client is None:
        return _fallback_window_count(key, window_seconds, record)

    now = time.time()
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(key, 0, now - window_seconds)
        if record:
            pipe.zadd(key, {f"{now}:{uuid.uuid4()}": now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds)
        results = await pipe.execute()
    except _REDIS_UNAVAILABLE_ERRORS as exc:
        _note_redis_unavailable(exc, "_sliding_window_count")
        return _fallback_window_count(key, window_seconds, record)
    return results[2] if record else results[1]


async def is_rate_limited(phone: str) -> bool:
    """
    Sliding-window rate limit backed by Redis, so it's shared across every
    worker process — a plain in-memory dict only rate-limits within a
    single process; with N uvicorn/gunicorn workers each would independently
    allow 15/min, silently multiplying the real limit by N.
    """
    count = await _sliding_window_count(f"ratelimit:{phone}", RATE_LIMIT_WINDOW_SECONDS)
    return count > RATE_LIMIT_MAX_MESSAGES


async def is_otp_rate_limited(purpose: str, phone: str) -> bool:
    """Same sliding-window approach as is_rate_limited, but its own key
    namespace/threshold — used for both requesting and verifying an OTP
    (see app.services.otp), so neither can be spammed or brute-forced."""
    count = await _sliding_window_count(f"otprate:{purpose}:{phone}", OTP_RATE_LIMIT_WINDOW_SECONDS)
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
    count = await _sliding_window_count(f"signuprate:{key}", SIGNUP_RATE_LIMIT_WINDOW_SECONDS)
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


# Disposable "last worksheet answers pending" state for
# app.services.chat_core's worksheet-generation intent — a student who
# asks for a worksheet gets the questions immediately, with the answer key
# held back until they reply "answers"; that key needs to live SOMEWHERE
# between those two turns. Redis with a short TTL, rather than a new
# Student column, since this is purely transient per-conversation state
# with no reason to survive indefinitely (a student who never asks for the
# answers just has this expire quietly). Same in-process-dict fallback
# every other Redis-backed helper here uses when Redis isn't reachable.
PENDING_WORKSHEET_TTL_SECONDS = 30 * 60
_fallback_worksheets: dict[str, list] = {}


async def set_pending_worksheet_answers(student_id: int, questions: list[dict]) -> None:
    key = f"worksheet:{student_id}"
    client = _client()
    if client is not None:
        try:
            await client.set(key, json.dumps(questions), ex=PENDING_WORKSHEET_TTL_SECONDS)
            return
        except _REDIS_UNAVAILABLE_ERRORS as exc:
            _note_redis_unavailable(exc, "set_pending_worksheet_answers")
    _fallback_worksheets[key] = questions


async def get_pending_worksheet_answers(student_id: int) -> list[dict] | None:
    key = f"worksheet:{student_id}"
    client = _client()
    if client is not None:
        try:
            raw = await client.get(key)
            return json.loads(raw) if raw else None
        except _REDIS_UNAVAILABLE_ERRORS as exc:
            _note_redis_unavailable(exc, "get_pending_worksheet_answers")
    return _fallback_worksheets.get(key)


async def clear_pending_worksheet_answers(student_id: int) -> None:
    key = f"worksheet:{student_id}"
    client = _client()
    if client is not None:
        try:
            await client.delete(key)
            return
        except _REDIS_UNAVAILABLE_ERRORS as exc:
            _note_redis_unavailable(exc, "clear_pending_worksheet_answers")
    _fallback_worksheets.pop(key, None)


async def claim_single_use_token(jti: str, ttl_seconds: int) -> bool:
    """
    Atomically mark a one-shot token id as used (SETNX with a TTL). True
    the first time for a given `jti`, False on every reuse within
    `ttl_seconds`. Used by the voice-call ticket (see
    app.services.student_auth.consume_voice_call_ticket). Fails OPEN to a
    per-process fallback set when Redis is unreachable — the token's own
    short expiry is then the only guard, which is the documented trade-off.
    """
    client = _client()
    if client is not None:
        try:
            return bool(await client.set(f"usedtoken:{jti}", "1", ex=ttl_seconds, nx=True))
        except _REDIS_UNAVAILABLE_ERRORS as exc:
            _note_redis_unavailable(exc, "claim_single_use_token")
    now = time.monotonic()
    for used_jti, expires_at in list(_fallback_used_tokens.items()):
        if expires_at <= now:
            _fallback_used_tokens.pop(used_jti, None)
    if jti in _fallback_used_tokens:
        return False
    _fallback_used_tokens[jti] = now + ttl_seconds
    return True


_fallback_used_tokens: dict[str, float] = {}


def student_lock(phone: str):
    """
    Distributed lock so only one worker process at a time processes
    messages for a given student — an in-memory asyncio.Lock only
    serializes within a single process; across multiple workers, two
    processes could still race on the same student's chat history (the
    original duplicate-reply bug this was built to fix).

    Prefer student_turn_lock below for `async with` use on a request path:
    this raw lock raises on a Redis outage and on a release after its TTL
    expired, both of which student_turn_lock turns into a warning instead.
    """
    client = _client()
    if client is None:
        return _fallback_locks[phone]
    return client.lock(
        f"studentlock:{phone}",
        timeout=STUDENT_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=STUDENT_LOCK_BLOCKING_TIMEOUT_SECONDS,
    )


@asynccontextmanager
async def student_turn_lock(phone: str):
    """
    `async with` wrapper around student_lock for one student turn, with the
    two outage behaviours every channel wants:

    - Redis unreachable on acquire -> fall back to the in-process lock for
      this phone (serializes within this worker; a no-op across workers)
      with a throttled warning, instead of failing the whole turn.
    - LockNotOwnedError on release (the lock's TTL elapsed while the turn
      was still running) -> a warning, NOT an exception: by the time the
      body has finished the reply has been sent and billed, so raising here
      would make the caller's error handling treat a completed turn as a
      failed one (see STUDENT_LOCK_TIMEOUT_SECONDS for the double-billing
      this caused live).

    Failure to acquire within STUDENT_LOCK_BLOCKING_TIMEOUT_SECONDS (another
    worker genuinely holds it) still raises redis.exceptions.LockError —
    that's a real "try again later", not an outage.
    """
    lock = student_lock(phone)
    try:
        acquired = await lock.acquire()
    except _REDIS_UNAVAILABLE_ERRORS as exc:
        _note_redis_unavailable(exc, "student_turn_lock")
        lock = _fallback_locks[phone]
        acquired = await lock.acquire()
    if not acquired:
        # asyncio.Lock.acquire always returns True; redis' Lock.acquire
        # returns False only when blocking=False, which isn't used here —
        # kept as a guard so a silent non-acquisition can never fall
        # through into the critical section.
        raise LockError(f"Unable to acquire student lock for {phone}")
    try:
        yield
    finally:
        try:
            release = lock.release()
            if asyncio.iscoroutine(release):
                await release
        except LockNotOwnedError:
            logger.warning(
                "student lock for ****%s expired before the turn finished (TTL %ss) — the turn completed; "
                "treating as released", phone[-4:], STUDENT_LOCK_TIMEOUT_SECONDS,
            )
        except _REDIS_UNAVAILABLE_ERRORS as exc:
            _note_redis_unavailable(exc, "student_turn_lock.release")


# Daily platform-wide raw-spend ceiling, shared by every channel that
# spends AI credit (WhatsApp, the student web/app endpoints, the voice
# call) — lived in app.routers.whatsapp until the voice-call WebSocket was
# found to apply none of the gates the other channels do (audit, Sept
# 2026). Alerts (logger.error) once per IST day.
_fallback_alert_keys: set[str] = set()


async def platform_spend_cap_exceeded(db) -> bool:
    # Lazy import: cost_tracker is a heavier module (models, business
    # rules) that nothing else in this limiter module needs.
    from app.services import cost_tracker

    spend = cost_tracker.platform_spend_today(db)
    if spend < settings.daily_platform_spend_cap_inr:
        return False
    alert_key = f"platform_spend_alerted:{datetime.now(cost_tracker.IST).date().isoformat()}"
    already_alerted = True
    client = _client()
    try:
        if client is not None:
            already_alerted = not await client.set(alert_key, "1", ex=24 * 3600, nx=True)
        else:
            already_alerted = alert_key in _fallback_alert_keys
            _fallback_alert_keys.add(alert_key)
    except Exception:
        pass
    if not already_alerted:
        logger.error(
            "DAILY PLATFORM SPEND CAP HIT: raw spend ₹%.2f >= cap ₹%.2f — AI tutoring paused until IST midnight",
            spend, settings.daily_platform_spend_cap_inr,
        )
    return True
