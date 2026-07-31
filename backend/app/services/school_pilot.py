from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.core import Centre, ChatHistory, CreditEvent, SchoolCreditEvent, SchoolPilotGrant, Student
from app.services import cost_tracker, school_billing

PILOT_CREDIT_SERVICE = "school_pilot_credit"
PILOT_TEACHER_TOOLS_SERVICE = "school_pilot_teacher_tools"

# A pilot needs enough breadth to prove value, without exposing an
# unbounded cost surface. Image generation remains a paid add-on because a
# single image can consume a large proportion of a learner's pilot wallet.
PILOT_STUDENT_FEATURES = {
    "voice": True,
    "ocr": True,
    "image_generation": False,
    "documents": True,
    "youtube_videos": True,
}
MAX_PILOT_STUDENTS = 100
MAX_PILOT_CREDITS_PER_STUDENT = 50.0
MAX_PILOT_TEACHER_TOOL_CREDITS = 500.0
MIN_PILOT_DAYS = 7
MAX_PILOT_DAYS = 45


def launch_pilot(
    db: Session, centre: Centre, student_ids: list[int], credits_per_student: float,
    duration_days: int, teacher_tool_credits: float = MAX_PILOT_TEACHER_TOOL_CREDITS,
    note: str | None = None,
) -> list[Student]:
    """Start one controlled, atomic school pilot.

    Student wallets and the shared teacher-tool wallet are funded in the
    same transaction. A pilot cannot be silently relaunched while active;
    that prevents duplicate credit grants from a retry or sales action.
    """
    if not MIN_PILOT_DAYS <= duration_days <= MAX_PILOT_DAYS:
        raise ValueError(f"pilot duration must be between {MIN_PILOT_DAYS} and {MAX_PILOT_DAYS} days")
    if not 0 < credits_per_student <= MAX_PILOT_CREDITS_PER_STUDENT:
        raise ValueError(f"credits per student must be between ₹1 and ₹{MAX_PILOT_CREDITS_PER_STUDENT:.0f}")
    if not 0 <= teacher_tool_credits <= MAX_PILOT_TEACHER_TOOL_CREDITS:
        raise ValueError(f"teacher-tool credits must be between ₹0 and ₹{MAX_PILOT_TEACHER_TOOL_CREDITS:.0f}")
    if len(set(student_ids)) > MAX_PILOT_STUDENTS:
        raise ValueError(f"a pilot can include at most {MAX_PILOT_STUDENTS} students")
    now = datetime.now(timezone.utc)
    existing_expiry = centre.pilot_expires_at
    if existing_expiry and existing_expiry.tzinfo is None:
        existing_expiry = existing_expiry.replace(tzinfo=timezone.utc)
    if centre.pilot_status == "active" and existing_expiry and existing_expiry > now:
        raise ValueError("this school already has an active pilot")
    students = db.query(Student).filter(
        Student.id.in_(student_ids), Student.centre_id == centre.id,
        Student.is_staff_profile.is_(False), Student.is_deleted.is_(False),
    ).all()
    if len(students) != len(set(student_ids)):
        raise ValueError("every selected student must be an active learner in this school")
    centre.sales_status = "trial"
    centre.pilot_status = "active"
    centre.pilot_started_at = now
    centre.pilot_expires_at = now + timedelta(days=duration_days)
    for student in students:
        db.add(SchoolPilotGrant(
            centre_id=centre.id, student_id=student.id, pilot_started_at=now, amount=credits_per_student,
        ))
        student.features = {**(student.features or {}), **PILOT_STUDENT_FEATURES}
        db.add(CreditEvent(
            amount=credits_per_student, student_id=student.id,
            note=note or f"School pilot credit — {centre.name}", service=PILOT_CREDIT_SERVICE,
        ))
    if teacher_tool_credits:
        db.add(SchoolCreditEvent(
            amount=teacher_tool_credits, centre_id=centre.id,
            note=note or f"School pilot teacher tools — {centre.name}", service=PILOT_TEACHER_TOOLS_SERVICE,
        ))
    db.commit()
    return students


def pilot_outcome_report(db: Session, centre: Centre) -> dict:
    """A decision-grade snapshot for the school/government pilot owner."""
    grants = db.query(SchoolPilotGrant).filter(SchoolPilotGrant.centre_id == centre.id).all()
    student_ids = list({grant.student_id for grant in grants})
    started_at = centre.pilot_started_at
    if not student_ids or started_at is None:
        return {
            "school": centre.name, "pilot_status": centre.pilot_status, "pilot_started_at": started_at,
            "pilot_expires_at": centre.pilot_expires_at, "students_granted": 0, "students_activated": 0,
            "activation_rate_pct": 0, "student_messages": 0, "teacher_tool_spend": 0.0,
        }
    activated = (
        db.query(func.count(func.distinct(ChatHistory.student_id)))
        .filter(ChatHistory.student_id.in_(student_ids), ChatHistory.role == "user", ChatHistory.created_at >= started_at)
        .scalar() or 0
    )
    messages = (
        db.query(func.count(ChatHistory.id))
        .filter(ChatHistory.student_id.in_(student_ids), ChatHistory.role == "user", ChatHistory.created_at >= started_at)
        .scalar() or 0
    )
    teacher_tool_spend = (
        db.query(func.coalesce(func.sum(-SchoolCreditEvent.amount), 0))
        .filter(
            SchoolCreditEvent.centre_id == centre.id,
            SchoolCreditEvent.amount < 0,
            SchoolCreditEvent.created_at >= started_at,
        ).scalar() or 0
    )
    granted_credit = sum(float(grant.amount) for grant in grants)
    return {
        "school": centre.name,
        "pilot_status": centre.pilot_status,
        "pilot_started_at": centre.pilot_started_at,
        "pilot_expires_at": centre.pilot_expires_at,
        "students_granted": len(student_ids),
        "student_credit_granted": granted_credit,
        "students_activated": int(activated),
        "activation_rate_pct": round(int(activated) * 100 / len(student_ids), 1),
        "student_messages": int(messages),
        "messages_per_activated_student": round(int(messages) / int(activated), 1) if activated else 0,
        "teacher_tool_spend": float(teacher_tool_spend),
    }
