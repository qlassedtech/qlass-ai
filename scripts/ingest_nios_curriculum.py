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
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.core import Document, DocumentChunk  # noqa: E402
from app.services.document_client import extract_text_from_document  # noqa: E402
from app.services.embeddings import embed_texts, format_vector_literal  # noqa: E402

# A DB session per lesson (not one shared per subject) — confirmed live
# that an SSH-tunneled connection to production (see module docstring) can
# drop during the long idle gaps between lessons (Voyage's rate-limit
# pacing leaves the DB connection untouched for minutes at a time), which
# previously killed the whole remaining subject, not just that one lesson.
# One retry after a short pause covers a genuinely transient drop; a
# second consecutive failure is treated as the tunnel actually being down
# and is allowed to raise, since silently retrying forever would mask that.
DB_RETRY_DELAY_SECONDS = 5

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_document import _chunk_text  # noqa: E402

BOARD = "NIOS"
VERIFY_NIOS_TLS = False  # see module docstring

# (class label, subject name, course-material page URL, explicit lesson-
# URL regex or None). The Senior Secondary science/math subjects use
# NIOS's clean, consistent "<code>_<Subject>_Eng_LessonN.pdf" naming
# (matched generically — see _extract_lessons/_is_english_medium) and need
# no explicit pattern. Every other subject here uses a DIFFERENT, subject-
# specific naming scheme with no titled anchor text (confirmed live by
# inspecting each page directly rather than guessing), so those are given
# an exact regex instead of relying on a generic heuristic:
#   - Math (211)/Science (212), Secondary: "Chapter-N.pdf" under an
#     English-only folder (no Hindi-vs-English ambiguity to resolve).
#   - Social Science (213), Secondary: "Lesson-NN.pdf" specifically under
#     SecSocSciCour/English/ — the SAME lesson numbers also exist under a
#     second, differently-named English-medium folder on this page
#     (social_science_213_English_Medium/) with duplicate content; only
#     one of the two is used to avoid ingesting every lesson twice.
#   - English (202 Secondary, 302 Senior Secondary): "L_N.pdf" /
#     "302ELN.pdf" respectively.
#
# Hindi (201, 301) is deliberately excluded at both levels — confirmed
# live that at least the Senior Secondary Hindi PDFs use a legacy,
# non-Unicode font encoding (extracted "text" is glyph-codepoint garbage,
# not real Devanagari — verified this is NOT the same PDF the already-
# ingested NCERT Hindi books use, which extract correctly). Ingesting NIOS
# Hindi content risks silently polluting the corpus with unusable text;
# left out entirely rather than ingesting garbage.
SUBJECT_PAGES: list[tuple[str, str, str, "re.Pattern | None"]] = [
    ("12", "Mathematics", "https://nios.ac.in/online-course-material/sr-secondary-courses/Mathematics-(311).aspx", None),
    ("12", "Physics", "https://nios.ac.in/online-course-material/sr-secondary-courses/Physics-(312).aspx", None),
    ("12", "Chemistry", "https://nios.ac.in/online-course-material/sr-secondary-courses/Chemistry-(313).aspx", None),
    ("12", "Biology", "https://nios.ac.in/online-course-material/sr-secondary-courses/Biology-(314).aspx", None),
    ("12", "English", "https://nios.ac.in/online-course-material/sr-secondary-courses/English-(302).aspx",
     re.compile(r"srsec302new/302EL\d+\.pdf", re.IGNORECASE)),
    ("10", "Mathematics", "https://nios.ac.in/online-course-material/secondary-courses/Mathematics-(211)-Syllabus.aspx",
     re.compile(r"SecMathcour/Eng/Chapter-\d+\.pdf", re.IGNORECASE)),
    ("10", "Science", "https://nios.ac.in/online-course-material/secondary-courses/Science-and-Technology-(212)-Syllabus.aspx",
     re.compile(r"secscicour/English/Chapter-\d+\.pdf", re.IGNORECASE)),
    ("10", "Social Science", "https://nios.ac.in/online-course-material/secondary-courses/Social-Science-(213)-Syllabus.aspx",
     re.compile(r"SecSocSciCour/English/Lesson-(?!00)\d+\.pdf", re.IGNORECASE)),
    ("10", "English", "https://nios.ac.in/online-course-material/secondary-courses/english-(202)-syllabus.aspx",
     re.compile(r"Secengcour/book1/L_\d+\.pdf", re.IGNORECASE)),
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


def _chapter_label(title: str) -> str:
    return f"NIOS {title}" if not title.lower().startswith("nios") else title


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


def _extract_lessons_by_pattern(html: str, base_url: str, pattern: "re.Pattern") -> list[tuple[str, str]]:
    """
    For subjects with an explicit lesson-URL regex (see SUBJECT_PAGES) —
    these pages have no usable anchor text (confirmed live: empty or just
    "&nbsp;"), so the title is a generic "Lesson N" derived from the
    trailing number in the URL itself rather than real chapter names. The
    number is real content is still correctly identified/retrieved either
    way — this only affects the citation label shown to a student, not
    what gets embedded.

    `pattern` is matched against each full href attribute value (via
    LESSON_HREF_RE, same as _extract_lessons) rather than searched
    directly against the raw HTML — confirmed live that searching the raw
    HTML let `pattern` match on a mid-string fragment missing the leading
    "/media/documents/..." portion of the real href, which urljoin then
    silently resolved against the wrong base (the subject page's own
    directory instead of the site root), producing a URL that 404's even
    though the real file exists.
    """
    matches: set[str] = set()
    for href, _inner_html in LESSON_HREF_RE.findall(html):
        if pattern.search(href):
            matches.add(href)

    def _lesson_number(href: str) -> int:
        m = re.search(r"(\d+)(?=\.pdf$)", href, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    ordered = sorted(matches, key=_lesson_number)
    return [(f"Lesson {_lesson_number(href)}", urljoin(base_url, href)) for href in ordered]


async def _download_lesson(client: httpx.AsyncClient, url: str) -> bytes | None:
    """One retry on a fresh short pause — confirmed live that an entire
    124-lesson run once 100% download-failed due to what was, moments
    later, a plain reachable connection again (a transient network blip,
    not a real outage) — a single unretried failure per lesson wasted the
    whole run rather than just skipping the one bad request."""
    for attempt in range(2):
        try:
            resp = await client.get(url, timeout=60)
            if resp.status_code == 200 and resp.content:
                return resp.content
            if resp.status_code == 404:
                return None
        except httpx.HTTPError:
            pass
        if attempt == 0:
            await asyncio.sleep(5)
    return None


async def _ingest_lesson(db, client: httpx.AsyncClient, class_: str, subject: str, title: str, url: str) -> str:
    pdf_bytes = await _download_lesson(client, url)
    if pdf_bytes is None:
        return "download_failed"

    raw_text = extract_text_from_document(pdf_bytes, Path(url).name)
    del pdf_bytes
    if not raw_text or not raw_text.strip():
        return "no_text"

    chunks = _chunk_text(raw_text)
    if not chunks:
        return "no_chunks"

    chapter_label = _chapter_label(title)
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


def _existing_chapters(class_: str, subject: str) -> set[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Document.chapter)
            .filter(Document.class_ == class_, Document.subject == subject, Document.board == BOARD)
            .all()
        )
        return {row[0] for row in rows}
    finally:
        db.close()


async def _ingest_lesson_with_retry(class_: str, subject: str, title: str, url: str, client: httpx.AsyncClient) -> str:
    for attempt in range(2):
        db = SessionLocal()
        try:
            return await _ingest_lesson(db, client, class_, subject, title, url)
        except OperationalError:
            if attempt == 0:
                print("  DB connection dropped (tunnel blip?) — retrying once after a short pause...")
                await asyncio.sleep(DB_RETRY_DELAY_SECONDS)
                continue
            raise
        finally:
            db.close()


async def run() -> None:
    counts = {"ok": 0, "download_failed": 0, "no_text": 0, "no_chunks": 0, "page_fetch_failed": 0, "skipped_existing": 0}
    done = 0
    async with httpx.AsyncClient(verify=VERIFY_NIOS_TLS) as client:
        for class_, subject, page_url, lesson_pattern in SUBJECT_PAGES:
            try:
                page_resp = await client.get(page_url, timeout=30)
                page_resp.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"Could not fetch subject page {page_url}: {exc}")
                counts["page_fetch_failed"] += 1
                continue
            lessons = (
                _extract_lessons_by_pattern(page_resp.text, page_url, lesson_pattern)
                if lesson_pattern is not None
                else _extract_lessons(page_resp.text, page_url)
            )
            print(f"Class {class_} {subject}: {len(lessons)} lessons found")
            already_done = _existing_chapters(class_, subject)
            for title, url in lessons:
                done += 1
                chapter_label = _chapter_label(title)
                if chapter_label in already_done:
                    counts["skipped_existing"] += 1
                    print(f"[{done}] Class {class_} {subject} — {title}: already ingested, skipping")
                    continue
                status = await _ingest_lesson_with_retry(class_, subject, title, url, client)
                key = "ok" if status.startswith("ok") else status
                counts[key] = counts.get(key, 0) + 1
                print(f"[{done}] Class {class_} {subject} — {title}: {status}")
    print(f"\nDone: {counts}")


if __name__ == "__main__":
    asyncio.run(run())
