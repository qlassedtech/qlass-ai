from datetime import datetime, timedelta

from app.models.core import ChatHistory, Centre, Student
from app.services import cost_tracker
from app.services.habit import evaluate_habit_milestones, HABIT_MILESTONES


def _utcnow():
    return datetime.utcnow()


def _make_student(pg_db_session):
    centre = Centre(name="Test School")
    pg_db_session.add(centre)
    pg_db_session.commit()
    student = Student(name="Test Student", phone="919000000010", centre_id=centre.id)
    pg_db_session.add(student)
    pg_db_session.commit()
    pg_db_session.refresh(student)
    return student


def _add_question(pg_db_session, student_id, at: datetime):
    pg_db_session.add(ChatHistory(student_id=student_id, role="user", message="q", agent="tutor", created_at=at))
    pg_db_session.commit()


def test_day1_habit_milestone_pays_student_own_wallet(pg_db_session):
    student = _make_student(pg_db_session)
    signup = _utcnow() - timedelta(days=1, hours=12)
    student.created_at = signup
    pg_db_session.commit()

    _add_question(pg_db_session, student.id, signup + timedelta(days=1, hours=2))
    evaluate_habit_milestones(pg_db_session, student)
    pg_db_session.refresh(student)

    assert "day1" in student.habit_milestones_paid
    assert cost_tracker.get_balance(pg_db_session, student.id) == 5.0


def test_habit_milestone_not_paid_without_activity(pg_db_session):
    student = _make_student(pg_db_session)
    signup = _utcnow() - timedelta(days=1, hours=12)
    student.created_at = signup
    pg_db_session.commit()

    evaluate_habit_milestones(pg_db_session, student)
    pg_db_session.refresh(student)

    assert "day1" not in (student.habit_milestones_paid or [])
    assert cost_tracker.get_balance(pg_db_session, student.id) == 0.0


def test_full_21_day_habit_schedule(pg_db_session):
    student = _make_student(pg_db_session)

    for name, start, end, bonus in HABIT_MILESTONES:
        mid_days = (start + end) / 2
        student.created_at = _utcnow() - timedelta(days=mid_days)
        pg_db_session.commit()
        _add_question(pg_db_session, student.id, student.created_at + timedelta(days=mid_days - 0.01))
        evaluate_habit_milestones(pg_db_session, student)

    pg_db_session.refresh(student)
    expected_total = sum(b for _, _, _, b in HABIT_MILESTONES)
    assert set(student.habit_milestones_paid) == {name for name, _, _, _ in HABIT_MILESTONES}
    assert cost_tracker.get_balance(pg_db_session, student.id) == expected_total
    assert expected_total == 45.0  # 5 + 5 + 10 + 10 + 15
