import httpx
from app.config import settings


def verify_webhook_auth(auth_header: str | None) -> bool:
    """
    Wati doesn't sign webhook bodies with HMAC like Meta does. Instead, if you
    set a custom "Authorization" value under Wati's Webhook settings, Wati
    sends that same value back on every webhook call for you to check.
    Skips verification (returns True) if WATI_WEBHOOK_SECRET isn't configured.
    """
    if not settings.wati_webhook_secret:
        return True
    if not auth_header:
        return False
    return auth_header.removeprefix("Bearer ") == settings.wati_webhook_secret


AUDIO_TYPES = {"audio", "voice", "ptt"}


def parse_incoming_message(payload: dict) -> tuple[str, str] | None:
    """
    Returns (from_phone, message_text) or None if this payload isn't an
    inbound text message from a customer (e.g. it's our own outgoing message
    being echoed back, a status update, or a voice/image message — see
    parse_incoming_audio for voice notes).

    Based on Wati's documented webhook shape for the "message received" event:
    {"eventType": "message", "owner": false, "type": "text", "waId": "...", "text": "..."}
    """
    if payload.get("owner") is True:
        return None  # our own outgoing message, not an inbound one
    if payload.get("type") not in (None, "text"):
        return None

    from_phone = payload.get("waId")
    text = payload.get("text")
    if not from_phone or not text:
        return None
    return from_phone, text


def parse_incoming_audio(payload: dict) -> tuple[str, str] | None:
    """
    Returns (from_phone, whatsapp_message_id) for an inbound voice note, or
    None otherwise. The message id is used to fetch the audio bytes via
    download_media().

    NOTE: assumes Wati uses "audio" as the type for voice notes and still
    includes "whatsappMessageId" — unverified against a real voice-note
    payload from your Wati account; adjust AUDIO_TYPES/field names if the
    real payload differs once you send a real voice note through.
    """
    if payload.get("owner") is True:
        return None
    if payload.get("type") not in AUDIO_TYPES:
        return None

    from_phone = payload.get("waId")
    message_id = payload.get("whatsappMessageId") or payload.get("id")
    if not from_phone or not message_id:
        return None
    return from_phone, message_id


async def download_media(message_id: str) -> bytes | None:
    """Fetch the raw bytes of an incoming media message (e.g. a voice note)."""
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return None

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/ext/v3/conversations/messages/file/{message_id}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}", "Accept": "application/octet-stream"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError:
        return None


async def send_whatsapp_audio(to_phone: str, audio_bytes: bytes, filename: str = "reply.wav") -> dict:
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v1/sendSessionFile/{to_phone}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    files = {"file": (filename, audio_bytes, "audio/wav")}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, files=files)
            resp.raise_for_status()
            return {"sent": True, "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        return {"sent": False, "reason": f"Wati API error {exc.response.status_code}: {exc.response.text}"}
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"Wati request failed: {exc}"}


async def send_whatsapp_message(to_phone: str, body: str) -> dict:
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v1/sendSessionMessage/{to_phone}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, params={"messageText": body})
            resp.raise_for_status()
            return {"sent": True, "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        return {"sent": False, "reason": f"Wati API error {exc.response.status_code}: {exc.response.text}"}
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"Wati request failed: {exc}"}


async def send_broadcast_template(
    template_name: str, broadcast_name: str, receivers: list[dict]
) -> dict:
    """
    Bulk-send an approved WhatsApp template to many contacts at once.

    Unlike send_whatsapp_message (which replies within an existing 24h session),
    this is how you *initiate* contact with people who haven't messaged you
    recently — WhatsApp requires a template approved in your Wati account for
    this; free-form text is not allowed for business-initiated messages.

    `receivers` — list of {"whatsappNumber": "91...", "customParams": [{"name": "...", "value": "..."}]}
    NOTE: verify field names against your Wati account's real bulk-send response
    once you have an approved template to test with — untested against live Wati.
    """
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v2/sendTemplateMessages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    payload = {
        "template_name": template_name,
        "broadcast_name": broadcast_name,
        "receivers": receivers,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return {"sent": True, "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        return {"sent": False, "reason": f"Wati API error {exc.response.status_code}: {exc.response.text}"}
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"Wati request failed: {exc}"}
