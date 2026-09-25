"""
Ops alerts to a human, over WhatsApp — the one channel the person running
Skoolgpt is guaranteed to actually look at.

Before this, a provider outage was invisible until a student complained:
Anthropic returning 402 (credit balance exhausted) turned every tutor reply
into a polite "having trouble reaching the AI service" apology, Sarvam
running out of credits silently dropped every voice note, and an expired
Wati token meant nothing was delivered at all — each logged server-side
only, where nobody was watching (audit, Sept 2026).

Two pieces:

- note_provider_error(provider, status): cheap, synchronous. Bumps an
  hourly Redis counter (ops:err:<provider>:<yyyymmdd-hh>, UTC) that
  scripts/ops_heartbeat.py reads to catch a slow burn of errors that never
  individually trips an alert.
- alert_ops(kind, message): async. Sends the message to OPS_ALERT_PHONE
  (or SUPPORT_PHONE), deduped per `kind` with a Redis cooldown so a
  provider that's down for an hour produces ONE message, not one per
  failed student turn. Never raises — an alert path that can itself take
  down a request handler would be worse than no alert.

report_provider_error() combines the two for the HTTP-client call sites
(llm_client, sarvam_client, whatsapp_client): always counts, and schedules
an immediate alert for the statuses that mean "a human has to act now"
(auth/credit/rate-limit problems and provider 5xx).

The async Redis client is app.services.rate_limit._client() (one per event
loop, from the same REDIS_URL); the sync one below is built from that URL
too. When Redis isn't reachable everything degrades to an in-process dict,
same as the rate limiter does.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import redis as redis_sync

from app.config import settings
from app.services import rate_limit
from app.services.phone import normalize_phone

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 3600
PROVIDER_ERROR_KEY_TTL_SECONDS = 3 * 3600  # the heartbeat only ever reads the current + previous hour

# Short timeouts on the sync client: note_provider_error runs inline on the
# request path (inside an async handler, even), so a Redis that's hung must
# cost milliseconds, not the default blocking connect.
_redis_sync = (
    redis_sync.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=0.5, socket_connect_timeout=0.5)
    if settings.redis_url
    else None
)

# Per-process fallbacks when Redis isn't reachable — a cooldown that only
# dedups within one worker still beats no dedup (or a crash) at all.
_fallback_cooldowns: dict[str, float] = {}
_fallback_error_counts: dict[str, int] = {}

# asyncio only keeps a weak reference to a task created with create_task —
# a fire-and-forget alert could be garbage-collected mid-send without this.
_background_tasks: set[asyncio.Task] = set()

# Statuses that mean "a human has to act now" (not just a flaky request):
# auth failure/expired token (401/403), out of credits (402), rate limited
# (429), and any provider-side 5xx.
_IMMEDIATE_ALERT_STATUSES = {401, 402, 403, 429}
_STATUS_REASONS = {
    401: "API key invalid or expired",
    402: "credit balance too low — payment required",
    403: "token expired or lacks permission",
    429: "rate limited",
}
# provider -> (display name, what breaks for students, what to do about it)
_PROVIDER_INFO = {
    "anthropic": ("Anthropic API", "every tutor reply is failing", "Top up / check the key: console.anthropic.com"),
    "sarvam": ("Sarvam API", "voice notes (speech-to-text and text-to-speech) are failing", "Top up / check the key: dashboard.sarvam.ai"),
    "wati": ("Wati (WhatsApp) API", "outbound WhatsApp messages are NOT being delivered", "Check the API token at app.wati.io"),
}


def ops_alert_phone() -> str:
    return normalize_phone(settings.ops_alert_phone or settings.support_phone)


def provider_error_key(provider: str, at: datetime | None = None) -> str:
    """Hourly counter key, UTC — scripts/ops_heartbeat.py builds the same key to read it back."""
    hour = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%d-%H")
    return f"ops:err:{provider}:{hour}"


def note_provider_error(provider: str, status: int | None) -> None:
    """Sync + cheap: bump this hour's error counter for `provider`. Never raises."""
    key = provider_error_key(provider)
    try:
        if _redis_sync is not None:
            pipe = _redis_sync.pipeline()
            pipe.incr(key)
            pipe.expire(key, PROVIDER_ERROR_KEY_TTL_SECONDS)
            pipe.execute()
            return
    except Exception as exc:  # Redis down — fall through to the in-process counter
        logger.warning("alerts: could not record provider error in Redis (%s); using in-process counter", exc)
    _fallback_error_counts[key] = _fallback_error_counts.get(key, 0) + 1


def get_provider_error_count(provider: str, at: datetime | None = None) -> int:
    """How many errors note_provider_error recorded for `provider` in the hour containing `at` (default: now)."""
    key = provider_error_key(provider, at)
    try:
        if _redis_sync is not None:
            return int(_redis_sync.get(key) or 0)
    except Exception as exc:
        logger.warning("alerts: could not read provider error count from Redis (%s)", exc)
    return _fallback_error_counts.get(key, 0)


async def _claim_cooldown(kind: str, cooldown_seconds: int) -> bool:
    """True if this call is the first for `kind` within the cooldown window (and claims it)."""
    key = f"ops:alert:{kind}"
    try:
        client = rate_limit._client()
        if client is not None:
            return bool(await client.set(key, "1", nx=True, ex=cooldown_seconds))
    except Exception as exc:
        logger.warning("alerts: Redis cooldown check failed (%s); using in-process cooldown", exc)
    now = time.monotonic()
    last = _fallback_cooldowns.get(key)
    if last is not None and now - last < cooldown_seconds:
        return False
    _fallback_cooldowns[key] = now
    return True


async def alert_ops(kind: str, message: str, *, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> bool:
    """
    Send `message` to the ops phone unless an alert of this `kind` already
    went out within `cooldown_seconds`. Returns True only when a message
    was actually sent. Never raises.

    The cooldown is claimed BEFORE the send, deliberately: a Wati outage
    alert is itself sent through Wati, whose failure handler calls back
    into this module — claiming first is what stops that from recursing.
    """
    try:
        if not await _claim_cooldown(kind, cooldown_seconds):
            return False
        # Lazy import — whatsapp_client imports this module for its own
        # failure reporting, so a top-level import here would be circular.
        from app.services.whatsapp_client import send_whatsapp_message

        text = f"🚨 Skoolgpt ops alert [{kind}]\n{message}"
        logger.error("OPS ALERT [%s]: %s", kind, message)
        result = await send_whatsapp_message(ops_alert_phone(), text)
        if not result.get("sent"):
            logger.error("alerts: could not deliver ops alert [%s]: %s", kind, result.get("reason"))
            return False
        return True
    except Exception as exc:
        logger.error("alerts: alert_ops(%s) failed: %s", kind, exc)
        return False


def schedule_alert(kind: str, message: str, *, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS) -> bool:
    """
    Fire-and-forget alert_ops from code that can't (or shouldn't) await it.
    Returns False when there's no running event loop — a sync caller just
    gets the counter from note_provider_error, and the heartbeat picks the
    problem up on its next run.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    try:
        task = loop.create_task(alert_ops(kind, message, cooldown_seconds=cooldown_seconds))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return True
    except Exception as exc:
        logger.error("alerts: could not schedule alert [%s]: %s", kind, exc)
        return False


def is_immediate_alert_status(status: int | None) -> bool:
    return status is not None and (status in _IMMEDIATE_ALERT_STATUSES or status >= 500)


def provider_error_message(provider: str, status: int | None) -> str:
    name, impact, action = _PROVIDER_INFO.get(provider, (provider, "requests to it are failing", "Check the provider dashboard"))
    reason = _STATUS_REASONS.get(status) or ("provider-side error" if status and status >= 500 else "request failed")
    status_text = f"returned {status} ({reason})" if status is not None else f"is unreachable ({reason})"
    return f"{name} {status_text} — {impact}. {action}"


def report_provider_error(provider: str, status: int | None) -> None:
    """
    One call for every HTTP-client failure handler: always counts the error
    (for the heartbeat's slow-burn check), and schedules an immediate
    deduped alert when the status is one a human has to act on. Sync and
    never raises, so it's safe inside an `except` block on the request path.
    """
    try:
        note_provider_error(provider, status)
        if is_immediate_alert_status(status):
            schedule_alert(f"{provider}_{status}", provider_error_message(provider, status))
    except Exception as exc:
        logger.error("alerts: report_provider_error(%s, %s) failed: %s", provider, status, exc)
