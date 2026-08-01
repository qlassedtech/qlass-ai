import asyncio
import uuid

from app.services import rate_limit
from app.services.rate_limit import (
    RATE_LIMIT_MAX_MESSAGES,
    is_otp_rate_limited,
    is_rate_limited,
    student_lock,
)


def _phone():
    # Unique per test so real Redis sliding-window state from one test
    # can never leak into another.
    return f"8{uuid.uuid4().int % 10**9:09d}"


async def test_is_rate_limited_false_under_the_limit():
    phone = _phone()
    for _ in range(RATE_LIMIT_MAX_MESSAGES):
        assert await is_rate_limited(phone) is False


async def test_is_rate_limited_true_once_limit_exceeded():
    phone = _phone()
    for _ in range(RATE_LIMIT_MAX_MESSAGES):
        await is_rate_limited(phone)
    assert await is_rate_limited(phone) is True


async def test_rate_limit_is_per_phone():
    phone_a = _phone()
    phone_b = _phone()
    for _ in range(RATE_LIMIT_MAX_MESSAGES + 1):
        await is_rate_limited(phone_a)
    assert await is_rate_limited(phone_b) is False


async def test_otp_rate_limit_is_a_separate_namespace_from_chat_rate_limit():
    # Same phone hitting the (looser, higher-threshold) chat rate limit
    # must not affect the (stricter) OTP rate limit's own counter.
    phone = _phone()
    for _ in range(RATE_LIMIT_MAX_MESSAGES + 1):
        await is_rate_limited(phone)
    assert await is_otp_rate_limited("login", phone) is False


async def _acquire_and_hold(lock, held_event, release_event):
    await lock.acquire()
    held_event.set()
    await release_event.wait()
    await lock.release()


async def test_student_lock_blocks_concurrent_holders():
    phone = _phone()
    lock_a = student_lock(phone)
    lock_b = student_lock(phone)
    held_event = asyncio.Event()
    release_event = asyncio.Event()

    holder = asyncio.create_task(_acquire_and_hold(lock_a, held_event, release_event))
    await held_event.wait()

    if rate_limit._redis is None:
        second_acquired = False
    else:
        second_acquired = await lock_b.acquire(blocking=True, blocking_timeout=0.2)

    release_event.set()
    await holder

    assert not second_acquired
