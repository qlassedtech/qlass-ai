from app.models.core import Document, DocumentChunk
from app.services import retrieval
from app.services.retrieval import RetrievedChunk, build_citation_footer, fetch_candidate_chunks, fetch_hybrid_candidates


def _add_chunk(db, content, class_="9", subject="Science", board="CBSE", chapter="Cell Structure"):
    doc = Document(title=chapter, class_=class_, subject=subject, chapter=chapter, board=board)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    db.add(DocumentChunk(document_id=doc.id, chunk_index=0, content=content))
    db.commit()
    return doc


def test_retrieves_matching_chunk_scoped_to_class_and_board(pg_db_session):
    _add_chunk(
        pg_db_session,
        "The cell is the basic structural and functional unit of all living organisms.",
        class_="9", board="CBSE",
    )
    chunks = fetch_candidate_chunks(pg_db_session, "what is a cell", class_="9", board="CBSE")
    assert len(chunks) == 1
    assert "structural and functional unit" in chunks[0].content
    assert chunks[0].class_ == "9"
    assert chunks[0].subject == "Science"
    assert chunks[0].chapter == "Cell Structure"
    assert chunks[0].board == "CBSE"


def test_does_not_return_chunk_from_a_different_class(pg_db_session):
    _add_chunk(pg_db_session, "Newton's laws of motion govern how objects move.", class_="11", board="CBSE")
    chunks = fetch_candidate_chunks(pg_db_session, "newton's laws of motion", class_="9", board="CBSE")
    assert chunks == []


def test_searches_unscoped_when_no_class_board_given(pg_db_session):
    _add_chunk(pg_db_session, "Photosynthesis converts light energy into chemical energy in plants.", class_="10")
    chunks = fetch_candidate_chunks(pg_db_session, "photosynthesis", class_=None, board=None)
    assert len(chunks) == 1


def test_returns_empty_list_when_nothing_matches(pg_db_session):
    chunks = fetch_candidate_chunks(pg_db_session, "something with no matching content anywhere", class_="9", board="CBSE")
    assert chunks == []


def test_empty_query_returns_empty_list_without_querying(pg_db_session):
    chunks = fetch_candidate_chunks(pg_db_session, "   ", class_="9", board="CBSE")
    assert chunks == []


async def test_hybrid_candidates_falls_back_to_keyword_only_when_voyage_not_configured(pg_db_session, monkeypatch):
    """
    settings.voyage_api_key is unset in every test/dev environment by
    default — fetch_hybrid_candidates must still work exactly like plain
    fetch_candidate_chunks in that case (embed_text short-circuits to None,
    see app.services.embeddings), not raise or silently drop results.
    """
    _add_chunk(pg_db_session, "The cell is the basic structural and functional unit of all living organisms.", class_="9", board="CBSE")

    async def fake_embed_text(text, input_type):
        return None  # what embed_text actually returns when voyage_api_key isn't set

    monkeypatch.setattr(retrieval, "embed_text", fake_embed_text)
    chunks = await fetch_hybrid_candidates(pg_db_session, "what is a cell", class_="9", board="CBSE")
    assert len(chunks) == 1
    assert "structural and functional unit" in chunks[0].content


async def test_hybrid_candidates_adds_semantic_matches_keyword_search_missed(pg_db_session, monkeypatch):
    """
    The actual point of adding semantic search: a paraphrase that shares no
    keyword with the source chunk (so plain full-text search would return
    nothing for it) still surfaces here once Voyage is "configured" (mocked
    — no real network call in a unit test).
    """
    _add_chunk(pg_db_session, "Water expands when it freezes, so ice is less dense than liquid water.", class_="9", board="CBSE")
    monkeypatch.setattr(
        retrieval, "fetch_semantic_candidates",
        lambda db, q, c, b, limit=8: _fake_semantic_result(),
    )
    chunks = await fetch_hybrid_candidates(pg_db_session, "why does ice float", class_="9", board="CBSE")
    assert len(chunks) == 1
    assert "less dense" in chunks[0].content


async def _fake_semantic_result():
    return [RetrievedChunk(
        content="Water expands when it freezes, so ice is less dense than liquid water.",
        class_="9", subject="Science", chapter="States of Matter", board="CBSE",
    )]


def _chunk(class_="9", subject="Science", chapter="Cell Structure", board="CBSE") -> RetrievedChunk:
    return RetrievedChunk(content="some content", class_=class_, subject=subject, chapter=chapter, board=board)


def test_citation_footer_is_none_for_no_chunks():
    assert build_citation_footer([]) is None


def test_citation_footer_names_the_real_chapter():
    footer = build_citation_footer([_chunk()])
    assert footer == "📖 Source: Class 9 Science — Cell Structure"


def test_citation_footer_cites_only_the_best_ranked_chunk_not_the_whole_list():
    """
    Confirmed live: citing every retrieved chunk produced a wall of 6
    unrelated chapters (Hindi literature, English readers, unrelated Math
    topics) on a single answer. chunks arrives already ranked by relevance
    (see app.services.chat_core) — only the first (best) one should ever
    be cited, regardless of how many were retrieved as candidate context.
    """
    footer = build_citation_footer([_chunk(chapter="Cell Structure"), _chunk(chapter="Sound Waves")])
    assert footer == "📖 Source: Class 9 Science — Cell Structure"
    assert "Sound Waves" not in footer


def test_citation_footer_skips_a_chunk_with_no_chapter_recorded():
    """
    Never fabricate a citation for a chunk missing chapter metadata — this
    is meant to be a real, checkable reference, so a gap in the source data
    should just be silently omitted from the footer, not papered over.
    """
    footer = build_citation_footer([_chunk(chapter=None)])
    assert footer is None
