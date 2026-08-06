import asyncio
import time

import httpx

from app.config import settings

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage batches embedding requests — one call for many texts is both
# cheaper and faster than one call per chunk, so ingestion sends chunks in
# batches of this size rather than one at a time.
MAX_BATCH_SIZE = 128

# Voyage's free tier is rate-limited to 3 requests/minute AND 10,000
# tokens/minute until a payment method is added on the account (confirmed
# live: a single real NCERT chapter — ~20 chunks, ~13,000 tokens — 429'd
# even as the very first request of a fresh minute, and kept 429'ing on
# every retry, because the TPM cap alone was already exceeded regardless
# of RPM pacing). MAX_BATCH_CHARS keeps each individual request under that
# TPM budget (~4 chars/token for English is the usual rule of thumb —
# 24,000 chars is a conservative ~6,000-token estimate, leaving real
# margin rather than cutting it exactly at 10,000).
MAX_BATCH_CHARS = 24_000

# 21s comfortably clears one request every 20s (the 3 RPM cap).
RATE_LIMIT_RETRY_SECONDS = 21

# Tracks the last paced request across ALL embed_texts calls in this
# process, not just within one call — scripts/bulk_ingest_pdfs.py makes one
# embed_texts call per chapter file, back to back with no gap of its own,
# so pacing that only applied *within* a single call's own sub-batches
# still let consecutive chapters blow the per-minute budget (confirmed
# live: chapter 1 succeeded, every chapter after it 429'd even with
# in-call retries, because each chapter's first request had no delay since
# the previous chapter's last one).
_last_rate_limited_request_at: float = 0.0


async def _pace_rate_limited_request() -> None:
    global _last_rate_limited_request_at
    elapsed = time.monotonic() - _last_rate_limited_request_at
    if elapsed < RATE_LIMIT_RETRY_SECONDS:
        await asyncio.sleep(RATE_LIMIT_RETRY_SECONDS - elapsed)
    _last_rate_limited_request_at = time.monotonic()


def _char_budget_batches(texts: list[str]) -> list[list[str]]:
    """Groups texts into batches that stay under MAX_BATCH_CHARS combined
    (not just MAX_BATCH_SIZE by count) — a handful of long textbook chunks
    can blow the per-minute token budget well before hitting the count
    limit. A single text longer than the budget still gets its own batch
    (sent alone) rather than being split mid-text."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for t in texts:
        if current and (len(current) >= MAX_BATCH_SIZE or current_chars + len(t) > MAX_BATCH_CHARS):
            batches.append(current)
            current, current_chars = [], 0
        current.append(t)
        current_chars += len(t)
    if current:
        batches.append(current)
    return batches


async def embed_texts(
    texts: list[str], input_type: str, retry_on_rate_limit: bool = False, max_retries: int = 5,
) -> list[list[float]] | None:
    """
    Embeds a batch of texts with Voyage. `input_type` must be "document"
    when embedding textbook chunks for storage, or "query" when embedding
    a student's live question — Voyage trains asymmetric embeddings for
    each side of a retrieval pair, so using the wrong one measurably hurts
    match quality even though both return same-shaped vectors.

    `retry_on_rate_limit` waits out a 429 and retries (up to max_retries
    times) rather than giving up — meant for offline batch ingestion (see
    scripts/ingest_document.py), where waiting ~20s between chapters is a
    non-issue. Left False by default (used by embed_text, the live
    student-chat query path via app.services.retrieval) since a student
    mid-conversation should never be stuck waiting out someone else's rate
    limit — a live query just fails fast and falls back to full-text
    search instead.

    Returns None (never raises) if Voyage isn't configured or the call
    fails (including exhausting retries) — every caller treats that as
    "semantic retrieval unavailable right now," not a hard error, so a
    Voyage outage or a not-yet-set API key never breaks the tutor itself
    (see app.services.retrieval, which still has full-text search as an
    always-available fallback).
    """
    if not settings.voyage_api_key or not texts:
        return None

    headers = {"Authorization": f"Bearer {settings.voyage_api_key}", "Content-Type": "application/json"}
    embeddings: list[list[float]] = []
    batches = _char_budget_batches(texts)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for batch in batches:
                # Paced before EVERY request (not just after a 429, and not
                # just within this one call) — see _pace_rate_limited_
                # request's docstring for why a per-call-only pace still
                # wasn't enough.
                if retry_on_rate_limit:
                    await _pace_rate_limited_request()
                payload = {
                    "input": batch,
                    "model": settings.voyage_embedding_model,
                    "input_type": input_type,
                    "output_dimension": settings.voyage_embedding_dimensions,
                }
                attempt = 0
                while True:
                    resp = await client.post(VOYAGE_API_URL, headers=headers, json=payload)
                    if resp.status_code == 429 and retry_on_rate_limit and attempt < max_retries:
                        attempt += 1
                        # Same pacer as above (not a bare sleep) — keeps
                        # _last_rate_limited_request_at accurate, so the
                        # *next* batch/chapter's own pacing check isn't
                        # fooled by a stale timestamp from before this wait.
                        await _pace_rate_limited_request()
                        continue
                    resp.raise_for_status()
                    break
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
    always embedded one at a time (no batching opportunity at query time).
    Never retries on rate limit (see embed_texts) — fails fast so a live
    chat turn is never stuck waiting."""
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
