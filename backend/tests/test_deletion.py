from datetime import datetime, timezone

from app.models.core import Centre, ChatHistory, Parent, Student, TopicProgress
from app.services.deletion import ANONYMIZED_NAME, fulfill_deletion_request, list_pending_deletion_requests


def _make_student(db_session, centre_id=None, deletion_requested=False):
    if centre_id is None:
        centre = Centre(name="Test School")
        db_session.add(centre)
        db_session.commit()
        centre_id = centre.id
    student = Student(
        name="Real Student", phone="919000000099", centre_id=centre_id,
        deletion_requested_at=datetime.now(timezone.utc) if deletion_requested else None,
    )
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_pending_deletion_requests_only_lists_unfulfilled(db_session):
    pending = _make_student(db_session, deletion_requested=True)
    not_requested = _make_student(db_session, centre_id=pending.centre_id, deletion_requested=False)

    requests = list_pending_deletion_requests(db_session, centre_id=None)
    ids = [s.id for s in requests]
    assert pending.id in ids
    assert not_requested.id not in ids


def test_fulfill_deletion_anonymizes_and_removes_content(db_session):
    student = _make_student(db_session, deletion_requested=True)
    db_session.add(ChatHistory(student_id=student.id, role="user", message="hi"))
    db_session.add(TopicProgress(student_id=student.id, topic="algebra", is_correct=True))
    db_session.add(Parent(student_id=student.id, name="Parent", phone="919000000098"))
    db_session.commit()

    fulfill_deletion_request(db_session, student.id)

    updated = db_session.query(Student).filter(Student.id == student.id).first()
    assert updated.name == ANONYMIZED_NAME
    assert updated.phone != "919000000099"
    assert updated.is_deleted is True
    assert db_session.query(ChatHistory).filter(ChatHistory.student_id == student.id).count() == 0
    assert db_session.query(TopicProgress).filter(TopicProgress.student_id == student.id).count() == 0
    assert db_session.query(Parent).filter(Parent.student_id == student.id).count() == 0


def test_fulfill_deletion_keeps_credit_events_for_audit_trail(db_session):
    from app.models.core import CreditEvent

    student = _make_student(db_session, deletion_requested=True)
    db_session.add(CreditEvent(amount=-10, student_id=student.id, service="claude_sonnet"))
    db_session.commit()

    fulfill_deletion_request(db_session, student.id)

    assert db_session.query(CreditEvent).filter(CreditEvent.student_id == student.id).count() == 1
