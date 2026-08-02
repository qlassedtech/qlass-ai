from app.models.core import Answer, Centre, Question, Quiz, Student
from app.services import quiz_flow
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


def _fake_questions(n: int) -> list[dict]:
    return [{"question": f"Q{i}?", "answer": f"A{i}", "question_type": "short_answer"} for i in range(n)]


async def test_start_quiz_creates_quiz_and_sets_active_quiz_id(db_session, monkeypatch):
    student = _make_student(db_session)

    async def fake_generate(topic, student_class, num_questions=5, board=None):
        return _fake_questions(3), LLMResult(text="", model="claude-haiku-4-5-20251001", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(quiz_flow, "generate_quiz_questions", fake_generate)
    reply = await quiz_flow.start_quiz(db_session, student, "circular motion")

    assert "3-question quiz" in reply
    assert "Question 1/3: Q0?" in reply
    assert student.active_quiz_id is not None
    quiz = db_session.query(Quiz).filter(Quiz.id == student.active_quiz_id).first()
    assert quiz.title == "circular motion"
    assert quiz.is_mock_test is False
    assert db_session.query(Question).filter(Question.quiz_id == quiz.id).count() == 3


async def test_start_quiz_with_no_questions_does_not_start_one(db_session, monkeypatch):
    student = _make_student(db_session)

    async def fake_generate(topic, student_class, num_questions=5, board=None):
        return [], LLMResult(text="", model="claude-haiku-4-5-20251001")

    monkeypatch.setattr(quiz_flow, "generate_quiz_questions", fake_generate)
    reply = await quiz_flow.start_quiz(db_session, student, "an obscure topic")

    assert "couldn't put together a quiz" in reply
    assert student.active_quiz_id is None


async def test_start_mock_test_uses_mock_test_question_count(db_session, monkeypatch):
    student = _make_student(db_session)
    captured = {}

    async def fake_generate(topic, student_class, num_questions=5, board=None):
        captured["num_questions"] = num_questions
        return _fake_questions(2), LLMResult(text="", model="claude-haiku-4-5-20251001")

    monkeypatch.setattr(quiz_flow, "generate_quiz_questions", fake_generate)
    reply = await quiz_flow.start_mock_test(db_session, student, "board exam review")

    assert captured["num_questions"] == quiz_flow.MOCK_TEST_QUESTION_COUNT
    assert "mock test" in reply.lower()
    quiz = db_session.query(Quiz).filter(Quiz.id == student.active_quiz_id).first()
    assert quiz.is_mock_test is True


def test_stop_quiz_clears_active_quiz_id(db_session):
    student = _make_student(db_session)
    student.active_quiz_id = 42
    db_session.commit()

    reply = quiz_flow.stop_quiz(db_session, student)

    assert "stopped" in reply.lower()
    assert student.active_quiz_id is None


async def _start_a_quiz(db_session, student, monkeypatch, n=2):
    async def fake_generate(topic, student_class, num_questions=5, board=None):
        return _fake_questions(n), LLMResult(text="", model="claude-haiku-4-5-20251001")

    monkeypatch.setattr(quiz_flow, "generate_quiz_questions", fake_generate)
    await quiz_flow.start_quiz(db_session, student, "topic")


async def test_handle_quiz_answer_advances_to_next_question(db_session, monkeypatch):
    student = _make_student(db_session)
    await _start_a_quiz(db_session, student, monkeypatch, n=2)

    async def fake_grade(question, correct_answer, given_answer):
        return True, LLMResult(text="yes", model="claude-haiku-4-5-20251001")

    monkeypatch.setattr(quiz_flow, "grade_answer", fake_grade)
    reply = await quiz_flow.handle_quiz_answer(db_session, student, "A0", quiz_skip=False)

    assert "Correct!" in reply
    assert "Question 2/2: Q1?" in reply
    assert student.active_quiz_id is not None  # quiz not done yet


async def test_handle_quiz_answer_completes_quiz_and_writes_weak_topic_on_wrong_answer(db_session, monkeypatch):
    """
    The actual fix: a wrong quiz answer must show up in weak_topics (via
    TopicProgress) so the tutor proactively revisits it in a later regular
    conversation — previously quiz mistakes were a dead end, stored only in
    the Answer table for scoring that one quiz.
    """
    student = _make_student(db_session)
    await _start_a_quiz(db_session, student, monkeypatch, n=1)

    async def fake_grade(question, correct_answer, given_answer):
        return False, LLMResult(text="no", model="claude-haiku-4-5-20251001")

    monkeypatch.setattr(quiz_flow, "grade_answer", fake_grade)
    reply = await quiz_flow.handle_quiz_answer(db_session, student, "wrong answer", quiz_skip=False)

    assert "Quiz complete!" in reply
    assert "0/1" in reply
    assert student.active_quiz_id is None

    from app.models.core import TopicProgress
    progress_rows = db_session.query(TopicProgress).filter(TopicProgress.student_id == student.id).all()
    assert len(progress_rows) == 1
    assert progress_rows[0].is_correct is False
    assert progress_rows[0].topic == "topic"


async def test_handle_quiz_answer_records_skip_without_grading_call(db_session, monkeypatch):
    student = _make_student(db_session)
    await _start_a_quiz(db_session, student, monkeypatch, n=1)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("grade_answer should not be called for a skip")

    monkeypatch.setattr(quiz_flow, "grade_answer", fail_if_called)
    reply = await quiz_flow.handle_quiz_answer(db_session, student, "we didn't study this", quiz_skip=True)

    assert "Skipped" in reply
    answer = db_session.query(Answer).filter(Answer.student_id == student.id).first()
    assert answer.is_correct is None
