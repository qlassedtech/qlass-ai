import asyncio

import httpx
from app.config import settings

READ_API_VERSION = "v3.2"
POLL_ATTEMPTS = 10
POLL_DELAY_SECONDS = 1


async def extract_text_from_image(image_bytes: bytes) -> str | None:
    """
    OCR an image via Azure AI Vision's Read API. Returns the extracted text
    (lines joined with newlines), or None if not configured or the call fails.

    The Read API is asynchronous: submit the image, then poll the returned
    operation URL until Azure finishes processing it.
    """
    if not settings.azure_vision_endpoint or not settings.azure_vision_key:
        return None

    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_vision_key,
        "Content-Type": "application/octet-stream",
    }
    submit_url = f"{settings.azure_vision_endpoint.rstrip('/')}/vision/{READ_API_VERSION}/read/analyze"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            submit_resp = await client.post(submit_url, headers=headers, content=image_bytes)
            submit_resp.raise_for_status()
            operation_url = submit_resp.headers.get("Operation-Location")
            if not operation_url:
                return None

            for _ in range(POLL_ATTEMPTS):
                await asyncio.sleep(POLL_DELAY_SECONDS)
                poll_resp = await client.get(operation_url, headers=headers)
                poll_resp.raise_for_status()
                result = poll_resp.json()
                status = result.get("status")
                if status == "succeeded":
                    lines = [
                        line["text"]
                        for read_result in result.get("analyzeResult", {}).get("readResults", [])
                        for line in read_result.get("lines", [])
                    ]
                    return "\n".join(lines) if lines else None
                if status == "failed":
                    return None
            return None  # timed out waiting for Azure to finish
    except httpx.HTTPError:
        return None
