import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.core import (
    Answer, ChatHistory, Notification, Parent, ProcessedWebhookMessage, RevisionSchedule, Student, StudySession,
    TopicProgress,
)
from app.services.uploads import UPLOAD_ROOT

logger = logging.getLogger(__name__)

ANONYMIZED_NAME = "Deleted Student"
UPLOAD_URL_PREFIX = "/static/uploads/"


def list_pending_deletion_requests(db: Session, centre_id: int | None) -> list[Student]:
    query = db.query(Student).filter(Student.deletion_requested_at.isnot(None), Student.is_deleted.is_(False))
    if centre_id is not None:
        query = query.filter(Student.centre_id == centre_id)
    return query.order_by(Student.deletion_requested_at.asc()).all()


def _delete_upload_file(photo_url: str | None) -> None:
    """
    Removes the photo file save_image_upload wrote under
    backend/static/uploads/ — nulling Student.photo_url alone left the
    actual image on disk, still served at its (random but guessable-if-
    known) URL. Only ever touches a path that resolves inside UPLOAD_ROOT,
    so a malformed/foreign photo_url can't delete anything else.
    """
    if not photo_url or not photo_url.startswith(UPLOAD_URL_PREFIX):
        return
    path = (UPLOAD_ROOT / photo_url[len(UPLOAD_URL_PREFIX):]).resolve()
    if UPLOAD_ROOT.resolve() not in path.parents:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("could not delete upload file %s during deletion fulfilment", path)


def _delete_webhook_payloads_for_phones(db: Session, phones: set[str]) -> None:
    """
    Raw inbound WATI payloads (see app.routers.whatsapp) carry the sender's
    number as `waId` plus the message text — deleted here for the
    student's phone(s). `payload["waId"].as_string()` is SQLAlchemy's
    dialect-portable JSON path lookup: `payload ->> 'waId'` on Postgres
    (JSONB), `JSON_EXTRACT(payload, '$."waId"')` on the SQLite test DB —
    same code runs on both.
    """
    if not phones:
        return
    (
        db.query(ProcessedWebhookMessage)
        .filter(ProcessedWebhookMessage.payload["waId"].as_string().in_(phones))
        .delete(synchronize_session=False)
    )


def fulfill_deletion_request(db: Session, student_id: int) -> Student:
    """
    Erases this student's PII and content, keeping only what's needed for
    Qlass's own financial audit trail (credit_events rows stay — they're
    keyed by student_id, not by name/phone, and their `note` is only ever
    a system label like "Qlass trial credit"/"Habit milestone: …" or an
    admin's own free text, never the student's phone/name/email; deleting
    a paid ledger would break accounting). Everything else that carries
    the student's identity or content goes: chat history, topic-level
    academic records and quiz answers, spaced-repetition schedule, study
    sessions, the linked parent, notifications addressed to their phone,
    raw webhook payloads from their phone, and the uploaded photo file
    itself — plus every identifying column on the Student row (phone,
    whatsapp_phone, email, fcm_token, referral_code), not just name/phone.
    """
    student = db.query(Student).filter(Student.id == student_id).first()
    if student is None:
        raise ValueError(f"no student with id={student_id}")

    phones = {p for p in (student.phone, student.whatsapp_phone) if p}

    db.query(ChatHistory).filter(ChatHistory.student_id == student_id).delete()
    db.query(TopicProgress).filter(TopicProgress.student_id == student_id).delete()
    db.query(Answer).filter(Answer.student_id == student_id).delete()
    db.query(RevisionSchedule).filter(RevisionSchedule.student_id == student_id).delete()
    db.query(StudySession).filter(StudySession.student_id == student_id).delete()
    db.query(Parent).filter(Parent.student_id == student_id).delete()
    if phones:
        db.query(Notification).filter(Notification.recipient_phone.in_(phones)).delete(synchronize_session=False)
    _delete_webhook_payloads_for_phones(db, phones)
    _delete_upload_file(student.photo_url)

    student.name = ANONYMIZED_NAME
    student.phone = f"deleted-{student.id}"
    student.whatsapp_phone = None
    student.email = None
    student.fcm_token = None
    student.referral_code = None
    student.password_hash = None
    student.photo_url = None
    student.active_document_text = None
    student.last_discussed_topic = None
    student.gender = None
    student.school = None
    student.is_deleted = True
    db.commit()
    db.refresh(student)
    return student
