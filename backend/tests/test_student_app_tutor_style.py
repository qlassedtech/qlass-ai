import pytest

from app.models.core import Centre, Student
from app.routers.student_app import SetTutorStyleRequest, set_tutor_style


def _make_student(db_session):
    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="Test Student", phone="919000000003", centre_id=centre.id, class_="8")
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_set_tutor_style_persists_hint_first(db_session):
    student = _make_student(db_session)
    assert student.tutor_style == "balanced"

    summary = set_tutor_style(SetTutorStyleRequest(style="hint_first"), db_session, student)

    assert student.tutor_style == "hint_first"
    assert summary["tutor_style"] == "hint_first"


def test_set_tutor_style_rejects_unknown_value(db_session):
    from fastapi import HTTPException

    student = _make_student(db_session)
    with pytest.raises(HTTPException):
        set_tutor_style(SetTutorStyleRequest(style="strict"), db_session, student)
