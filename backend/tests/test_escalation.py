from app.models.core import Centre, Student, Teacher
from app.services.escalation import (
    ESCALATION_THRESHOLD,
    format_escalation_message,
    get_escalation_recipients,
    record_hint_outcome,
)


def _make_student(db_session):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone="919000000001", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_consecutive_wrong_answers_build_up_the_streak(db_session):
    student = _make_student(db_session)
    for _ in range(ESCALATION_THRESHOLD - 1):
        assert record_hint_outcome(student, evaluated=True, correct=False) is False
    assert record_hint_outcome(student, evaluated=True, correct=False) is True


def test_correct_answer_resets_the_streak(db_session):
    student = _make_student(db_session)
    record_hint_outcome(student, evaluated=True, correct=False)
    record_hint_outcome(student, evaluated=True, correct=False)
    record_hint_outcome(student, evaluated=True, correct=True)
    assert student.consecutive_unresolved_hints == 0
    # Needs a full fresh streak after the reset, not just one more wrong answer.
    assert record_hint_outcome(student, evaluated=True, correct=False) is False


def test_non_evaluated_turn_does_not_affect_streak(db_session):
    """
    A hint given on a fresh problem, an explanation, or off-topic chat isn't
    evidence the student is stuck — only a genuinely WRONG answer to a
    check question counts. Previously this counted any hint-not-solve turn
    toward escalation, which fired for students doing completely fine (the
    tutor's normal hint-first teaching style), not just students actually
    struggling.
    """
    student = _make_student(db_session)
    record_hint_outcome(student, evaluated=True, correct=False)
    record_hint_outcome(student, evaluated=False, correct=None)
    assert student.consecutive_unresolved_hints == 1


def test_escalation_recipients_are_scoped_to_the_students_school(db_session):
    centre_a = Centre(name="School A")
    centre_b = Centre(name="School B")
    db_session.add_all([centre_a, centre_b])
    db_session.commit()
    db_session.add_all([
        Teacher(name="Teacher A", phone="919000000010", centre_id=centre_a.id, role="teacher"),
        Teacher(name="Admin A", phone="919000000011", centre_id=centre_a.id, role="admin"),
        Teacher(name="Teacher B", phone="919000000012", centre_id=centre_b.id, role="teacher"),
        Teacher(name="Qlass Staff", phone="919000000013", centre_id=None, role="super_admin"),
    ])
    db_session.commit()

    recipients = get_escalation_recipients(db_session, centre_a.id)
    phones = {r.phone for r in recipients}
    assert phones == {"919000000010", "919000000011"}


def test_escalation_message_mentions_topic():
    message = format_escalation_message("Riya", "circular motion")
    assert "Riya" in message
    assert "circular motion" in message
