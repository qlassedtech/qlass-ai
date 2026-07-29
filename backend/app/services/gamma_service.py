import httpx

from app.config import settings

# Gamma's Generate API (https://developers.gamma.app) is beta/paid and
# async: POST creates a generation job, then you poll GET until it's done.
_BASE_URL = "https://public-api.gamma.app/v1.0"


def _headers() -> dict:
    return {"X-API-KEY": settings.gamma_api_key or "", "Content-Type": "application/json"}


async def create_presentation_generation(topic: str, num_cards: int = 8) -> str:
    """Kicks off a presentation generation job, returns Gamma's generation_id."""
    async with httpx.AsyncClient(timeout=30) as http_client:
        response = await http_client.post(
            f"{_BASE_URL}/generations",
            headers=_headers(),
            json={"inputText": topic, "format": "presentation", "numCards": num_cards, "textMode": "generate"},
        )
        response.raise_for_status()
        return response.json()["generationId"]


async def get_generation_status(generation_id: str) -> dict:
    """
    Returns {"status": "pending"|"completed"|"failed", "url": str | None,
    "credits_deducted": int | None}. credits_deducted is Gamma's own
    actual-cost figure (only present once completed) — billed to the
    school ledger at that point rather than a flat per-call estimate,
    since real cost varies a lot by slide count and image options (a
    3-slide test deck cost 13 credits; a 20-slide deck with AI images
    would cost far more).
    """
    async with httpx.AsyncClient(timeout=30) as http_client:
        response = await http_client.get(f"{_BASE_URL}/generations/{generation_id}", headers=_headers())
        response.raise_for_status()
        data = response.json()
        return {
            "status": data.get("status"),
            "url": data.get("gammaUrl"),
            "credits_deducted": (data.get("credits") or {}).get("deducted"),
        }
