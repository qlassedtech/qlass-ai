"""
Ingests a textbook chapter (PDF, .docx, or plain .txt) into the documents/
document_chunks tables so app.services.retrieval can ground the tutor's
answers in it — full-text search (see database/migrations/0038) always
gets indexed; a Voyage embedding per chunk (see database/migrations/0041
and app.services.embeddings) also gets stored when VOYAGE_API_KEY is set,
enabling app.services.retrieval.fetch_semantic_candidates for this chunk.
Embedding is silently skipped (not an error) when the key isn't set —
full-text search alone still works either way.

This is a per-chapter tool, not a bulk NCERT-library importer — point it at
one real chapter file at a time (from wherever your NCERT PDFs actually
are), tagged with the class/subject/board it belongs to. Re-running with
the same class/subject/chapter/board replaces that chapter's chunks rather
than duplicating them, so it's safe to re-ingest a corrected file. See
scripts/bulk_ingest_pdfs.py to ingest a whole directory of chapter files at
once via a manifest CSV.

Usage:
    python scripts/ingest_document.py <file> --class 9 --subject Science \\
        --chapter "The Fundamental Unit of Life" --board CBSE
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.core import Document, DocumentChunk  # noqa: E402
from app.services.document_client import extract_text_from_document  # noqa: E402
from app.services.embeddings import embed_texts, format_vector_literal  # noqa: E402

# ~1000 chars keeps each chunk focused enough for a relevant full-text
# match without being so small that a concept gets split across chunks.
CHUNK_CHARS = 1000
CHUNK_OVERLAP_CHARS = 150


def _chunk_text(text: str) -> list[str]:
    """Splits on paragraph boundaries where possible, falling back to a
    fixed character window with overlap so no sentence is cut with zero
    surrounding context in either neighboring chunk."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > CHUNK_CHARS:
            chunks.append(current)
            current = current[-CHUNK_OVERLAP_CHARS:] + "\n\n" + para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        chunks.append(current)
    return chunks


async def ingest(file_path: str, class_: str, subject: str, chapter: str, board: str) -> None:
    path = Path(file_path)
    raw_bytes = path.read_bytes()
    if path.suffix.lower() == ".txt":
        raw_text = raw_bytes.decode("utf-8", errors="ignore")
    else:
        raw_text = extract_text_from_document(raw_bytes, path.name)
    if not raw_text or not raw_text.strip():
        print(f"Couldn't extract any text from {file_path} — nothing ingested.")
        return

    chunks = _chunk_text(raw_text)
    if not chunks:
        print("No chunks produced — file may be empty after extraction.")
        return

    # Embedding is a network call per chapter, not per chunk (see
    # app.services.embeddings' batching) — done before opening the DB
    # transaction below so a slow/failed Voyage call never leaves a
    # half-committed chapter.
    embeddings = None
    if settings.voyage_api_key:
        embeddings = await embed_texts(chunks, input_type="document", retry_on_rate_limit=True)
        if embeddings is None:
            print("Voyage embedding call failed — ingesting with full-text search only, no semantic vectors this run.")

    db = SessionLocal()
    try:
        # Replace this exact chapter's existing chunks/document rather than
        # accumulating duplicates on a re-run.
        existing = (
            db.query(Document)
            .filter(Document.class_ == class_, Document.subject == subject,
                    Document.chapter == chapter, Document.board == board)
            .first()
        )
        if existing:
            db.query(DocumentChunk).filter(DocumentChunk.document_id == existing.id).delete()
            db.delete(existing)
            db.commit()

        document = Document(title=chapter, class_=class_, subject=subject, chapter=chapter, board=board)
        db.add(document)
        db.commit()
        db.refresh(document)

        for i, chunk_content in enumerate(chunks):
            db.add(DocumentChunk(document_id=document.id, chunk_index=i, content=chunk_content))
        db.commit()

        if embeddings:
            # Written via raw SQL, not the ORM — see app.models.core's
            # comment on the `embedding` column for why it isn't a mapped
            # attribute.
            chunk_rows = (
                db.query(DocumentChunk.id)
                .filter(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )
            for (chunk_id,), embedding in zip(chunk_rows, embeddings):
                db.execute(
                    text("UPDATE document_chunks SET embedding = CAST(:vec AS vector) WHERE id = :id"),
                    {"vec": format_vector_literal(embedding), "id": chunk_id},
                )
            db.commit()

        embed_note = f", {len(embeddings)} embedded" if embeddings else ""
        print(f"Ingested {len(chunks)} chunk(s){embed_note} for Class {class_} {board} {subject} — {chapter!r}.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", help="Path to the chapter file (.pdf, .docx, or .txt)")
    parser.add_argument("--class", dest="class_", required=True, help="Class/grade, e.g. 9")
    parser.add_argument("--subject", required=True, help="Subject, e.g. Science")
    parser.add_argument("--chapter", required=True, help="Chapter title, e.g. \"The Fundamental Unit of Life\"")
    parser.add_argument("--board", default="CBSE", help="Board (default: CBSE)")
    args = parser.parse_args()
    asyncio.run(ingest(args.file, args.class_, args.subject, args.chapter, args.board))


if __name__ == "__main__":
    main()
