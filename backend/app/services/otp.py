import random

import redis.asyncio as redis

from app.config import settings

OTP_TTL_SECONDS = 10 * 60

_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
# Fallback so a single-process dev setup without Redis running still works —
# same limitation as active_profile.py/rate_limit.py's own fallbacks.
_fallback_store: dict[str, str] = {}


def _key(purpose: str, phone: str) -> str:
    return f"otp:{purpose}:{phone}"


async def generate_and_store_otp(purpose: str, phone: str) -> str:
    otp = f"{random.randint(0, 999999):06d}"
    key = _key(purpose, phone)
    if _redis is None:
        _fallback_store[key] = otp
    else:
        await _redis.set(key, otp, ex=OTP_TTL_SECONDS)
    return otp


async def verify_otp(purpose: str, phone: str, otp: str) -> bool:
    key = _key(purpose, phone)
    stored = _fallback_store.get(key) if _redis is None else await _redis.get(key)
    if not stored or stored != otp:
        return False
    if _redis is None:
        _fallback_store.pop(key, None)
    else:
        await _redis.delete(key)
    return True
