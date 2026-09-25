import hmac
import secrets

from app.services import rate_limit

OTP_TTL_SECONDS = 10 * 60

# Approved as a WhatsApp Authentication-category template (not Utility —
# Meta rejects OTP-style messages submitted under Utility, since
# verification codes are required to go through Authentication). Body:
# "Your AI Tutor login code is {{1}}. It expires in 10 minutes." Shared by
# all three OTP login flows (student/parent/teacher) — each still messages
# a genuinely cold contact (no guaranteed active 24h session), so a plain
# send_whatsapp_message session message would silently fail to deliver the
# same way it did for the teacher flow; only an approved template can
# reliably initiate contact.
LOGIN_OTP_TEMPLATE_NAME = "ai_tutor_signup_activation"

# Redis comes from rate_limit._client(): one client per event loop, built
# from settings.redis_url — this module used to open its own module-level
# client, which was bound to whichever loop first touched it (see the
# comment on rate_limit._loop_clients) and bypassed the test suite's
# REDIS_URL override. Fallback so a single-process dev setup without Redis
# running still works — same limitation as active_profile.py/rate_limit.py's
# own fallbacks.
_fallback_store: dict[str, str] = {}


def _key(purpose: str, phone: str) -> str:
    return f"otp:{purpose}:{phone}"


async def _store(key: str, value: str) -> None:
    client = rate_limit._client()
    if client is None:
        _fallback_store[key] = value
    else:
        await client.set(key, value, ex=OTP_TTL_SECONDS)


async def _consume(key: str) -> str | None:
    """Read-and-delete: returns the stored value (or None) and clears it so it can't be reused."""
    client = rate_limit._client()
    if client is None:
        return _fallback_store.pop(key, None)
    stored = await client.get(key)
    if stored is not None:
        await client.delete(key)
    return stored


async def generate_and_store_otp(purpose: str, phone: str) -> str:
    otp = f"{secrets.randbelow(1_000_000):06d}"
    await _store(_key(purpose, phone), otp)
    return otp


async def verify_otp(purpose: str, phone: str, otp: str) -> bool:
    key = _key(purpose, phone)
    client = rate_limit._client()
    stored = _fallback_store.get(key) if client is None else await client.get(key)
    if not stored or not hmac.compare_digest(stored, otp):
        return False
    if client is None:
        _fallback_store.pop(key, None)
    else:
        await client.delete(key)
    return True


def _optional_key(purpose: str, phone: str) -> str:
    return f"otp-optional:{purpose}:{phone}"


async def mark_otp_optional(purpose: str, phone: str) -> None:
    """
    Records, server-side, that this phone/purpose combo doesn't need OTP
    verification — used when a registration flow tried to send a WhatsApp
    OTP and Wati reported the number genuinely isn't on WhatsApp (see
    app.services.whatsapp_client.send_template_message's invalid_number
    flag), so a password-only registration should still be allowed to
    proceed. Consumed by consume_otp_optional at the actual verify step,
    rather than trusting the client's own claim that no OTP was sent — an
    attacker submitting a real, WhatsApp-reachable number they don't
    control could otherwise just omit the otp field and claim it wasn't
    needed.
    """
    await _store(_optional_key(purpose, phone), "1")


async def consume_otp_optional(purpose: str, phone: str) -> bool:
    """Returns whether mark_otp_optional was called for this phone/purpose
    (and hasn't expired), clearing it so it can't be reused for a second
    registration attempt."""
    return bool(await _consume(_optional_key(purpose, phone)))
