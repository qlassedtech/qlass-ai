"""
Streams the core NCERT curriculum (Classes 4-12: Math/Science/English/
Hindi/Social Science, plus Physics/Chemistry/Biology for 11-12) straight
into the documents/document_chunks tables, one chapter at a time —
download PDF, extract text, chunk, embed, write to DB, delete the PDF —
rather than downloading the whole ~578-chapter set to disk first. This
matters on a disk-constrained machine: keeping every chapter's PDF around
at once could be several GB, when only one is ever needed on disk at a
time.

The book list (BOOKS) is a hand-curated selection of the primary
English-medium textbook per class/subject, sourced from ncert.nic.in's own
textbook.php dropdown data (Social Science kept as its real separate
books — History/Geography/Civics/Economics — same as NCERT itself
distributes it, not collapsed into one).

Resumable: re-running skips nothing automatically, but ingest_document's
underlying replace-by-(class,subject,chapter,board) logic means re-running
after a partial failure just re-does that one chapter cleanly, not the
whole run — safe to restart after an interruption.

Usage:
    python scripts/ingest_ncert_curriculum.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.core import Document, DocumentChunk  # noqa: E402
from app.services.document_client import extract_text_from_document  # noqa: E402
from app.services.embeddings import embed_texts, format_vector_literal  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_document import _chunk_text  # noqa: E402

PDF_BASE_URL = "https://ncert.nic.in/textbook/pdf/"
BOARD = "CBSE"

# (class, subject, book_code, num_chapters, book_title)
BOOKS: list[tuple[str, str, str, int, str]] = [
    # demh1/dhhn1/deen1 ("Math-Magic"/"Rimjhim"/"Marigold") 404'd entirely
    # against the real site — confirmed live these are discontinued titles,
    # superseded by the NCF-revised books below (same "old title still
    # listed in the dropdown but the per-chapter PDFs were pulled" pattern
    # as Class 9 Science's old "Vigyan" vs new "Exploration").
    ("4", "Mathematics", "demm1", 14, "Math-Mela"),
    ("4", "Hindi", "dhve1", 13, "Veena"),
    ("4", "English", "desa1", 12, "Santoor"),
    ("5", "Mathematics", "eemh1", 14, "Math-Magic"),
    ("5", "Hindi", "ehhn1", 18, "Rimjhim"),
    ("5", "English", "eeen1", 10, "Marigold"),
    ("6", "Mathematics", "fegp1", 10, "Ganita Prakash"),
    # fhvs1/fehl1 ("Vasant"/"Honeysuckle") 404'd entirely — replaced with
    # the same Malhar/Poorvi titles that already work for Classes 7-8.
    ("6", "Hindi", "fhml1", 13, "Malhar"),
    ("6", "English", "fepr1", 5, "Poorvi"),
    ("6", "Science", "fecu1", 12, "Curiosity"),
    # fess1/fess2/fess3 (the old separate History/Geography/Civics books)
    # 404'd entirely — replaced by "Exploring Society India and Beyond",
    # the single NCF-revised book that supersedes all three (same
    # consolidation already used for Classes 7-8's Social Science).
    ("6", "Social Science", "fees1", 14, "Exploring Society India and Beyond"),
    ("7", "Mathematics", "gegp1", 8, "Ganita Prakash Part-I"),
    ("7", "Mathematics", "gegp2", 7, "Ganita Prakash Part-II"),
    ("7", "Hindi", "ghml1", 10, "Malhar"),
    ("7", "English", "gepr1", 5, "Poorvi"),
    ("7", "Science", "gecu1", 12, "Curiosity"),
    ("7", "Social Science", "gees1", 12, "Exploring Society India and Beyond Part-I"),
    ("7", "Social Science", "gees2", 8, "Exploring Society India and Beyond Part-II"),
    ("8", "Mathematics", "hegp1", 7, "Ganita Prakash Part-I"),
    ("8", "Mathematics", "hegp2", 7, "Ganita Prakash Part-II"),
    ("8", "Hindi", "hhvs1", 13, "Vasant"),
    ("8", "English", "hehd1", 8, "Honeydew"),
    ("8", "Science", "hesc1", 13, "Science"),
    ("8", "Social Science", "hess4", 5, "Resource And Development (Geography)"),
    ("8", "Social Science", "hess3", 8, "Social And Political Life"),
    ("8", "Social Science", "hess2", 8, "Our Pasts-III"),
    ("9", "Mathematics", "iemh1", 8, "Ganita Manjari"),
    # ihks1 ("Kshitij") 404'd entirely — replaced with "Ganga", confirmed
    # working. iess1/iess2/iess3 (old separate Contemporary India/
    # Economics/Contemporary World books) also 404'd entirely — dropped
    # rather than chasing individual replacements, since "Understanding
    # Society India and Beyond" (iest1, below) is the single NCF-revised
    # book that already supersedes all three and was already ingested
    # successfully.
    ("9", "Hindi", "ihga1", 12, "Ganga"),
    ("9", "English", "iebe1", 8, "Kaveri"),
    ("9", "Science", "iesc1", 13, "Exploration"),
    ("9", "Social Science", "iest1", 9, "Understanding Society India and Beyond Part-I"),
    ("10", "Mathematics", "jemh1", 14, "Mathematics"),
    ("10", "Hindi", "jhks1", 12, "Kshitij-2"),
    ("10", "English", "jeff1", 9, "First Flight"),
    ("10", "Science", "jesc1", 13, "Science"),
    ("10", "Social Science", "jess1", 7, "Contemporary India"),
    ("10", "Social Science", "jess2", 5, "Understanding Economic Development"),
    ("10", "Social Science", "jess3", 5, "India and the Contemporary World-II"),
    ("10", "Social Science", "jess4", 5, "Democratic Politics"),
    ("11", "Mathematics", "kemh1", 14, "Mathematics"),
    ("11", "Hindi", "khar1", 16, "Aroh"),
    ("11", "English", "kehb1", 14, "Hornbill"),
    ("11", "Physics", "keph1", 7, "Physics Part-I"),
    ("11", "Physics", "keph2", 7, "Physics Part-II"),
    ("11", "Chemistry", "kech1", 6, "Chemistry Part-I"),
    ("11", "Chemistry", "kech2", 3, "Chemistry Part-II"),
    ("11", "Biology", "kebo1", 19, "Biology"),
    ("12", "Mathematics", "lemh1", 6, "Mathematics Part-I"),
    ("12", "Mathematics", "lemh2", 7, "Mathematics Part-II"),
    ("12", "Hindi", "lhar1", 15, "Aroh"),
    ("12", "English", "lefl1", 13, "Flamingo"),
    ("12", "Physics", "leph1", 8, "Physics Part-I"),
    ("12", "Physics", "leph2", 6, "Physics Part-II"),
    ("12", "Chemistry", "lech1", 5, "Chemistry-I"),
    ("12", "Chemistry", "lech2", 5, "Chemistry-II"),
    ("12", "Biology", "lebo1", 13, "Biology"),
]


async def _download(code: str, chapter_num: int) -> bytes | None:
    """
    A fresh httpx.AsyncClient per call, not one shared/reused across the
    whole multi-hour run — confirmed live as the actual cause of a 41.5%
    failure rate in the first full run: downloads are spaced out by the
    ~21s+ Voyage rate-limit pacing between chapters (see
    app.services.embeddings), long enough for a reused connection's
    keep-alive to go stale and fail on the next request. One retry on a
    fresh connection covers the rest (genuine transient blips).
    """
    url = f"{PDF_BASE_URL}{code}{chapter_num:02d}.pdf"
    for _ in range(2):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=60)
            if resp.status_code == 200 and resp.content:
                return resp.content
            if resp.status_code == 404:
                return None  # genuinely doesn't exist — retrying won't help
        except httpx.HTTPError:
            pass
    return None


async def _ingest_chapter(db, class_: str, subject: str, book_title: str, code: str, chapter_num: int) -> str:
    """Downloads, extracts, chunks, embeds, and stores one chapter — the
    PDF bytes never touch disk (kept in memory only, discarded after text
    extraction), so this never accumulates storage regardless of how many
    chapters run."""
    pdf_bytes = await _download(code, chapter_num)
    if pdf_bytes is None:
        return "download_failed"

    raw_text = extract_text_from_document(pdf_bytes, f"{code}{chapter_num:02d}.pdf")
    del pdf_bytes
    if not raw_text or not raw_text.strip():
        return "no_text"

    chunks = _chunk_text(raw_text)
    if not chunks:
        return "no_chunks"

    chapter_label = f"{book_title} — Chapter {chapter_num}"
    embeddings = await embed_texts(chunks, input_type="document", retry_on_rate_limit=True)

    existing = (
        db.query(Document)
        .filter(Document.class_ == class_, Document.subject == subject,
                Document.chapter == chapter_label, Document.board == BOARD)
        .first()
    )
    if existing:
        db.query(DocumentChunk).filter(DocumentChunk.document_id == existing.id).delete()
        db.delete(existing)
        db.commit()

    document = Document(title=chapter_label, class_=class_, subject=subject, chapter=chapter_label, board=BOARD)
    db.add(document)
    db.commit()
    db.refresh(document)

    for i, chunk_content in enumerate(chunks):
        db.add(DocumentChunk(document_id=document.id, chunk_index=i, content=chunk_content))
    db.commit()

    if embeddings:
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
        return f"ok ({len(chunks)} chunks, embedded)"
    return f"ok ({len(chunks)} chunks, FTS only — embedding failed)"


async def run(retry_missing_only: bool = False) -> None:
    """
    retry_missing_only=True skips any (class, subject, chapter) already
    present in the documents table — meant for a cleanup pass after a run
    that had download failures (see the "ok"/"download_failed" per-book
    logic above and this module's docstring on why those happen: NCERT
    connection blips, not missing content), so re-running doesn't
    re-download/re-embed the ~60% that already succeeded.
    """
    total_chapters = sum(n for *_, n, _ in BOOKS)
    done = 0
    counts = {"ok": 0, "download_failed": 0, "no_text": 0, "no_chunks": 0, "skipped_existing": 0}
    for class_, subject, code, num_chapters, book_title in BOOKS:
        db = SessionLocal()
        try:
            existing_chapters: set[str] = set()
            if retry_missing_only:
                rows = (
                    db.query(Document.chapter)
                    .filter(Document.class_ == class_, Document.subject == subject, Document.board == BOARD)
                    .all()
                )
                existing_chapters = {row[0] for row in rows}
            for chapter_num in range(1, num_chapters + 1):
                done += 1
                chapter_label = f"{book_title} — Chapter {chapter_num}"
                if retry_missing_only and chapter_label in existing_chapters:
                    counts["skipped_existing"] += 1
                    continue
                status = await _ingest_chapter(db, class_, subject, book_title, code, chapter_num)
                key = "ok" if status.startswith("ok") else status
                counts[key] = counts.get(key, 0) + 1
                print(f"[{done}/{total_chapters}] Class {class_} {subject} — {book_title} ch.{chapter_num}: {status}")
        finally:
            db.close()
    print(f"\nDone: {counts}")


if __name__ == "__main__":
    asyncio.run(run(retry_missing_only="--retry-missing" in sys.argv))
