import httpx
from app.config import settings

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
EDUCATION_CATEGORY_ID = "27"  # YouTube's "Education" category — filters out loosely-matching non-educational content


def _grade_qualified_query(query: str, student_class: str | None) -> str:
    """
    Ensure the search is grade-appropriate even if the tutor's generated
    query text forgot to mention the class — the model is instructed to
    include it (e.g. "...class 12 physics"), but relying on the model to
    remember every time is fragile; this is a server-side safety net so
    results are never accidentally pitched at the wrong grade level.
    """
    if not student_class or student_class.lower() in query.lower():
        return query
    return f"{query} class {student_class}"


async def find_best_video(
    query: str, student_language_code: str | None = None, student_class: str | None = None
) -> dict | None:
    """
    Find the best-matching educational video for a topic via YouTube Data
    API v3. Returns {"title": ..., "url": ...} or None if not configured,
    nothing found, or the call fails.

    Search is restricted to embeddable, safe-search-strict, medium-length
    (4-20 min), Education-category videos ordered by relevance — medium
    length skews toward actual explainer/lecture content rather than shorts
    or multi-hour streams, and the Education category filters out videos
    that loosely match the search keywords but aren't actually educational
    content (e.g. a vlog or song that happens to mention the topic word).

    `student_language_code` (e.g. "hi-IN") biases results toward that
    language — otherwise every search query (always written in English,
    since the tutor only ever writes in English) would default to
    English-language videos even for a Hindi-medium student who might get
    more out of a Hindi-language explainer. `student_class` (e.g. "8")
    ensures the search is pitched at the right grade level even if the
    query text itself didn't mention it.
    """
    if not settings.youtube_api_key:
        return None

    relevance_language = "hi" if (student_language_code or "").startswith("hi") else "en"
    params = {
        "key": settings.youtube_api_key,
        "q": _grade_qualified_query(query, student_class),
        "part": "snippet",
        "type": "video",
        "maxResults": 1,
        "order": "relevance",
        "safeSearch": "strict",
        "videoEmbeddable": "true",
        "videoDuration": "medium",
        "videoCategoryId": EDUCATION_CATEGORY_ID,
        "relevanceLanguage": relevance_language,
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
