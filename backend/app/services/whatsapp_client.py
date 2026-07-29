from urllib.parse import urlparse, parse_qs

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
IMAGE_TYPES = {"image"}
DOCUMENT_TYPES = {"document"}


def guess_filename_from_media_url(media_url: str) -> str:
    """
    Wati's media URLs put the real filename in a "fileName" query param
    (e.g. ".../showFile?fileName=data/documents/<id>.pdf"), not the URL path
    itself — confirmed against real audio payloads from Wati.
    """
    parsed = urlparse(media_url)
    query_filename = parse_qs(parsed.query).get("fileName", [None])[0]
    return query_filename or parsed.path


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
    Returns (from_phone, media_url) for an inbound voice note, or None
    otherwise. media_url is used to fetch the audio bytes via download_media().

    Confirmed against a real voice-note payload from Wati: the "data" field
    holds a direct, ready-to-fetch URL
    (https://.../api/file/showFile?fileName=data/audios/<id>.opus) —
    it's not looked up by message id via a separate endpoint.
    """
    if payload.get("owner") is True:
        return None
    if payload.get("type") not in AUDIO_TYPES:
        return None

    from_phone = payload.get("waId")
    media_url = payload.get("data")
    if not from_phone or not media_url:
        return None
    return from_phone, media_url


def parse_incoming_image(payload: dict) -> tuple[str, str] | None:
    """
    Returns (from_phone, media_url) for an inbound image (e.g. a photo of a
    textbook question or homework), or None otherwise. Mirrors
    parse_incoming_audio — same "data" field convention for media messages.
    """
    if payload.get("owner") is True:
        return None
    if payload.get("type") not in IMAGE_TYPES:
        return None

    from_phone = payload.get("waId")
    media_url = payload.get("data")
    if not from_phone or not media_url:
        return None
    return from_phone, media_url


def parse_incoming_document(payload: dict) -> tuple[str, str] | None:
    """
    Returns (from_phone, media_url) for an inbound document (PDF or Word
    file), or None otherwise. Mirrors parse_incoming_audio/image.
    """
    if payload.get("owner") is True:
        return None
    if payload.get("type") not in DOCUMENT_TYPES:
        return None

    from_phone = payload.get("waId")
    media_url = payload.get("data")
    if not from_phone or not media_url:
        return None
    return from_phone, media_url


async def download_media(media_url: str) -> bytes | None:
    """Fetch the raw bytes of an incoming media message (e.g. a voice note)."""
    if not settings.whatsapp_token:
        return None

    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(media_url, headers=headers)
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError:
        return None


async def send_whatsapp_audio(to_phone: str, audio_bytes: bytes, filename: str = "reply.ogg") -> dict:
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v1/sendSessionFile/{to_phone}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    # Opus in an OGG container — WhatsApp's own voice notes use this; WAV
    # gets rejected outright ("File is not allowed by WhatsApp"), confirmed
    # against a real send attempt.
    files = {"file": (filename, audio_bytes, "audio/ogg")}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, files=files)
            resp.raise_for_status()
            return {"sent": True, "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        return {"sent": False, "reason": f"Wati API error {exc.response.status_code}: {exc.response.text}"}
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"Wati request failed: {exc}"}


async def send_whatsapp_image(to_phone: str, image_bytes: bytes, caption: str, filename: str = "diagram.png") -> dict:
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v1/sendSessionFile/{to_phone}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    files = {"file": (filename, image_bytes, "image/png")}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, params={"caption": caption}, files=files)
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


async def send_whatsapp_buttons(to_phone: str, body: str, buttons: list[str], footer: str | None = None) -> dict:
    """
    Sends an interactive message with 1-3 tappable reply buttons, via Wati's
    documented sendInteractiveButtonsMessage endpoint (docs.wati.io/reference/
    post_api-v1-sendinteractivebuttonsmessage). Each button's text is capped
    at 20 chars per Wati's own limit — truncated rather than rejected, since
    a slightly-cut label beats a hard failure on a menu send.
    """
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}
    if not 1 <= len(buttons) <= 3:
        return {"sent": False, "reason": "WhatsApp interactive buttons must be 1-3 options"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v1/sendInteractiveButtonsMessage"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}", "Content-Type": "application/json"}
    payload: dict = {"body": body, "buttons": [{"text": b[:20]} for b in buttons]}
    if footer:
        payload["footer"] = footer[:60]

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=headers, params={"whatsappNumber": to_phone}, json=payload)
            resp.raise_for_status()
            return {"sent": True, "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        return {"sent": False, "reason": f"Wati API error {exc.response.status_code}: {exc.response.text}"}
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"Wati request failed: {exc}"}


def parse_incoming_button_reply(payload: dict) -> tuple[str, str] | None:
    """
    Returns (from_phone, button_text) when an inbound webhook payload is a
    tap on an interactive button/list reply sent by send_whatsapp_buttons,
    or None otherwise.

    Confirmed against a real live button tap (2026-07-29): Wati sends
    {"type": "interactive", "text": "<button title>", "waId": "...",
    "interactiveButtonReply": {"id": "1", "title": "<button title>"},
    "listReply": None, ...} — "text" at the top level already carries the
    tapped button's title too, but interactiveButtonReply/listReply are
    checked first since they're the more explicit/structured source.
    """
    if payload.get("owner") is True:
        return None

    from_phone = payload.get("waId")
    if not from_phone:
        return None

    button_reply = payload.get("interactiveButtonReply") or payload.get("listReply")
    if isinstance(button_reply, dict):
        text = button_reply.get("title") or button_reply.get("text") or button_reply.get("id")
        if text:
            return from_phone, text

    if payload.get("type") == "interactive" and payload.get("text"):
        return from_phone, payload["text"]

    return None


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
