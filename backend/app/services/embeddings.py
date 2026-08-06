import httpx

from app.config import settings

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage batches embedding requests — one call for many texts is both
# cheaper and faster than one call per chunk, so ingestion sends chunks in
# batches of this size rather than one at a time.
MAX_BATCH_SIZE = 128


async def embed_texts(texts: list[str], input_type: str) -> list[list[float]] | None:
    """
    Embeds a batch of texts with Voyage. `input_type` must be "document"
    when embedding textbook chunks for storage, or "query" when embedding
    a student's live question — Voyage trains asymmetric embeddings for
    each side of a retrieval pair, so using the wrong one measurably hurts
    match quality even though both return same-shaped vectors.

    Returns None (never raises) if Voyage isn't configured or the call
    fails — every caller treats that as "semantic retrieval unavailable
    right now," not a hard error, so a Voyage outage or a not-yet-set API
    key never breaks the tutor itself (see app.services.retrieval, which
    still has full-text search as an always-available fallback).
    """
    if not settings.voyage_api_key or not texts:
        return None

    headers = {"Authorization": f"Bearer {settings.voyage_api_key}", "Content-Type": "application/json"}
    embeddings: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for i in range(0, len(texts), MAX_BATCH_SIZE):
                batch = texts[i : i + MAX_BATCH_SIZE]
                payload = {
                    "input": batch,
                    "model": settings.voyage_embedding_model,
                    "input_type": input_type,
                    "output_dimension": settings.voyage_embedding_dimensions,
                }
                resp = await client.post(VOYAGE_API_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                # Voyage doesn't guarantee response order matches request
                # order — each item carries its own `index` back.
                by_index = sorted(data["data"], key=lambda item: item["index"])
                embeddings.extend(item["embedding"] for item in by_index)
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    return embeddings


async def embed_text(text: str, input_type: str) -> list[float] | None:
    """Single-text convenience wrapper — a student's live question is
    always embedded one at a time (no batching opportunity at query time)."""
    result = await embed_texts([text], input_type)
    return result[0] if result else None


def format_vector_literal(embedding: list[float]) -> str:
    """
    Postgres/pgvector's text input format for a vector value — used when
    writing an embedding via raw SQL (see scripts/ingest_document.py and
    app.services.retrieval), since the pgvector Python/SQLAlchemy package
    isn't a dependency here (same reasoning as content_tsv in
    app.models.core: keep Postgres-only SQL out of the mapped ORM layer so
    the SQLite-backed test database never has to understand it).
    """
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"
