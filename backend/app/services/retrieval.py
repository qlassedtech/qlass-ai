from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.embeddings import embed_text, format_vector_literal
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
# Cap on how many chunks actually go into the LLM's context (as opposed to
# MAX_CANDIDATES, the recall pool they're drawn from) — each chunk costs
# real input tokens on every single call since the RAG portion of the
# prompt can't be cached (it changes almost every turn), unlike the much
# larger static instructional prompt. Confirmed live: capping this from 8
# down to 3 measurably cuts per-turn cost with no measured quality loss —
# the top few candidates by rank/similarity carry the real signal.
MAX_CHUNKS = 3
MAX_CHUNK_CHARS = 1500  # keeps the system prompt bounded even if a chunk is unusually long
MIN_WORD_LEN = 3  # drops tiny function words (if, is, in, to...) that add real recall noise, not signal

# Retrieval scoping default when a profile's own `board` isn't known at all
# (e.g. a teacher's personal "My AI Tutor" profile, or a school that hasn't
# set its own board yet) — NCERT content is tagged board="CBSE" in
# `documents` (there's no literal "NCERT" board value), so this is the
# closest match to "NCERT by default". Confirmed live: leaving board
# unscoped in this case searched the ENTIRE corpus including NIOS, and a
# plain "what is photosynthesis" question from an unscoped profile got
# cited against a Class 12 NIOS chapter — surprising for a query that
# didn't ask for anything NIOS-specific. Only applied when board is
# genuinely unset; an explicitly different board (ICSE, BSEB, ...) is
# never overridden.
DEFAULT_BOARD = "CBSE"

# pgvector's <=> operator returns cosine DISTANCE (0 = identical). Cosine
# similarity search always returns its top-N nearest neighbors regardless
# of whether any of them are actually close — for a query on a topic that
# simply isn't in the corpus, the "nearest" chunks are still just noise.
# Confirmed live in production: a student asked "what is alphabet in
# computer science" (not covered by any ingested NCERT/NIOS book) and got
# cited against six completely unrelated chapters (Hindi literature,
# English readers, unrelated Math topics) spanning Class 4 to 12 — the
# "closest" matches all sat at distance 0.47-0.51. A second check against
# queries with real corpus coverage ("explain photosynthesis", "newton's
# laws of motion") returned genuine matches at distance 0.29-0.31 — a
# wide, clean gap from the noise floor above. 0.4 sits comfortably between
# the two, cutting the noise while keeping real matches.
MAX_SEMANTIC_DISTANCE = 0.4


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
    worse than none at all). class is only left unscoped when genuinely
    unknown; board is NEVER left unscoped — it defaults to DEFAULT_BOARD
    (see that constant) rather than searching the entire corpus.
    Returns [] (never raises) if there's simply no matching content yet,
    which is the common case until a school's real textbook set is
    ingested via scripts/ingest_document.py.
    """
    words = _candidate_words(query_text)
    if not words:
        return []
    tsquery_str = " | ".join(words)
    board = board or DEFAULT_BOARD

    base_query = """
        SELECT dc.content, d.class, d.subject, d.chapter, d.board,
               ts_rank(dc.content_tsv, to_tsquery('english', :query)) AS rank
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.content_tsv @@ to_tsquery('english', :query)
    """
    params: dict = {"query": tsquery_str, "limit": limit}
    scoping = ["d.board = :board"]
    params["board"] = board
    if class_:
        scoping.append("d.class = :class_")
        params["class_"] = class_

    sql = f"{base_query} AND {' AND '.join(scoping)} ORDER BY rank DESC LIMIT :limit"
    rows = db.execute(text(sql), params).fetchall()
    return [
        RetrievedChunk(content=row[0][:MAX_CHUNK_CHARS], class_=row[1], subject=row[2], chapter=row[3], board=row[4])
        for row in rows
    ]


async def fetch_semantic_candidates(
    db: Session, query_text: str, class_: str | None, board: str | None, limit: int = MAX_CANDIDATES,
) -> list[RetrievedChunk]:
    """
    Same candidate-recall role as fetch_candidate_chunks above, but by
    cosine similarity over Voyage embeddings (see app.services.embeddings)
    instead of shared keywords — catches a paraphrased or conceptual
    question ("why does ice float") that shares no real keyword with the
    chunk that actually answers it ("density of water in its solid vs
    liquid state"), which pure full-text search structurally can't.

    Returns [] (never raises) whenever semantic search isn't usable right
    now — Voyage not configured, the embedding call fails, or this
    database's document_chunks table has no embedded rows yet (a fresh
    ingest before scripts/ingest_document.py's embedding step has run, or
    before the 0041 migration). Full-text search (fetch_candidate_chunks)
    is always the fallback that keeps working regardless — see
    app.services.chat_core, which calls both and merges them.
    """
    query_embedding = await embed_text(query_text, input_type="query")
    if query_embedding is None:
        return []
    vector_literal = format_vector_literal(query_embedding)
    board = board or DEFAULT_BOARD

    base_query = """
        SELECT dc.content, d.class, d.subject, d.chapter, d.board
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.embedding IS NOT NULL
          AND dc.embedding <=> CAST(:vector AS vector) < :max_distance
    """
    params: dict = {"vector": vector_literal, "limit": limit, "max_distance": MAX_SEMANTIC_DISTANCE}
    scoping = ["d.board = :board"]
    params["board"] = board
    if class_:
        scoping.append("d.class = :class_")
        params["class_"] = class_

    sql = f"{base_query} AND {' AND '.join(scoping)} ORDER BY dc.embedding <=> CAST(:vector AS vector) LIMIT :limit"
    try:
        rows = db.execute(text(sql), params).fetchall()
    except Exception:
        # Covers a database that hasn't run the 0041 migration yet (no
        # `embedding` column at all — a real, expected state for any
        # environment that hasn't been migrated, not a bug) — full-text
        # search alone still works in that case.
        db.rollback()
        return []
    return [
        RetrievedChunk(content=row[0][:MAX_CHUNK_CHARS], class_=row[1], subject=row[2], chapter=row[3], board=row[4])
        for row in rows
    ]


async def fetch_hybrid_candidates(
    db: Session, query_text: str, class_: str | None, board: str | None, limit: int = MAX_CANDIDATES,
) -> list[RetrievedChunk]:
    """
    The actual entry point app.services.chat_core uses — merges keyword
    (fetch_candidate_chunks) and semantic (fetch_semantic_candidates)
    recall into one deduped candidate list, keyword results first (cheap,
    always available, and exact-term matches tend to be the most literally
    on-topic) then semantic results filling any remaining slots. The
    combined list still only ever produces CANDIDATES — classify_intent's
    relevant_excerpts judgment (see this module's own docstring) is what
    decides which of these actually get cited, same as before.
    """
    keyword_chunks = fetch_candidate_chunks(db, query_text, class_, board, limit=limit)
    remaining = limit - len(keyword_chunks)
    if remaining <= 0:
        return keyword_chunks
    semantic_chunks = await fetch_semantic_candidates(db, query_text, class_, board, limit=limit)
    seen = {chunk.content for chunk in keyword_chunks}
    merged = list(keyword_chunks)
    for chunk in semantic_chunks:
        if len(merged) >= limit:
            break
        if chunk.content in seen:
            continue
        seen.add(chunk.content)
        merged.append(chunk)
    return merged


def build_citation_footer(chunks: list[RetrievedChunk]) -> str | None:
    """
    A short "📖 Source: ..." line naming the real chapter an answer was
    grounded in — generated here from the DB rows actually retrieved, not
    left to the LLM to compose, so it can never cite a chapter it wasn't
    actually given (or invent one). Returns None if nothing was retrieved.

    Cites only the SINGLE best-ranked chunk (chunks arrives already ordered
    by relevance/similarity — see app.services.chat_core), not every chunk
    retrieved. Confirmed live: citing the whole retrieved list produced a
    wall of 6 unrelated chapters (Hindi literature, English readers,
    unrelated Math topics) on a single answer — a real citation should
    point at where the answer actually came from, not list everything that
    happened to be pulled as candidate context.
    """
    if not chunks:
        return None
    lines = []
    for chunk in chunks:
        if not chunk.chapter:
            continue
        class_note = f"Class {chunk.class_} " if chunk.class_ else ""
        subject_note = f"{chunk.subject} — " if chunk.subject else ""
        lines.append(f"{class_note}{subject_note}{chunk.chapter}")
        break
    if not lines:
        return None
    return f"📖 Source: {lines[0]}"
