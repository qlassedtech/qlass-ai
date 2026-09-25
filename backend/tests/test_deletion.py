from datetime import datetime, timezone

from sqlalchemy import inspect as sa_inspect

from app.models.core import (
    Answer, Centre, ChatHistory, CreditEvent, Notification, Parent, ProcessedWebhookMessage, Question, Quiz,
    RevisionSchedule, Student, StudySession, TopicProgress,
)
from app.services import deletion
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


def test_fulfill_deletion_leaves_no_phone_or_email_anywhere(db_session, tmp_path, monkeypatch):
    """
    Audit H4: the original erasure cleared name/phone/photo_url but left
    whatsapp_phone/email/fcm_token/referral_code on the row, plus quiz
    answers, revision schedule, study sessions, notifications to the
    phone, raw webhook payloads from the phone, and the photo file on
    disk. Sweep every column and every related table for the identifiers.
    """
    phone, wa_phone, email = "919000000099", "919000000077", "asha@example.com"
    monkeypatch.setattr(deletion, "UPLOAD_ROOT", tmp_path)
    photo = tmp_path / "photos" / "abc123.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"\xff\xd8\xff fake jpeg")

    student = _make_student(db_session, deletion_requested=True)
    student.whatsapp_phone = wa_phone
    student.email = email
    student.fcm_token = "fcm-token-xyz"
    student.referral_code = "ASHA123"
    student.password_hash = "hash"
    student.photo_url = "/static/uploads/photos/abc123.jpg"
    student.last_discussed_topic = "Real Numbers"
    db_session.commit()

    quiz = Quiz(student_id=student.id)
    db_session.add(quiz)
    db_session.commit()
    question = Question(quiz_id=quiz.id, question_text="2+2?", correct_answer="4")
    db_session.add(question)
    db_session.commit()
    db_session.add_all([
        Answer(question_id=question.id, student_id=student.id, given_answer="4", is_correct=True),
        RevisionSchedule(student_id=student.id, topic="algebra", due_at=datetime.now(timezone.utc)),
        StudySession(student_id=student.id),
        Notification(recipient_phone=phone, message="digest"),
        Notification(recipient_phone=wa_phone, message="reminder"),
        Notification(recipient_phone="919000000055", message="someone else's"),
        ProcessedWebhookMessage(message_id="m1", status="completed", payload={"waId": phone, "text": "my homework"}),
        ProcessedWebhookMessage(message_id="m2", status="completed", payload={"waId": wa_phone, "text": "hi"}),
        ProcessedWebhookMessage(message_id="m3", status="completed", payload={"waId": "919000000055", "text": "other"}),
        ProcessedWebhookMessage(message_id="m4", status="completed", payload=None),
    ])
    db_session.commit()

    fulfill_deletion_request(db_session, student.id)

    updated = db_session.query(Student).filter(Student.id == student.id).first()
    for column in sa_inspect(Student).columns.keys():
        value = str(getattr(updated, column) or "")
        assert phone not in value, column
        assert wa_phone not in value, column
        assert email not in value, column
    assert updated.whatsapp_phone is None and updated.email is None
    assert updated.fcm_token is None and updated.referral_code is None
    assert updated.password_hash is None and updated.last_discussed_topic is None
    assert updated.photo_url is None
    assert not photo.exists()

    assert db_session.query(Answer).filter(Answer.student_id == student.id).count() == 0
    assert db_session.query(RevisionSchedule).filter(RevisionSchedule.student_id == student.id).count() == 0
    assert db_session.query(StudySession).filter(StudySession.student_id == student.id).count() == 0
    assert db_session.query(Notification).filter(Notification.recipient_phone.in_([phone, wa_phone])).count() == 0
    assert db_session.query(Notification).count() == 1  # the unrelated recipient's row is untouched
    remaining_ids = {row.message_id for row in db_session.query(ProcessedWebhookMessage).all()}
    assert remaining_ids == {"m3", "m4"}


def test_fulfill_deletion_never_deletes_a_file_outside_the_uploads_root(db_session, tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(deletion, "UPLOAD_ROOT", uploads)
    outside = tmp_path / "secret.txt"
    outside.write_text("keep me")

    student = _make_student(db_session, deletion_requested=True)
    student.photo_url = "/static/uploads/../secret.txt"
    db_session.commit()

    fulfill_deletion_request(db_session, student.id)

    assert outside.exists()
