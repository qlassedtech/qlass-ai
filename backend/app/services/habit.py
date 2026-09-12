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


def next_milestone_countdown(student: Student) -> str | None:
    """
    Proactive streak visibility — "Day 4 of your streak — 3 more days to
    your next ₹10 bonus!" — rather than only ever showing the streak
    number retroactively (see app.services.progress_report). Looks at the
    NEXT not-yet-earned HABIT_MILESTONES entry relative to elapsed days
    since signup, regardless of whether the student is active every single
    day (elapsed_days, like evaluate_habit_milestones above, is simple
    calendar time since Student.created_at, not a consecutive-activity
    streak) — this is a countdown to the next reward checkpoint, not the
    same "consecutive days active" number app.services.progress_report.
    get_activity_stats reports elsewhere; both are shown together in
    format_progress_message. Returns None once every milestone is already
    earned, or created_at is unset (shouldn't happen for a real row, but
    keeps this safe to call unconditionally).
    """
    paid = set(student.habit_milestones_paid or [])
    if len(paid) == len(HABIT_MILESTONES):
        return None

    created_at = student.created_at
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed_days = (datetime.now(timezone.utc) - created_at).days

    for name, start, _end, bonus in HABIT_MILESTONES:
        if name in paid:
            continue
        if elapsed_days < start:
            days_away = start - elapsed_days
            day_word = "day" if days_away == 1 else "days"
            return (
                f"Day {elapsed_days} of your journey — {days_away} more {day_word} to your next "
                f"₹{bonus:.0f} bonus!"
            )
        # Already inside this milestone's activity window (or past it) but
        # not yet paid — evaluate_habit_milestones pays it the moment
        # there's real activity in-window, so there's nothing meaningful to
        # count down to; move on to checking the next milestone instead of
        # reporting a misleading "0 days away" for one that's really just
        # pending today's message.
    return None
