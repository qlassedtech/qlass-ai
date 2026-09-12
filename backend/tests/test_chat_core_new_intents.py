from app.models.core import Centre, Student
from app.services import chat_core
from app.services.intent_classifier import MessageClassification
from app.services.llm_client import LLMResult


def _make_student(db_session, phone="919000000010"):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone=phone, centre_id=centre.id, class_="8", board="CBSE")
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


def _patch_common(monkeypatch, classification_fn):
    def fake_fetch_candidate_chunks(*a, **kw):
        return []
    monkeypatch.setattr(chat_core, "fetch_candidate_chunks", fake_fetch_candidate_chunks)
    monkeypatch.setattr(chat_core, "fetch_higher_class_chunks", fake_fetch_candidate_chunks)
    monkeypatch.setattr(chat_core, "classify_intent", classification_fn)
    monkeypatch.setattr(chat_core, "get_welcome_back_note", lambda db, sid: None)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("tutor_agent.respond should not be called for this intent")
    monkeypatch.setattr(chat_core.tutor_agent, "respond", fail_if_called)


async def test_hint_mode_on_command_switches_tutor_style(db_session, monkeypatch):
    student = _make_student(db_session)
    assert student.tutor_style == "balanced"

    async def fake_classify(message_text, **kwargs):
        return _classification(hint_mode="on")

    _patch_common(monkeypatch, fake_classify)

    result = await chat_core.process_message(db_session, student, "turn on hint mode")

    assert student.tutor_style == "hint_first"
    assert "hint mode is now on" in result.reply_text.lower()


async def test_hint_mode_off_command_switches_back_to_balanced(db_session, monkeypatch):
    student = _make_student(db_session)
    student.tutor_style = "hint_first"
    db_session.commit()

    async def fake_classify(message_text, **kwargs):
        return _classification(hint_mode="off")

    _patch_common(monkeypatch, fake_classify)

    result = await chat_core.process_message(db_session, student, "hint mode off")

    assert student.tutor_style == "balanced"
    assert "hint mode is now off" in result.reply_text.lower()


async def test_notes_request_summarizes_recent_history(db_session, monkeypatch):
    student = _make_student(db_session)

    async def fake_classify(message_text, **kwargs):
        return _classification(wants_notes=True)

    _patch_common(monkeypatch, fake_classify)

    async def fake_call_llm(system_prompt, messages, model):
        return LLMResult(text="- Point one\n- Point two", model=model, input_tokens=5, output_tokens=5)

    monkeypatch.setattr("app.services.llm_client.call_llm", fake_call_llm)

    result = await chat_core.process_message(db_session, student, "give me notes")

    assert "Point one" in result.reply_text
    assert "Notes on what we've covered" in result.reply_text


async def test_worksheet_request_then_answers_reveal(db_session, monkeypatch):
    student = _make_student(db_session)

    async def fake_classify_worksheet(message_text, **kwargs):
        return _classification(wants_worksheet=True, worksheet_topic="fractions")

    _patch_common(monkeypatch, fake_classify_worksheet)

    async def fake_generate_quiz_questions(topic, student_class, num_questions=5, board=None):
        questions = [{"question": f"Q{i} on {topic}", "answer": f"A{i}", "question_type": "short_answer"} for i in range(3)]
        return questions, LLMResult(text="", model="claude-haiku-4-5-20251001", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(chat_core, "generate_quiz_questions", fake_generate_quiz_questions)

    result = await chat_core.process_message(db_session, student, "worksheet on fractions")
    assert "Q0 on fractions" in result.reply_text
    assert "A0" not in result.reply_text  # answers held back
    assert "answers" in result.reply_text.lower()

    # Second turn: student asks to reveal the answers.
    async def fake_classify_other(message_text, **kwargs):
        return _classification()
    monkeypatch.setattr(chat_core, "classify_intent", fake_classify_other)

    result2 = await chat_core.process_message(db_session, student, "answers")
    assert "A0" in result2.reply_text
