from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.text_utils import tokenize_words

# Retrieval uses Postgres full-text search rather than real vector
# embeddings — no embeddings provider is wired up anywhere in this
# codebase (chromadb isn't even in requirements.txt despite
# CHROMA_PERSIST_DIR existing as a setting; openai/google-generativeai are
# in requirements.txt but never actually imported anywhere). Full-text
# search needs zero new API keys/dependencies and works well for
# well-structured textbook chapter content — this can be swapped for real
# vector search later without changing the documents/document_chunks
# schema (see database/migrations/0038_add_document_chunks_fulltext_search.sql).
#
# This module only does CANDIDATE recall (cheap, cast a wide net) — the
# actual relevance judgment is folded into the existing classify_intent
# LLM call (see app.services.intent_classifier's relevant_excerpts field)
# rather than a separate call here or a keyword heuristic. Earlier versions
# tried fixing bad citations with a hand-picked "filler word" list (know/
# think/want/...) and later a corpus-frequency cutoff — both were still
# guessing at relevance from surface word statistics. Confirmed live: a
# student replying "Don't know" to a quiz prompt got cited against
# Calculus/Probability/Life Processes chunks that share no real topic with
# anything being discussed — the kind of judgment only an LLM reading the
# actual message and the actual excerpt can reliably make, and since
# classify_intent already runs once per message regardless, that judgment
# rides along in the same call instead of paying for a dedicated one.
MAX_CANDIDATES = 8
MAX_CHUNKS = 3
MAX_CHUNK_CHARS = 1500  # keeps the system prompt bounded even if a chunk is unusually long
MIN_WORD_LEN = 3  # drops tiny function words (if, is, in, to...) that add real recall noise, not signal


@dataclass
class RetrievedChunk:
    """content plus the source chapter's identity, so a citation can be
    shown to the student without trusting the LLM to accurately recall
    (or worse, invent) which chapter it actually came from — see
    build_citation_footer."""
    content: str
    class_: str | None
    subject: str | None
    chapter: str | None
    board: str | None


def _candidate_words(query_text: str) -> list[str]:
    words = [w for w in tokenize_words(query_text) if len(w) >= MIN_WORD_LEN]
    return list(dict.fromkeys(words))  # de-duped, order preserved


def fetch_candidate_chunks(
    db: Session, query_text: str, class_: str | None, board: str | None, limit: int = MAX_CANDIDATES,
) -> list[RetrievedChunk]:
    """
    Up to `limit` textbook chunks that SHARE A KEYWORD with `query_text` —
    recall only, not a relevance judgment (see module docstring: that's
    classify_intent's job). Scoped to this student's class and board when
    known — a Class 8 CBSE student must never get a Class 11 or BSEB chunk
    just because the keywords overlap, so scoping is never relaxed once
    class/board are known (confirmed live during development: an earlier
    version fell back to an unscoped search whenever the scoped one came
    up empty, which defeated the whole point — a wrong-class chunk is
    worse than none at all). Only queries unscoped when class/board
    genuinely aren't known.
    Returns [] (never raises) if there's simply no matching content yet,
    which is the common case until a school's real textbook set is
    ingested via scripts/ingest_document.py.
    """
    words = _candidate_words(query_text)
    if not words:
        return []
    tsquery_str = " | ".join(words)

    base_query = """
        SELECT dc.content, d.class, d.subject, d.chapter, d.board,
               ts_rank(dc.content_tsv, to_tsquery('english', :query)) AS rank
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.content_tsv @@ to_tsquery('english', :query)
    """
    params: dict = {"query": tsquery_str, "limit": limit}
    scoping = []
    if class_:
        scoping.append("d.class = :class_")
        params["class_"] = class_
    if board:
        scoping.append("d.board = :board")
        params["board"] = board

    sql = f"{base_query} AND {' AND '.join(scoping)} ORDER BY rank DESC LIMIT :limit" if scoping \
        else f"{base_query} ORDER BY rank DESC LIMIT :limit"
    rows = db.execute(text(sql), params).fetchall()
    return [
        RetrievedChunk(content=row[0][:MAX_CHUNK_CHARS], class_=row[1], subject=row[2], chapter=row[3], board=row[4])
        for row in rows
    ]


def build_citation_footer(chunks: list[RetrievedChunk]) -> str | None:
    """
    A short "📖 Source: ..." line naming the real chapter(s) an answer was
    grounded in — generated here from the DB rows actually retrieved, not
    left to the LLM to compose, so it can never cite a chapter it wasn't
    actually given (or invent one). Returns None if nothing was retrieved.
    Dedupes by chapter, since the top-N chunks can come from the same
    chapter more than once.
    """
    if not chunks:
        return None
    seen = set()
    lines = []
    for chunk in chunks:
        key = (chunk.class_, chunk.subject, chunk.chapter)
        if key in seen or not chunk.chapter:
            continue
        seen.add(key)
        class_note = f"Class {chunk.class_} " if chunk.class_ else ""
        subject_note = f"{chunk.subject} — " if chunk.subject else ""
        lines.append(f"{class_note}{subject_note}{chunk.chapter}")
    if not lines:
        return None
    label = "Sources" if len(lines) > 1 else "Source"
    return f"📖 {label}: " + "; ".join(lines)
