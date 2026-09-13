from datetime import datetime, timedelta, timezone

from app.models.core import Centre, RevisionSchedule, Student
from app.services import revision_scheduler


def _make_student(db_session, phone="919000000020"):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone=phone, centre_id=centre.id, class_="8")
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def _aware(dt):
    # SQLite silently drops tzinfo on round-trip (see tests/conftest.py) —
    # reattach UTC before comparing against a tz-aware "now".
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _get_row(db_session, student_id, topic):
    return (
        db_session.query(RevisionSchedule)
        .filter(RevisionSchedule.student_id == student_id, RevisionSchedule.topic == topic)
        .first()
    )


def test_wrong_answer_creates_schedule_at_stage_zero_due_tomorrow(db_session):
    student = _make_student(db_session)

    revision_scheduler.on_topic_result(db_session, student.id, "Photosynthesis", is_correct=False)

    row = _get_row(db_session, student.id, "Photosynthesis")
    assert row is not None
    assert row.interval_stage == 0
    now = datetime.now(timezone.utc)
    expected_due = now + timedelta(days=revision_scheduler.LADDER_DAYS[0])
    assert abs((_aware(row.due_at) - expected_due).total_seconds()) < 5
    assert row.last_reviewed_at is not None


def test_correct_answer_with_no_existing_schedule_does_nothing(db_session):
    student = _make_student(db_session)

    revision_scheduler.on_topic_result(db_session, student.id, "Newton's Laws", is_correct=True)

    row = _get_row(db_session, student.id, "Newton's Laws")
    assert row is None


def test_correct_answer_on_existing_schedule_advances_stage_and_due_date(db_session):
    student = _make_student(db_session)
    revision_scheduler.on_topic_result(db_session, student.id, "Algebra", is_correct=False)
    row = _get_row(db_session, student.id, "Algebra")
    assert row.interval_stage == 0
    first_due_at = row.due_at

    revision_scheduler.on_topic_result(db_session, student.id, "Algebra", is_correct=True)

    row = _get_row(db_session, student.id, "Algebra")
    assert row.interval_stage == 1
    assert _aware(row.due_at) > _aware(first_due_at)
    now = datetime.now(timezone.utc)
    expected_due = now + timedelta(days=revision_scheduler.LADDER_DAYS[1])
    assert abs((_aware(row.due_at) - expected_due).total_seconds()) < 5


def test_subsequent_wrong_answer_resets_stage_to_zero(db_session):
    student = _make_student(db_session)
    revision_scheduler.on_topic_result(db_session, student.id, "Trigonometry", is_correct=False)
    revision_scheduler.on_topic_result(db_session, student.id, "Trigonometry", is_correct=True)
    row = _get_row(db_session, student.id, "Trigonometry")
    assert row.interval_stage == 1

    revision_scheduler.on_topic_result(db_session, student.id, "Trigonometry", is_correct=False)

    row = _get_row(db_session, student.id, "Trigonometry")
    assert row.interval_stage == 0
    now = datetime.now(timezone.utc)
    expected_due = now + timedelta(days=revision_scheduler.LADDER_DAYS[0])
    assert abs((_aware(row.due_at) - expected_due).total_seconds()) < 5


def test_get_due_reviews_returns_only_rows_at_or_past_due_date(db_session):
    student = _make_student(db_session)
    now = datetime.now(timezone.utc)

    overdue = RevisionSchedule(student_id=student.id, topic="Overdue Topic", due_at=now - timedelta(days=1))
    exactly_due = RevisionSchedule(student_id=student.id, topic="Exactly Due Topic", due_at=now)
    not_due_yet = RevisionSchedule(student_id=student.id, topic="Future Topic", due_at=now + timedelta(days=5))
    db_session.add_all([overdue, exactly_due, not_due_yet])
    db_session.commit()

    due = revision_scheduler.get_due_reviews(db_session, cutoff=now)
    due_topics = {row.topic for row in due}

    assert due_topics == {"Overdue Topic", "Exactly Due Topic"}
