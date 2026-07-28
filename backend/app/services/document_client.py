import io

import fitz  # pymupdf
from docx import Document


def extract_text_from_pdf(pdf_bytes: bytes) -> str | None:
    """Extract text from a PDF's pages, joined with blank lines between pages."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            pages = [page.get_text().strip() for page in doc]
        text = "\n\n".join(p for p in pages if p)
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
