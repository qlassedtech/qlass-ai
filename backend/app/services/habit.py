from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.core import ChatHistory, Student
from app.services import cost_tracker
from app.services.whatsapp_client import send_whatsapp_message

# (milestone name, day-window start, day-window end [exclusive], bonus) —
# a 21-day engagement-building schedule: the student earns a small credit
# for each checkpoint day they're still actively asking questions, same
# "checked live on every message" mechanic as referral milestones (see
# app.services.referral), just paid to the student's OWN wallet rather than
# a referrer's.
HABIT_MILESTONES: list[tuple[str, int, int, float]] = [
    ("day1", 1, 2, 5.0),
    ("day3", 3, 4, 5.0),
    ("day7", 7, 8, 10.0),
    ("day14", 14, 15, 10.0),
    ("day21", 21, 22, 15.0),
]


async def evaluate_habit_milestones(db: Session, student: Student) -> None:
    """
    Called on every real tutoring question (see whatsapp.py and
    student_chat.py) — pays the student a small bonus the first time they
    engage on each of the day1/3/7/14/21 checkpoints since their own
    signup. No-ops immediately once every milestone is settled.
    """
    paid = set(student.habit_milestones_paid or [])
    if len(paid) == len(HABIT_MILESTONES):
        return

    created_at = student.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed_days = (datetime.now(timezone.utc) - created_at).days

    changed = False
    for name, start, end, bonus in HABIT_MILESTONES:
        if name in paid or not (start <= elapsed_days < end):
            continue
        window_start = created_at + timedelta(days=start)
        window_end = created_at + timedelta(days=end)
        has_activity = (
            db.query(ChatHistory)
            .filter(
                ChatHistory.student_id == student.id,
                ChatHistory.role == "user",
                ChatHistory.created_at >= window_start,
                ChatHistory.created_at < window_end,
            )
            .first()
            is not None
        )
        if has_activity:
            cost_tracker.grant_habit_credit(db, student.id, bonus, note=f"Habit milestone: {name}")
            paid.add(name)
            changed = True
            # A bonus a student never hears about doesn't build a habit —
            # it's just an invisible ledger entry. Best-effort: a WhatsApp
            # send failure here shouldn't break the actual tutoring reply
            # this was evaluated alongside.
            try:
                await send_whatsapp_message(
                    student.phone,
                    f"🔥 Nice streak! You've earned ₹{bonus:.0f} in bonus AI credits for sticking with "
                    "it. Keep it up!",
                )
            except Exception:
                pass

    if changed:
        student.habit_milestones_paid = list(paid)
        db.commit()
