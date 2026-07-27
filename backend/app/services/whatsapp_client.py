import httpx
from app.config import settings

GRAPH_URL = "https://graph.facebook.com/v19.0"


def parse_incoming_message(payload: dict) -> tuple[str, str] | None:
    """
    Returns (from_phone, message_text) or None if this payload isn't a
    plain text user message (e.g. it's a status/delivery callback).
    """
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]["value"]
        messages = change.get("messages")
        if not messages:
            return None
        msg = messages[0]
        from_phone = msg["from"]
        text = msg.get("text", {}).get("body", "")
        return from_phone, text
    except (KeyError, IndexError):
        return None


async def send_whatsapp_message(to_phone: str, body: str) -> dict:
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id:
        return {"sent": False, "reason": "WhatsApp credentials not configured in .env"}

    url = f"{GRAPH_URL}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": body},
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return {"sent": True, "response": resp.json()}
