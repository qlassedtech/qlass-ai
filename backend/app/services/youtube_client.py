import httpx
from app.config import settings

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
EDUCATION_CATEGORY_ID = "27"  # YouTube's "Education" category — filters out loosely-matching non-educational content
CANDIDATE_COUNT = 5  # how many search results to check for a language match before giving up and using the top one


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


async def _pick_language_matched_id(client: httpx.AsyncClient, candidate_ids: list[str], target_language: str) -> str:
    """
    search.list's `relevanceLanguage` param is only a ranking hint, not a
    filter — YouTube's own docs say a highly-relevant result in a different
    language can still rank first, and confirmed live: relevanceLanguage=en
    still returned a Telugu video as the top (and only-requested) result.
    videos.list's snippet DOES carry the uploader-declared audio/caption
    language, so this fetches that for the top few candidates and picks the
    first one that actually matches — real metadata, not a guess from the
    title text. Falls back to the first candidate if none declare a
    matching language (many videos don't set this field at all), so a
    missing tag never means "no video" when a decent one was found.
    """
    resp = await client.get(
        YOUTUBE_VIDEOS_URL,
        params={"key": settings.youtube_api_key, "id": ",".join(candidate_ids), "part": "snippet"},
    )
    resp.raise_for_status()
    items = resp.json().get("items") or []
    by_id = {item["id"]: item["snippet"] for item in items}
    for video_id in candidate_ids:
        snippet = by_id.get(video_id)
        if not snippet:
            continue
        video_language = snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or ""
        if video_language.lower().startswith(target_language):
            return video_id
    return candidate_ids[0]


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

    target_language = "hi" if (student_language_code or "").startswith("hi") else "en"
    params = {
        "key": settings.youtube_api_key,
        "q": _grade_qualified_query(query, student_class),
        "part": "snippet",
        "type": "video",
        "maxResults": CANDIDATE_COUNT,
        "order": "relevance",
        "safeSearch": "strict",
        "videoEmbeddable": "true",
        "videoDuration": "medium",
        "videoCategoryId": EDUCATION_CATEGORY_ID,
        "relevanceLanguage": target_language,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(YOUTUBE_SEARCH_URL, params=params)
            resp.raise_for_status()
            items = resp.json().get("items") or []
            if not items:
                return None
            candidates = {item["id"]["videoId"]: item["snippet"]["title"] for item in items}
            candidate_ids = list(candidates.keys())
            chosen_id = (
                await _pick_language_matched_id(client, candidate_ids, target_language)
                if len(candidate_ids) > 1
                else candidate_ids[0]
            )
            return {"title": candidates[chosen_id], "url": f"https://www.youtube.com/watch?v={chosen_id}"}
    except httpx.HTTPError:
        return None
