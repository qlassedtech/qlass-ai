import io

import fitz  # pymupdf
from docx import Document
from sqlalchemy.orm import Session

from app.models.core import Student

# A message this long is almost certainly a multi-question problem set (a
# pasted homework list, an OCR'd photo of one, a transcribed voice note, or
# an extracted PDF/Word document) rather than a single question — pin it as
# the student's active document (see Student.active_document_text) so later
# turns like "now Q5" still work once the original message has scrolled out
# of the chat-history window. Applied uniformly regardless of which channel
# (WhatsApp, web, native app) or source (typed, OCR, STT, file) produced the
# text — see apply_active_document_pin.
ACTIVE_DOCUMENT_MIN_CHARS = 400
# Upper bound on what actually gets pinned — confirmed live that a 33,000-
# char paste got stored with no cap at all, permanently bloating that
# student's system prompt (and cost) for the rest of the session. 8,000
# chars comfortably covers a real 20-30 question DPP/homework sheet while
# still bounding the worst case.
ACTIVE_DOCUMENT_MAX_CHARS = 8000


def apply_active_document_pin(db: Session, student: Student, message_text: str) -> None:
    if len(message_text) >= ACTIVE_DOCUMENT_MIN_CHARS:
        student.active_document_text = message_text[:ACTIVE_DOCUMENT_MAX_CHARS]
        db.commit()


def extract_text_from_pdf(pdf_bytes: bytes) -> str | None:
    """Extract text from a PDF's pages, joined with blank lines between pages."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            pages = [page.get_text().strip() for page in doc]
        text = "\n\n".join(p for p in pages if p)
        # Some PDFs' content streams decode to text containing embedded NUL
        # bytes (confirmed live: a NIOS lesson PDF triggered "A string
        # literal cannot contain NUL (0x00) characters" on insert) —
        # Postgres text columns reject them outright, so this is stripped
        # here rather than at every ingestion call site.
        text = text.replace("\x00", "")
        return text or None
    except Exception:
        return None


def extract_text_from_docx(docx_bytes: bytes) -> str | None:
    """Extract text from a Word (.docx) document's paragraphs."""
    try:
        doc = Document(io.BytesIO(docx_bytes))
        paragraphs = [p.text.strip() for p in doc.paragraphs]
        text = "\n".join(p for p in paragraphs if p)
        return text or None
    except Exception:
        return None


def extract_text_from_document(file_bytes: bytes, filename: str) -> str | None:
    """Dispatch to the right extractor based on file extension. .doc (legacy
    binary Word format, not .docx) isn't supported — python-docx only reads
    the modern XML-based format."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)
    if lower.endswith(".docx"):
        return extract_text_from_docx(file_bytes)
    return None
