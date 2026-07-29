from app.models.core import Centre, Student, TopicProgress
from app.services.analytics import MIN_EVALUATED_FOR_RISK, get_school_analytics
from app.services.escalation import ESCALATION_THRESHOLD


def _make_student(db_session, name="Student", consecutive_unresolved_hints=0):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(
        name=name, phone="919000000001", centre_id=centre.id,
        consecutive_unresolved_hints=consecutive_unresolved_hints,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_poor_accuracy_with_enough_samples_flags_as_at_risk(db_session):
    student = _make_student(db_session)
    for i in range(MIN_EVALUATED_FOR_RISK):
        # 1 correct out of MIN_EVALUATED_FOR_RISK -> well under the 50% threshold
        is_correct = i == 0
        db_session.add(TopicProgress(student_id=student.id, topic="algebra", is_correct=is_correct))
    db_session.commit()

    result = get_school_analytics(db_session, [student.id], centre_id=None)
    at_risk_ids = {s["id"] for s in result["at_risk_students"]}
    assert student.id in at_risk_ids


def test_poor_accuracy_with_too_few_samples_is_not_flagged(db_session):
    student = _make_student(db_session)
    db_session.add(TopicProgress(student_id=student.id, topic="algebra", is_correct=False))
    db_session.commit()

    result = get_school_analytics(db_session, [student.id], centre_id=None)
    assert result["at_risk_students"] == []


def test_good_accuracy_is_not_flagged(db_session):
    student = _make_student(db_session)
    for _ in range(MIN_EVALUATED_FOR_RISK):
        db_session.add(TopicProgress(student_id=student.id, topic="algebra", is_correct=True))
    db_session.commit()

    result = get_school_analytics(db_session, [student.id], centre_id=None)
    assert result["at_risk_students"] == []


def test_high_hint_streak_flags_as_at_risk_even_with_no_topic_progress(db_session):
    student = _make_student(db_session, consecutive_unresolved_hints=ESCALATION_THRESHOLD - 1)
    result = get_school_analytics(db_session, [student.id], centre_id=None)
    at_risk_ids = {s["id"] for s in result["at_risk_students"]}
    assert student.id in at_risk_ids
