from sqlalchemy.orm import Session

from app.models.core import ChatHistory, Parent, Student, TopicProgress

ANONYMIZED_NAME = "Deleted Student"


def list_pending_deletion_requests(db: Session, centre_id: int | None) -> list[Student]:
    query = db.query(Student).filter(Student.deletion_requested_at.isnot(None), Student.is_deleted.is_(False))
    if centre_id is not None:
        query = query.filter(Student.centre_id == centre_id)
    return query.order_by(Student.deletion_requested_at.asc()).all()


def fulfill_deletion_request(db: Session, student_id: int) -> Student:
    """
    Erases this student's PII and content, keeping only what's needed for
    Qlass's own financial audit trail (credit_events rows stay — they're
    already keyed by student_id, not by name/phone, and deleting a paid
    ledger would break accounting). Chat history and topic-level academic
    records ARE deleted since those are the actual sensitive content a
    parent asked to be erased.
    """
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise ValueError(f"no student with id={student_id}")

    db.query(ChatHistory).filter(ChatHistory.student_id == student_id).delete()
    db.query(TopicProgress).filter(TopicProgress.student_id == student_id).delete()
    db.query(Parent).filter(Parent.student_id == student_id).delete()

    student.name = ANONYMIZED_NAME
    student.phone = f"deleted-{student.id}"
    student.photo_url = None
    student.active_document_text = None
    student.gender = None
    student.school = None
    student.is_deleted = True
    db.commit()
    db.refresh(student)
    return student
