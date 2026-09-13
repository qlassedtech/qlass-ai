"""
Tests for GET /admin/analytics/ai-costs (see app.routers.admin.get_ai_costs)
— per-feature Claude spend visibility, gated the same way as the sales
pipeline (/admin/schools): only super_admin/org_admin, never a plain
school-level "teacher" or "admin", since this is business-sensitive
billing/COGS data. Calls the router function directly, matching
test_admin_classrooms.py's pattern (no HTTP layer/token needed).
"""
import pytest
from fastapi import HTTPException

from app.models.core import Centre, Student, Teacher
from app.routers.admin import get_ai_costs
from app.services import cost_tracker


def _make_school(db_session, name="Test School"):
    centre = Centre(name=name)
    db_session.add(centre)
    db_session.commit()
    return centre


def _make_teacher(db_session, centre, phone, role="teacher"):
    teacher = Teacher(name="T", phone=phone, centre_id=centre.id, role=role)
    db_session.add(teacher)
    db_session.commit()
    return teacher


def _make_student(db_session, centre, phone="919000000001"):
    student = Student(name="Student", phone=phone, centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    db_session.refresh(student)
    return student


def test_teacher_role_forbidden(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000010", role="teacher")

    with pytest.raises(HTTPException) as exc_info:
        get_ai_costs(days=30, db=db_session, teacher=teacher)
    assert exc_info.value.status_code == 403


def test_school_admin_role_forbidden(db_session):
    """
    A school's own "admin" can see /admin/analytics for their own school,
    but this is a DIFFERENT, more sensitive endpoint (Qlass's own AI COGS
    across every school) — same gating as /admin/schools, which a plain
    "admin" also can't see.
    """
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000011", role="admin")

    with pytest.raises(HTTPException) as exc_info:
        get_ai_costs(days=30, db=db_session, teacher=teacher)
    assert exc_info.value.status_code == 403


def test_super_admin_sees_breakdown(db_session):
    centre = _make_school(db_session)
    student = _make_student(db_session, centre)
    cost_tracker.record_claude_usage(
        db_session, "claude-sonnet-4-6", 1000, 500, student.id, feature="tutor_reply",
    )
    super_admin = Teacher(name="SA", phone="919000000012", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()

    result = get_ai_costs(days=30, db=db_session, teacher=super_admin)
    assert any(row["feature"] == "tutor_reply" for row in result)


def test_org_admin_sees_breakdown(db_session):
    centre = _make_school(db_session)
    student = _make_student(db_session, centre)
    cost_tracker.record_claude_usage(
        db_session, "claude-haiku-4-5-20251001", 500, 200, student.id, feature="quiz_generate",
    )
    org_admin = Teacher(name="OA", phone="919000000013", role="org_admin")
    db_session.add(org_admin)
    db_session.commit()

    result = get_ai_costs(days=30, db=db_session, teacher=org_admin)
    assert any(row["feature"] == "quiz_generate" for row in result)
