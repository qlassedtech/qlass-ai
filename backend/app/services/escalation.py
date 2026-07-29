from sqlalchemy.orm import Session

from app.models.core import Student, Teacher

# After this many consecutive hint-only turns (no direct solve in between),
# the student is probably stuck on something the AI tutor alone isn't
# resolving — worth a human teacher's attention rather than just more hints.
ESCALATION_THRESHOLD = 3


def record_hint_outcome(student: Student, solved_directly: bool | None) -> bool:
    """
    Updates the consecutive-unresolved-hints streak for this turn and
    returns True if it just crossed ESCALATION_THRESHOLD (the caller is
    responsible for actually notifying the teacher and committing/resetting
    the counter — this function only mutates the in-memory Student object).
    """
    if solved_directly is True:
        student.consecutive_unresolved_hints = 0
        return False
    if solved_directly is False:
        student.consecutive_unresolved_hints = (student.consecutive_unresolved_hints or 0) + 1
        return student.consecutive_unresolved_hints >= ESCALATION_THRESHOLD
    return False  # solved_directly is None — not a problem-to-solve turn, no change


def get_escalation_recipients(db: Session, centre_id: int | None) -> list[Teacher]:
    """
    Every teacher/admin at this student's school — there's no per-student
    "assigned teacher" concept in this product yet, and schools using this
    are small enough that notifying the whole staff is reasonable rather
    than guessing at a single owner.
    """
    if centre_id is None:
        return []
    return db.query(Teacher).filter(Teacher.centre_id == centre_id, Teacher.role.in_(("teacher", "admin"))).all()


def format_escalation_message(student_name: str, topic: str | None) -> str:
    topic_note = f" with *{topic}*" if topic else ""
    return (
        f"🆘 Heads up — {student_name} has needed hints on {ESCALATION_THRESHOLD} questions in a row"
        f"{topic_note} without solving one directly. Might be worth a quick check-in with them."
    )


def format_student_requested_help_message(student_name: str) -> str:
    """
    Distinct from format_escalation_message — this is the student
    explicitly asking for a teacher (via the "🆘 Talk to Teacher" menu
    button or typing the phrase directly), not the automatic hint-streak
    trigger, so it shouldn't imply they've been struggling on any specific
    question.
    """
    return f"🙋 {student_name} just asked to talk to their teacher on Qlass AI Tutor — might be worth reaching out."
