from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.core import Student

# Shown to a parent before they confirm consent (see record_consent) — kept
# here as the single source of truth for what's actually being agreed to,
# rather than duplicating this text in the frontend and letting it drift.
CONSENT_STATEMENT = (
    "I confirm I am this student's parent/guardian and consent to Skoolgpt "
    "Learning collecting and processing my child's chat history, academic "
    "performance data, and phone number to provide AI tutoring on their "
    "school's behalf."
)


def has_given_consent(student: Student) -> bool:
    return student.consent_given_at is not None


def record_consent(db: Session, student_id: int) -> Student:
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise ValueError(f"no student with id={student_id}")
    student.consent_given_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(student)
    return student
