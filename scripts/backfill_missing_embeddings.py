"""
Finds document_chunks rows with no embedding (see app.services.embeddings)
and embeds just those — a targeted cleanup pass after a large ingestion
run where a small fraction of chunks hit a transient Voyage failure (rate
limit, timeout) that survived even the in-call retry, rather than
re-running the whole ingestion.

Usage:
    python scripts/backfill_missing_embeddings.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.services.embeddings import embed_texts, format_vector_literal  # noqa: E402

# One chunk's worth of DB round-trips per batch, not one giant IN clause —
# keeps a single failed batch's blast radius small and progress visible.
BATCH_SIZE = 50


async def run() -> None:
    db = SessionLocal()
    embedded, failed = 0, 0
    try:
        while True:
            rows = db.execute(
                text("SELECT id, content FROM document_chunks WHERE embedding IS NULL LIMIT :n"),
                {"n": BATCH_SIZE},
            ).fetchall()
            if not rows:
                break
            ids = [r[0] for r in rows]
            contents = [r[1] for r in rows]
            embeddings = await embed_texts(contents, input_type="document", retry_on_rate_limit=True)
            if embeddings is None:
                print(f"Batch of {len(ids)} failed even after retries — stopping (re-run to pick up where this left off).")
                failed += len(ids)
                break
            for chunk_id, embedding in zip(ids, embeddings):
                db.execute(
                    text("UPDATE document_chunks SET embedding = CAST(:vec AS vector) WHERE id = :id"),
                    {"vec": format_vector_literal(embedding), "id": chunk_id},
                )
            db.commit()
            embedded += len(ids)
            print(f"Embedded {embedded} so far...")
    finally:
        db.close()
    print(f"\nDone: {embedded} embedded, {failed} failed.")


if __name__ == "__main__":
    asyncio.run(run())
