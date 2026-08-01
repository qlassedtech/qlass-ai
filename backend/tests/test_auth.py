"""
Regression tests for the audit-fixed JWT type-separation — a teacher token
must never work as a student token and vice versa, even if a teacher_id
and a student_id happen to collide numerically.
"""
import jwt as pyjwt

from app.config import settings
from app.services.teacher_auth import create_access_token, JWT_ALGORITHM
from app.services.student_auth import create_student_access_token


def test_teacher_token_has_type_claim():
    token = create_access_token(teacher_id=5)
    payload = pyjwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    assert payload["type"] == "teacher"
    assert payload["sub"] == "5"


def test_student_token_has_type_claim():
    token = create_student_access_token(student_id=5)
    payload = pyjwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    assert payload["type"] == "student"
    assert payload["sub"] == "5"


def test_teacher_and_student_tokens_are_distinguishable_even_with_same_id():
    """
    The exact scenario the type claim protects against: a teacher and a
    student happen to share the same numeric id (5) in their respective
    tables — the two tokens must still be unambiguous.
    """
    teacher_token = create_access_token(teacher_id=5)
    student_token = create_student_access_token(student_id=5)
    assert teacher_token != student_token

    teacher_payload = pyjwt.decode(teacher_token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    student_payload = pyjwt.decode(student_token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    assert teacher_payload["type"] != student_payload["type"]
