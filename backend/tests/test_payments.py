import pytest
from fastapi import HTTPException

from app.models.core import Student
from app.routers.payments import _find_real_student


def _make_student(db_session, phone, name="Student", is_staff_profile=False):
    student = Student(name=name, phone=phone, is_staff_profile=is_staff_profile)
    db_session.add(student)
    db_session.commit()
    return student


def test_find_real_student_resolves_by_student_id_on_a_shared_family_phone(db_session):
    # Regression test for a real bug: a shared family phone with two
    # children previously always resolved to whichever sibling had the
    # LOWEST database id, regardless of which one a payment link was
    # actually generated for — silently crediting the wrong child's
    # wallet. student_id must let the caller pin the exact student.
    older = _make_student(db_session, "919000000001", name="Older Sibling")
    younger = _make_student(db_session, "919000000001", name="Younger Sibling")

    resolved = _find_real_student(db_session, "919000000001", student_id=younger.id)

    assert resolved.id == younger.id
    assert resolved.id != older.id


def test_find_real_student_falls_back_to_lowest_id_without_student_id(db_session):
    older = _make_student(db_session, "919000000002", name="Older Sibling")
    _make_student(db_session, "919000000002", name="Younger Sibling")

    resolved = _find_real_student(db_session, "919000000002")

    assert resolved.id == older.id


def test_find_real_student_rejects_student_id_that_does_not_match_phone(db_session):
    # A student_id must never let a caller claim a payment against an
    # unrelated phone number's student — student_id only narrows within
    # that phone's own real profiles, it never widens access.
    unrelated = _make_student(db_session, "919000000099", name="Unrelated Student")

    with pytest.raises(HTTPException) as exc_info:
        _find_real_student(db_session, "919000000003", student_id=unrelated.id)
    assert exc_info.value.status_code == 404


def test_find_real_student_excludes_staff_profiles(db_session):
    _make_student(db_session, "919000000004", name="Teacher's Own Tutor Profile", is_staff_profile=True)
    real = _make_student(db_session, "919000000004", name="Real Student")

    resolved = _find_real_student(db_session, "919000000004")

    assert resolved.id == real.id


def test_find_real_student_404s_when_no_student_exists_for_phone(db_session):
    with pytest.raises(HTTPException) as exc_info:
        _find_real_student(db_session, "919999999999")
    assert exc_info.value.status_code == 404
