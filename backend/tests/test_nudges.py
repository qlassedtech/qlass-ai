from datetime import datetime, timedelta, timezone

from app.models.core import Centre, Document, DocumentChunk, Student
from app.services import nudges
from app.services.nudges import NUDGE_TYPES, pick_next_nudge, record_nudge_sent


def _make_student(pg_db_session, **overrides):
    centre = Centre(name="Test School")
    pg_db_session.add(centre)
    pg_db_session.commit()
    defaults = dict(name="Test Student", phone="919000000030", centre_id=centre.id, class_="9", board="CBSE")
    defaults.update(overrides)
    student = Student(**defaults)
    pg_db_session.add(student)
    pg_db_session.commit()
    pg_db_session.refresh(student)
    return student


def _add_chunk(pg_db_session, content="Water expands when it freezes.", class_="9", board="CBSE"):
    doc = Document(title="States of Matter", class_=class_, subject="Science", chapter="States of Matter", board=board)
    pg_db_session.add(doc)
    pg_db_session.commit()
    pg_db_session.refresh(doc)
    pg_db_session.add(DocumentChunk(document_id=doc.id, chunk_index=0, content=content))
    pg_db_session.commit()


async def test_record_nudge_sent_stamps_the_type_with_a_timestamp(pg_db_session):
    student = _make_student(pg_db_session)
    record_nudge_sent(pg_db_session, student, "social_proof")
    assert "social_proof" in student.nudges_sent
    datetime.fromisoformat(student.nudges_sent["social_proof"])  # doesn't raise


async def test_a_type_sent_recently_is_not_picked_again_within_cooldown(pg_db_session):
    """
    The actual point of nudges_sent: social_proof and feature_highlight
    were both just sent (inside cooldown), so only fun_fact remains
    eligible — and this student has no ingested chunks for their class, so
    even that has nothing to send. Net result: no nudge this round, not a
    fallback to a type still on cooldown.
    """
    student = _make_student(pg_db_session)  # no features enabled, no chunks ingested for class 9
    student.nudges_sent = {
        "social_proof": datetime.now(timezone.utc).isoformat(),
        "feature_highlight": datetime.now(timezone.utc).isoformat(),
    }
    pg_db_session.commit()

    picked = await pick_next_nudge(pg_db_session, student)
    assert picked is None


async def test_a_type_sent_long_ago_is_eligible_again(pg_db_session):
    student = _make_student(pg_db_session)
    old = (datetime.now(timezone.utc) - timedelta(days=nudges.NUDGE_COOLDOWN_DAYS + 1)).isoformat()
    student.nudges_sent = {t: old for t in NUDGE_TYPES}
    pg_db_session.commit()

    picked = await pick_next_nudge(pg_db_session, student)
    assert picked is not None
    assert picked[0] in NUDGE_TYPES


async def test_feature_highlight_skips_a_feature_the_student_has_no_access_to(pg_db_session):
    student = _make_student(pg_db_session, features={"voice": False, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False})
    highlight = nudges._next_feature_highlight(pg_db_session, student)
    assert highlight is None


async def test_feature_highlight_offers_an_enabled_unused_feature(pg_db_session):
    student = _make_student(pg_db_session, features={"voice": True, "ocr": False, "image_generation": False, "documents": False, "youtube_videos": False})
    highlight = nudges._next_feature_highlight(pg_db_session, student)
    assert highlight is not None
    assert "voice note" in highlight.lower()


async def test_fun_fact_returns_none_without_ingested_content_for_the_class(pg_db_session):
    """
    The actual coverage gap this was built around: a class/board with no
    ingested textbook chunks yet must silently skip fun_fact rather than
    let the LLM invent something ungrounded — see the module docstring.
    """
    student = _make_student(pg_db_session, class_="12", board="ICSE")
    fact = await nudges._generate_fun_fact(pg_db_session, student)
    assert fact is None


async def test_fun_fact_is_grounded_in_a_real_retrieved_chunk(pg_db_session, monkeypatch):
    student = _make_student(pg_db_session, class_="9", board="CBSE")
    _add_chunk(pg_db_session, content="Water expands when it freezes, making ice less dense than liquid water.")

    class FakeResult:
        text = "Did you know? Ice floats because it's less dense than liquid water! 🧊"

    async def fake_call_llm(system_prompt, messages, model):
        assert "Water expands" in messages[0]["content"]
        return FakeResult()

    monkeypatch.setattr(nudges, "call_llm", fake_call_llm)
    fact = await nudges._generate_fun_fact(pg_db_session, student)
    assert fact == FakeResult.text
