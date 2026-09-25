"""
Every app.services.rate_limit helper must FAIL OPEN when Redis is
unreachable — before this (audit, Sept 2026) a Redis outage made
is_rate_limited/student_lock raise on every call, so every WhatsApp message
failed all three attempts with nothing sent.
"""
import logging

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.services import rate_limit


class _DeadPipeline:
    def __getattr__(self, name):
        # zremrangebyscore / zadd / zcard / expire — queued locally, fine.
        return lambda *a, **kw: self

    async def execute(self):
        raise RedisConnectionError("Error 61 connecting to localhost:6379. Connection refused.")


class _DeadLock:
    async def acquire(self, *a, **kw):
        raise RedisConnectionError("connection refused")

    async def release(self):
        raise RedisConnectionError("connection refused")


class _DeadRedis:
    """Every operation raises the same ConnectionError a down Redis produces."""

    def pipeline(self):
        return _DeadPipeline()

    def lock(self, *a, **kw):
        return _DeadLock()

    async def set(self, *a, **kw):
        raise RedisConnectionError("connection refused")

    async def get(self, *a, **kw):
        raise RedisConnectionError("connection refused")

    async def delete(self, *a, **kw):
        raise RedisConnectionError("connection refused")


@pytest.fixture()
def dead_redis(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", _DeadRedis())
    monkeypatch.setattr(rate_limit, "_last_outage_log_at", 0.0)
    rate_limit._fallback_timestamps.clear()
    rate_limit._fallback_used_tokens.clear()
    yield
    rate_limit._fallback_timestamps.clear()


async def test_is_rate_limited_fails_open(dead_redis):
    for _ in range(rate_limit.RATE_LIMIT_MAX_MESSAGES):
        assert await rate_limit.is_rate_limited("919000000201") is False


async def test_student_turn_lock_yields_instead_of_raising(dead_redis):
    entered = False
    async with rate_limit.student_turn_lock("919000000202"):
        entered = True
    assert entered


async def test_signup_and_otp_buckets_fall_back_to_the_in_process_window(dead_redis):
    for _ in range(rate_limit.SIGNUP_RATE_LIMIT_MAX_ATTEMPTS):
        assert await rate_limit.is_signup_rate_limited("outage-signup") is False
    assert await rate_limit.is_signup_rate_limited("outage-signup") is True

    for _ in range(rate_limit.OTP_RATE_LIMIT_MAX_ATTEMPTS):
        assert await rate_limit.is_otp_rate_limited("login", "919000000203") is False
    assert await rate_limit.is_otp_rate_limited("login", "919000000203") is True


async def test_login_blocking_and_payment_limits_fail_open_via_fallback(dead_redis):
    assert await rate_limit.is_login_blocked("919000000204", "1.2.3.4") is False
    for _ in range(rate_limit.LOGIN_FAIL_MAX_PER_IP):
        await rate_limit.record_login_failure("919000000204", "1.2.3.4")
    assert await rate_limit.is_login_blocked("919000000204", "1.2.3.4") is True  # in-process bucket still counts
    assert await rate_limit.is_payment_rate_limited("919000000205", "1.2.3.4") is False


async def test_worksheet_state_and_single_use_tokens_fall_back(dead_redis):
    await rate_limit.set_pending_worksheet_answers(4242, [{"question": "q", "answer": "a"}])
    assert await rate_limit.get_pending_worksheet_answers(4242) == [{"question": "q", "answer": "a"}]
    await rate_limit.clear_pending_worksheet_answers(4242)
    assert await rate_limit.get_pending_worksheet_answers(4242) is None

    assert await rate_limit.claim_single_use_token("jti-outage", 120) is True
    assert await rate_limit.claim_single_use_token("jti-outage", 120) is False


async def test_outage_is_logged_once_per_minute_not_per_call(dead_redis, caplog, monkeypatch):
    noted = []
    from app.services import alerts

    monkeypatch.setattr(alerts, "note_provider_error", lambda provider, status: noted.append((provider, status)))
    with caplog.at_level(logging.WARNING, logger="app.services.rate_limit"):
        for _ in range(5):
            await rate_limit.is_rate_limited("919000000206")
        async with rate_limit.student_turn_lock("919000000206"):
            pass
    outage_lines = [r for r in caplog.records if "Redis unavailable" in r.getMessage()]
    assert len(outage_lines) == 1
    assert noted and all(entry == ("redis", None) for entry in noted)
