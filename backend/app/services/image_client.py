import base64

import httpx
from app.config import settings


async def generate_image(prompt: str) -> bytes | None:
    """
    Generate an image via Azure AI Foundry's unified inference endpoint
    (gpt-image series — DALL-E 3 was retired March 2026). Returns PNG bytes,
    or None if not configured or the call fails. gpt-image models always
    return base64 — there's no URL mode.

    Unlike the older "openai/deployments/{name}/images/generations?api-version=..."
    pattern, Foundry's newer unified endpoint (AZURE_IMAGE_ENDPOINT already
    includes the full path, e.g. ".../openai/v1/images/generations") takes
    the deployment/model name in the request body instead — verified
    directly against a real Foundry resource.
    """
    if not (settings.azure_image_endpoint and settings.azure_image_key and settings.azure_image_deployment):
        return None

    headers = {"api-key": settings.azure_image_key, "Content-Type": "application/json"}
    payload = {
        "model": settings.azure_image_deployment,
        "prompt": prompt,
        "size": "1024x1024",
        "n": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(settings.azure_image_endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json().get("data") or []
            if not data or "b64_json" not in data[0]:
                return None
            return base64.b64decode(data[0]["b64_json"])
    except httpx.HTTPError:
        return None
