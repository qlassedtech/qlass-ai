"""
Streams NIOS (National Institute of Open Schooling) Secondary and Senior
Secondary course material into documents/document_chunks — same streaming
approach as scripts/ingest_ncert_curriculum.py (download one lesson's PDF
into memory, extract, chunk, embed, store, discard), but the lesson list
is DISCOVERED by scraping each subject's real course-material page rather
than a hand-curated code+count list, since NIOS's own per-subject pages
already list every lesson with its real title and a direct PDF link —
more reliable than guessing a URL pattern.

IMPORTANT — run this from a machine that isn't the production server: the
production VPS times out entirely trying to reach nios.ac.in (confirmed
live — NIOS/Indian-government sites commonly geo-block non-Indian IP
ranges, and the VPS is hosted outside India), while it works fine from an
Indian residential/ISP connection. Point DATABASE_URL at the production DB
through an SSH tunnel instead (see the deploy notes in the repo README's
"Talking to production Postgres from elsewhere" — or just:
    ssh -f -N -L 15432:localhost:5432 -p <port> <user>@<host>
    DATABASE_URL=postgresql://<user>:<pass>@localhost:15432/<db> \\
        VOYAGE_API_KEY=... python scripts/ingest_nios_curriculum.py

nios.ac.in's TLS certificate chain trips some local CA bundles (confirmed
against an outdated local cacert.pem) even though the certificate itself
is valid — VERIFY_NIOS_TLS below is the one place that's toggled off for
this specific known-good domain; nothing else in this script or its
downstream calls disables verification.

Usage:
    python scripts/ingest_nios_curriculum.py
"""
import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.core import Document, DocumentChunk  # noqa: E402
from app.services.document_client import extract_text_from_document  # noqa: E402
from app.services.embeddings import embed_texts, format_vector_literal  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_document import _chunk_text  # noqa: E402

BOARD = "NIOS"
VERIFY_NIOS_TLS = False  # see module docstring

# (class label, subject name, course-material page URL). Scoped to just
# the Senior Secondary (Class XII equivalency) science/math subjects that
# use NIOS's clean, consistent "<code>_<Subject>_Eng_LessonN.pdf" (or the
# Biology-specific bare-"E"-suffix variant — see _is_english_medium)
# naming — confirmed live to extract real, well-titled lessons reliably.
#
# Deliberately NOT included yet: Class X (Secondary) Math/Science, which
# use a DIFFERENT "Chapter-N.pdf" naming instead of "Lesson" and mix in
# "Learner Guide"/worksheet PDFs under similar-looking folder names; and
# English/Hindi at both levels, whose pages surface worksheet PDFs
# ("WS 1", "WS 2"...) more prominently than the actual lesson content in
# the site's own HTML. Both need their own per-subject page inspection
# (same as NCERT's stragglers) rather than a blind naming-pattern guess —
# left as a follow-up, not attempted here.
SUBJECT_PAGES: list[tuple[str, str, str]] = [
    ("12", "Mathematics", "https://nios.ac.in/online-course-material/sr-secondary-courses/Mathematics-(311).aspx"),
    ("12", "Physics", "https://nios.ac.in/online-course-material/sr-secondary-courses/Physics-(312).aspx"),
    ("12", "Chemistry", "https://nios.ac.in/online-course-material/sr-secondary-courses/Chemistry-(313).aspx"),
    ("12", "Biology", "https://nios.ac.in/online-course-material/sr-secondary-courses/Biology-(314).aspx"),
]

# Matches an anchor's visible text and href for an English-medium lesson
# PDF — NIOS uses a few different naming conventions across subjects
# (e.g. "..._Eng_Lesson3.pdf" vs "Lesson-03.pdf"), so this only requires
# "lesson" (case-insensitive) and "eng" somewhere in the URL, excluding
# non-lesson materials (lab manuals, "first page", full "Book" PDFs,
# curriculum/bifurcation/syllabus documents) that share the same folder.
LESSON_HREF_RE = re.compile(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
EXCLUDE_PATTERNS = re.compile(r"first_page|curriculum|bifurcation|instructions|syllabus|labm|book-?\d|question", re.IGNORECASE)
HTML_ENTITIES = {"&nbsp;": " ", "&amp;": "&", "&#39;": "'", "&quot;": '"'}


def _is_english_medium(href: str) -> bool:
    """
    NIOS doesn't use one consistent English-medium marker across subjects
    — confirmed live: Mathematics/Physics/Chemistry use an "Eng" folder
    (.../311_Maths_Eng/...), Biology uses a bare "E" suffix on the folder
    name instead (.../SrSec314NewE/..., vs "...NewH" for Hindi). Checked in
    that order: explicit Hindi markers always exclude first (a folder like
    "NewE" containing a lesson that also mentions "Hindi" elsewhere in the
    path would otherwise slip through), then "eng" as a substring, then a
    bare trailing "e" on the immediate parent folder name.
    """
    href_lower = href.lower()
    if "hindi" in href_lower or "_hin" in href_lower or "_h_" in href_lower:
        return False
    if "eng" in href_lower:
        return True
    parent = Path(href).parent.name.lower()
    return parent.endswith("e") and not parent.endswith("he")


def _clean_title(inner_html: str, href: str) -> str:
    title = re.sub(r"<[^>]+>", " ", inner_html)
    for entity, char in HTML_ENTITIES.items():
        title = title.replace(entity, char)
    title = re.sub(r"\s+", " ", title).strip()
    return title if title and title not in ("-", "L") else Path(href).stem


def _extract_lessons(html: str, base_url: str) -> list[tuple[str, str]]:
    """Returns [(title, absolute_pdf_url), ...] for English-medium lesson
    links on a NIOS subject page, deduped and in document order."""
    seen: set[str] = set()
    lessons: list[tuple[str, str]] = []
    for href, inner_html in LESSON_HREF_RE.findall(html):
        if "lesson" not in href.lower():
            continue
        if not _is_english_medium(href):
            continue
        if EXCLUDE_PATTERNS.search(href):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        lessons.append((_clean_title(inner_html, href), url))
    return lessons


async def _ingest_lesson(db, client: httpx.AsyncClient, class_: str, subject: str, title: str, url: str) -> str:
    try:
        resp = await client.get(url, timeout=60)
        if resp.status_code != 200 or not resp.content:
            return "download_failed"
        pdf_bytes = resp.content
    except httpx.HTTPError:
        return "download_failed"

    raw_text = extract_text_from_document(pdf_bytes, Path(url).name)
    del pdf_bytes
    if not raw_text or not raw_text.strip():
        return "no_text"

    chunks = _chunk_text(raw_text)
    if not chunks:
        return "no_chunks"

    chapter_label = f"NIOS {title}" if not title.lower().startswith("nios") else title
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
    return f"ok ({len(chunks)} chunks, FTS only)"


async def run() -> None:
    counts = {"ok": 0, "download_failed": 0, "no_text": 0, "no_chunks": 0, "page_fetch_failed": 0}
    done = 0
    async with httpx.AsyncClient(verify=VERIFY_NIOS_TLS) as client:
        for class_, subject, page_url in SUBJECT_PAGES:
            try:
                page_resp = await client.get(page_url, timeout=30)
                page_resp.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"Could not fetch subject page {page_url}: {exc}")
                counts["page_fetch_failed"] += 1
                continue
            lessons = _extract_lessons(page_resp.text, page_url)
            print(f"Class {class_} {subject}: {len(lessons)} lessons found")
            db = SessionLocal()
            try:
                for title, url in lessons:
                    done += 1
                    status = await _ingest_lesson(db, client, class_, subject, title, url)
                    key = "ok" if status.startswith("ok") else status
                    counts[key] = counts.get(key, 0) + 1
                    print(f"[{done}] Class {class_} {subject} — {title}: {status}")
            finally:
                db.close()
    print(f"\nDone: {counts}")


if __name__ == "__main__":
    asyncio.run(run())
