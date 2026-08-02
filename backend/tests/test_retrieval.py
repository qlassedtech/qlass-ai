from app.models.core import Document, DocumentChunk
from app.services.retrieval import RetrievedChunk, build_citation_footer, fetch_candidate_chunks


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


def _chunk(class_="9", subject="Science", chapter="Cell Structure", board="CBSE") -> RetrievedChunk:
    return RetrievedChunk(content="some content", class_=class_, subject=subject, chapter=chapter, board=board)


def test_citation_footer_is_none_for_no_chunks():
    assert build_citation_footer([]) is None


def test_citation_footer_names_the_real_chapter():
    footer = build_citation_footer([_chunk()])
    assert footer == "📖 Source: Class 9 Science — Cell Structure"


def test_citation_footer_dedupes_same_chapter_across_multiple_chunks():
    footer = build_citation_footer([_chunk(), _chunk()])
    assert footer.count("Cell Structure") == 1


def test_citation_footer_lists_multiple_distinct_chapters():
    footer = build_citation_footer([_chunk(chapter="Cell Structure"), _chunk(chapter="Sound Waves")])
    assert "Sources" in footer
    assert "Cell Structure" in footer
    assert "Sound Waves" in footer


def test_citation_footer_skips_a_chunk_with_no_chapter_recorded():
    """
    Never fabricate a citation for a chunk missing chapter metadata — this
    is meant to be a real, checkable reference, so a gap in the source data
    should just be silently omitted from the footer, not papered over.
    """
    footer = build_citation_footer([_chunk(chapter=None)])
    assert footer is None
