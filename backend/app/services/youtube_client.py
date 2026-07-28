import httpx
from app.config import settings

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


async def find_best_video(query: str) -> dict | None:
    """
    Find the best-matching educational video for a topic via YouTube Data
    API v3. Returns {"title": ..., "url": ...} or None if not configured,
    nothing found, or the call fails.

    Search is restricted to embeddable, safe-search-strict, medium-length
    (4-20 min) videos ordered by relevance — medium length skews toward
    actual explainer/lecture content rather than shorts or multi-hour
    streams, which tend to be a poor fit for "explain this topic" requests.
    """
    if not settings.youtube_api_key:
        return None

    params = {
        "key": settings.youtube_api_key,
        "q": query,
        "part": "snippet",
        "type": "video",
        "maxResults": 1,
        "order": "relevance",
        "safeSearch": "strict",
        "videoEmbeddable": "true",
        "videoDuration": "medium",
        "relevanceLanguage": "en",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(YOUTUBE_SEARCH_URL, params=params)
            resp.raise_for_status()
            items = resp.json().get("items") or []
            if not items:
                return None
            video_id = items[0]["id"]["videoId"]
            title = items[0]["snippet"]["title"]
            return {"title": title, "url": f"https://www.youtube.com/watch?v={video_id}"}
    except httpx.HTTPError:
        return None
