"""
Regression test for a real, confirmed production bug found while adding
per-feature Claude cost tracking: POST /admin/quizzes/assign billed the
school ledger via `school_billing.record_claude_usage(db, centre_id,
"quiz_assignment", ...)`, but "quiz_assignment" was never a key in
school_billing.PRICING (only "workbook_pdf" and "roster_extraction" were) —
so every real "assign quiz to class" request crashed with a KeyError right
after generating the questions, with no test catching it. Fixed by adding a
"quiz_assignment" PRICING entry (Haiku rates, matching QUIZ_MODEL). This
test just needs to confirm the endpoint no longer raises.
"""
from unittest.mock import AsyncMock, patch

from app.models.core import Centre, Student, Teacher
from app.routers import admin
from app.services import school_billing
from app.services.llm_client import LLMResult


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


def _make_student(db_session, centre, phone, class_="10"):
    student = Student(name="Student", phone=phone, centre_id=centre.id, class_=class_)
    db_session.add(student)
    db_session.commit()
    return student


async def test_assign_quiz_bills_school_ledger_without_keyerror(db_session):
    centre = _make_school(db_session)
    teacher = _make_teacher(db_session, centre, "919000000020")
    _make_student(db_session, centre, "919000000021", class_="10")
    school_billing.add_trial_credits(db_session, centre.id)
    balance_before = school_billing.get_balance(db_session, centre.id)

    fake_questions = [{"question": "2+2?", "answer": "4", "question_type": "short_answer"}]
    fake_result = LLMResult(text="...", model="claude-haiku-4-5-20251001", input_tokens=100, output_tokens=50)

    with patch.object(admin, "generate_quiz_questions", AsyncMock(return_value=(fake_questions, fake_result))):
        response = await admin.assign_quiz(
            admin.AssignQuizRequest(topic="Addition", class_="10"), db=db_session, teacher=teacher,
        )

    assert response is not None
    balance_after = school_billing.get_balance(db_session, centre.id)
    assert balance_after < balance_before  # actually billed, not silently skipped
