from sqlalchemy.orm import Session

from app.models.core import Student, Teacher

# The one escalation path guaranteed to reach an actual human regardless of
# whether a student is linked to a real partner school at all — a self-
# signup "Qlass Direct" student has zero registered teachers to notify
# (confirmed live: get_escalation_recipients returns [] for that centre),
# so every "talk to a human" flow across every channel (WhatsApp, web,
# Android, teacher's My AI Tutor) needs this as the honest fallback rather
# than claiming a teacher was notified when nobody was.
QLASS_SUPPORT_PHONE = "9031003985"

# After this many consecutive WRONG check-question answers, the student has
# made several genuine attempts and still isn't getting it — worth a human
# teacher's attention. Deliberately NOT based on the tutor giving a hint
# instead of solving a problem outright (see record_hint_outcome) — that's
# just the tutor's normal hint-first teaching style and happens on almost
# every problem regardless of whether the student is actually struggling,
# so counting it toward escalation fired a "might be worth a check-in"
# message to the teacher for students who were doing completely fine.
# Escalating to a human is a real event this product should reach for
# sparingly — the AI tutor is expected to be the front line, not routinely
# loop in a teacher.
ESCALATION_THRESHOLD = 4


def record_hint_outcome(student: Student, evaluated: bool, correct: bool | None) -> bool:
    """
    Updates the consecutive-wrong-answers streak for this turn and returns
    True if it just crossed ESCALATION_THRESHOLD (the caller is responsible
    for actually notifying the teacher and committing/resetting the counter
    — this function only mutates the in-memory Student object). Only
    `evaluated` turns move the counter at all — an explanation, a hint on a
    fresh problem, or off-topic chat doesn't mean anything either way about
    whether the student is stuck.
    """
    if not evaluated:
        return False
    if correct is True:
        student.consecutive_unresolved_hints = 0
        return False
    student.consecutive_unresolved_hints = (student.consecutive_unresolved_hints or 0) + 1
    return student.consecutive_unresolved_hints >= ESCALATION_THRESHOLD


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
        f"🆘 Heads up — {student_name} has gotten {ESCALATION_THRESHOLD} check questions wrong in a row"
        f"{topic_note} despite multiple attempts. Might be worth a quick check-in with them."
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
