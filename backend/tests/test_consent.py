from app.models.core import Centre, Student
from app.services.consent import has_given_consent, record_consent


def _make_student(db_session):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone="919000000001", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_consent_not_given_by_default(db_session):
    student = _make_student(db_session)
    assert has_given_consent(student) is False


def test_record_consent_sets_timestamp(db_session):
    student = _make_student(db_session)
    updated = record_consent(db_session, student.id)
    assert has_given_consent(updated) is True
    assert updated.consent_given_at is not None
