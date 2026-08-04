from app.models.core import Centre, Student
from app.services import student_chat
from app.services.intent_classifier import MessageClassification
from app.services.llm_client import LLMResult


def _make_student(db_session):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone="919000000001", centre_id=centre.id, class_="8")
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _classification(**overrides) -> MessageClassification:
    defaults = dict(
        intent="other", quiz_topic=None, mock_test_topic=None, wants_mock_test=False, quiz_skip=False,
        llm_result=LLMResult(text="", model="claude-haiku-4-5-20251001", input_tokens=1, output_tokens=1),
    )
    defaults.update(overrides)
    return MessageClassification(**defaults)


async def test_quiz_request_starts_a_real_quiz_not_a_free_text_reply(db_session, monkeypatch):
    """
    The actual bug being fixed: frontend/src/pages/Chat.tsx promises "a
    quick quiz" but process_web_message previously only ever called
    tutor_agent.respond — no real quiz. A student asking for one should now
    get the same scored Question-1-of-N flow WhatsApp already has.
    """
    student = _make_student(db_session)

    async def fake_classify_intent(message_text, last_discussed_topic=None, last_assistant_message=None, candidate_chunks=None):
        return _classification(quiz_topic="photosynthesis")

    async def fake_start_quiz(db, student, topic):
        student.active_quiz_id = 999
        db.commit()
        return f"Great, let's do a 3-question quiz on *{topic}*! 📝\n\nQuestion 1/3: ..."

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("tutor_agent.respond should not be called for a quiz request")

    # fetch_candidate_chunks runs raw Postgres full-text-search SQL
    # (to_tsquery/@@) that the SQLite-backed db_session fixture can't
    # execute — these tests are about quiz/routing logic, not retrieval,
    # so it's mocked out the same way tutor_agent.respond often is below.
    monkeypatch.setattr(student_chat, "fetch_candidate_chunks", lambda *a, **kw: [])
    monkeypatch.setattr(student_chat, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(student_chat, "start_quiz", fake_start_quiz)
    monkeypatch.setattr(student_chat.tutor_agent, "respond", fail_if_called)

    reply = await student_chat.process_web_message(db_session, student, "quiz me on photosynthesis")

    assert "quiz on *photosynthesis*" in reply
    assert "Question 1/3" in reply
    assert student.active_quiz_id == 999


async def test_answer_during_active_quiz_is_graded_not_treated_as_a_new_message(db_session, monkeypatch):
    student = _make_student(db_session)
    student.active_quiz_id = 42
    db_session.commit()

    async def fake_classify_intent(message_text, last_discussed_topic=None, last_assistant_message=None, candidate_chunks=None):
        return _classification(quiz_skip=False)

    async def fake_handle_quiz_answer(db, student, message_text, quiz_skip):
        return "Correct! ✅\n\nQuestion 2/3: ..."

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("tutor_agent.respond should not be called mid-quiz")

    # fetch_candidate_chunks runs raw Postgres full-text-search SQL
    # (to_tsquery/@@) that the SQLite-backed db_session fixture can't
    # execute — these tests are about quiz/routing logic, not retrieval,
    # so it's mocked out the same way tutor_agent.respond often is below.
    monkeypatch.setattr(student_chat, "fetch_candidate_chunks", lambda *a, **kw: [])
    monkeypatch.setattr(student_chat, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(student_chat, "handle_quiz_answer", fake_handle_quiz_answer)
    monkeypatch.setattr(student_chat.tutor_agent, "respond", fail_if_called)

    reply = await student_chat.process_web_message(db_session, student, "my answer")

    assert "Correct!" in reply


async def test_non_quiz_message_still_uses_the_tutor_agent(db_session, monkeypatch):
    student = _make_student(db_session)

    async def fake_classify_intent(message_text, last_discussed_topic=None, last_assistant_message=None, candidate_chunks=None):
        return _classification()

    async def fake_respond(profile, message, history, weak_topics, **kwargs):
        return {
            "reply": "Photosynthesis is...", "lang": "en-IN",
            "usage": {
                "main_model": "claude-sonnet-4-6", "main_input_tokens": 1, "main_output_tokens": 1,
                "main_cache_write_tokens": 0, "main_cache_read_tokens": 0,
                "classify_model": "claude-haiku-4-5-20251001", "classify_input_tokens": 1, "classify_output_tokens": 1,
                "classify_cache_write_tokens": 0, "classify_cache_read_tokens": 0,
            },
            "solved_directly": None, "evaluated": False, "topic": "photosynthesis", "correct": None,
            "citation": None, "video_query": None,
        }

    # fetch_candidate_chunks runs raw Postgres full-text-search SQL
    # (to_tsquery/@@) that the SQLite-backed db_session fixture can't
    # execute — these tests are about quiz/routing logic, not retrieval,
    # so it's mocked out the same way tutor_agent.respond often is below.
    monkeypatch.setattr(student_chat, "fetch_candidate_chunks", lambda *a, **kw: [])
    monkeypatch.setattr(student_chat, "classify_intent", fake_classify_intent)
    monkeypatch.setattr(student_chat.tutor_agent, "respond", fake_respond)

    reply = await student_chat.process_web_message(db_session, student, "what is photosynthesis")

    assert reply == "Photosynthesis is..."
    assert student.last_discussed_topic == "photosynthesis"
