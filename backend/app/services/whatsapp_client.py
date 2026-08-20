import hmac
import uuid
from urllib.parse import urlparse, parse_qs

import httpx
from app.config import settings


def verify_webhook_auth(auth_header: str | None, query_secret: str | None = None) -> bool:
    """
    Wati doesn't sign webhook bodies with HMAC like Meta does. Two ways to
    authenticate a call are accepted, either is enough:
      1. A custom "Authorization" header value set under Wati's Webhook
         settings, sent back on every call.
      2. A `?secret=...` query parameter embedded directly in the webhook
         URL registered with Wati — added as an alternative for a webhook
         dashboard (or a person configuring it) that doesn't expose a way
         to set a custom header, only a plain URL to paste in.
    Skips verification entirely (returns True) if WATI_WEBHOOK_SECRET isn't
    configured — app.config logs a loud startup warning when that's true
    outside development, since an unset secret means this webhook accepts
    ANY POST as if it were a real inbound WhatsApp message (see the
    SECURITY WARNING in that log line for what to do about it).

    hmac.compare_digest (not ==) so a byte-by-byte timing side-channel can't
    help an attacker recover the secret across many requests.
    """
    if not settings.wati_webhook_secret:
        return True
    if query_secret and hmac.compare_digest(query_secret, settings.wati_webhook_secret):
        return True
    if not auth_header:
        return False
    return hmac.compare_digest(auth_header.removeprefix("Bearer "), settings.wati_webhook_secret)


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


def _is_wati_media_host(media_url: str) -> bool:
    """
    media_url comes straight from an inbound webhook payload — untrusted
    input. Without this check, download_media would fetch ANY attacker-
    supplied URL while attaching the real WhatsApp API bearer token in the
    request headers (an SSRF that also exfiltrates a live credential to
    whatever host the attacker names, e.g. an internal service or a
    server they control). Only ever fetch from Wati's own domain, derived
    from the already-trusted wati_api_endpoint config rather than a
    hardcoded string, so a sandbox/different-tenant Wati host still works.
    """
    if not settings.wati_api_endpoint:
        return False
    expected_host = urlparse(settings.wati_api_endpoint).hostname
    actual_host = urlparse(media_url).hostname
    return bool(expected_host) and actual_host == expected_host


async def download_media(media_url: str) -> bytes | None:
    """Fetch the raw bytes of an incoming media message (e.g. a voice note)."""
    if not settings.whatsapp_token:
        return None
    if not _is_wati_media_host(media_url):
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


async def send_whatsapp_file(to_phone: str, file_bytes: bytes, content_type: str, filename: str, caption: str | None = None) -> dict:
    """
    Generic version of send_whatsapp_audio/send_whatsapp_image above —
    Wati's sendSessionFile endpoint isn't actually media-type-specific
    (both of those just hardcode their own content_type), it renders as an
    image/video/document bubble based on the content_type given here. Used
    by app.routers.leads to let an external portal send a photo, video, or
    PDF to a lead, not just plain text/template messages.
    """
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v1/sendSessionFile/{to_phone}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    files = {"file": (filename, file_bytes, content_type)}
    params = {"caption": caption} if caption else {}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, params=params, files=files)
            resp.raise_for_status()
            return {"sent": True, "response": resp.json()}
    except httpx.HTTPStatusError as exc:
        return {"sent": False, "reason": f"Wati API error {exc.response.status_code}: {exc.response.text}"}
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"Wati request failed: {exc}"}


# Generous but bounded — WhatsApp itself caps media size well below this
# (100MB for video/documents, 5MB for a plain image), so this exists to
# stop an obviously-wrong or malicious URL (e.g. a multi-GB file) from
# tying up a request indefinitely, not to match WhatsApp's own limits
# exactly; Wati's own upload call will reject anything it doesn't accept.
MAX_EXTERNAL_MEDIA_BYTES = 64 * 1024 * 1024


async def fetch_external_media(url: str) -> tuple[bytes, str] | None:
    """
    Fetches a file from an arbitrary URL a caller supplied (see
    app.routers.leads' media_url) — deliberately a SEPARATE function from
    download_media above, which is hard-restricted to Wati's own media
    host specifically because it attaches our Wati bearer token; that
    token must never be sent to a portal-supplied URL. Only http/https,
    streamed with a byte cap rather than loaded via a Content-Length
    header alone (a malicious/misconfigured server could lie about it).
    Returns (bytes, content_type) or None if the fetch failed, wasn't
    http(s), or exceeded the size cap.
    """
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                chunks = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_EXTERNAL_MEDIA_BYTES:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks), content_type
    except httpx.HTTPError:
        return None


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
    OR a tap on a Quick Reply button attached to an approved template (see
    send_template_message/app.services.nudges — a template button is a
    different message component from our own interactive send, so Wati
    reports it with a different `type`), or None otherwise.

    Confirmed against a real live button tap (2026-07-29): Wati sends
    {"type": "interactive", "text": "<button title>", "waId": "...",
    "interactiveButtonReply": {"id": "1", "title": "<button title>"},
    "listReply": None, ...} — "text" at the top level already carries the
    tapped button's title too, but interactiveButtonReply/listReply are
    checked first since they're the more explicit/structured source.

    A template Quick Reply tap instead arrives as {"type": "button",
    "text": "<button title>", "waId": "...", "button": {"text": "...",
    "payload": "..."}} per Wati's webhook docs — same "text" carries the
    title" pattern, just a different top-level `type`.
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

    if payload.get("type") in ("interactive", "button") and payload.get("text"):
        return from_phone, payload["text"]

    return None


async def send_template_message(to_phone: str, template_name: str, params: list[dict]) -> dict:
    """
    Sends an approved WhatsApp template to a single contact — how you
    *initiate* contact with someone who hasn't messaged recently (unlike
    send_whatsapp_message, which only works within an existing 24h session).

    Uses Wati's singular /api/v1/sendTemplateMessage endpoint, NOT the bulk
    /api/v2/sendTemplateMessages endpoint used by send_broadcast_template
    below — the bulk endpoint silently degrades for this account: it
    returns {"result": true, "error": null} but flags every receiver
    isValidWhatsAppNumber: false and never actually delivers, for numbers
    that demonstrably do have WhatsApp. Confirmed live: this singular
    endpoint returns validWhatsAppNumber: true and genuinely delivers for
    the exact same template/number/account. Prefer this for any single-
    recipient send (OTPs, registration welcome); send_broadcast_template is
    only for genuine many-recipient broadcasts, where this singular
    endpoint doesn't apply — but note it inherits the same unverified/
    possibly-degraded status this function was written to work around.

    `params` — [{"name": "1", "value": "123456"}, ...] for each {{n}}
    placeholder in the template body, in order.
    """
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}
    if not settings.wati_channel_number:
        return {"sent": False, "reason": "WATI_CHANNEL_NUMBER not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v1/sendTemplateMessage"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}", "Content-Type": "application/json"}
    payload = {
        "template_name": template_name,
        "broadcast_name": f"{template_name}-{uuid.uuid4()}",
        "channel_number": settings.wati_channel_number,
        "parameters": params,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, params={"whatsappNumber": to_phone}, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("validWhatsAppNumber", True):
                # invalid_number distinguishes this from every other failure
                # reason below — a caller that has a non-WhatsApp fallback
                # (e.g. app.routers.admin.register_school, which lets a
                # school admin register with password alone when their
                # number genuinely isn't on WhatsApp) checks this flag
                # rather than string-matching `reason`, which is free to
                # change wording without silently breaking that check.
                return {
                    "sent": False, "invalid_number": True,
                    "reason": f"Wati reports this isn't a valid WhatsApp number: {to_phone}",
                }
            # Confirmed live: Wati can return HTTP 200 with "result": false in
            # the body (e.g. a blank/invalid template parameter) rather than
            # an HTTP error status — raise_for_status() above only catches
            # the latter. Checked here too so a genuine soft-failure isn't
            # silently reported as sent (see this function's own callers,
            # which log an error whenever sent is False).
            if data.get("result") is False:
                return {"sent": False, "reason": data.get("info") or "Wati reported result: false", "response": data}
            return {"sent": True, "response": data}
    except httpx.HTTPStatusError as exc:
        return {"sent": False, "reason": f"Wati API error {exc.response.status_code}: {exc.response.text}"}
    except httpx.HTTPError as exc:
        return {"sent": False, "reason": f"Wati request failed: {exc}"}


async def send_broadcast_template(
    template_name: str, broadcast_name: str, receivers: list[dict]
) -> dict:
    """
    Bulk-send an approved WhatsApp template to many contacts at once — the
    only reason to prefer this over send_template_message is a genuine
    many-recipient broadcast (see app.routers.broadcast), where sending one
    HTTP request per recipient isn't practical.

    `receivers` — list of {"whatsappNumber": "91...", "customParams": [{"name": "...", "value": "..."}]}
    ("localMessageId" is filled in automatically below if omitted.)

    STATUS: unverified for real delivery on this account. A single-
    recipient test against this endpoint (with channel_number correctly
    set) returned isValidWhatsAppNumber: false and did not deliver, for a
    number confirmed to have WhatsApp — see send_template_message's
    docstring, which is why every single-recipient OTP/registration send
    was switched to that function instead. Whether this bulk endpoint
    behaves differently at real scale (many receivers in one call, as
    opposed to the single-receiver shape tested) is untested — treat any
    broadcast sent through this function as unconfirmed until checked
    against a real campaign's actual delivery.
    """
    if not settings.whatsapp_token or not settings.wati_api_endpoint:
        return {"sent": False, "reason": "Wati credentials not configured in .env"}
    if not settings.wati_channel_number:
        return {"sent": False, "reason": "WATI_CHANNEL_NUMBER not configured in .env"}

    url = f"{settings.wati_api_endpoint.rstrip('/')}/api/v2/sendTemplateMessages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    receivers = [
        {"localMessageId": str(uuid.uuid4()), **r} if "localMessageId" not in r else r
        for r in receivers
    ]
    payload = {
        "template_name": template_name,
        "broadcast_name": broadcast_name,
        "channel_number": settings.wati_channel_number,
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
