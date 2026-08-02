"""
Ingests a textbook chapter (PDF, .docx, or plain .txt) into the documents/
document_chunks tables so app.services.retrieval can ground the tutor's
answers in it — see database/migrations/0038_add_document_chunks_fulltext_search.sql
for why this uses Postgres full-text search rather than a vector embedding
store (no embeddings provider is wired up anywhere in this codebase).

This is a per-chapter tool, not a bulk NCERT-library importer — point it at
one real chapter file at a time (from wherever your NCERT PDFs actually
are), tagged with the class/subject/board it belongs to. Re-running with
the same class/subject/chapter/board replaces that chapter's chunks rather
than duplicating them, so it's safe to re-ingest a corrected file.

Usage:
    python scripts/ingest_document.py <file> --class 9 --subject Science \\
        --chapter "The Fundamental Unit of Life" --board CBSE
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Document, DocumentChunk  # noqa: E402
from app.services.document_client import extract_text_from_document  # noqa: E402

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


def ingest(file_path: str, class_: str, subject: str, chapter: str, board: str) -> None:
    path = Path(file_path)
    raw_bytes = path.read_bytes()
    if path.suffix.lower() == ".txt":
        text = raw_bytes.decode("utf-8", errors="ignore")
    else:
        text = extract_text_from_document(raw_bytes, path.name)
    if not text or not text.strip():
        print(f"Couldn't extract any text from {file_path} — nothing ingested.")
        return

    chunks = _chunk_text(text)
    if not chunks:
        print("No chunks produced — file may be empty after extraction.")
        return

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
        print(f"Ingested {len(chunks)} chunk(s) for Class {class_} {board} {subject} — {chapter!r}.")
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
    ingest(args.file, args.class_, args.subject, args.chapter, args.board)


if __name__ == "__main__":
    main()
