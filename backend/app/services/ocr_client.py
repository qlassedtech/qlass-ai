import asyncio

import httpx
from app.config import settings

# qlass-dpp-ocr (the Azure resource actually provisioned for this) is an
# Azure AI Document Intelligence resource, not a plain Computer Vision
# resource — confirmed live, the Computer Vision Read API (`/vision/v3.2/
# read/analyze`) this used to call returned 401 "invalid subscription key
# or wrong API endpoint" against it even with a valid key, because that
# API surface doesn't exist on this resource type at all. Document
# Intelligence's prebuilt-read model does the same plain-OCR job (no
# custom model/training needed) via a different API path, confirmed
# working end-to-end against this exact resource.
DOC_INTELLIGENCE_API_VERSION = "2023-07-31"
POLL_ATTEMPTS = 15
POLL_DELAY_SECONDS = 1


async def extract_text_from_image(image_bytes: bytes) -> str | None:
    """
    OCR an image via Azure AI Document Intelligence's prebuilt-read model.
    Returns the extracted text, or None if not configured or the call fails.

    Analysis is asynchronous: submit the image, then poll the returned
    operation URL until Azure finishes processing it.
    """
    if not settings.azure_vision_endpoint or not settings.azure_vision_key:
        return None

    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_vision_key,
        "Content-Type": "application/octet-stream",
    }
    submit_url = (
        f"{settings.azure_vision_endpoint.rstrip('/')}/formrecognizer/documentModels/"
        f"prebuilt-read:analyze?api-version={DOC_INTELLIGENCE_API_VERSION}"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            submit_resp = await client.post(submit_url, headers=headers, content=image_bytes)
            submit_resp.raise_for_status()
            operation_url = submit_resp.headers.get("Operation-Location")
            if not operation_url:
                return None

            poll_headers = {"Ocp-Apim-Subscription-Key": settings.azure_vision_key}
            for _ in range(POLL_ATTEMPTS):
                await asyncio.sleep(POLL_DELAY_SECONDS)
                poll_resp = await client.get(operation_url, headers=poll_headers)
                poll_resp.raise_for_status()
                result = poll_resp.json()
                status = result.get("status")
                if status == "succeeded":
                    content = result.get("analyzeResult", {}).get("content")
                    return content or None
                if status == "failed":
                    return None
            return None  # timed out waiting for Azure to finish
    except httpx.HTTPError:
        return None
